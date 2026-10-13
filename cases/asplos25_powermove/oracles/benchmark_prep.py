from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import SCRIPT_WORKLOADS, WORKLOADS


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
			if workload.startswith("qaoa_"):
				try:
					gates = ast.literal_eval(text)
				except (SyntaxError, ValueError) as exc:
					errors.append(f"{workload}: invalid gate list: {exc}")
					continue
				if not isinstance(gates, list):
					errors.append(f"{workload}: expected a CZ gate list")
					continue
				degrees = [0] * qubits
				valid_gates = True
				for gate in gates:
					if not (
						isinstance(gate, tuple)
						and len(gate) == 2
						and all(type(vertex) is int for vertex in gate)
						and gate[0] != gate[1]
						and 0 <= min(gate) <= max(gate) < qubits
					):
						errors.append(f"{workload}: invalid CZ gate {gate!r}")
						valid_gates = False
						break
					degrees[gate[0]] += 1
					degrees[gate[1]] += 1
				if not valid_gates:
					continue
				if not gates or len({frozenset(gate) for gate in gates}) != len(gates):
					errors.append(f"{workload}: expected nonempty distinct CZ gates")
				elif workload.startswith(("qaoa_regular3_", "qaoa_regular4_")):
					degree = 3 if workload.startswith("qaoa_regular3_") else 4
					if set(degrees) != {degree}:
						errors.append(
							f"{workload}: expected a {qubits}-node {degree}-regular graph"
						)
			elif workload.startswith("qsim_rand_0.3_"):
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
					errors.append(f"{workload}: expected at least ten {qubits}-qubit Pauli strings")
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
			*(
				self.path_check(
					name=f"script_{script}",
					path=self.runtime_path("test_scripts", f"{script}.py"),
					kind=PathKind.FILE,
				)
				for script in SCRIPT_WORKLOADS
			),
		)
