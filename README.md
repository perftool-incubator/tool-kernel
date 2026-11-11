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
- `--perf-gen-local-report <ON|OFF>` — Generate an additional human-readable `perf-report.txt` alongside the archived `perf.data` (default: `OFF`). `kerneltools-stop` itself takes this as a no-argument flag; `rickshaw.json`'s `param_regex` rewrites `ON` to the bare flag and drops `OFF` (and its value) entirely before the script ever sees it -- mirroring the same ON/OFF-fixup convention used by bench-trafficgen for its own no-argument flags.
- `--sysfs-trace-collection <system|per-cpu>` — Trace buffer collection mode (default: `per-cpu`)

### Perf CDM metrics

When the `perf` subtool is enabled, `kerneltools-start` records the collector's
boot time in `kerneltools-boot-time.txt` and captures the collector's CPU
topology in `kerneltools-cpu-topology.txt`. At stop time, after `perf archive`
and before compressing `perf.data`, `kerneltools-stop` generates
`perf-report.csv.xz` with one-second time buckets.

The controller post-processor converts each symbol's sampled event period in
each bucket into a CDM sample. The event section in the perf report controls the
metric type:

- `cycles` or `cpu-cycles` (including modifiers such as `cycles:u`) produces
  type `cpu-cycles-spent`.
- `instructions` (including modifiers such as `instructions:k`) produces type
  `cpu-instructions-spent`.
- Unsupported events are skipped with a warning so their values cannot be mixed
  into a metric with different units.

All perf samples use source `perf-record`, class `count`, and default
aggregation `sum`. Breakouts are `function`, `cpu`, `cmd`, `package`, `die`,
`core`, `thread`, `shared-l1-domain`, `shared-l2-domain`,
`shared-l3-domain`, and `shared-l4-domain`. The value is the sum of the raw
event `Period` values for that function, CPU, cmd, event, and time bucket,
avoiding the two-decimal precision loss in perf's displayed `Overhead` column.

For the default cycles event, `cpu-cycles-spent` represents sampled CPU cycles
spent in the function on that CPU; it is not wall-clock or CPU time unless event
frequency is known. `cpu-instructions-spent` similarly represents sampled
retired instructions. Multiple events, such as
`--record-opts "-e cycles,instructions"`, produce separate metrics and are never
summed together. The package, die, core, thread, and shared-cache-domain labels
come from the topology captured on the collection host. If a function, CPU, and
cmd combination does not appear in a bucket, no sample is emitted for that
combination in that bucket; gaps are not zero-filled.

## Integration

Kerneltools runs as a profiler tool on endpoint nodes. It is allowed on profiler, master, and worker collector roles but blocked on client and server roles. Data compression and report generation (perf archive, perf reports, trace-cmd reports) happens at stop time. The CDM post-processor (`kerneltools-post-process.py`) converts turbostat and perf-record output into crucible's canonical metric format; other subtools are unaffected.
