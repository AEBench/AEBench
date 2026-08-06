from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

_SMOKE_SCRIPT = """
import os
import pathlib
import shutil
import subprocess
import sys

args = ("ab,bc->ac", "2,3,4", "(0,1)", "FP32", "0", "0")
env = os.environ.copy()
env.update(
	EINSUM_IR_BACKEND="TPP",
	EINSUM_IR_REORDER_DIMS="1",
	OMP_NUM_THREADS="1",
)

failures = []
candidates = {path for path in pathlib.Path(".").rglob("bench_expression") if path.is_file()}
if on_path := shutil.which("bench_expression"):
	candidates.add(pathlib.Path(on_path))
for executable in sorted(candidates):
	try:
		result = subprocess.run(
			(executable, *args),
			capture_output=True,
			text=True,
			timeout=120,
			env=env,
			check=False,
		)
	except OSError as exc:
		failures.append(f"{executable}: {exc}")
		continue
	output = result.stdout + result.stderr
	if result.returncode == 0 and "CSV_DATA: einsum_ir" in output:
		print(output)
		raise SystemExit(0)
	failures.append(f"{executable}: exit {result.returncode}")

print("; ".join(failures) if failures else "no bench_expression executable found")
raise SystemExit(1)
"""


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="tpp_benchmark_smoke",
				cmd=("python3", "-B", "-c", _SMOKE_SCRIPT),
				cwd=self.runtime_path(),
				timeout_seconds=150.0,
			),
		)
