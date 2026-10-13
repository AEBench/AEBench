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
	RuntimePath,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import FAMILIES, SCRIPT_WORKLOADS, WORKLOADS

_FIDELITY_COMPONENTS = (
	"one_qubit_fidelity",
	"two_qubit_fidelity",
	"excitation_fidelity",
	"transfer_fidelity",
	"coherence_fidelity",
)
_METRICS = (
	"transfer_duration",
	"move_duration",
	"fidelity",
	*_FIDELITY_COMPONENTS,
	"movement_stages",
)
_REFERENCE_METRICS = ("one_qubit_fidelity", "two_qubit_fidelity", "excitation_fidelity")
_FAILURE_RE = re.compile(r"Traceback \(most recent call last\):|\b(?:Error|Exception):")
# Require at least two orders of magnitude for the large-circuit storage claim.
_LARGE_STORAGE_MIN_GAIN = 100.0


def _join(base: OraclePath, name: str) -> OraclePath:
	if isinstance(base, RuntimePath):
		return RuntimePath.from_parts(base.value, name)
	return Path(base, name)


def _load_json(text: str, label: str) -> dict[str, object]:
	try:
		payload = json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError(f"{label}: invalid JSON: {exc}") from exc
	if not isinstance(payload, dict):
		raise ValueError(f"{label}: expected a JSON object")
	return payload


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
	if not isinstance(value, (int, float)) or isinstance(value, bool):
		raise ValueError(f"{label}: expected a number")
	number = float(value)
	if not math.isfinite(number) or number < 0 or (positive and number == 0):
		raise ValueError(f"{label}: invalid value {value!r}")
	return number


def _parse_metrics(value: object, label: str) -> dict[str, float]:
	if not isinstance(value, dict):
		raise ValueError(f"{label}: expected a metrics object")
	missing = sorted(set(_METRICS) - value.keys())
	if missing:
		raise ValueError(f"{label}: missing metrics {missing}")

	metrics = {
		name: _finite_number(value[name], f"{label}.{name}", positive=True) for name in _METRICS
	}
	for name in ("fidelity", *_FIDELITY_COMPONENTS):
		if metrics[name] > 1:
			raise ValueError(f"{label}.{name}: fidelity exceeds 1")
	stages = value["movement_stages"]
	if not isinstance(stages, int) or isinstance(stages, bool):
		raise ValueError(f"{label}.movement_stages: expected an integer")
	metrics["movement_stages"] = stages

	product = math.prod(metrics[name] for name in _FIDELITY_COMPONENTS)
	if not math.isclose(metrics["fidelity"], product, rel_tol=1e-8, abs_tol=1e-12):
		raise ValueError(f"{label}: total fidelity does not match its five components")
	return metrics


def parse_comparison(text: str, label: str) -> dict[int, tuple[dict[str, float], dict[str, float]]]:
	"""Read the upstream qubit list followed by two sets of nine metric lists."""
	lines = [line for line in text.splitlines() if line.strip()]
	if len(lines) != 19:
		raise ValueError(f"{label}: expected 19 lines, got {len(lines)}")
	try:
		rows = [ast.literal_eval(line) for line in lines]
	except (SyntaxError, ValueError) as exc:
		raise ValueError(f"{label}: invalid metric list: {exc}") from exc
	if not all(isinstance(row, list) for row in rows):
		raise ValueError(f"{label}: every line must be a list")
	qubits = rows[0]
	if not qubits or any(type(n) is not int or n <= 0 for n in qubits):
		raise ValueError(f"{label}: invalid qubit counts")
	if len(set(qubits)) != len(qubits):
		raise ValueError(f"{label}: duplicate qubit counts")
	if any(len(row) != len(qubits) for row in rows[1:]):
		raise ValueError(f"{label}: metric list lengths do not match the qubit list")
	comparisons = {}
	for index, n in enumerate(qubits):
		groups = []
		for offset in (1, 10):
			values = {name: rows[offset + i][index] for i, name in enumerate(_METRICS)}
			groups.append(_parse_metrics(values, f"{label}[{n}]"))
		comparisons[n] = (groups[0], groups[1])
	return comparisons


def _within_tolerance(observed: float, expected: float, relative: float, absolute: float) -> bool:
	return abs(observed - expected) <= max(abs(expected) * relative, absolute)


def _validate_metrics(
	metrics: dict[str, float],
	reference: object,
	label: str,
	*,
	relative: float,
	absolute: float,
) -> dict[str, float]:
	if not isinstance(reference, dict):
		raise ValueError(f"{label}: invalid reference metrics")
	# Placement consumes random state, so movement and coherence vary with call order.
	for metric in _REFERENCE_METRICS:
		expected = _finite_number(
			reference.get(metric),
			f"reference {label}.{metric}",
			positive=True,
		)
		if not _within_tolerance(metrics[metric], expected, relative, absolute):
			raise ValueError(f"{label}.{metric}: {metrics[metric]:.6g} differs from {expected:.6g}")
	return metrics


