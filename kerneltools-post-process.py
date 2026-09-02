#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Post-process kernel tool output and emit CDM metrics.

Runs in the kernel tool's data directory (one per profiler instance).
Dispatches to per-subtool handlers based on which output files exist.
Currently handles: turbostat, perf-stat, toplev, hw-counters.

Metrics emitted
---------------
turbostat — system aggregate (all topology fields = "-"):
    turbostat:cpu-busy-pct          utilization  %
    turbostat:cpu-freq-avg-mhz      throughput   MHz
    turbostat:package-power-watt    throughput   W

turbostat — per-CPU (numeric CPU field):
    turbostat:cpu-busy-pct          utilization  %     {cpu: N}
    turbostat:cpu-busy-freq-mhz     throughput   MHz   {cpu: N}
    turbostat:c1-pct                utilization  %     {cpu: N}
    turbostat:c2-pct                utilization  %     {cpu: N}
    turbostat:ipc                   throughput         {cpu: N}

perf-stat — per-CPU (from `perf stat -a -A -I N -x , -e cycles,instructions,...`):
    perf-stat:ipc                       throughput         {cpu: N}
    perf-stat:cache-miss-rate           percentage   %     {cpu: N}
    perf-stat:backend-stall-rate        percentage   %     {cpu: N}
    perf-stat:frontend-stall-rate       percentage   %     {cpu: N}

toplev — system-wide Top-Down Methodology (from `toplev.py -l3 -I N -x ,`):
    toplev:frontend-bound           percentage   %
    toplev:backend-bound            percentage   %
    toplev:memory-bound             percentage   %
    toplev:core-bound               percentage   %
    toplev:bad-speculation          percentage   %
    toplev:retiring                 percentage   %

hw-counters — per-CPU and per-UMC:
    perf-stat:ipc                       throughput         {cpu: N}
    perf-stat:cache-miss-rate           percentage   %     {cpu: N}
    perf-stat:backend-stall-rate        percentage   %     {cpu: N}
    perf-stat:frontend-stall-rate       percentage   %     {cpu: N}
    hw-umc:bytes-sec                    throughput   B/s   {num: N}
    hw-umc:cas-count                    count              {num: N}
    hw-umc:rd-bytes-sec                 throughput   B/s   {num: N}
    hw-umc:cas-rd-count                 count              {num: N}
    hw-umc:wr-bytes-sec                 throughput   B/s   {num: N}
    hw-umc:cas-wr-count                 count              {num: N}
