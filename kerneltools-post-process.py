#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

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

    print("kerneltools post-processing complete")


if __name__ == "__main__":
    main()
