from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, ElementwiseSimilarityThresholdCheck
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck, CheckResult


def _load_expected_counts(path: Path, *, executor: RuntimeCheckExecutor) -> dict[str, int]:
	try:
		raw = json.loads(executor.read_file_text(path))
	except OSError as exc:
		raise ValueError(f"failed to read expected bug counts: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid expected bug counts JSON: {exc}") from exc

	if not isinstance(raw, dict):
		raise ValueError(f"expected bug counts JSON must be an object, got {type(raw).__name__}")

	counts: dict[str, int] = {}
	for benchmark, value in raw.items():
		if not isinstance(benchmark, str) or not benchmark.strip():
			raise ValueError(f"invalid benchmark name in expected bug counts: {benchmark!r}")
		if not isinstance(value, int):
			raise ValueError(f"expected bug count for {benchmark!r} must be an integer")
		counts[benchmark] = value

	return counts


def _count_bug_dirs(path: Path, *, executor: RuntimeCheckExecutor) -> int:
	if not executor.path_is_dir(path):
		return 0

	try:
		return sum(
			1
			for entry in executor.glob(path, "*")
			if executor.path_is_dir(RuntimePath.from_parts(entry.as_posix()))
		)
	except OSError:
		return 0


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BugTotalsCheck(BaseCheck):
	expected_path: Path
	workspace_dir: Path
	observed_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			expected = _load_expected_counts(self.expected_path, executor=executor)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		benchmarks = list(expected.keys())
		observed = {
			benchmark: _count_bug_dirs(
				self.workspace_dir / f"{benchmark}_test" / "bugs", executor=executor
			)
			for benchmark in benchmarks
		}

		try:
			result = executor.run_process_capture(
				cmd=(
					"sh",
					"-c",
					'mkdir -p "$(dirname "$1")" && printf "%s\\n" "$2" > "$1"',
					"sh",
					str(executor.resolve_path(self.observed_path)),
					json.dumps(observed, indent=2, sort_keys=True),
				),
				cwd=None,
				env=None,
				timeout_seconds=10.0,
			)
			if result.returncode != 0:
				raise OSError(result.stderr or f"exit code {result.returncode}")
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"failed to write observed bug totals: {exc}")

		result = ElementwiseSimilarityThresholdCheck(
			name="bugs_totals_match",
			observed=[float(observed[benchmark]) for benchmark in benchmarks],
			reference=[float(expected[benchmark]) for benchmark in benchmarks],
			threshold=1.0,
		).check(executor)

		if result.ok:
			return CheckResult.success()

		return CheckResult.failure(
			f"{result.message}\nobserved totals written to {self.observed_path}"
		)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			BugTotalsCheck(
				name="bugs_totals_match",
				expected_path=self.ref_path("bugs_expected.json"),
				workspace_dir=self.workspace_path(),
				observed_path=self.output_path("bugs_observed.json"),
			),
		)
