#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Post-process kernel tool output and emit CDM metrics.

Runs in the kernel tool's data directory (one per profiler instance).
Discovers output files for each subtool and dispatches to the
appropriate processor.

Subtools handled
----------------
turbostat  turbostat-stdout.txt(.xz)
tcp-probe  tcp-probe-stdout.txt(.xz)

Turbostat metrics
-----------------
System aggregate (all topology fields = "-"):
    cpu-busy-pct          utilization  %
    cpu-freq-avg-mhz      throughput   MHz
    package-power-watt    throughput   W

Per-CPU (numeric CPU field):
    cpu-busy-pct          utilization  %     {cpu: N}
    cpu-busy-freq-mhz     throughput   MHz   {cpu: N}
    c1-pct                utilization  %     {cpu: N}
    c2-pct                utilization  %     {cpu: N}
    ipc                   throughput         {cpu: N}

TCP-probe metrics (from bpftrace tcp:tcp_probe tracepoint)
----------------------------------------------------------
Per connection (breakout by src/dst/sport/dport):
    snd_cwnd              count              {src, dst, sport, dport}
    ssthresh              count              {src, dst, sport, dport}
    snd_wnd               count              {src, dst, sport, dport}
    srtt                  count        us    {src, dst, sport, dport}
    rcv_wnd               count              {src, dst, sport, dport}
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


# ── Turbostat ────────────────────────────────────────────────────────────────

def process_turbostat(log_file: str) -> None:
    print(f"Post-processing turbostat: {log_file}")
    source = "turbostat"
    metrics = CDMMetrics()

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

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
            for metric_type, cdm_class, col in (
                ("cpu-busy-pct",       "utilization", "Busy%"),
                ("cpu-freq-avg-mhz",   "throughput",  "Avg_MHz"),
                ("package-power-watt", "throughput",  "PkgWatt"),
            ):
                val = _safe_float(fields, col_idx, col)
                if val is not None:
                    desc = {"source": source, "class": cdm_class, "type": metric_type}
                    metrics.log_sample(source, desc, {}, {**sample_base, "value": val})

        elif cpu_field not in ("-", ""):
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
                    desc = {"source": source, "class": cdm_class, "type": metric_type}
                    metrics.log_sample(source, desc, names, {**sample_base, "value": val})

    fh.close()
    metrics.finish_samples()
    print("Post-processing for turbostat complete")


# ── TCP-probe ─────────────────────────────────────────────────────────────────
# Parses bpftrace output from tcp-probe.bt (tcp:tcp_probe tracepoint).
# Output format: nsecs src_ip sport dst_ip dport snd_cwnd ssthresh snd_wnd srtt rcv_wnd
# Handles sub-millisecond collisions by averaging within each ms bucket.

_TCP_PROBE_METRICS = ["snd_cwnd", "ssthresh", "snd_wnd", "srtt", "rcv_wnd"]


def _read_clock_offset(data_dir: str) -> float:
    """Read monotonic-to-epoch offset recorded at collection start."""
    offset_file = os.path.join(data_dir, "clock_offset.txt")
    if not os.path.exists(offset_file):
        print("WARNING: clock_offset.txt not found, timestamps may be wrong")
        return 0.0
    with open(offset_file) as f:
        parts = f.read().strip().split()
    if len(parts) == 2:
        mono, epoch = float(parts[0]), float(parts[1])
        print(f"Clock offset: mono={mono:.3f} epoch={epoch:.3f} offset={epoch-mono:.3f}")
        return epoch - mono
    return 0.0


def process_tcp_probe(log_file: str) -> None:
    print(f"Post-processing tcp-probe: {log_file}")
    source = "tcp-probe"
    data_dir = os.path.dirname(log_file) or "."
    clock_offset = _read_clock_offset(data_dir)

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    # First pass: parse bpftrace lines into per-connection, per-ms buckets.
    # connections[conn_key][ts_ms] = ([sum0, sum1, ...], count)
    connections: dict = {}
    line_count = skip_count = 0

    for raw_line in fh:
        parts = raw_line.strip().split()
        if len(parts) != 10:
            skip_count += 1
            continue
        try:
            nsecs = int(parts[0])
            src_ip = parts[1]
            sport = parts[2]
            dst_ip = parts[3]
            dport = parts[4]
            values = [int(parts[i]) for i in range(5, 10)]
        except (ValueError, IndexError):
            skip_count += 1
            continue

        # bpftrace nsecs is nanoseconds since boot; convert to epoch ms
        ts_ms = int((nsecs / 1e9 + clock_offset) * 1000)
        conn_key = (src_ip, sport, dst_ip, dport)

        if conn_key not in connections:
            connections[conn_key] = {}
        bucket = connections[conn_key]
        if ts_ms in bucket:
            sums, cnt = bucket[ts_ms]
            for i in range(5):
                sums[i] += values[i]
            bucket[ts_ms] = (sums, cnt + 1)
        else:
            bucket[ts_ms] = ([float(v) for v in values], 1)
        line_count += 1

    fh.close()

    # Second pass: emit CDM samples.
    metrics = CDMMetrics()
    idx_cache: dict = {}
    sample_count = collapsed = 0

    for conn_key, ts_data in connections.items():
        src_ip, sport, dst_ip, dport = conn_key
        names = {"src": src_ip, "dst": dst_ip, "sport": sport, "dport": dport}

        for ts_ms in sorted(ts_data):
            sums, cnt = ts_data[ts_ms]
            if cnt > 1:
                collapsed += cnt - 1
            avgs = [s / cnt for s in sums]

            for i, metric_name in enumerate(_TCP_PROBE_METRICS):
                cache_key = (metric_name, conn_key)
                if cache_key in idx_cache:
                    metrics.log_sample_by_idx(idx_cache[cache_key], avgs[i], ts_ms)
                else:
                    desc = {"class": "count", "source": source, "type": metric_name}
                    sample = {"value": avgs[i], "end": ts_ms}
                    idx = metrics.log_sample(source, desc, names, sample)
                    if idx is not None:
                        idx_cache[cache_key] = idx
            sample_count += 1

    metrics.finish_samples()
    print(f"tcp-probe: {line_count} events → {sample_count} ms samples "
          f"({collapsed} sub-ms averaged, {skip_count} skipped)")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _safe_float(fields: list[str], col_idx: dict[str, int], name: str) -> float | None:
    idx = col_idx.get(name)
    if idx is None or idx >= len(fields):
        return None
    try:
        return float(fields[idx])
    except ValueError:
        return None


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("kerneltools-post-process")

    files = sorted(os.listdir("."))
    print(f"files to process:\n {' '.join(files)}")

    # turbostat: top-level file
    turbostat_files = [f for f in files if re.match(r"^turbostat-stdout\.txt(\.xz)?$", f)]
    if len(turbostat_files) > 1:
        print(f"ERROR: multiple turbostat files: {turbostat_files}")
    elif turbostat_files:
        process_turbostat(turbostat_files[0])

    # tcp-probe: bpftrace output in tcp-probe-data/ subdirectory
    tcp_probe_out = "tcp-probe-data/tcp-probe.out"
    tcp_probe_xz = tcp_probe_out + ".xz"
    if os.path.exists(tcp_probe_xz):
        process_tcp_probe(tcp_probe_xz)
    elif os.path.exists(tcp_probe_out):
        process_tcp_probe(tcp_probe_out)

    print("kerneltools post-processing complete")


if __name__ == "__main__":
    main()
