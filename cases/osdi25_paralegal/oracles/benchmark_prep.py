"""Checks for the bounded smoke configuration and reference provenance."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.oracle_checks_runtime import (
	RuntimeCheckExecutor,
	check_path_exists,
	check_path_is_dir,
)
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult

from .common import (
	find_artifact_root,
	load_expected_manifest,
	load_toml,
	run_process,
	validate_expected_files,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		workspace = self.workspace_path()
		return (
			Check(
				name="atomic_data_smoke_configuration",
				fn=lambda executor: self._check_smoke_config(workspace, executor),
			),
			Check(
				name="codeql_expected_output_manifest",
				fn=lambda executor: self._check_codeql_manifest(workspace, executor),
			),
			Check(
				name="writable_experiment_output_directories",
				fn=lambda executor: self._check_output_directories(workspace, executor),
			),
		)

	def _check_smoke_config(
		self,
		workspace: Path,
		executor: RuntimeCheckExecutor,
	) -> CheckResult:
		artifact_root = find_artifact_root(workspace, executor=executor)
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		reference_path = self.ref_path("smoke_bench_config.toml")
		observed_path = artifact_root / "paralegal-bench" / "bconf" / "aebench-smoke-config.toml"
		try:
			reference = load_toml(reference_path, executor=executor)
			observed = load_toml(observed_path, executor=executor)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"cannot load smoke configuration: {exc}")
		if observed != reference:
			return CheckResult.failure(
				f"{observed_path} does not match the bundled bounded smoke configuration"
			)

		if reference.get("paralegal-home-dir") != "../paralegal":
			return CheckResult.failure("smoke config has the wrong paralegal-home-dir")
		if reference.get("pdg-timeout") != "15min":
			return CheckResult.failure("smoke config has the wrong pdg-timeout")
		if reference.get("repeats") != 1:
			return CheckResult.failure("smoke config must run exactly one repetition")
		app_config = reference.get("app-config", {}).get("atomic-data", {})
		if app_config.get("source-dir") != "case-studies/atomic-server":
			return CheckResult.failure("smoke config does not select the atomic-data source")
		experiments = reference.get("experiment", {}).get("smoke")
		if not isinstance(experiments, list) or len(experiments) != 1:
			return CheckResult.failure("smoke config must contain exactly one experiment")
		experiment = experiments[0]
		if (
			experiment.get("mode") != "case-study"
			or experiment.get("application") != "atomic-data"
			or experiment.get("policy-mode") != "unified"
			or experiment.get("cnl") is not True
		):
			return CheckResult.failure("smoke experiment fields do not match the bounded claim")

		required_inputs = (
			artifact_root / "paralegal",
			artifact_root / "paralegal-bench" / "case-studies" / "atomic-server",
			artifact_root
			/ "paralegal-bench"
			/ "case-studies"
			/ "atomic-server"
			/ "external-annotations.toml",
		)
		missing = [
			str(path) for path in required_inputs if not check_path_exists(path, executor=executor)
		]
		if missing:
			return CheckResult.failure("smoke inputs are missing: " + ", ".join(missing))
		return CheckResult.success("bounded atomic-data smoke configuration is ready")

	def _check_codeql_manifest(
		self,
		workspace: Path,
		executor: RuntimeCheckExecutor,
	) -> CheckResult:
		artifact_root = find_artifact_root(workspace, executor=executor)
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		try:
			entries = load_expected_manifest(
				self.ref_path("codeql_expected_manifest.ref.json"), executor=executor
			)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"cannot load CodeQL manifest: {exc}")
		errors = validate_expected_files(
			artifact_root / "codeql-experimentation",
			entries,
			executor=executor,
		)
		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success(
			f"validated hashes, byte counts, and row counts for {len(entries)} "
			"CodeQL expected tables"
		)

	def _check_output_directories(
		self,
		workspace: Path,
		executor: RuntimeCheckExecutor,
	) -> CheckResult:
		artifact_root = find_artifact_root(workspace, executor=executor)
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		directories = (
			artifact_root / "codeql-experimentation" / "results",
			artifact_root / "paralegal-bench" / "results",
		)
		errors: list[str] = []
		for directory in directories:
			if not check_path_is_dir(directory, executor=executor):
				errors.append(f"missing {directory}")
				continue
			writable = run_process(
				("test", "-w", str(directory)),
				executor=executor,
				timeout_seconds=10.0,
			)
			if not writable.ok:
				errors.append(f"{directory} is not writable")
		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success("CodeQL and Paralegal result directories are writable")
