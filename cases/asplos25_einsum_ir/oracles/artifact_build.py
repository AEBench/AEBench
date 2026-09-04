from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_path_is_file,
	glob,
	resolve_check_executable,
	run_check_process_capture,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_SMOKE_ARGS = ("ab,bc->ac", "2,3,4", "(0,1)", "FP32", "0", "0")
_SMOKE_ENV = {
	"EINSUM_IR_BACKEND": "TPP",
	"EINSUM_IR_REORDER_DIMS": "1",
	"OMP_NUM_THREADS": "1",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkSmokeCheck(BaseCheck):
	cwd: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			candidates = {
				RuntimePath.from_parts(str(path))
				for path in glob(self.cwd, "**/bench_expression", executor=executor)
			}
			candidates = {
				path for path in candidates if check_path_is_file(path, executor=executor)
			}
			if on_path := resolve_check_executable("bench_expression", executor=executor):
				candidates.add(RuntimePath.from_parts(on_path))
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"could not locate bench_expression: {exc}")

		failures: list[str] = []
		for executable in sorted(candidates, key=str):
			try:
				result = run_check_process_capture(
					cmd=(str(executable), *_SMOKE_ARGS),
					cwd=self.cwd,
					env=_SMOKE_ENV,
					timeout_seconds=120.0,
					executor=executor,
				)
			except (OSError, RuntimeError, ValueError) as exc:
				failures.append(f"{executable}: {exc}")
				continue
			output = result.stdout + result.stderr
			if result.returncode == 0 and "CSV_DATA: einsum_ir" in output:
				return CheckResult.success(
					"bench_expression completed a TPP smoke test",
					stdout=result.stdout,
					stderr=result.stderr,
					returncode=result.returncode,
				)
			failures.append(f"{executable}: exit {result.returncode}")

		detail = "; ".join(failures) if failures else "no bench_expression executable found"
		return CheckResult.failure(detail)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			BenchmarkSmokeCheck(
				name="tpp_benchmark_smoke",
				cwd=self.runtime_path(),
			),
		)
