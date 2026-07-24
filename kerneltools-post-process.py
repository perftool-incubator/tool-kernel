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

TCP-probe metrics (from ss -t -i -n polling)
--------------------------------------------
Per connection (breakout by local port):
    tcp-cwnd              count        MSS   {local_port: N}
    tcp-ssthresh          count        MSS   {local_port: N}
    tcp-rtt-ms            latency      ms    {local_port: N}
    tcp-bytes-sent        count        B     {local_port: N}
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

# ss -t -i -n produces intervals separated by "=== <epoch_ms> ===" markers.
# Each connection spans two lines:
#   tcp  ESTAB  0  0  local_addr:port  peer_addr:port
#        cubic wscale:... rtt:N.N/N.N ... cwnd:N ssthresh:N bytes_sent:N ...
_SS_CONN_RE = re.compile(
    r"^tcp\s+ESTAB\s+\d+\s+\d+\s+(\S+):(\d+)\s+\S+:\d+"
)
_SS_STAT_FIELDS = {
    "cwnd":       ("tcp-cwnd",       "count",   lambda v: float(v)),
    "ssthresh":   ("tcp-ssthresh",   "count",   lambda v: float(v)),
    "bytes_sent": ("tcp-bytes-sent", "count",   lambda v: float(v)),
}


def _parse_rtt(stats_line: str) -> float | None:
    """Extract the mean RTT from 'rtt:N.NNN/N.NNN' (ms)."""
    m = re.search(r"\brtt:([\d.]+)/[\d.]+", stats_line)
    return float(m.group(1)) if m else None


def _parse_stat(stats_line: str, key: str) -> float | None:
    m = re.search(r"\b" + re.escape(key) + r":([\d]+)", stats_line)
    return float(m.group(1)) if m else None


def process_tcp_probe(log_file: str) -> None:
    print(f"Post-processing tcp-probe: {log_file}")
    source = "tcp-probe"
    metrics = CDMMetrics()

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    ts_ms: int = 0
    pending_conn: tuple[str, str] | None = None  # (local_addr, local_port)

    for raw_line in fh:
        line = raw_line.rstrip("\n")

        # Interval separator
        m = re.match(r"^=== (\d+) ===$", line)
        if m:
            ts_ms = int(m.group(1))
            pending_conn = None
            continue

        if not ts_ms:
            continue

        # Connection summary line
        m = _SS_CONN_RE.match(line)
        if m:
            pending_conn = (m.group(1), m.group(2))
            continue

        # Socket internals line (indented)
        if pending_conn and line.startswith(" ") and line.strip():
            local_port = pending_conn[1]
            names = {"local_port": local_port}
            sample_base = {"end": ts_ms}

            for key, (metric_type, cdm_class, cast) in _SS_STAT_FIELDS.items():
                val = _parse_stat(line, key)
                if val is not None:
                    desc = {"source": source, "class": cdm_class, "type": metric_type}
                    metrics.log_sample(source, desc, names, {**sample_base, "value": cast(val)})

            rtt = _parse_rtt(line)
            if rtt is not None:
                desc = {"source": source, "class": "latency", "type": "tcp-rtt-ms"}
                metrics.log_sample(source, desc, names, {**sample_base, "value": rtt})

            pending_conn = None
            continue

        pending_conn = None

    fh.close()
    metrics.finish_samples()
    print("Post-processing for tcp-probe complete")


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

    dispatch = [
        (r"^turbostat-stdout\.txt(\.xz)?$", process_turbostat),
        (r"^tcp-probe-stdout\.txt(\.xz)?$", process_tcp_probe),
    ]

    for pattern, handler in dispatch:
        matches = [f for f in files if re.match(pattern, f)]
        if len(matches) > 1:
            print(f"ERROR: multiple files match {pattern}: {matches}")
        elif matches:
            handler(matches[0])

    print("kerneltools post-processing complete")


if __name__ == "__main__":
    main()
