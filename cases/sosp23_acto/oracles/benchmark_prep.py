from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind, utils


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		workspace = self.workspace_path()
		campaign_data = workspace / "campaign-data"

		return (
			self.path_check(
				name="reproduce_bugs_script",
				path=workspace / "reproduce_bugs.py",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="produce_table6_script",
				path=workspace / "produce_table_6.py",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="collect_table8_script",
				path=workspace / "collect_number_of_ops.py",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="bug_reproduction_data",
				path=workspace / "test",
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="operator_configs",
				path=workspace / "data",
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="campaign_data",
				path=campaign_data,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="campaign_data_initialized",
				path=campaign_data / ".git",
				kind=PathKind.ANY,
			),
			self.directory_glob_count_check(
				name="operator_config_count",
				directory=workspace / "data",
				pattern="*/config.json",
				min_count=12,
			),
			self.directory_glob_count_check(
				name="bug_reproduction_input_count",
				directory=workspace / "test",
				pattern="*/mutated-*.yaml",
				min_count=56,
			),
			self.directory_glob_count_check(
				name="campaign_test_plan_count",
				directory=campaign_data,
				pattern="testrun-*/test_plan.json",
				min_count=12,
			),
			self.directory_glob_count_check(
				name="campaign_run_info_count",
				directory=campaign_data,
				pattern="testrun-*/testrun_info.json",
				min_count=12,
			),
			self.directory_glob_count_check(
				name="campaign_diff_log_count",
				directory=campaign_data,
				pattern="testrun-*/post_diff_test/test.log",
				min_count=12,
			),
		)
