from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import WORKLOADS


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleasedInputsCheck(BaseCheck):
	paths: dict[str, tuple[int, OraclePath]]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		errors: list[str] = []
		for workload, (qubits, path) in self.paths.items():
			try:
				text = check_read_file_text(path, executor=executor)
			except (OSError, RuntimeError, ValueError) as exc:
				errors.append(f"{workload}: {exc}")
				continue
			if workload == "qaoa_regular3_30":
				try:
					gates = ast.literal_eval(text)
				except (SyntaxError, ValueError) as exc:
					errors.append(f"{workload}: invalid gate list: {exc}")
				continue
				degrees = [0] * qubits
				for gate in gates:
					if not (
						isinstance(gate, tuple)
						and len(gate) == 2
						and all(isinstance(vertex, int) for vertex in gate)
						and gate[0] != gate[1]
						and 0 <= min(gate) <= max(gate) < qubits
					):
						errors.append(f"{workload}: invalid CZ gate {gate!r}")
						break
					degrees[gate[0]] += 1
					degrees[gate[1]] += 1
				if len(gates) != 45 or len({frozenset(gate) for gate in gates}) != 45:
					errors.append(f"{workload}: expected 45 distinct CZ gates")
				elif set(degrees) != {3}:
					errors.append(f"{workload}: expected a 30-node 3-regular graph")
			elif workload == "qsim_rand_0.3_10":
				try:
					strings = ast.literal_eval(text)
				except (SyntaxError, ValueError) as exc:
					errors.append(f"{workload}: invalid Pauli strings: {exc}")
					continue
				if not (
					isinstance(strings, list)
					and len(strings) >= 10
					and all(
						isinstance(value, str)
						and len(value) == qubits
						and set(value) <= set("IXYZ")
						for value in strings[:10]
					)
				):
					errors.append(f"{workload}: expected at least ten 10-qubit Pauli strings")
			elif "OPENQASM 2.0;" not in text or f"[{qubits}]" not in text:
				errors.append(f"{workload}: invalid {qubits}-qubit OpenQASM input")

		if errors:
			return CheckResult.failure("invalid released inputs: " + "; ".join(errors))
		return CheckResult.success(f"validated all {len(self.paths)} released inputs")


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			ReleasedInputsCheck(
				name="released_evaluation_inputs",
				paths={
					workload: (qubits, self.runtime_path(path))
					for workload, (qubits, path) in WORKLOADS.items()
				},
			),
		)
