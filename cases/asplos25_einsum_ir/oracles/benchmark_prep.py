from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import WORKLOAD_CONFIGS


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkloadConfigsCheck(BaseCheck):
	files: Sequence[tuple[str, OraclePath]]
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		errors: list[str] = []
		for workload, path in self.files:
			try:
				lines = [
					line.strip()
					for line in check_read_file_text(path, executor=self.executor).splitlines()
					if line.strip()
				]
			except OSError as exc:
				errors.append(f"{workload}: {exc}")
				continue
			if len(lines) != 1:
				errors.append(f"{workload}: expected one benchmark configuration")
				continue
			try:
				fields = shlex.split(lines[0])
			except ValueError as exc:
				errors.append(f"{workload}: {exc}")
				continue
			if len(fields) != 3:
				errors.append(f"{workload}: expected expression, dimensions, and path")
				continue
			try:
				dimensions = [int(value) for value in fields[1].split(",")]
			except ValueError:
				errors.append(f"{workload}: dimension sizes are not integers")
				continue
			if not dimensions or any(value <= 0 for value in dimensions):
				errors.append(f"{workload}: dimension sizes must be positive")

		if errors:
			return CheckResult.failure("invalid released workload configs: " + "; ".join(errors))
		return CheckResult.success(f"validated all {len(self.files)} released workload configs")


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			WorkloadConfigsCheck(
				name="released_workload_configs",
				files=tuple(
					(workload, self.runtime_path(path)) for workload, path in WORKLOAD_CONFIGS
				),
				executor=self.executor,
			),
		)
