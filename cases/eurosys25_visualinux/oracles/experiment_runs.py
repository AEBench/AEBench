from __future__ import annotations
import json
import dataclasses
import pstats
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.reporting import BaseCheck, CheckResult
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import CommandCheck, PathCheck, PathKind, ListSimilarityCheck

@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class JSONPlotAggregateStructCheck(BaseCheck):
    name: str
    out_dir: Path
    refs_file: Path
    
    def check(self) -> CheckResult:
        if not self.refs_file.exists():
            return CheckResult.failure(f"Reference file not found: {self.refs_file}")
            
        try:
            with open(self.refs_file, 'r') as f:
                refs_data = json.load(f)
                expected_files = set(refs_data.get("expected_files", []))
                expected_structs = set(refs_data.get("expected_structures", []))
        except Exception as e:
            return CheckResult.failure(f"Failed to load reference file: {e}")

        found_structs = set()
        found_files = set()
        
        if not self.out_dir.exists():
            return CheckResult.failure(f"Directory not found: {self.out_dir}")
            
        for json_file in self.out_dir.rglob("*.json"):
            found_files.add(json_file.name)
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    boxes = data.get("pool", {}).get("boxes", {})
                    for box in boxes.values():
                        if "type" in box:
                            found_structs.add(box["type"])
            except Exception:
                pass
                
        missing_files = expected_files - found_files
        if missing_files:
            return CheckResult.failure(f"Missing expected plot files: {missing_files}")
            
        missing_structs = expected_structs - found_structs
        if missing_structs:
            return CheckResult.failure(f"Missing required kernel structures across plots: {missing_structs}")
            
        return CheckResult.success(message=f"Found all {len(expected_files)} files and {len(expected_structs)} structures.")

@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PStatsSimilarityCheck(BaseCheck):
    name: str
    observed_perf_file: Path
    reference_perf_file: Path
    min_similarity: float = 0.8
    
    def check(self) -> CheckResult:
        if not self.observed_perf_file.exists():
            return CheckResult.failure(f"Observed perf file not found: {self.observed_perf_file}")
        if not self.reference_perf_file.exists():
            return CheckResult.failure(f"Reference perf file not found: {self.reference_perf_file}")
            
        try:
            obs_stats = pstats.Stats(str(self.observed_perf_file))
            obs_sorted = sorted(obs_stats.stats.values(), key=lambda x: x[3], reverse=True)
            obs_list = [float(x[3]) for x in obs_sorted[:50]]
            
            ref_stats = pstats.Stats(str(self.reference_perf_file))
            ref_sorted = sorted(ref_stats.stats.values(), key=lambda x: x[3], reverse=True)
            ref_list = [float(x[3]) for x in ref_sorted[:50]]
            
            min_len = min(len(obs_list), len(ref_list))
            if min_len == 0:
                return CheckResult.failure("Empty perf profiles.")
            
            obs_list = obs_list[:min_len]
            ref_list = ref_list[:min_len]
            
            sim_check = ListSimilarityCheck(
                name=f"{self.name}_similarity", 
                observed=obs_list, 
                reference=ref_list,
                min_similarity=self.min_similarity
            )
            return sim_check.check()
            
        except Exception as e:
            return CheckResult.failure(f"Failed to parse perf files or compute similarity: {e}")

class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = self.workspace_path()
        return (
            # 1. Verify that the profiling framework generated the raw perf output
            PathCheck(
                name="visualinux_sync_perf_exists",
                path=repo_root / "tmp" / "visualinux-sync.perf",
                kind=PathKind.FILE,
            ),
            # 2. Verify that the exported JSON plots contain all expected structs and files
            JSONPlotAggregateStructCheck(
                name="exported_plots_structs_exist",
                out_dir=repo_root / "out",
                refs_file=self.ref_path("results.json")
            ),
            # 3. Verify that the performance profile is statistically similar to the reference baseline (C4)
            PStatsSimilarityCheck(
                name="perf_similarity_matches_baseline",
                observed_perf_file=repo_root / "tmp" / "visualinux-sync.perf",
                reference_perf_file=self.ref_path("performance.ref.perf")
            ),
        )