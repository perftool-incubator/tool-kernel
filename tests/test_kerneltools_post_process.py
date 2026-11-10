#!/usr/bin/env python3

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "kerneltools-post-process.py"
SPEC = importlib.util.spec_from_file_location("kerneltools_post_process", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_perf_report_aggregates_by_function_and_cpu():
    lines = [
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol,IPC [IPC Coverage]",
        " 20.00%,100,1,1.0000,0,cmd,[.] do_work,-      -",
        " 30.00%,150,1,1.0000,1,cmd,[.] do_work,-      -",
        " 10.00%,50,1,2.0000,0,cmd,[k] schedule,-      -",
        "  5.00%,25,1,3.0000,0,cmd,[.] do_work,-      -",
        " 80.00%,250,1,1.0000,0,cmd,[.] other_work,-      -",
        " 90.00%,450,1,2.0000,0,cmd,[k] other_work,-      -",
        " 95.00%,475,1,3.0000,0,cmd,[.] other_work,-      -",
    ]

    samples = MODULE._parse_perf_report(lines, 1700000000)

    assert samples == [
        {
            "function": "do_work",
            "cpu": "0",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 100,
        },
        {
            "function": "do_work",
            "cpu": "1",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 150,
        },
        {
            "function": "other_work",
            "cpu": "0",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 250,
        },
        {
            "function": "other_work",
            "cpu": "0",
            "begin": 1700000002000,
            "end": 1700000002999,
            "value": 450,
        },
        {
            "function": "schedule",
            "cpu": "0",
            "begin": 1700000002000,
            "end": 1700000002999,
            "value": 50,
        },
        {
            "function": "do_work",
            "cpu": "0",
            "begin": 1700000003000,
            "end": 1700000003999,
            "value": 25,
        },
        {
            "function": "other_work",
            "cpu": "0",
            "begin": 1700000003000,
            "end": 1700000003999,
            "value": 475,
        },
    ]


def test_parse_perf_report_preserves_function_cpu_gaps():
    lines = [
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 10.00%,100,1,1.0000,0,cmd,[.] one",
        " 20.00%,200,1,3.0000,0,cmd,[.] one",
    ]

    samples = MODULE._parse_perf_report(lines, 0)

    assert [(sample["begin"], sample["end"], sample["value"]) for sample in samples] == [
        (1000, 1999, 100),
        (3000, 3999, 200),
    ]


def test_parse_perf_report_uses_raw_period_for_precision():
    lines = [
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 0.00%,1,1,1.0000,0,cmd,[.] rare_function",
        " 0.00%,999999,1,1.0000,0,cmd,[.] common_function",
    ]

    samples = MODULE._parse_perf_report(lines, 0)

    assert samples == [
        {
            "function": "common_function",
            "cpu": "0",
            "begin": 1000,
            "end": 1999,
            "value": 999999,
        },
        {
            "function": "rare_function",
            "cpu": "0",
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
            "function": "do_work",
            "cpu": "0",
            "begin": 1000,
            "end": 1999,
            "value": 250,
        },
        {
            "function": "do_work",
            "cpu": "0",
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
                "type": "cpu-cycles-spent",
                "default-aggregation": "sum",
            },
            {"function": "do_work", "cpu": "0"},
            {"begin": 1000, "end": 1999, "value": 250},
        )
    ]
    assert metrics.logged_by_idx == [(10, 500, 3999, 3000)]
