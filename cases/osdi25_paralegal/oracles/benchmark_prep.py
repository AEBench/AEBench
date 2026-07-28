"""Checks for the bounded smoke configuration and reference provenance."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult

from .common import (
	find_artifact_root,
	load_expected_manifest,
	load_toml,
	validate_expected_files,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		artifact_root = find_artifact_root(self.workspace_path())
		return (
			Check(
				name="atomic_data_smoke_configuration",
				fn=lambda: self._check_smoke_config(artifact_root),
			),
			Check(
				name="codeql_expected_output_manifest",
				fn=lambda: self._check_codeql_manifest(artifact_root),
			),
			Check(
				name="writable_experiment_output_directories",
				fn=lambda: self._check_output_directories(artifact_root),
			),
		)

	def _check_smoke_config(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		reference_path = self.ref_path("smoke_bench_config.toml")
		observed_path = artifact_root / "paralegal-bench" / "bconf" / "aebench-smoke-config.toml"
		try:
			reference = load_toml(reference_path)
			observed = load_toml(observed_path)
		except (OSError, ValueError) as exc:
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
		missing = [str(path) for path in required_inputs if not path.exists()]
		if missing:
			return CheckResult.failure("smoke inputs are missing: " + ", ".join(missing))
		return CheckResult.success("bounded atomic-data smoke configuration is ready")

	def _check_codeql_manifest(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		try:
			entries = load_expected_manifest(self.ref_path("codeql_expected_manifest.ref.json"))
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"cannot load CodeQL manifest: {exc}")
		errors = validate_expected_files(
			artifact_root / "codeql-experimentation",
			entries,
		)
		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success(
			f"validated hashes, byte counts, and row counts for {len(entries)} "
			"CodeQL expected tables"
		)

	def _check_output_directories(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		directories = (
			artifact_root / "codeql-experimentation" / "results",
			artifact_root / "paralegal-bench" / "results",
		)
		errors: list[str] = []
		for directory in directories:
			if not directory.is_dir():
				errors.append(f"missing {directory}")
				continue
			try:
				with tempfile.NamedTemporaryFile(dir=directory, prefix=".aebench-write-"):
					pass
			except OSError as exc:
				errors.append(f"{directory} is not writable: {exc}")
		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success("CodeQL and Paralegal result directories are writable")
