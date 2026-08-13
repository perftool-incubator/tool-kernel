# kerneltools
[![CI Actions Status](https://github.com/perftool-incubator/tool-kernel/workflows/crucible-ci/badge.svg)](https://github.com/perftool-incubator/tool-kernel/actions)

Wrapper scripts for Linux kernel profiling tools, integrated with the [crucible](https://github.com/perftool-incubator/crucible) performance testing framework.

## Subtools

Kerneltools runs one or more subtools during test execution:

| Subtool | Purpose |
|---------|---------|
| turbostat | CPU power and frequency monitoring (x86) |
| perf | CPU profiling and hardware event recording |
| intel-speed-select | CPU frequency scaling controls (x86) |
| trace-cmd | Kernel function tracing via ftrace |
| sysfs-trace | Direct kernel trace buffer access via debugfs |

## Configuration

The start script accepts these parameters:
- `--subtools <list>` — Comma-separated subtools to run (default: `turbostat`)
- `--interval <seconds>` — Collection interval (default: `10`)
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

Kerneltools runs as a profiler tool on endpoint nodes. It is allowed on profiler, master, and worker collector roles but blocked on client and server roles. Data compression and post-processing (perf archive, trace-cmd reports) happens at stop time. The CDM post-processor (`kerneltools-post-process.py`) converts turbostat output into crucible's canonical metric format; other subtools are unaffected.
