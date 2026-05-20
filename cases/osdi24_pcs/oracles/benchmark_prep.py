from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from evaluator.oracles import utils


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir

		return (
			FilesystemPathCheck(
				name="simulation_dir",
				path=repo_root / "simulation",
				path_type=PathType.DIRECTORY,
			),
			FilesystemPathCheck(
				name="sim_py",
				path=repo_root / "simulation" / "sim.py",
				path_type=PathType.FILE,
			),
			FilesystemPathCheck(
				name="data_dir",
				path=repo_root / "data",
				path_type=PathType.DIRECTORY,
			),
			FilesystemPathCheck(
				name="pcs_configs_dir",
				path=repo_root / "data" / "PCS_configs",
				path_type=PathType.DIRECTORY,
			),
			FilesystemPathCheck(
				name="run_toy_example_sh",
				path=repo_root / "run_toy_example.sh",
				path_type=PathType.FILE,
			),
			FilesystemPathCheck(
				name="run_workload2_sh",
				path=repo_root / "run_workload2.sh",
				path_type=PathType.FILE,
			),
			FilesystemPathCheck(
				name="run_workload3_sh",
				path=repo_root / "run_workload3.sh",
				path_type=PathType.FILE,
			),
			FilesystemPathCheck(
				name="profile_time_per_sim_sh",
				path=repo_root / "profile_time_per_sim.sh",
				path_type=PathType.FILE,
			),
			FilesystemPathCheck(
				name="profile_sensitivity_error_in_size_sh",
				path=repo_root / "profile_sensitivity_error_in_size.sh",
				path_type=PathType.FILE,
			),
		)
