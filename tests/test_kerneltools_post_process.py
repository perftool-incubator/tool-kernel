#!/usr/bin/env python3

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "kerneltools-post-process.py"
SPEC = importlib.util.spec_from_file_location("kerneltools_post_process", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_perf_report_aggregates_symbols_and_uses_contiguous_buckets():
    lines = [
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol,IPC [IPC Coverage]",
        " 20.00%,100,1,1.0000,0,cmd,[.] do_work,-      -",
        " 30.00%,150,1,1.0000,1,cmd,[.] do_work,-      -",
        " 10.00%,50,1,2.0000,0,cmd,[k] schedule,-      -",
        "  5.00%,25,1,3.0000,0,cmd,[.] do_work,-      -",
    ]

    samples = MODULE._parse_perf_report(lines, 1700000000)

    assert samples == [
        {
            "function": "do_work",
            "begin": 1700000001000,
            "end": 1700000001999,
            "value": 0.5,
        },
        {
            "function": "schedule",
            "begin": 1700000002000,
            "end": 1700000002999,
            "value": 0.1,
        },
        {
            "function": "do_work",
            "begin": 1700000003000,
            "end": 1700000003999,
            "value": 0.05,
        },
    ]


def test_parse_perf_report_preserves_symbol_gaps():
    lines = [
        "# Overhead,Period,Samples,Time,CPU,Command,Symbol",
        " 10.00%,100,1,1.0000,0,cmd,[.] one",
        " 20.00%,200,1,3.0000,0,cmd,[.] one",
    ]

    samples = MODULE._parse_perf_report(lines, 0)

    assert [sample["begin"] for sample in samples] == [1000, 3000]
    assert [sample["end"] for sample in samples] == [1999, 3999]


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
            "begin": 1000,
            "end": 1999,
            "value": 0.25,
        },
        {
            "function": "do_work",
            "begin": 3000,
            "end": 3999,
            "value": 0.5,
        },
    ]
    metrics = FakeMetrics()

    MODULE._emit_perf_samples(samples, metrics)

    assert metrics.logged == [
        (
            "perf-record",
            {
                "source": "perf-record",
                "class": "percentage",
                "type": "overhead-pct",
                "default-aggregation": "sum",
            },
            {"function": "do_work"},
            {"begin": 1000, "end": 1999, "value": 0.25},
        )
    ]
    assert metrics.logged_by_idx == [(10, 0.5, 3999, 3000)]
