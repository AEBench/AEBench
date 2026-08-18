from __future__ import annotations
import json
from dataclasses import dataclass
import pstats
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.reporting import BaseCheck, CheckResult
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import ListSimilarityCheck

@dataclass(frozen=True, slots=True, kw_only=True)
class JSONPlotAggregateStructCheck(BaseCheck):
    name: str
    out_dir: Path
    def check(self) -> CheckResult:
        from .const import EXPECTED_FILES, EXPECTED_STRUCTURES
        expected_files = set(EXPECTED_FILES)
        expected_structs = set(EXPECTED_STRUCTURES)

        found_structs = set()
        found_files = set()
        
        if not self.out_dir.exists():
            return CheckResult.failure(f"Directory not found: {self.out_dir}")
            
        subdirs = [d for d in self.out_dir.iterdir() if d.is_dir()]
        if not subdirs:
            return CheckResult.failure(f"No run directories found in {self.out_dir}")
            
        latest_dir = max(subdirs, key=lambda d: d.stat().st_mtime)
        
        for json_file in latest_dir.rglob("*.json"):
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

def _extract_top_50_cumtime(perf_file: Path) -> list[float]:
    stats = pstats.Stats(str(perf_file))
    sorted_stats = sorted(stats.stats.values(), key=lambda x: x[3], reverse=True)
    return [float(x[3]) for x in sorted_stats[:50]]

@dataclass(frozen=True, slots=True, kw_only=True)
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
            obs_list = _extract_top_50_cumtime(self.observed_perf_file)
            ref_list = _extract_top_50_cumtime(self.reference_perf_file)
            
            if len(obs_list) < len(ref_list):
                return CheckResult.failure(f"Incomplete profile: expected at least {len(ref_list)} entries, found {len(obs_list)}.")
            if len(ref_list) == 0:
                return CheckResult.failure("Empty reference perf profile.")
            
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
            # 1. Verify that the exported JSON plots contain all expected structs and files
            JSONPlotAggregateStructCheck(
                name="exported_plots_structs_exist",
                out_dir=repo_root / "out",
            ),
            # 2. Verify that the performance profile is statistically similar to the reference baseline (C4)
            PStatsSimilarityCheck(
                name="perf_similarity_matches_baseline",
                observed_perf_file=repo_root / "tmp" / "visualinux-sync.perf",
                reference_perf_file=self.ref_path("performance.ref.perf")
            ),
        )