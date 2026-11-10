#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

TOOLBOX_HOME = os.environ.get("TOOLBOX_HOME")
if TOOLBOX_HOME:
    sys.path.append(str(Path(TOOLBOX_HOME) / "python"))

TURBOSTAT_SOURCE = "turbostat"
PERF_SOURCE = "perf-record"
PERF_METRIC_TYPE = "cpu-cycles-spent"
PERF_REPORT_BASENAME = "perf-report.csv"
PERF_BOOT_TIME_FILE = "kerneltools-boot-time.txt"
PERF_CPU_TOPOLOGY_FILE = "kerneltools-cpu-topology.txt"
PERF_TIME_QUANTUM_MS = 1000


def _safe_float(fields: list[str], col_idx: dict[str, int], name: str) -> float | None:
    idx = col_idx.get(name)
    if idx is None or idx >= len(fields):
        return None
    try:
        return float(fields[idx])
    except ValueError:
        return None


def _clean_perf_symbol(raw_symbol: str) -> str | None:
    symbol = raw_symbol.strip()
    if symbol in ("", "-"):
        return None

    # perf decorates symbols with their execution domain. The first pass keeps
    # the symbol itself as the function breakout and leaves domain breakouts
    # for a later change.
    for prefix in ("[.] ", "[k] ", "[g] ", "[guest] ", "[kernel.kallsyms] "):
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):].strip()
            break

    return symbol or None


def _safe_int(fields: list[str], col_idx: dict[str, int], name: str) -> int | None:
    idx = col_idx.get(name)
    if idx is None or idx >= len(fields):
        return None
    try:
        return int(fields[idx])
    except ValueError:
        return None


