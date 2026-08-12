from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_read_file_text,
	glob,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_PRIMARY_ROW = re.compile(r"^prob-([0-9]+(?:\.[0-9]+)?):,?\s*(.+)$")
_FAILURE_MARKERS = (
	"Traceback (most recent call last)",
	"FileNotFoundError",
	"ModuleNotFoundError",
	"ImportError",
	"No such file",
	"TimeoutError",
	"timed out",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PythonSourcesCheck(BaseCheck):
	root: RuntimePath
	paths: Sequence[str]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		for relative_path in self.paths:
			path = RuntimePath.from_parts(self.root.value, relative_path)
			try:
				source = check_read_file_text(path, executor=executor)
				compile(source, relative_path, "exec")
			except (OSError, RuntimeError, SyntaxError, UnicodeError, ValueError) as exc:
				return CheckResult.failure(f"{relative_path}: could not compile: {exc}")
		return CheckResult.success(message=f"all {len(self.paths)} entrypoints compile")


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetManifestCheck(BaseCheck):
	root: RuntimePath
	files: Sequence[str]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		errors: list[str] = []
		for relative_path in self.files:
			path = RuntimePath.from_parts(self.root.value, relative_path)
			try:
				text = check_read_file_text(path, executor=executor)
			except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
				errors.append(f"{relative_path}: unreadable: {exc}")
				continue
			reader = csv.reader(io.StringIO(text))
			try:
				header = next(reader)
				first_row = next(reader)
			except (csv.Error, StopIteration) as exc:
				errors.append(f"{relative_path}: missing header or data row: {exc}")
				continue
			if not header or len(first_row) != len(header):
				errors.append(f"{relative_path}: first data row does not match the header")
		if errors:
			return CheckResult.failure("; ".join(errors[:8]))
		return CheckResult.success(
			message=f"all {len(self.files)} released datasets contain tabular data"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentLogCheck(BaseCheck):
	path: OraclePath
	figure: str
	required: Sequence[str]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			text = check_read_file_text(self.path, executor=executor)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"{self.figure}: could not read log: {exc}")

		begin = f"===== BEGIN {self.figure} ====="
		end = f"===== END {self.figure} status=0 ====="
		lines = [line.strip() for line in text.splitlines() if line.strip()]
		if lines.count(begin) != 1 or lines.count(end) != 1:
			return CheckResult.failure(
				f"{self.figure}: expected exactly one {begin!r} and {end!r} marker"
			)
		if lines[0] != begin or lines[-1] != end:
			return CheckResult.failure(
				f"{self.figure}: begin/end markers must delimit the complete log"
			)
		body = set(lines[1:-1])
		missing = [signature for signature in self.required if signature not in body]
		if missing:
			return CheckResult.failure(f"{self.figure}: missing run signature(s): {missing}")
		failures = [marker for marker in _FAILURE_MARKERS if marker in text]
		if failures:
			return CheckResult.failure(f"{self.figure}: execution failure marker(s): {failures}")
		return CheckResult.success(
			message=f"{self.figure}: complete log covers {len(self.required)} predictor runs"
		)


def _metric_row(line: str, *, label: str) -> tuple[float, tuple[float, float, float, float]]:
	match = _PRIMARY_ROW.fullmatch(line.strip())
	if match is None:
		raise ValueError(f"{label}: malformed thresholded row {line!r}")
	threshold = float(match.group(1))
	parts = [part.strip() for part in match.group(2).split(",")]
	if len(parts) != 4:
		raise ValueError(f"{label}: expected four metrics, got {len(parts)}")
	try:
		values = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
	except ValueError as exc:
		raise ValueError(f"{label}: non-numeric metric") from exc
	if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
		raise ValueError(f"{label}: metrics must be finite values in [0, 1]")
	return threshold, values


def _parse_metric_file(
	text: str, *, label: str, expected_threshold: float
) -> tuple[float, float, float, float]:
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	if len(lines) != 2:
		raise ValueError(f"{label}: expected exactly two result rows, got {len(lines)}")
	threshold, primary = _metric_row(lines[0], label=label)
	if not math.isclose(threshold, expected_threshold, abs_tol=1e-12):
		raise ValueError(f"{label}: threshold {threshold} != expected {expected_threshold}")
	if ":" not in lines[1]:
		raise ValueError(f"{label}: malformed default row {lines[1]!r}")
	default_label, default_data = lines[1].split(":", 1)
	if default_label not in {"rf", "Default RF"}:
		raise ValueError(f"{label}: unexpected default row label {default_label!r}")
	default_parts = [part.strip() for part in default_data.lstrip(", ").split(",")]
	if len(default_parts) != 4:
		raise ValueError(f"{label}: default row must contain four metrics")
	try:
		default_values = [float(part) for part in default_parts]
	except ValueError as exc:
		raise ValueError(f"{label}: non-numeric default metric") from exc
	if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in default_values):
		raise ValueError(f"{label}: default metrics must be finite values in [0, 1]")
	return primary


