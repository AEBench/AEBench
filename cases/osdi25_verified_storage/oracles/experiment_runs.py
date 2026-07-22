from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleExperimentRunsBase


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        verify_script = """
import sys, json, csv, os
from pathlib import Path

# Add script directory to path to allow imports
sys.path.append('.')

# 1. Startup Times & Microbenchmarks

# Startup Time Trend: Empty startup time must be lower than Full startup time for all systems.
# Hardware Discrepancy: Assert that Viper starts faster than CapybaraKV (both empty and full).
sys.argv = ['script', 'results/artifact_evaluation_results/microbenchmark']
import get_startup_times
res_startup = get_startup_times.parse_files('results/artifact_evaluation_results/microbenchmark', 5)
for kv in ['pmemrocksdb', 'viper', 'capybarakv']:
    assert res_startup[kv]['empty'] < res_startup[kv]['full'], f"{kv} empty >= full"
assert res_startup['viper']['empty'] < res_startup['capybarakv']['empty'], "Viper empty >= CapybaraKV empty"
assert res_startup['viper']['full'] < res_startup['capybarakv']['full'], "Viper full >= CapybaraKV full"

# Latency Trend: Sequential Reads (Seq Get) latency must be lower than Random Reads (Rand Get) latency.
res_latency = json.load(open('results.json'))
for kv in ['pmemrocksdb', 'capybarakv', 'redis']:
    seq_get = res_latency[kv]['sequential_get'][0]
    rand_get = res_latency[kv]['rand_get'][0]
    assert seq_get < rand_get, f"{kv} seq_get >= rand_get"


# 2. Macrobenchmarks (YCSB) & Resource Footprints

# Storage Footprint (Rounded): Extract storage bytes via get_mem_and_storage_use.py and assert exact match.
# Memory Footprint: Check that DRAM usage closely aligns with the reported gigabytes.
# Fake args for get_mem_and_storage_use to prevent it from crashing on import (since it runs main())
sys.argv = ['script', '1', 'capybarakv', '1', '1', 'results/artifact_evaluation_results']
import get_mem_and_storage_use as ms
res_mem = ms.parse_files(['capybarakv'], [1], 'results/artifact_evaluation_results')
assert round(res_mem['capybarakv']['storage']) == 18, "Storage footprint mismatch"
# Memory footprint in the paper is around 2.8 GiB. A reasonable range is [2.0, 4.0].
assert 2.0 <= res_mem['capybarakv']['mem'] <= 4.0, "Memory footprint out of expected bounds"

# Scaling Trend: Check that read-heavy workloads (Runs B, C, D) have significantly higher throughput at 16 threads vs. 1 thread.
# Client-Server Bottleneck: Verify that pmem-Redis has the lowest absolute throughput across the majority of tests.
# Emulated PM Throughput Expectations: Ensure the oracle does not penalize CapybaraKV if its throughput is lower than Viper's on emulated PM.
def read_ycsb(filename):
    data = {}
    with open(filename) as f:
        reader = list(csv.reader(f))
        for i in range(len(reader)):
            if not reader[i] or not reader[i][0]: continue
            workload = reader[i][0]
            if len(reader) > i + 1:
                row = reader[i + 1]
                if len(row) >= 5 and row[0] == '':
                    data[workload] = {
                        'redis': float(row[1]),
                        'pmemrocksdb': float(row[2]),
                        'viper': float(row[3]),
                        'capybarakv': float(row[4])
                    }
    return data

t1 = read_ycsb('ycsb_1thread.csv')
t16 = read_ycsb('ycsb_16thread.csv')

for run in ['Runb', 'Runc', 'Rund']:
    assert t16[run]['capybarakv'] > t1[run]['capybarakv'] * 1.5, f"Scaling failed for {run}"

for run, stats in t16.items():
    if run != 'Loada' and run != 'Loade' and run != 'Loadx':
        assert stats['redis'] < stats['pmemrocksdb'], "Redis is not the bottleneck"

print('ALL PERFORMANCE CHECKS PASSED')
"""
        return (
            self.command_check(
                name="performance_benchmarks_verify",
                cwd=self.workspace_path("osdi25", "capybaraKV", "evaluation"),
                cmd=("/usr/bin/python3", "-c", verify_script),
                signature="ALL PERFORMANCE CHECKS PASSED",
                timeout_seconds=60.0,
            ),
        )