from __future__ import annotations

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
	glob,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_MODES = ("non_storage", "with_storage")
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
_FAILURE_RE = re.compile(r"\b(?:traceback|timed?\s*out|error|failed)\b", re.IGNORECASE)


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

	product = math.prod(metrics[name] for name in _FIDELITY_COMPONENTS)
	if not math.isclose(metrics["fidelity"], product, rel_tol=1e-8, abs_tol=1e-12):
		raise ValueError(f"{label}: total fidelity does not match its five components")
	compile_seconds = _finite_number(
		value.get("compile_seconds"),
		f"{label}.compile_seconds",
		positive=True,
	)
	metrics["compile_seconds"] = compile_seconds
	return metrics


def _within_tolerance(observed: float, expected: float, relative: float, absolute: float) -> bool:
	return abs(observed - expected) <= max(abs(expected) * relative, absolute)


def _validate_metrics(
	value: object,
	reference: object,
	label: str,
	*,
	relative: float,
	absolute: float,
) -> dict[str, float]:
	metrics = _parse_metrics(value, label)
	if not isinstance(reference, dict):
		raise ValueError(f"{label}: invalid reference metrics")
	for metric in _METRICS:
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
	expected_workloads: tuple[str, ...]

	def _validate_file_sets(self, executor: RuntimeCheckExecutor) -> None:
		expected_results = {f"{name}.json" for name in self.expected_workloads}
		expected_logs = {f"{name}.log" for name in self.expected_workloads}
		observed_results = {
			path.name for path in glob(self.results_dir, "*.json", executor=executor)
		}
		observed_logs = {path.name for path in glob(self.logs_dir, "*.log", executor=executor)}
		if observed_results != expected_results:
			raise ValueError(
				f"result set mismatch: missing {sorted(expected_results - observed_results)}, "
				f"unexpected {sorted(observed_results - expected_results)}"
			)
		if observed_logs != expected_logs:
			raise ValueError(
				f"log set mismatch: missing {sorted(expected_logs - observed_logs)}, "
				f"unexpected {sorted(observed_logs - expected_logs)}"
			)

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			reference = _load_json(self.reference_path.read_text(encoding="utf-8"), "reference")
			workload_refs = reference["workloads"]
			if not isinstance(workload_refs, dict) or set(workload_refs) != set(
				self.expected_workloads
			):
				raise ValueError("reference has an unexpected workload set")
			relative = _finite_number(reference["relative_tolerance"], "relative tolerance")
			absolute = _finite_number(reference["absolute_tolerance"], "absolute tolerance")
			self._validate_file_sets(executor)
		except (KeyError, OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"invalid PowerMove evaluation setup: {exc}")

		errors: list[str] = []
		observed: dict[str, dict[str, dict[str, float]]] = {}
		for workload in self.expected_workloads:
			try:
				result = _load_json(
					check_read_file_text(
						_join(self.results_dir, f"{workload}.json"),
						executor=executor,
					),
					workload,
				)
				workload_ref = workload_refs[workload]
				if not isinstance(workload_ref, dict):
					raise ValueError(f"{workload}: invalid reference entry")
				if result.get("qubits") != workload_ref.get("qubits"):
					raise ValueError(f"{workload}: unexpected qubit count")
				if result.get("cz_blocks") != workload_ref.get("cz_blocks"):
					raise ValueError(f"{workload}: unexpected CZ-block count")
				observed[workload] = {}
				for mode in _MODES:
					observed[workload][mode] = _validate_metrics(
						result.get(mode),
						workload_ref.get(mode),
						f"{workload}.{mode}",
						relative=relative,
						absolute=absolute,
					)
				if workload == "qaoa_regular3_30":
					observed[workload]["enola"] = _validate_metrics(
						result.get("enola"),
						workload_ref.get("enola"),
						f"{workload}.enola",
						relative=relative,
						absolute=absolute,
					)

				log = check_read_file_text(
					_join(self.logs_dir, f"{workload}.log"),
					executor=executor,
				)
				begin = f"===== BEGIN {workload} ====="
				end = f"===== END {workload} status=0 ====="
				if (
					log.count(begin) != 1
					or log.count(end) != 1
					or log.index(begin) >= log.index(end)
				):
					raise ValueError(f"{workload}: incomplete execution log markers")
				outside_markers = log.replace(begin, "").replace(end, "")
				if _FAILURE_RE.search(outside_markers):
					raise ValueError(f"{workload}: log reports an error, failure, or timeout")
			except (OSError, RuntimeError, ValueError) as exc:
				errors.append(str(exc))

		if errors:
			return CheckResult.failure("invalid PowerMove results: " + "; ".join(errors))

		for workload, modes in observed.items():
			if not math.isclose(modes["with_storage"]["excitation_fidelity"], 1.0):
				errors.append(f"{workload}: storage mode did not eliminate excitation error")
			if not math.isclose(
				modes["non_storage"]["two_qubit_fidelity"],
				modes["with_storage"]["two_qubit_fidelity"],
				rel_tol=1e-8,
				abs_tol=1e-12,
			):
				errors.append(f"{workload}: two-qubit fidelity changed across storage modes")

		for workload in ("qaoa_regular3_30", "qsim_rand_0.3_10", "qft_18", "bv_14"):
			if (
				observed[workload]["with_storage"]["fidelity"]
				<= observed[workload]["non_storage"]["fidelity"]
			):
				errors.append(f"{workload}: storage did not improve total fidelity")
		if (
			observed["vqe_30"]["with_storage"]["fidelity"]
			>= observed["vqe_30"]["non_storage"]["fidelity"]
		):
			errors.append("vqe_30: expected the released storage-overhead tradeoff")
		qaoa = observed["qaoa_regular3_30"]
		if qaoa["non_storage"]["fidelity"] <= qaoa["enola"]["fidelity"]:
			errors.append("qaoa_regular3_30: continuous router did not improve on Enola fidelity")
		powermove_execution = (
			qaoa["non_storage"]["transfer_duration"] + qaoa["non_storage"]["move_duration"]
		)
		enola_execution = qaoa["enola"]["transfer_duration"] + qaoa["enola"]["move_duration"]
		if powermove_execution >= enola_execution:
			errors.append("qaoa_regular3_30: continuous router did not reduce execution time")

		if errors:
			return CheckResult.failure("PowerMove claim mismatch: " + "; ".join(errors))
		return CheckResult.success(
			"all five workloads match the reference and reproduce the router and storage-zone effects"
		)
