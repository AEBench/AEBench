"""Semantic checks for CodeQL tables and the bounded atomic-data run."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.oracle_checks_runtime import (
	RuntimeCheckExecutor,
	check_path_is_dir,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult

from .common import (
	PARALEGAL_BENCH_COMMIT,
	PARALEGAL_COMMIT,
	find_artifact_root,
	latest_result_directory,
	load_expected_manifest,
	load_toml,
	parse_codeql_table,
	parse_smoke_results,
	validate_controller_results,
	validate_expected_files,
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		workspace = self.workspace_path()
		return (
			Check(
				name="codeql_semantic_results",
				fn=lambda executor: self._check_codeql_results(workspace, executor),
			),
			Check(
				name="atomic_data_smoke_results",
				fn=lambda executor: self._check_smoke_results(workspace, executor),
			),
		)

	def _check_codeql_results(
		self,
		workspace: Path,
		executor: RuntimeCheckExecutor,
	) -> CheckResult:
		artifact_root = find_artifact_root(workspace, executor=executor)
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		codeql_root = artifact_root / "codeql-experimentation"
		try:
			entries = load_expected_manifest(
				self.ref_path("codeql_expected_manifest.ref.json"), executor=executor
			)
			provenance_errors = validate_expected_files(codeql_root, entries, executor=executor)
			if provenance_errors:
				return CheckResult.failure("; ".join(provenance_errors))
			result_dir = latest_result_directory(codeql_root / "results", executor=executor)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"cannot locate CodeQL results: {exc}")

		for log_name in ("stdout.log", "stderr.log"):
			log_path = result_dir / log_name
			try:
				log_text = check_read_file_text(log_path, executor=executor)
			except (OSError, RuntimeError, ValueError):
				log_text = ""
			if not log_text:
				return CheckResult.failure(f"CodeQL runner log is missing or empty: {log_path}")

		mismatches: list[str] = []
		for entry in entries:
			expected_path = codeql_root / entry.expected_path
			actual_path = result_dir / entry.result_path
			try:
				expected = parse_codeql_table(
					check_read_file_text(expected_path, encoding="utf-8", executor=executor)
				)
				actual = parse_codeql_table(
					check_read_file_text(actual_path, encoding="utf-8", executor=executor)
				)
			except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
				mismatches.append(f"{entry.result_path}: {exc}")
				continue
			if actual != expected:
				mismatches.append(
					f"{entry.result_path}: semantic table differs from {entry.expected_path}"
				)

		if mismatches:
			return CheckResult.failure("; ".join(mismatches))
		return CheckResult.success(
			f"all {len(entries)} CodeQL intermediate tables semantically match"
		)

	def _check_smoke_results(
		self,
		workspace: Path,
		executor: RuntimeCheckExecutor,
	) -> CheckResult:
		artifact_root = find_artifact_root(workspace, executor=executor)
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		bench_root = artifact_root / "paralegal-bench"
		try:
			result_dir = latest_result_directory(
				bench_root / "results", executor=executor, suffix="-run"
			)
			results = parse_smoke_results(
				check_read_file_text(
					result_dir / "results.csv", encoding="utf-8", executor=executor
				)
			)
			controller_count = validate_controller_results(
				check_read_file_text(
					result_dir / "controllers.csv", encoding="utf-8", executor=executor
				),
				expected_run_ids=results.run_ids,
			)
			observed_config = load_toml(result_dir / "bench-config.toml", executor=executor)
			reference_config = load_toml(
				self.ref_path("smoke_bench_config.toml"), executor=executor
			)
			system_info = load_toml(result_dir / "sys.toml", executor=executor)
		except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
			return CheckResult.failure(f"invalid smoke result bundle: {exc}")

		if observed_config != reference_config:
			return CheckResult.failure(
				"the result bundle was not generated with the bounded smoke config"
			)
		commit_fields = {
			"paralegal_commit": PARALEGAL_COMMIT,
			"griswold_commit": PARALEGAL_BENCH_COMMIT,
			"repo_commit": PARALEGAL_BENCH_COMMIT,
		}
		mismatches = [
			f"{field}={system_info.get(field)!r}, expected {expected!r}"
			for field, expected in commit_fields.items()
			if system_info.get(field) != expected
		]
		if mismatches:
			return CheckResult.failure("smoke provenance mismatch: " + "; ".join(mismatches))

		prefix = result_dir.name.removesuffix("-run")
		log_dir = bench_root / "results" / f"{prefix}-logs"
		if not check_path_is_dir(log_dir, executor=executor):
			return CheckResult.failure(f"matching smoke log directory is missing: {log_dir}")
		evidence_files = (
			log_dir / "compile.stdout.txt",
			log_dir / "compile.stderr.txt",
			log_dir / "policy.out.txt",
		)
		missing_evidence: list[str] = []
		for path in evidence_files:
			try:
				if not check_read_file_text(path, executor=executor):
					missing_evidence.append(str(path))
			except (OSError, RuntimeError, ValueError):
				missing_evidence.append(str(path))
		if missing_evidence:
			return CheckResult.failure(
				"smoke run logs are missing or empty: " + ", ".join(missing_evidence)
			)

		return CheckResult.success(
			f"validated {len(results.rows)} atomic-data result rows, "
			f"{controller_count} controller rows, pinned commits, config, and logs"
		)