"""

from __future__ import annotations

import os
import json
import re
import sys
import time
from pathlib import Path

TOOLBOX_HOME = os.environ.get("TOOLBOX_HOME")
if TOOLBOX_HOME:
    sys.path.append(str(Path(TOOLBOX_HOME) / "python"))

from toolbox.cdm_metrics import CDMMetrics
from toolbox.fileio import open_read_text_file

SOURCE = "turbostat"


def _safe_float(fields: list[str], col_idx: dict[str, int], name: str) -> float | None:
    idx = col_idx.get(name)
    if idx is None or idx >= len(fields):
        return None
    try:
        return float(fields[idx])
    except ValueError:
        return None


def process_turbostat(log_file: str) -> None:
    print(f"Post-processing turbostat: {log_file}")
    metrics = CDMMetrics()
    metric_idx_cache: dict[tuple, int] = {}

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    col_idx: dict[str, int] = {}

    for raw_line in fh:
        line = raw_line.rstrip("\n")

        # Column header — repeated at the start of each interval.
        if line.startswith("usec\t"):
            cols = line.split("\t")
            col_idx = {c: i for i, c in enumerate(cols)}
            continue

        # Skip topology preamble and blank lines.
        if not col_idx or not line:
            continue
        c = line[0]
        if not (c.isdigit() or c == " "):
            continue

        fields = line.split("\t")
        if len(fields) < 10:
            continue

        ts_ms_val = _safe_float(fields, col_idx, "Time_Of_Day_Seconds")
        if ts_ms_val is None:
            continue
        ts_ms = int(ts_ms_val * 1000)
        sample_base = {"end": ts_ms}

        package = fields[col_idx["Package"]] if "Package" in col_idx else "-"
        cpu_field = fields[col_idx["CPU"]] if "CPU" in col_idx else "-"

        if package == "-":
            # System aggregate row
            for metric_type, cdm_class, default_agg, col in (
                ("cpu-busy-pct",       "percentage",  "avg", "Busy%"),
                ("cpu-freq-avg-mhz",   "throughput",  "avg", "Avg_MHz"),
                ("package-power-watt", "throughput",  "sum", "PkgWatt"),
            ):
                val = _safe_float(fields, col_idx, col)
                if val is not None:
                    if cdm_class == "percentage":
                        val /= 100
                    cache_key = ("system", metric_type)
                    if cache_key in metric_idx_cache:
                        metrics.log_sample_by_idx(metric_idx_cache[cache_key], val, ts_ms)
                    else:
                        desc = {"source": SOURCE, "class": cdm_class, "type": metric_type, "default-aggregation": default_agg}
                        idx = metrics.log_sample(SOURCE, desc, {}, {**sample_base, "value": val})
                        metric_idx_cache[cache_key] = idx

        elif cpu_field not in ("-", ""):
            # Per-CPU row
            try:
                cpu_num = str(int(cpu_field, 0))
            except ValueError:
                continue
            names = {"cpu": cpu_num}
            for metric_type, cdm_class, default_agg, col in (
                ("cpu-busy-pct",      "percentage",  "avg", "Busy%"),
                ("cpu-busy-freq-mhz", "throughput",  "avg", "Bzy_MHz"),
                ("c1-pct",            "percentage",  "avg", "C1%"),
                ("c2-pct",            "percentage",  "avg", "C2%"),
                ("ipc",               "throughput",  "avg", "IPC"),
            ):
                val = _safe_float(fields, col_idx, col)
                if val is not None:
                    if cdm_class == "percentage":
                        val /= 100
                    cache_key = ("cpu", cpu_num, metric_type)
                    if cache_key in metric_idx_cache:
                        metrics.log_sample_by_idx(metric_idx_cache[cache_key], val, ts_ms)
                    else:
                        desc = {"source": SOURCE, "class": cdm_class, "type": metric_type, "default-aggregation": default_agg}
                        idx = metrics.log_sample(SOURCE, desc, names, {**sample_base, "value": val})
                        metric_idx_cache[cache_key] = idx

    fh.close()
    metrics.finish_samples()
    print("Post-processing for turbostat complete")


SOURCE_PERF_STAT = "perf-stat"
SOURCE_TOPLEV = "toplev"

# Mapping from toplev metric path fragments to CDM type names
_TOPLEV_METRIC_MAP = {
    "Frontend_Bound":              "frontend-bound",
    "Backend_Bound.Memory_Bound":  "memory-bound",
    "Backend_Bound.Core_Bound":    "core-bound",
    "Backend_Bound":               "backend-bound",
    "Bad_Speculation":             "bad-speculation",
    "Retiring":                    "retiring",
}


def get_turbostat_start_time() -> float | None:
    try:
        files = sorted(os.listdir("."))
        turbostat_files = [
            f for f in files
            if re.match(r"^turbostat-stdout\.txt(\.xz)?$", f)
        ]
        if not turbostat_files:
            return None
        fh, _ = open_read_text_file(turbostat_files[0])
        col_idx: dict[str, int] = {}
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if line.startswith("usec\t"):
                cols = line.split("\t")
                col_idx = {c: i for i, c in enumerate(cols)}
                continue
            if not col_idx or not line:
                continue
            c = line[0]
            if not (c.isdigit() or c == " "):
                continue
            fields = line.split("\t")
            val = _safe_float(fields, col_idx, "Time_Of_Day_Seconds")
            if val is not None:
                fh.close()
                return val
        fh.close()
    except Exception:
        pass
    return None


def get_boot_time(perf_first_ts: float = 0.0) -> float:
    """Determine the system boot time in seconds since Unix epoch."""
    # Method 1: Read from kerneltools-boot-time.txt if available
    try:
        if os.path.exists("kerneltools-boot-time.txt"):
            with open("kerneltools-boot-time.txt", "r") as f:
                val = f.read().strip()
                if val:
                    return float(val)
    except Exception:
        pass

    # Method 2: Align with turbostat's Time_Of_Day_Seconds if available
    if perf_first_ts > 0:
        turbostat_start = get_turbostat_start_time()
        if turbostat_start is not None:
            # Calculate boot time by subtracting the first perf monotonic timestamp
            # from the first turbostat epoch timestamp
            return turbostat_start - perf_first_ts

    # Method 3: Read /proc/stat btime
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except Exception:
        pass

    # Method 4: Fallback using python time and monotonic clocks
    try:
        return time.time() - time.monotonic()
    except Exception:
        pass

    return 0.0


def process_perf_stat(log_file: str) -> None:
    """Parse `perf stat -a -A -I N -x ,` CSV output and emit CDM metrics.

    CSV columns (from perf stat -x ,):
      timestamp, cpu, count, unit, event, event_runtime, pcnt_running, metric_value, metric_unit

    We collect per-CPU cycles and instructions across the interval, derive IPC,
    and emit cache-miss-rate and llc-load-miss-rate where events are present.
    """
    print(f"Post-processing perf-stat: {log_file}")

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    # Accumulate per-interval per-CPU event counts.
    # key: (timestamp_ms, cpu) -> {event: count}
    intervals: dict[tuple[int, str], dict[str, float]] = {}
    boot_time = None

    for raw_line in fh:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            ts_s   = float(parts[0])
            cpu    = parts[1].strip()          # e.g. "CPU3"
            count_s = parts[2].strip()
            event  = parts[4].strip()
        except (ValueError, IndexError):
            continue

        if count_s in ("<not supported>", "<not counted>", ""):
            continue
        try:
            count = float(count_s.replace(",", ""))
        except ValueError:
            continue

        # Normalise event name (strip qualifiers like ":u")
        event = event.split(":")[0]
        if boot_time is None:
            boot_time = get_boot_time(ts_s)
        ts_epoch_s = ts_s + boot_time
        ts_ms = int(round(ts_epoch_s * 1000))
        key = (ts_ms, cpu)
        intervals.setdefault(key, {})[event] = count

    fh.close()

    if not intervals:
        print("WARNING: no perf-stat data found")
        return

    metrics = CDMMetrics()

    for (ts_ms, cpu), evts in sorted(intervals.items()):
        cycles          = evts.get("cycles", 0)
        instructions    = evts.get("instructions", 0)
        cache_miss      = evts.get("cache-misses", None)
        cache_ref       = evts.get("cache-references", None)
        stall_backend   = evts.get("stalled-cycles-backend", None)
        stall_frontend  = evts.get("stalled-cycles-frontend", None)

        # Strip "CPU" prefix for the breakout name
        cpu_num = cpu.replace("CPU", "") if cpu.startswith("CPU") else cpu
        names = {"cpu": cpu_num}
        sample_base = {"end": ts_ms}

        if cycles > 0 and instructions > 0:
            ipc = instructions / cycles
            metrics.log_sample(
                SOURCE_PERF_STAT,
                {"source": SOURCE_PERF_STAT, "class": "throughput", "type": "ipc"},
                names,
                {**sample_base, "value": ipc},
            )

        if cache_miss is not None and cache_ref is not None and cache_ref > 0:
            rate = cache_miss / cache_ref * 100.0
            metrics.log_sample(
                SOURCE_PERF_STAT,
                {"source": SOURCE_PERF_STAT, "class": "percentage", "type": "cache-miss-rate"},
                names,
                {**sample_base, "value": rate},
            )

        if stall_backend is not None and cycles > 0:
            rate = stall_backend / cycles * 100.0
            metrics.log_sample(
                SOURCE_PERF_STAT,
                {"source": SOURCE_PERF_STAT, "class": "percentage", "type": "backend-stall-rate"},
                names,
                {**sample_base, "value": rate},
            )

        if stall_frontend is not None and cycles > 0:
            rate = stall_frontend / cycles * 100.0
            metrics.log_sample(
                SOURCE_PERF_STAT,
                {"source": SOURCE_PERF_STAT, "class": "percentage", "type": "frontend-stall-rate"},
                names,
                {**sample_base, "value": rate},
            )

    metrics.finish_samples()
    print("Post-processing for perf-stat complete")


def process_hw_counters(log_file: str) -> None:
    """Parse `hw-counters-stdout.txt` CSV output and emit CDM metrics."""
    print(f"Post-processing hw-counters: {log_file}")

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    metrics = CDMMetrics()
    found = 0

    for raw_line in fh:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("kind")
        ts_ms = rec.get("ts_ms", 0)
        if kind == "meta":
            continue
        elif kind == "cpu":
            cpu_num = str(rec.get("cpu", "?"))
            cycles = rec.get("cycles", 0)
            instructions = rec.get("instructions", 0)
            cache_misses = rec.get("cache_misses", 0)
            stall_backend = rec.get("stall_backend", 0)
            stall_frontend = rec.get("stall_frontend", 0)
            names = {"cpu": cpu_num}
            sample_base = {"end": ts_ms}
            if cycles > 0 and instructions > 0:
                metrics.log_sample(SOURCE_PERF_STAT, {"source": SOURCE_PERF_STAT, "class": "throughput", "type": "ipc"}, names, {**sample_base, "value": instructions / cycles})
            if cycles > 0:
                metrics.log_sample(SOURCE_PERF_STAT, {"source": SOURCE_PERF_STAT, "class": "percentage", "type": "cache-miss-rate"}, names, {**sample_base, "value": cache_misses / cycles * 100.0})
                metrics.log_sample(SOURCE_PERF_STAT, {"source": SOURCE_PERF_STAT, "class": "percentage", "type": "stall-backend-rate"}, names, {**sample_base, "value": stall_backend / cycles * 100.0})
                metrics.log_sample(SOURCE_PERF_STAT, {"source": SOURCE_PERF_STAT, "class": "percentage", "type": "stall-frontend-rate"}, names, {**sample_base, "value": stall_frontend / cycles * 100.0})
            found += 1
        elif kind == "umc":
            umc_name = rec.get("umc", "unknown")
            ev_name = rec.get("event", "cas_all")
            bytes_val = rec.get("bytes", 0)
            cas_delta = rec.get("cas_delta", 0)
            # Strip amd_umc_ prefix and use num (existing CDM field) for the breakout
            umc_num = umc_name.replace("amd_umc_", "") if umc_name.startswith("amd_umc_") else umc_name
            names = {"num": umc_num}
            sample_base = {"end": ts_ms}

            if ev_name == "cas_rd":
                metrics.log_sample("hw-umc", {"source": "hw-umc", "class": "throughput", "type": "rd-bytes-sec"}, names, {**sample_base, "value": bytes_val})
                metrics.log_sample("hw-umc", {"source": "hw-umc", "class": "count", "type": "cas-rd-count"}, names, {**sample_base, "value": cas_delta})
            elif ev_name == "cas_wr":
                metrics.log_sample("hw-umc", {"source": "hw-umc", "class": "throughput", "type": "wr-bytes-sec"}, names, {**sample_base, "value": bytes_val})
                metrics.log_sample("hw-umc", {"source": "hw-umc", "class": "count", "type": "cas-wr-count"}, names, {**sample_base, "value": cas_delta})
            else:
                metrics.log_sample("hw-umc", {"source": "hw-umc", "class": "throughput", "type": "bytes-sec"}, names, {**sample_base, "value": bytes_val})
                metrics.log_sample("hw-umc", {"source": "hw-umc", "class": "count", "type": "cas-count"}, names, {**sample_base, "value": cas_delta})
            found += 1

    fh.close()

    if found == 0:
        print("WARNING: no hw-counters metric data found")
        return

    metrics.finish_samples()
    print(f"Post-processing for hw-counters complete ({found} data points)")


def process_toplev(log_file: str) -> None:
    """Parse `toplev.py -l3 -I N -x ,` CSV output and emit CDM metrics.

    toplev CSV columns:
      timestamp, cpu, area, metric, value, unit, [description, ...]

    With -x , and no --cpu flag, cpu field is empty (system-wide).
    We emit each recognised Top-Down metric as a CDM utilization % sample.
    """
    print(f"Post-processing toplev: {log_file}")

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    metrics = CDMMetrics()
    found = 0
    boot_time = None

    for raw_line in fh:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            ts_s   = float(parts[0])
            metric = parts[3].strip()
            val_s  = parts[4].strip()
        except (ValueError, IndexError):
            continue

        if val_s in ("", "N/A", "nan"):
            continue
        try:
            value = float(val_s)
        except ValueError:
            continue

        # Match metric to a known CDM type
        cdm_type = None
        for key, name in _TOPLEV_METRIC_MAP.items():
            if key in metric:
                cdm_type = name
                break
        if cdm_type is None:
            continue

        if boot_time is None:
            boot_time = get_boot_time(ts_s)
        ts_epoch_s = ts_s + boot_time
        ts_ms = int(round(ts_epoch_s * 1000))
        desc = {"source": SOURCE_TOPLEV, "class": "percentage", "type": cdm_type}
        metrics.log_sample(SOURCE_TOPLEV, desc, {}, {"end": ts_ms, "value": value})
        found += 1

    fh.close()

    if found == 0:
        print("WARNING: no toplev metric data found")
        return

    metrics.finish_samples()
    print(f"Post-processing for toplev complete ({found} data points)")


def main() -> None:
    print("kerneltools-post-process")

    files = sorted(os.listdir("."))
    print(f"files to process:\n {' '.join(files)}")

    turbostat_files = [
        f for f in files
        if re.match(r"^turbostat-stdout\.txt(\.xz)?$", f)
    ]

    if len(turbostat_files) > 1:
        print(f"ERROR: multiple turbostat files found: {turbostat_files}")
    elif not turbostat_files:
        print("WARNING: no turbostat-stdout.txt file found — skipping")
    else:
        process_turbostat(turbostat_files[0])

    perf_stat_files = [
        f for f in files
        if re.match(r"^perf-stat-stdout\.txt(\.xz)?$", f)
    ]

    if len(perf_stat_files) > 1:
        print(f"ERROR: multiple perf-stat files found: {perf_stat_files}")
    elif perf_stat_files:
        process_perf_stat(perf_stat_files[0])

    hw_counters_files = [
        f for f in files
        if re.match(r"^hw-counters-stdout\.txt(\.xz)?$", f)
    ]

    if len(hw_counters_files) > 1:
        print(f"ERROR: multiple hw-counters files found: {hw_counters_files}")
    elif hw_counters_files:
        process_hw_counters(hw_counters_files[0])

    toplev_files = [
        f for f in files
        if re.match(r"^toplev-stdout\.txt(\.xz)?$", f)
    ]

    if len(toplev_files) > 1:
        print(f"ERROR: multiple toplev files found: {toplev_files}")
    elif toplev_files:
        process_toplev(toplev_files[0])

    print("kerneltools post-processing complete")


if __name__ == "__main__":
    main()