def _mean(values: Sequence[float]) -> float:
	return sum(values) / len(values)


def _reference_number(value: object, label: str) -> float:
	if not isinstance(value, int | float) or isinstance(value, bool):
		raise ValueError(f"{label}: expected a number")
	number = float(value)
	if not math.isfinite(number):
		raise ValueError(f"{label}: expected a finite number")
	return number


def _reference_range(value: object, label: str) -> tuple[float, float]:
	if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) != 2:
		raise ValueError(f"{label}: expected a two-number range")
	return (
		_reference_number(value[0], f"{label} lower bound"),
		_reference_number(value[1], f"{label} upper bound"),
	)


@dataclass(frozen=True, slots=True, kw_only=True)
class CalchasMetricsCheck(BaseCheck):
	root: RuntimePath
	reference: Mapping[str, object]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		figures = self.reference.get("figures")
		if not isinstance(figures, Mapping):
			return CheckResult.failure("metrics reference: figures must be an object")
		metrics: dict[tuple[str, str, str], tuple[float, float, float, float]] = {}
		errors: list[str] = []

		for figure, raw_specs in figures.items():
			if not isinstance(figure, str) or not isinstance(raw_specs, Mapping):
				errors.append("metrics reference: each figure must map a name to file specs")
				continue
			directory = RuntimePath.from_parts(self.root.value, figure)
			expected_names = set(raw_specs)
			try:
				actual_names = {path.name for path in glob(directory, "*.csv", executor=executor)}
			except (OSError, RuntimeError, ValueError) as exc:
				return CheckResult.failure(f"{figure}: could not list metric files: {exc}")
			if actual_names != expected_names:
				errors.append(
					f"{figure}: metric file set mismatch "
					f"(missing {sorted(expected_names - actual_names)}, "
					f"extra {sorted(actual_names - expected_names)})"
				)
				continue

			for filename, raw_spec in raw_specs.items():
				if not isinstance(filename, str) or not isinstance(raw_spec, Mapping):
					errors.append(f"metrics reference: invalid file spec in {figure}")
					continue
				model = raw_spec.get("model")
				predictor = raw_spec.get("predictor")
				if not isinstance(model, str) or not isinstance(predictor, str):
					errors.append(f"metrics reference: invalid model or predictor for {filename}")
					continue
				try:
					text = check_read_file_text(
						RuntimePath.from_parts(directory.value, filename),
						executor=executor,
					)
					metrics[(figure, model, predictor)] = _parse_metric_file(
						text,
						label=f"{figure}/{filename}",
						expected_threshold=float(raw_spec["threshold"]),
					)
				except (KeyError, OSError, TypeError, ValueError) as exc:
					errors.append(str(exc))

		if errors:
			return CheckResult.failure("; ".join(errors[:8]))

		claims = self.reference.get("claims")
		if not isinstance(claims, Mapping):
			return CheckResult.failure("metrics reference: claims must be an object")
		try:
			return self._check_claims(metrics, claims)
		except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
			return CheckResult.failure(f"metrics reference: malformed claims: {exc}")

	def _check_claims(
		self,
		metrics: Mapping[tuple[str, str, str], tuple[float, float, float, float]],
		claims: Mapping[str, object],
	) -> CheckResult:
		errors: list[str] = []
		figure12_ranges = claims.get("figure12_mean_ranges")
		figure12_f1_min = claims.get("figure12_f1_min")
		if not isinstance(figure12_ranges, Mapping) or not isinstance(figure12_f1_min, Mapping):
			raise ValueError("Figure 12 claims must be objects")

		fig12 = [
			metrics[("figure12", "RF", predictor)] for predictor in ("row", "col", "bank", "server")
		]
		for metric_name, index in (("precision", 1), ("recall", 2), ("f1", 3)):
			bounds = _reference_range(
				figure12_ranges.get(metric_name),
				f"figure12_mean_ranges.{metric_name}",
			)
			mean_value = _mean([row[index] for row in fig12])
			if not bounds[0] <= mean_value <= bounds[1]:
				errors.append(
					f"Figure 12 mean {metric_name} {mean_value:.4f} outside "
					f"[{bounds[0]}, {bounds[1]}]"
				)

		for predictor, minimum in figure12_f1_min.items():
			if not isinstance(predictor, str):
				raise ValueError("figure12_f1_min keys must be strings")
			minimum_value = _reference_number(minimum, f"figure12_f1_min.{predictor}")
			value = metrics[("figure12", "RF", predictor)][3]
			if value < minimum_value:
				errors.append(f"Figure 12 {predictor} F1 {value:.4f} < {minimum_value}")

		row_recall = metrics[("figure12", "RF", "row")][2]
		row_recall_min = _reference_number(
			claims.get("figure12_row_recall_min"), "figure12_row_recall_min"
		)
		if row_recall < row_recall_min:
			errors.append(f"Figure 12 row recall {row_recall:.4f} < {row_recall_min}")

		svm = [
			metrics[("figure13", "SVM", predictor)]
			for predictor in ("row", "col", "bank", "server")
		]
		svm_precision = _mean([row[1] for row in svm])
		svm_recall = _mean([row[2] for row in svm])
		svm_precision_max = _reference_number(
			claims.get("svm_mean_precision_max"), "svm_mean_precision_max"
		)
		svm_recall_min = _reference_number(claims.get("svm_mean_recall_min"), "svm_mean_recall_min")
		if svm_precision > svm_precision_max:
			errors.append(f"Figure 13 SVM mean precision {svm_precision:.4f} > {svm_precision_max}")
		if svm_recall < svm_recall_min:
			errors.append(f"Figure 13 SVM mean recall {svm_recall:.4f} < {svm_recall_min}")

		micro = ("row", "col", "bank")
		tree_f1 = _mean(
			[metrics[("figure12", "RF", predictor)][3] for predictor in micro]
			+ [metrics[("figure13", "GBDT", predictor)][3] for predictor in micro]
		)
		svm_f1 = _mean([metrics[("figure13", "SVM", predictor)][3] for predictor in micro])
		advantage = tree_f1 - svm_f1
		advantage_min = _reference_number(
			claims.get("tree_micro_f1_advantage_min"), "tree_micro_f1_advantage_min"
		)
		if advantage < advantage_min:
			errors.append(f"tree-model micro-level F1 advantage {advantage:.4f} < {advantage_min}")

		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success(
			message=(
				"12 predictor/model outputs are complete; "
				f"Figure 12 mean F1={_mean([row[3] for row in fig12]):.3f}, "
				f"tree-vs-SVM micro F1 advantage={advantage:.3f}"
			)
		)
