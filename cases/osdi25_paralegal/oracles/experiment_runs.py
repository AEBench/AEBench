"""Semantic checks for CodeQL tables and the bounded atomic-data run."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase
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
		artifact_root = find_artifact_root(self.workspace_path())
		return (
			Check(
				name="codeql_semantic_results",
				fn=lambda: self._check_codeql_results(artifact_root),
			),
			Check(
				name="atomic_data_smoke_results",
				fn=lambda: self._check_smoke_results(artifact_root),
			),
		)

	def _check_codeql_results(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		codeql_root = artifact_root / "codeql-experimentation"
		try:
			entries = load_expected_manifest(self.ref_path("codeql_expected_manifest.ref.json"))
			provenance_errors = validate_expected_files(codeql_root, entries)
			if provenance_errors:
				return CheckResult.failure("; ".join(provenance_errors))
			result_dir = latest_result_directory(codeql_root / "results")
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"cannot locate CodeQL results: {exc}")

		for log_name in ("stdout.log", "stderr.log"):
			log_path = result_dir / log_name
			if not log_path.is_file() or log_path.stat().st_size == 0:
				return CheckResult.failure(f"CodeQL runner log is missing or empty: {log_path}")

		mismatches: list[str] = []
		for entry in entries:
			expected_path = codeql_root / entry.expected_path
			actual_path = result_dir / entry.result_path
			try:
				expected = parse_codeql_table(expected_path.read_text(encoding="utf-8"))
				actual = parse_codeql_table(actual_path.read_text(encoding="utf-8"))
			except (OSError, UnicodeError, ValueError) as exc:
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

	def _check_smoke_results(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")
		bench_root = artifact_root / "paralegal-bench"
		try:
			result_dir = latest_result_directory(bench_root / "results", suffix="-run")
			results = parse_smoke_results((result_dir / "results.csv").read_text(encoding="utf-8"))
			controller_count = validate_controller_results(
				(result_dir / "controllers.csv").read_text(encoding="utf-8"),
				expected_run_ids=results.run_ids,
			)
			observed_config = load_toml(result_dir / "bench-config.toml")
			reference_config = load_toml(self.ref_path("smoke_bench_config.toml"))
			system_info = load_toml(result_dir / "sys.toml")
		except (OSError, UnicodeError, ValueError) as exc:
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
		if not log_dir.is_dir():
			return CheckResult.failure(f"matching smoke log directory is missing: {log_dir}")
		evidence_files = (
			log_dir / "compile.stdout.txt",
			log_dir / "compile.stderr.txt",
			log_dir / "policy.out.txt",
		)
		missing_evidence = [
			str(path) for path in evidence_files if not path.is_file() or path.stat().st_size == 0
		]
		if missing_evidence:
			return CheckResult.failure(
				"smoke run logs are missing or empty: " + ", ".join(missing_evidence)
			)

		return CheckResult.success(
			f"validated {len(results.rows)} atomic-data result rows, "
			f"{controller_count} controller rows, pinned commits, config, and logs"
		)
