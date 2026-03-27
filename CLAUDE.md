# Kerneltools

## Purpose
Wrapper scripts for Linux kernel profiling tools (perf, turbostat, intel-speed-select, trace-cmd, sysfs-trace). Collects kernel-level performance data during benchmark execution.

## Languages
- Bash: start/stop scripts (`kerneltools-start`, `kerneltools-stop`)
- Python: `boot_to_epoch.py` — time conversion utility for perf time range filtering

## Key Files
| File | Purpose |
|------|---------|
| `kerneltools-start` | Launches configured subtools with parameters for interval, recording options, frequencies |
| `kerneltools-stop` | Kills collectors, generates perf archives, trace-cmd reports, compresses output with xz |
| `boot_to_epoch.py` | Converts between boot time and Unix epoch for perf time filtering |
| `rickshaw.json` | Rickshaw integration: endpoint allow/block lists, file deployment |
| `workshop.json` | Engine image build: distro packages and kernel source compilation for perf/turbostat |

## Configuration
- `--subtools <list>` — Comma-separated subtools (default: `turbostat`)
- `--interval <seconds>` — Collection interval (default: `10`)
- `--record-opts`, `--trace-cmd-record-opts` — Extra options for perf/trace-cmd
- `--base-freq`, `--turbo-freq`, `--core-power` — Turbostat frequency/power settings
- `--sysfs-trace-setup`, `--sysfs-trace-cleanup` — Setup/cleanup commands for sysfs-trace

## Conventions
- Primary branch is `master`
- Runs as a profiler tool on master/worker/profiler roles, blocked on client/server
- Standard Bash modelines and 4-space indentation
- No post-process script — processing happens at stop time
