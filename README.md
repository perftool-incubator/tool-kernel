# kerneltools
[![CI Actions Status](https://github.com/perftool-incubator/tool-kernel/workflows/crucible-ci/badge.svg)](https://github.com/perftool-incubator/tool-kernel/actions)

Wrapper scripts for Linux kernel profiling tools, integrated with the [crucible](https://github.com/perftool-incubator/crucible) performance testing framework.

## Subtools

Kerneltools runs one or more subtools during test execution:

| Subtool | Purpose |
|---------|---------|
| turbostat | CPU power and frequency monitoring (x86) |
| perf | CPU profiling and hardware event recording |
| perf-stat | Lightweight per-CPU IPC and stall breakdown via perf stat |
| toplev | Top-Down Methodology analysis (x86 Intel) |
| hw-counters | Direct PMU counter reads and AMD UMC memory controller bandwidth via Python |
| intel-speed-select | CPU frequency scaling controls (x86) |
| trace-cmd | Kernel function tracing via ftrace |
| sysfs-trace | Direct kernel trace buffer access via debugfs |

## Configuration

The start script accepts these parameters:
- `--subtools <list>` — Comma-separated subtools to run (default: `turbostat`)
- `--interval <seconds>` — Collection interval (default: `10`)
- `--cpu-list <list>` — Comma-separated CPUs or ranges for hw-counters (e.g. `192-207,576-591`)
- `--umc-list <list>` — Comma-separated UMC indices or ranges for hw-counters (e.g. `0-7,8-15`)
- `--umc-events <list>` — Comma-separated AMD UMC events: `cas_all`, `cas_rd`, `cas_wr` (default: `cas_all`)
- `--record-opts <opts>` — Additional options passed to perf record
- `--base-freq <freq>` — Base frequency for turbostat
- `--turbo-freq <freq>` — Turbo frequency for turbostat
- `--core-power` — Enable core power reporting in turbostat
- `--trace-cmd-record-opts <opts>` — Additional options for trace-cmd record
- `--sysfs-trace-setup <cmd>` — Setup commands for sysfs-trace (repeatable)
- `--sysfs-trace-cleanup <cmd>` — Cleanup commands for sysfs-trace (repeatable)

The stop script additionally accepts:
- `--perf-gen-local-report <ON|OFF>` — Generate a local `perf-report.txt` alongside the archived `perf.data` (default: `OFF`). `kerneltools-stop` itself takes this as a no-argument flag; `rickshaw.json`'s `param_regex` rewrites `ON` to the bare flag and drops `OFF` (and its value) entirely before the script ever sees it -- mirroring the same ON/OFF-fixup convention used by bench-trafficgen for its own no-argument flags.
- `--sysfs-trace-collection <system|per-cpu>` — Trace buffer collection mode (default: `per-cpu`)

## Integration

Kerneltools runs as a profiler tool on endpoint nodes. It is allowed on profiler, master, and worker collector roles but blocked on client and server roles. Data compression and archival reports happen at stop time. The CDM post-processor (`kerneltools-post-process.py`) converts supported turbostat, perf-stat, toplev, and hw-counters output into Crucible's canonical metric format.
