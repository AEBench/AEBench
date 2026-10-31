from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

# Order of the nine per-compiler lists in data/<family>_{no_storage,storage}_compare.txt.
# The no_storage file holds PowerMove without storage, then Enola; the storage file holds
# PowerMove with storage, then without. Each file starts with the list of qubit counts.
METRICS = (
	"transfer_duration",
	"move_duration",
	"fidelity",
	"one_qubit_fidelity",
	"two_qubit_fidelity",
	"excitation_fidelity",
	"transfer_fidelity",
	"coherence_fidelity",
	"movement_stages",
)
FIDELITIES = METRICS[3:8]

# placer.py seeds the global RNG once at import, so durations, stages, transfer and
# coherence depend on which sizes a script compiled before. Gate-level fidelities don't.
SCORED = ("one_qubit_fidelity", "two_qubit_fidelity", "excitation_fidelity")

PRINTED_FIDELITY = re.compile(
	r"^cir_fidelity_(?:1q_gate|2q_gate|2q_gate_for_idle|atom_transfer|coherence) (\S+)$",
	re.MULTILINE,
)


def read_compare_file(text: str, qubits: int) -> tuple[dict[str, float], dict[str, float]]:
	"""Returns both compilers' metrics at the given size from one comparison file."""
	rows = [ast.literal_eval(line) for line in text.splitlines() if line.strip()]
	if len(rows) != 1 + 2 * len(METRICS):
		raise ValueError(f"expected {1 + 2 * len(METRICS)} lines, found {len(rows)}")
	if qubits not in rows[0]:
		raise ValueError(f"no results for {qubits} qubits")
	col = rows[0].index(qubits)
	values = [float(row[col]) for row in rows[1:]]
	n = len(METRICS)
	return dict(zip(METRICS, values[:n])), dict(zip(METRICS, values[n:]))


@dataclass(frozen=True, slots=True, kw_only=True)
class PowerMoveEvaluationCheck(BaseCheck):
	# workload -> (qubits, no_storage data, storage data, no_storage log, storage log)
	workloads: dict[str, tuple[int, OraclePath, OraclePath, OraclePath, OraclePath]]
	reference_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		reference = json.loads(self.reference_path.read_text(encoding="utf-8"))
		rel_tol = reference["relative_tolerance"]

		results: dict[str, dict[str, dict[str, float]]] = {}
		errors: list[str] = []
		for workload, (qubits, *paths) in self.workloads.items():
			try:
				no_storage_txt, storage_txt, no_storage_log, storage_log = (
					check_read_file_text(path, executor=executor) for path in paths
				)
				pm, enola = read_compare_file(no_storage_txt, qubits)
				pm_storage, _ = read_compare_file(storage_txt, qubits)
			except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
				errors.append(f"{workload}: {exc}")
				continue
			runs = {"non_storage": pm, "enola": enola, "with_storage": pm_storage}
			results[workload] = runs

			for mode, metrics in runs.items():
				if not all(0 < metrics[name] <= 1 for name in FIDELITIES):
					errors.append(f"{workload}.{mode}: fidelity outside (0, 1]")
				elif not math.isclose(
					metrics["fidelity"],
					math.prod(metrics[name] for name in FIDELITIES),
					rel_tol=1e-8,
				):
					errors.append(
						f"{workload}.{mode}: total fidelity is not the product of its components"
					)

			# qiskit 2's transpiler emits fewer CZs for QFT than qiskit 1, which the authors
			# used, so a workload lists one accepted reference per qiskit version that differs.
			mismatches = [
				[
					f"{workload}.{mode}.{name}: {runs[mode][name]:.6g}, expected {ref[mode][name]:.6g}"
					for mode in runs
					for name in SCORED
					if not math.isclose(runs[mode][name], ref[mode][name], rel_tol=rel_tol)
				]
				for ref in reference["workloads"][workload]
			]
			if all(mismatches):
				errors.extend(mismatches[0])

			# data/ can be restored from git history, so each result must also be printed in
			# the log of the run that wrote it. Both sides are float reprs, so match exactly.
			for log, modes in (
				(no_storage_log, ("non_storage", "enola")),
				(storage_log, ("with_storage",)),
			):
				if "Traceback (most recent call last):" in log:
					errors.append(f"{workload}: log has a Python traceback")
				printed = [float(value) for value in PRINTED_FIDELITY.findall(log)]
				logged = {tuple(printed[i : i + 5]) for i in range(0, len(printed), 5)}
				for mode in modes:
					if tuple(runs[mode][name] for name in FIDELITIES) not in logged:
						errors.append(f"{workload}.{mode}: fidelities not found in the log")

		if errors:
			return CheckResult.failure("invalid PowerMove results: " + "; ".join(errors))

		for workload, runs in results.items():
			if runs["with_storage"]["excitation_fidelity"] != 1:
				errors.append(f"{workload}: storage did not eliminate excitation error")
			improved = runs["with_storage"]["fidelity"] > runs["non_storage"]["fidelity"]
			# VQE is the paper's one case where storage-zone movement costs more than it saves.
			if workload == "vqe_30":
				if improved:
					errors.append(
						"vqe_30: storage improved fidelity, expected the paper's tradeoff"
					)
			elif not improved:
				errors.append(f"{workload}: storage did not improve total fidelity")
		qaoa = results["qaoa_regular3_30"]
		if qaoa["non_storage"]["fidelity"] <= qaoa["enola"]["fidelity"]:
			errors.append("qaoa_regular3_30: PowerMove fidelity is not above Enola")
		pm_time = qaoa["non_storage"]["transfer_duration"] + qaoa["non_storage"]["move_duration"]
		enola_time = qaoa["enola"]["transfer_duration"] + qaoa["enola"]["move_duration"]
		if pm_time >= enola_time:
			errors.append("qaoa_regular3_30: PowerMove execution time is not below Enola")

		if errors:
			return CheckResult.failure("PowerMove claim mismatch: " + "; ".join(errors))
		return CheckResult.success("all five workloads match the reference and the paper's claims")
