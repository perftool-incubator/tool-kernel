#!/usr/bin/env python3

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "kerneltools-post-process.py"
SPEC = importlib.util.spec_from_file_location("kerneltools_post_process", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CPU_TOPOLOGY = {
    0: {"package_id": 1, "die_id": 2, "core_id": 3, "thread_id": 0},
    1: {"package_id": 1, "die_id": 2, "core_id": 3, "thread_id": 1},
}
CYCLES_EVENT_HEADER = [
    "# Samples: 1  of event 'cycles'",
    "# Event count (approx.): 1",
]


def test_parse_perf_report_aggregates_by_function_cpu_and_command():
    lines = [
        *CYCLES_EVENT_HEADER,
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol,IPC [IPC Coverage]",
        " 20.00%,100,1,1.0000,0,cmd,[.] do_work,-      -",
        " 30.00%,150,1,1.0000,001,cmd,[.] do_work,-      -",
        " 15.00%,75,1,1.0000,1,other-cmd,[.] do_work,-      -",
        " 10.00%,50,1,2.0000,0,cmd,[k] schedule,-      -",
        "  5.00%,25,1,3.0000,0,cmd,[.] do_work,-      -",
        " 80.00%,250,1,1.0000,0,cmd,[.] other_work,-      -",
        " 90.00%,450,1,2.0000,0,cmd,[k] other_work,-      -",
        " 95.00%,475,1,3.0000,0,cmd,[.] other_work,-      -",
    ]

    samples = MODULE._parse_perf_report(lines, 1700000000, CPU_TOPOLOGY)

    assert samples == [
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 100,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "1",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "1",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 150,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "1",
            "cmd": "other-cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "1",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 75,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "other_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 250,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "other_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1700000002000,
            "end": 1700000002999,
            "value": 450,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "schedule",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1700000002000,
            "end": 1700000002999,
            "value": 50,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1700000003000,
            "end": 1700000003999,
            "value": 25,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "other_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1700000003000,
            "end": 1700000003999,
            "value": 475,
        },
    ]


def test_parse_perf_report_preserves_function_cpu_command_gaps():
    lines = [
        *CYCLES_EVENT_HEADER,
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 10.00%,100,1,1.0000,0,cmd,[.] one",
        " 20.00%,200,1,3.0000,0,cmd,[.] one",
    ]

    samples = MODULE._parse_perf_report(lines, 0, CPU_TOPOLOGY)

    assert [(sample["begin"], sample["end"], sample["value"]) for sample in samples] == [
        (1000, 1999, 100),
        (3000, 3999, 200),
    ]


def test_parse_perf_report_keeps_supported_event_types_separate():
    lines = [
        "# Samples: 1  of event 'cycles:u'",
        "# Event count (approx.): 1",
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 50.00%,100,1,1.0000,0,cmd,[.] do_work",
        "",
        "# Samples: 1  of event 'instructions:k'",
        "# Event count (approx.): 1",
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 50.00%,200,1,1.0000,0,cmd,[.] do_work",
    ]

    samples = MODULE._parse_perf_report(lines, 0, CPU_TOPOLOGY)

    assert [(sample["metric_type"], sample["value"]) for sample in samples] == [
        ("cpu-cycles", 100),
        ("cpu-instructions", 200),
    ]


def test_parse_perf_report_maps_default_event_set():
    events = (
        ("cycles", 100, "cpu-cycles"),
        ("instructions", 200, "cpu-instructions"),
        ("branch-instructions", 300, "cpu-branch-instructions"),
        ("branch-misses", 400, "cpu-branch-misses"),
        ("cache-misses", 500, "cpu-cache-misses"),
    )
    lines = []
    for event, value, _ in events:
        lines.extend(
            [
                f"# Samples: 1  of event '{event}'",
                "# Event count (approx.): 1",
                "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
                f" 20.00%,{value},1,1.0000,0,cmd,[.] do_work",
                "",
            ]
        )

    samples = MODULE._parse_perf_report(lines, 0, CPU_TOPOLOGY)

    assert {(sample["metric_type"], sample["value"]) for sample in samples} == {
        (metric_type, value) for _, value, metric_type in events
    }


def test_parse_perf_report_skips_unsupported_event_types():
    skipped_events = set()
    lines = [
        "# Samples: 1  of event 'task-clock'",
        "# Event count (approx.): 1",
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 100.00%,100,1,1.0000,0,cmd,[.] do_work",
    ]

    samples = MODULE._parse_perf_report(lines, 0, CPU_TOPOLOGY, skipped_events)

    assert samples == []
    assert skipped_events == {"task-clock"}


def test_parse_perf_report_uses_raw_period_for_precision():
    lines = [
        *CYCLES_EVENT_HEADER,
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 0.00%,1,1,1.0000,0,cmd,[.] rare_function",
        " 0.00%,999999,1,1.0000,0,cmd,[.] common_function",
    ]

    samples = MODULE._parse_perf_report(lines, 0, CPU_TOPOLOGY)

    assert samples == [
        {
            "metric_type": "cpu-cycles",
            "function": "common_function",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1000,
            "end": 1999,
            "value": 999999,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "rare_function",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1000,
            "end": 1999,
            "value": 1,
        },
    ]


def test_clean_perf_symbol_strips_execution_domain_markers():
    assert MODULE._clean_perf_symbol("[.] malloc") == "malloc"
    assert MODULE._clean_perf_symbol("[k] schedule") == "schedule"
    assert MODULE._clean_perf_symbol("[g] guest_func") == "guest_func"
    assert MODULE._clean_perf_symbol("[kernel.kallsyms] __x64_sys_read") == "__x64_sys_read"
    assert MODULE._clean_perf_symbol("-") is None


