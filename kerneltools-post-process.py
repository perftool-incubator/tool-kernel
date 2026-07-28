#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Post-process kernel tool output and emit CDM metrics.

Runs in the kernel tool's data directory (one per profiler instance).
Dispatches to per-subtool handlers based on which output files exist.
Currently handles: turbostat, perf-stat.

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
    perf-stat:cache-miss-rate           utilization  %     {cpu: N}
    perf-stat:backend-stall-rate        utilization  %     {cpu: N}
    perf-stat:frontend-stall-rate       utilization  %     {cpu: N}
"""

from __future__ import annotations

import os
import re
import sys
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
            for metric_type, cdm_class, col in (
                ("cpu-busy-pct",       "utilization", "Busy%"),
                ("cpu-freq-avg-mhz",   "throughput",  "Avg_MHz"),
                ("package-power-watt", "throughput",  "PkgWatt"),
            ):
                val = _safe_float(fields, col_idx, col)
                if val is not None:
                    desc = {"source": SOURCE, "class": cdm_class, "type": metric_type}
                    metrics.log_sample(SOURCE, desc, {}, {**sample_base, "value": val})

        elif cpu_field not in ("-", ""):
            # Per-CPU row
            try:
                cpu_num = str(int(cpu_field, 0))
            except ValueError:
                continue
            names = {"cpu": cpu_num}
            for metric_type, cdm_class, col in (
                ("cpu-busy-pct",      "utilization", "Busy%"),
                ("cpu-busy-freq-mhz", "throughput",  "Bzy_MHz"),
                ("c1-pct",            "utilization", "C1%"),
                ("c2-pct",            "utilization", "C2%"),
                ("ipc",               "throughput",  "IPC"),
            ):
                val = _safe_float(fields, col_idx, col)
                if val is not None:
                    desc = {"source": SOURCE, "class": cdm_class, "type": metric_type}
                    metrics.log_sample(SOURCE, desc, names, {**sample_base, "value": val})

    fh.close()
    metrics.finish_samples()
    print("Post-processing for turbostat complete")


SOURCE_PERF_STAT = "perf-stat"


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
        ts_ms = int(round(ts_s * 1000))
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
                {"source": SOURCE_PERF_STAT, "class": "utilization", "type": "cache-miss-rate"},
                names,
                {**sample_base, "value": rate},
            )

        if stall_backend is not None and cycles > 0:
            rate = stall_backend / cycles * 100.0
            metrics.log_sample(
                SOURCE_PERF_STAT,
                {"source": SOURCE_PERF_STAT, "class": "utilization", "type": "backend-stall-rate"},
                names,
                {**sample_base, "value": rate},
            )

        if stall_frontend is not None and cycles > 0:
            rate = stall_frontend / cycles * 100.0
            metrics.log_sample(
                SOURCE_PERF_STAT,
                {"source": SOURCE_PERF_STAT, "class": "utilization", "type": "frontend-stall-rate"},
                names,
                {**sample_base, "value": rate},
            )

    metrics.finish_samples()
    print("Post-processing for perf-stat complete")


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

    print("kerneltools post-processing complete")


if __name__ == "__main__":
    main()