@dataclass(frozen=True, slots=True, kw_only=True)
class PowerMoveEvaluationCheck(BaseCheck):
	results_dir: OraclePath
	logs_dir: OraclePath
	reference_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			reference = _load_json(self.reference_path.read_text(encoding="utf-8"), "reference")
			workload_refs = reference["workloads"]
			if not isinstance(workload_refs, dict) or set(workload_refs) != set(WORKLOADS):
				raise ValueError("reference has an unexpected workload set")
			relative = _finite_number(reference["relative_tolerance"], "relative tolerance")
			absolute = _finite_number(reference["absolute_tolerance"], "absolute tolerance")
		except (KeyError, OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"invalid PowerMove evaluation setup: {exc}")

		errors: list[str] = []
		observed: dict[str, dict[int, tuple[dict[str, float], dict[str, float]]]] = {}
		for script, workloads in SCRIPT_WORKLOADS.items():
			try:
				comparisons = parse_comparison(
					check_read_file_text(
						_join(self.results_dir, f"{script}.txt"),
						executor=executor,
					),
					script,
				)
				modes = (
					("non_storage", "enola")
					if "no_storage" in script
					else ("with_storage", "non_storage")
				)
				for workload in workloads:
					workload_ref = workload_refs[workload]
					if not isinstance(workload_ref, dict):
						raise ValueError(f"{workload}: invalid reference entry")
					n = WORKLOADS[workload][0]
					if workload_ref.get("qubits") != n:
						raise ValueError(f"{workload}: invalid reference qubit count")
					if n not in comparisons:
						raise ValueError(f"{script}: missing {n}-qubit result")
					for mode, metrics in zip(modes, comparisons[n], strict=True):
						_validate_metrics(
							metrics,
							workload_ref.get(mode),
							f"{workload}.{mode}",
							relative=relative,
							absolute=absolute,
						)
				observed[script] = comparisons
				log = check_read_file_text(
					_join(self.logs_dir, f"{script}.log"),
					executor=executor,
				)
				# Both upstream compilers print coherence_time after their final metrics.
				if len(re.findall(r"^coherence_time\s+", log, re.MULTILINE)) < 2 * len(comparisons):
					raise ValueError(f"{script}: incomplete compiler log")
				if _FAILURE_RE.search(log):
					raise ValueError(f"{script}: log reports an exception")
			except (OSError, RuntimeError, ValueError) as exc:
				errors.append(str(exc))

		if errors:
			return CheckResult.failure("invalid PowerMove results: " + "; ".join(errors))

		for family, (sizes, _) in FAMILIES.items():
			storage = observed[f"{family}_storage_compare"]
			baseline = observed[f"{family}_no_storage_compare"]
			gains = {}
			for n in sizes:
				stored, plain = storage[n]
				routed, enola = baseline[n]
				label = f"{family}_{n}"
				if not math.isclose(stored["excitation_fidelity"], 1.0):
					errors.append(f"{label}: storage did not eliminate excitation error")
				if not math.isclose(
					stored["two_qubit_fidelity"],
					plain["two_qubit_fidelity"],
					rel_tol=1e-8,
					abs_tol=1e-12,
				):
					errors.append(f"{label}: two-qubit fidelity changed across storage modes")
				if routed["fidelity"] <= enola["fidelity"]:
					errors.append(f"{label}: continuous router did not improve on Enola fidelity")
				if stored["fidelity"] <= enola["fidelity"]:
					errors.append(f"{label}: storage PowerMove did not improve on Enola fidelity")
				if (
					routed["transfer_duration"] + routed["move_duration"]
					>= enola["transfer_duration"] + enola["move_duration"]
				):
					errors.append(f"{label}: continuous router did not reduce execution time")
				if family == "vqe" and stored["fidelity"] >= plain["fidelity"]:
					errors.append(f"{label}: expected the storage-overhead tradeoff")
				gains[n] = routed["fidelity"] / enola["fidelity"]
			if gains[sizes[-1]] <= gains[sizes[0]]:
				errors.append(f"{family}: fidelity advantage did not grow with circuit size")
			if family in ("qft", "bv", "qsim_rand_0.3"):
				stored, plain = storage[sizes[-1]]
				if stored["fidelity"] / plain["fidelity"] < _LARGE_STORAGE_MIN_GAIN:
					errors.append(f"{family}: large-circuit storage gain is less than two orders")

		if errors:
			return CheckResult.failure("PowerMove claim mismatch: " + "; ".join(errors))
		return CheckResult.success(
			"all 23 Table 3 configurations reproduce Enola comparisons, scaling, "
			"excitation-error elimination, large-circuit storage gains, and VQE tradeoffs"
		)
