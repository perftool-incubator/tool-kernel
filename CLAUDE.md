# Kerneltools

## Purpose
Wrapper scripts for Linux kernel profiling tools (perf, turbostat, intel-speed-select, trace-cmd, sysfs-trace, perf-stat, toplev, hw-counters). Collects kernel-level performance data and emits CDM metrics during benchmark execution.

## Languages
- Bash: start/stop scripts (`kerneltools-start`, `kerneltools-stop`)
- Python: `kerneltools-hw-counters.py`, `kerneltools-post-process.py`, `boot_to_epoch.py`

## Key Files
| File | Purpose |
|------|---------|
| `kerneltools-start` | Launches configured subtools with parameters for interval, recording options, frequencies, CPU/UMC filters |
| `kerneltools-stop` | Kills collectors, generates perf archives, trace-cmd reports, compresses output with xz |
| `kerneltools-hw-counters.py` | Direct PMU hardware counter and AMD UMC memory controller bandwidth collector using `perf_event_open` |
| `kerneltools-post-process.py` | CDM metrics post-processor for turbostat, perf-stat, toplev, and hw-counters |
| `boot_to_epoch.py` | Converts between boot time and Unix epoch for perf time filtering |
| `rickshaw.json` | Rickshaw integration: endpoint allow/block lists, file deployment, post-process script |
| `workshop.json` | Engine image build: distro packages and kernel source compilation for perf/turbostat |
| `tool-metadata.json` | Machine-readable description, subtool list, and CDM-indexed status (consumed by `crucible tools list`) |
| `multiplex.json` | Parameter validation rules and `defaults` preset for multiplex (mirrors benchmark `multiplex.json`) |

## Configuration
- `--subtools <list>` — Comma-separated subtools (default: `turbostat`, options: `turbostat`, `perf`, `perf-stat`, `toplev`, `hw-counters`, `intel-speed-select`, `trace-cmd`, `sysfs-trace`)
- `--interval <seconds>` — Collection interval (default: `10`)
- `--cpu-list <list>` — Comma-separated CPUs or ranges for hw-counters (e.g. `192-207,576-591`)
- `--umc-list <list>` — Comma-separated UMC indices or ranges for hw-counters (e.g. `0-7,8-15`)
- `--umc-events <list>` — Comma-separated AMD UMC events: `cas_all`, `cas_rd`, `cas_wr` (default: `cas_all`)
- `--record-opts`, `--trace-cmd-record-opts` — Extra options for perf/trace-cmd
- `--base-freq`, `--turbo-freq`, `--core-power` — Turbostat frequency/power settings
- `--sysfs-trace-setup`, `--sysfs-trace-cleanup` — Setup/cleanup commands for sysfs-trace

## Conventions
- Primary branch is `master`
- Runs as a profiler tool on master/worker/profiler roles, blocked on client/server
- Standard Bash modelines and 4-space indentation
- Post-processing converts raw output to CDM metrics (`kerneltools-post-process.py`)
