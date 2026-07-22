from __future__ import annotations
import csv
import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.reporting import BaseCheck, Check, CheckResult
from evaluator.oracles.bases import CaseOracleExperimentRunsBase


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            Check(
                name="performance_benchmarks_verify",
                fn=self._verify_performance,
            ),
        )

    def _verify_performance(self) -> CheckResult:
        eval_dir = self.workspace_path("osdi25", "capybaraKV", "evaluation")
        results_dir = eval_dir / "results" / "artifact_evaluation_results"
        
        if not results_dir.is_dir():
            return CheckResult.failure(f"Missing results directory: {results_dir}")

        try:
            with open(self.ref_path("results.json"), "r", encoding="utf-8") as f:
                reference = json.load(f)
        except Exception as e:
            return CheckResult.failure(f"Failed to load reference data: {e}")

        kvs_list = reference["kvs"]
        storage_fp = reference["storage_footprint_gib"]
        mem_min = reference["memory_footprint_min_gib"]
        mem_max = reference["memory_footprint_max_gib"]
        scaling = reference["scaling_factor"]

        try:
            res_startup = {kv: {"full": [], "empty": []} for kv in kvs_list}
            for kv in kvs_list:
                for i in range(0, 5):
                    empty_file = results_dir / "microbenchmark" / kv / "empty_setup" / f"Run{i+1}"
                    full_file = results_dir / "microbenchmark" / kv / "full_setup" / f"Run{i}"
                    with open(empty_file) as f:
                        res_startup[kv]["empty"].append(int(f.read().strip()))
                    if kv == "redis":
                        res_startup[kv]["full"].append(0)
                    else:
                        with open(full_file) as f:
                            res_startup[kv]["full"].append(int(f.read().strip()))
            
            avg_startup = {kv: {"full": sum(res_startup[kv]["full"])/5.0, "empty": sum(res_startup[kv]["empty"])/5.0} for kv in kvs_list}
        except Exception as e:
            return CheckResult.failure(f"Failed parsing startup times: {e}")

        # Startup Time Trend: Empty startup time must be lower than Full startup time for all systems.
        for kv in kvs_list:
            if avg_startup[kv]['empty'] >= avg_startup[kv]['full']:
                return CheckResult.failure(f"{kv} empty startup time >= full startup time")
        
        # Hardware Discrepancy: Assert that Viper starts faster than CapybaraKV (both empty and full).
        if avg_startup['viper']['empty'] >= avg_startup['capybarakv']['empty']:
            return CheckResult.failure("Viper empty startup >= CapybaraKV empty startup")
        if avg_startup['viper']['full'] >= avg_startup['capybarakv']['full']:
            return CheckResult.failure("Viper full startup >= CapybaraKV full startup")

        # Latency Trend: Sequential Reads (Seq Get) latency must be lower than Random Reads (Rand Get) latency.
        try:
            with open(eval_dir / "results.json") as f:
                res_latency = json.load(f)
            
            for kv in ['pmemrocksdb', 'capybarakv', 'redis']:
                seq_get = res_latency[kv]['sequential_get'][0]
                rand_get = res_latency[kv]['rand_get'][0]
                if seq_get >= rand_get:
                    return CheckResult.failure(f"{kv} seq_get >= rand_get")
        except Exception as e:
            return CheckResult.failure(f"Failed testing latency trend: {e}")

        try:
            filename = results_dir / "mem_storage" / "capybarakv" / "Loada" / "Run1"
            pre_blocks = 0
            post_blocks = 0
            mem_gibs = []
            with open(filename) as f:
                for line in f:
                    if "Mem usage" in line:
                        mem_gibs.append(int(line.split()[2]) / (1024**3))
                    elif "Pre-experiment storage stats" in line:
                        pre_blocks = int(line.split()[5])
                    elif "Post-experiment storage stats" in line:
                        post_blocks = int(line.split()[5])
            
            if not mem_gibs or pre_blocks == 0 or post_blocks == 0:
                return CheckResult.failure("Missing mem/storage info in capybarakv Run1 logs")

            gb_used = (post_blocks - pre_blocks) / (1024**2)
            avg_mem = sum(mem_gibs)/len(mem_gibs)

            # Storage Footprint (Rounded): Extract storage bytes via get_mem_and_storage_use.py and assert exact match.
            if round(gb_used) != storage_fp:
                return CheckResult.failure(f"Storage footprint mismatch: {round(gb_used)} != {storage_fp}")
            
            # Memory Footprint: Check that DRAM usage closely aligns with the reported gigabytes.
            if not (mem_min <= avg_mem <= mem_max):
                return CheckResult.failure(f"Memory footprint out of bounds: {avg_mem}")
        except Exception as e:
            return CheckResult.failure(f"Failed parsing mem/storage: {e}")

        def read_ycsb(fname: Path):
            data = {}
            with open(fname) as f:
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

        try:
            t1 = read_ycsb(eval_dir / 'ycsb_1thread.csv')
            t16 = read_ycsb(eval_dir / 'ycsb_16thread.csv')

            # Scaling Trend: Check that read-heavy workloads (Runs B, C, D) have significantly higher throughput at 16 threads vs. 1 thread.
            for run in ['Runb', 'Runc', 'Rund']:
                if t16[run]['capybarakv'] <= t1[run]['capybarakv'] * scaling:
                    return CheckResult.failure(f"Scaling failed for {run}: {t16[run]['capybarakv']} not > {t1[run]['capybarakv']} * {scaling}")
            
            # Client-Server Bottleneck: Verify that pmem-Redis has the lowest absolute throughput across the majority of tests.
            # Emulated PM Throughput Expectations: Ensure the oracle does not penalize CapybaraKV if its throughput is lower than Viper's on emulated PM.
            for run, stats in t16.items():
                if run not in ['Loada', 'Loade', 'Loadx']:
                    if stats['redis'] >= stats['pmemrocksdb'] or stats['redis'] >= stats['viper'] or stats['redis'] >= stats['capybarakv']:
                        return CheckResult.failure(f"Redis is not the bottleneck for {run}")
        except Exception as e:
            return CheckResult.failure(f"Failed validating YCSB scalability trends: {e}")

        return CheckResult.success("All performance checks passed")