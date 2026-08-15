from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_read_file_text,
	glob,
	run_check_process_capture,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_DATASET_CHECK_SCRIPT = r"""
import hashlib
import json
import sys
from pathlib import Path

specs = json.loads(sys.argv[1])
errors = []
for rel_path, spec in specs.items():
	path = Path(rel_path)
	try:
		digest = hashlib.sha256(path.read_bytes()).hexdigest()
	except OSError as exc:
		errors.append(f"{rel_path}: unreadable: {exc}")
		continue
	if digest != spec["sha256"]:
		errors.append(f"{rel_path}: sha256 {digest} != {spec['sha256']}")
if errors:
	print("; ".join(errors[:8]), file=sys.stderr)
	raise SystemExit(1)
print(f"validated {len(specs)} released datasets")
"""

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
class DatasetManifestCheck(BaseCheck):
	root: OraclePath
	files: Mapping[str, Mapping[str, object]]
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		proc = run_check_process_capture(
			cmd=("python3", "-B", "-c", _DATASET_CHECK_SCRIPT, json.dumps(self.files)),
			cwd=self.root,
			env=None,
			timeout_seconds=180.0,
			executor=self.executor,
		)
		if proc.timed_out:
			return CheckResult.failure("dataset validation timed out", timed_out=True)
		if proc.returncode != 0:
			message = (proc.stderr or proc.stdout or "dataset validation failed").strip()
			return CheckResult.failure(message, stdout=proc.stdout, stderr=proc.stderr)
		return CheckResult.success(
			message=f"all {len(self.files)} released datasets match the pinned manifest"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentLogCheck(BaseCheck):
	path: OraclePath
	figure: str
	required: Sequence[str]
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		try:
			text = check_read_file_text(self.path, executor=self.executor)
		except OSError as exc:
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


@dataclass(frozen=True, slots=True, kw_only=True)
class CalchasMetricsCheck(BaseCheck):
	root: RuntimePath
	reference: Mapping[str, object]
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		figures = self.reference["figures"]
		assert isinstance(figures, Mapping)
		metrics: dict[tuple[str, str, str], tuple[float, float, float, float]] = {}
		errors: list[str] = []

		for figure, raw_specs in figures.items():
			assert isinstance(figure, str) and isinstance(raw_specs, Mapping)
			directory = RuntimePath.from_parts(self.root.value, figure)
			expected_names = set(raw_specs)
			try:
				actual_names = {
					path.name for path in glob(directory, "*.csv", executor=self.executor)
				}
			except OSError as exc:
				return CheckResult.failure(f"{figure}: could not list metric files: {exc}")
			if actual_names != expected_names:
				errors.append(
					f"{figure}: metric file set mismatch "
					f"(missing {sorted(expected_names - actual_names)}, "
					f"extra {sorted(actual_names - expected_names)})"
				)
				continue

			for filename, raw_spec in raw_specs.items():
				assert isinstance(filename, str) and isinstance(raw_spec, Mapping)
				model = str(raw_spec["model"])
				predictor = str(raw_spec["predictor"])
				try:
					text = check_read_file_text(
						RuntimePath.from_parts(directory.value, filename),
						executor=self.executor,
					)
					metrics[(figure, model, predictor)] = _parse_metric_file(
						text,
						label=f"{figure}/{filename}",
						expected_threshold=float(raw_spec["threshold"]),
					)
				except (OSError, ValueError) as exc:
					errors.append(str(exc))

		if errors:
			return CheckResult.failure("; ".join(errors[:8]))

		claims = self.reference["claims"]
		assert isinstance(claims, Mapping)
		fig12 = [
			metrics[("figure12", "RF", predictor)] for predictor in ("row", "col", "bank", "server")
		]
		for metric_name, index in (("precision", 1), ("recall", 2), ("f1", 3)):
			bounds = claims["figure12_mean_ranges"][metric_name]  # type: ignore[index]
			mean_value = _mean([row[index] for row in fig12])
			if not float(bounds[0]) <= mean_value <= float(bounds[1]):
				errors.append(
					f"Figure 12 mean {metric_name} {mean_value:.4f} outside "
					f"[{bounds[0]}, {bounds[1]}]"
				)

		for predictor, minimum in claims["figure12_f1_min"].items():  # type: ignore[union-attr]
			value = metrics[("figure12", "RF", predictor)][3]
			if value < float(minimum):
				errors.append(f"Figure 12 {predictor} F1 {value:.4f} < {minimum}")

		row_recall = metrics[("figure12", "RF", "row")][2]
		if row_recall < float(claims["figure12_row_recall_min"]):
			errors.append(
				f"Figure 12 row recall {row_recall:.4f} < {claims['figure12_row_recall_min']}"
			)

		svm = [
			metrics[("figure13", "SVM", predictor)]
			for predictor in ("row", "col", "bank", "server")
		]
		svm_precision = _mean([row[1] for row in svm])
		svm_recall = _mean([row[2] for row in svm])
		if svm_precision > float(claims["svm_mean_precision_max"]):
			errors.append(
				f"Figure 13 SVM mean precision {svm_precision:.4f} > "
				f"{claims['svm_mean_precision_max']}"
			)
		if svm_recall < float(claims["svm_mean_recall_min"]):
			errors.append(
				f"Figure 13 SVM mean recall {svm_recall:.4f} < {claims['svm_mean_recall_min']}"
			)

		micro = ("row", "col", "bank")
		tree_f1 = _mean(
			[metrics[("figure12", "RF", predictor)][3] for predictor in micro]
			+ [metrics[("figure13", "GBDT", predictor)][3] for predictor in micro]
		)
		svm_f1 = _mean([metrics[("figure13", "SVM", predictor)][3] for predictor in micro])
		advantage = tree_f1 - svm_f1
		if advantage < float(claims["tree_micro_f1_advantage_min"]):
			errors.append(
				f"tree-model micro-level F1 advantage {advantage:.4f} < "
				f"{claims['tree_micro_f1_advantage_min']}"
			)

		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success(
			message=(
				"12 predictor/model outputs are complete; "
				f"Figure 12 mean F1={_mean([row[3] for row in fig12]):.3f}, "
				f"tree-vs-SVM micro F1 advantage={advantage:.3f}"
			)
		)