def test_parse_cpu_topology_includes_cache_domains():
    lines = [
        "cpu=0 package=1 die=2 core=3 thread=0 shared-l1-domain=0 shared-l3-domain=0-3",
        "cpu=1 package=1 die=2 core=3 thread=1 shared-l1-domain=0 shared-l3-domain=0-3",
    ]

    assert MODULE._parse_cpu_topology(lines) == {
        0: {
            "package_id": 1,
            "die_id": 2,
            "core_id": 3,
            "thread_id": 0,
            "shared-l1-domain": "0",
            "shared-l3-domain": "0-3",
        },
        1: {
            "package_id": 1,
            "die_id": 2,
            "core_id": 3,
            "thread_id": 1,
            "shared-l1-domain": "0",
            "shared-l3-domain": "0-3",
        },
    }


def test_parse_perf_report_includes_cache_domain_breakouts():
    lines = [
        *CYCLES_EVENT_HEADER,
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 10.00%,100,1,1.0000,0,cmd,[.] do_work",
    ]
    topology = {
        0: {
            "package_id": 1,
            "die_id": 2,
            "core_id": 3,
            "thread_id": 0,
            "shared-l1-domain": "0",
            "shared-l2-domain": "0-1",
            "shared-l3-domain": "0-3",
            "shared-l4-domain": "0-7",
        }
    }

    samples = MODULE._parse_perf_report(lines, 0, topology)

    assert samples == [
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "shared-l1-domain": "0",
            "shared-l2-domain": "0-1",
            "shared-l3-domain": "0-3",
            "shared-l4-domain": "0-7",
            "begin": 1000,
            "end": 1999,
            "value": 100,
        }
    ]


def test_emit_perf_samples_includes_cache_domain_breakouts():
    class FakeMetrics:
        def __init__(self):
            self.names = None

        def log_sample(self, file_id, desc, names, sample):
            self.names = names
            return 1

        def log_sample_by_idx(self, idx, value, end, begin=None):
            pass

    metrics = FakeMetrics()
    samples = [
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "shared-l3-domain": "0-3",
            "begin": 1000,
            "end": 1999,
            "value": 100,
        }
    ]

    MODULE._emit_perf_samples(samples, metrics)

    assert metrics.names["shared-l3-domain"] == "0-3"


def test_parse_cpu_topology_ignores_malformed_lines():
    lines = [
        "cpu=0 package=1 die=2 core=3 thread=0",
        "not a topology line",
        "cpu=001 package=1 die=2 core=3 thread=1",
    ]

    assert MODULE._parse_cpu_topology(lines) == {
        0: {"package_id": 1, "die_id": 2, "core_id": 3, "thread_id": 0},
        1: {"package_id": 1, "die_id": 2, "core_id": 3, "thread_id": 1},
    }


def test_perf_bucket_boundaries_do_not_round_across_a_second():
    assert MODULE._perf_bucket_begin_ms(1000, 0.9999) == 1000000
    assert MODULE._perf_bucket_begin_ms(1000, 1.0000) == 1001000


def test_emit_perf_samples_uses_expected_metric_descriptor_and_breakout():
    class FakeMetrics:
        def __init__(self):
            self.logged = []
            self.logged_by_idx = []

        def log_sample(self, file_id, desc, names, sample):
            self.logged.append((file_id, desc, names, sample))
            return len(self.logged) + 9

        def log_sample_by_idx(self, idx, value, end, begin=None):
            self.logged_by_idx.append((idx, value, end, begin))

    samples = [
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 1000,
            "end": 1999,
            "value": 250,
        },
        {
            "metric_type": "cpu-cycles",
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "package": "1",
            "die": "2",
            "core": "3",
            "thread": "0",
            "begin": 3000,
            "end": 3999,
            "value": 500,
        },
    ]
    metrics = FakeMetrics()

    MODULE._emit_perf_samples(samples, metrics)

    assert metrics.logged == [
        (
            "perf-record",
            {
                "source": "perf-record",
                "class": "count",
                "type": "cpu-cycles",
                "default-aggregation": "sum",
            },
            {
                "function": "do_work",
                "cpu": "0",
                "cmd": "cmd",
                "package": "1",
                "die": "2",
                "core": "3",
                "thread": "0",
            },
            {"begin": 1000, "end": 1999, "value": 250},
        )
    ]
    assert metrics.logged_by_idx == [(10, 500, 3999, 3000)]


def test_emit_perf_samples_uses_event_specific_metric_types():
    class FakeMetrics:
        def __init__(self):
            self.logged = []

        def log_sample(self, file_id, desc, names, sample):
            self.logged.append((file_id, desc, names, sample))
            return len(self.logged)

        def log_sample_by_idx(self, idx, value, end, begin=None):
            raise AssertionError("unexpected sample aggregation")

    def sample(metric_type, value):
        return {
            "metric_type": metric_type,
            "function": "do_work",
            "cpu": "0",
            "cmd": "cmd",
            "begin": 1000,
            "end": 1999,
            "value": value,
        }

    metrics = FakeMetrics()
    MODULE._emit_perf_samples(
        [
            sample("cpu-cycles", 100),
            sample("cpu-instructions", 200),
        ],
        metrics,
    )

    assert [(entry[1]["type"], entry[3]["value"]) for entry in metrics.logged] == [
        ("cpu-cycles", 100),
        ("cpu-instructions", 200),
    ]