def _perf_bucket_begin_ms(boot_time_s: int, perf_time_s: float) -> int:
    perf_time_ms = int(perf_time_s * 1000)
    bucket_offset_ms = (perf_time_ms // PERF_TIME_QUANTUM_MS) * PERF_TIME_QUANTUM_MS
    return boot_time_s * 1000 + bucket_offset_ms


def _parse_cpu_topology(lines: Iterable[str]) -> dict[int, dict[str, int]]:
    topology: dict[int, dict[str, int]] = {}
    pattern = re.compile(
        r"^cpu=(\d+) package=(\d+) die=(\d+) core=(\d+) thread=(\d+)$"
    )
    for raw_line in lines:
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        cpu, package, die, core, thread = map(int, match.groups())
        topology[cpu] = {
            "package_id": package,
            "die_id": die,
            "core_id": core,
            "thread_id": thread,
        }
    return topology


def _parse_perf_report(
    lines: Iterable[str], boot_time_s: int, cpu_topology: dict[int, dict[str, int]] | None = None
) -> list[dict[str, object]]:
    col_idx: dict[str, int] = {}
    periods: dict[tuple[int, str, str, str], int] = {}

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.lstrip()

        if not stripped:
            continue

        if stripped.startswith("#"):
            if stripped.startswith("# Overhead"):
                cols = stripped.lstrip("#").split(",")
                col_idx = {col.strip(): i for i, col in enumerate(cols)}
            continue

        if not col_idx:
            continue

        fields = [field.strip() for field in line.split(",")]
        period = _safe_int(fields, col_idx, "Period")
        perf_time = _safe_float(fields, col_idx, "Time")
        cpu_idx = col_idx.get("CPU")
        command_idx = col_idx.get("Command")
        symbol_idx = col_idx.get("Symbol")
        if (
            period is None
            or period < 0
            or perf_time is None
            or cpu_idx is None
            or cpu_idx >= len(fields)
            or command_idx is None
            or command_idx >= len(fields)
            or symbol_idx is None
            or symbol_idx >= len(fields)
        ):
            continue

        function = _clean_perf_symbol(fields[symbol_idx])
        if function is None:
            continue
        cpu = fields[cpu_idx]
        command = fields[command_idx]
        if not cpu or not command:
            continue
        try:
            cpu = str(int(cpu))
        except ValueError:
            continue

        begin_ms = _perf_bucket_begin_ms(boot_time_s, perf_time)
        bucket_key = (begin_ms, function, cpu, command)
        periods[bucket_key] = periods.get(bucket_key, 0) + period

    samples: list[dict[str, object]] = []
    for (begin_ms, function, cpu, command), value in sorted(periods.items()):
        topology = (cpu_topology or {}).get(int(cpu), {})
        samples.append(
            {
                "function": function,
                "cpu": cpu,
                "cmd": command,
                "package": str(topology["package_id"]) if "package_id" in topology else None,
                "die": str(topology["die_id"]) if "die_id" in topology else None,
                "core": str(topology["core_id"]) if "core_id" in topology else None,
                "thread": str(topology["thread_id"]) if "thread_id" in topology else None,
                "begin": begin_ms,
                "end": begin_ms + PERF_TIME_QUANTUM_MS - 1,
                "value": value,
            }
        )
    return samples


def _emit_perf_samples(samples: list[dict[str, object]], metrics) -> None:
    desc = {
        "source": PERF_SOURCE,
        "class": "count",
        "type": PERF_METRIC_TYPE,
        "default-aggregation": "sum",
    }
    metric_idx_cache: dict[tuple[str, str, str], int] = {}

    for sample in samples:
        function = str(sample["function"])
        cpu = str(sample["cpu"])
        command = str(sample["cmd"])
        cache_key = (function, cpu, command)
        names = {"function": function, "cpu": cpu, "cmd": command}
        for breakout in ("package", "die", "core", "thread"):
            if sample.get(breakout) is not None:
                names[breakout] = str(sample[breakout])
        metric_sample = {
            "begin": sample["begin"],
            "end": sample["end"],
            "value": sample["value"],
        }

        if cache_key in metric_idx_cache:
            metrics.log_sample_by_idx(
                metric_idx_cache[cache_key],
                metric_sample["value"],
                metric_sample["end"],
                metric_sample["begin"],
            )
        else:
            metric_idx_cache[cache_key] = metrics.log_sample(
                PERF_SOURCE, desc, names, metric_sample
            )


def process_turbostat(log_file: str) -> None:
    print(f"Post-processing turbostat: {log_file}")
    from toolbox.cdm_metrics import CDMMetrics
    from toolbox.fileio import open_read_text_file

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
                        desc = {"source": TURBOSTAT_SOURCE, "class": cdm_class, "type": metric_type, "default-aggregation": default_agg}
                        idx = metrics.log_sample(TURBOSTAT_SOURCE, desc, {}, {**sample_base, "value": val})
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
                        desc = {"source": TURBOSTAT_SOURCE, "class": cdm_class, "type": metric_type, "default-aggregation": default_agg}
                        idx = metrics.log_sample(TURBOSTAT_SOURCE, desc, names, {**sample_base, "value": val})
                        metric_idx_cache[cache_key] = idx

    fh.close()
    metrics.finish_samples()
    print("Post-processing for turbostat complete")


def process_perf_report(report_file: str, boot_time_file: str) -> None:
    print(f"Post-processing perf-record: {report_file}")
    from toolbox.cdm_metrics import CDMMetrics
    from toolbox.fileio import open_read_text_file

    try:
        boot_time_s = int(Path(boot_time_file).read_text().strip())
    except (FileNotFoundError, ValueError):
        print(f"ERROR: could not read a valid boot time from {boot_time_file}")
        return

    try:
        fh, _ = open_read_text_file(report_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {report_file}")
        return

    try:
        try:
            with open(PERF_CPU_TOPOLOGY_FILE) as topology_fh:
                cpu_topology = _parse_cpu_topology(topology_fh)
        except FileNotFoundError:
            print("WARNING: could not find CPU topology file; omitting topology breakouts")
            cpu_topology = {}
        samples = _parse_perf_report(fh, boot_time_s, cpu_topology)
    finally:
        fh.close()

    if not samples:
        print("WARNING: no perf-report samples found")
        return

    metrics = CDMMetrics()
    _emit_perf_samples(samples, metrics)
    metrics.finish_samples()
    print(f"Post-processing for perf-record complete ({len(samples)} function buckets)")


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

    perf_report_files = [
        f for f in files
        if re.match(rf"^{re.escape(PERF_REPORT_BASENAME)}(\.xz)?$", f)
    ]

    if len(perf_report_files) > 1:
        print(f"ERROR: multiple perf report files found: {perf_report_files}")
    elif not perf_report_files:
        print("WARNING: no perf-report.csv file found — skipping")
    else:
        process_perf_report(perf_report_files[0], PERF_BOOT_TIME_FILE)

    print("kerneltools post-processing complete")


if __name__ == "__main__":
    main()
