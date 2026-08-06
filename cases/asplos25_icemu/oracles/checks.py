from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
	glob,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import nacho_power_result_name, reference_result_name


def _parse_metrics(text: str, label: str) -> dict[str, float]:
	metrics: dict[str, float] = {}
	for line in text.splitlines():
		if not line.strip():
			continue
		if ":" not in line:
			raise ValueError(f"{label}: malformed line {line!r}")
		key, raw_value = line.split(":", 1)
		key = key.strip()
		if not key or key in metrics:
			raise ValueError(f"{label}: empty or duplicate metric {key!r}")
		try:
			value = float(raw_value.strip())
		except ValueError as exc:
			raise ValueError(f"{label}: non-numeric {key} value {raw_value!r}") from exc
		if not math.isfinite(value) or value < 0:
			raise ValueError(f"{label}: invalid {key} value {value}")
		metrics[key] = value
	if metrics.get("cycles", 0) <= 0:
		raise ValueError(f"{label}: missing positive cycles metric")
	return metrics


def _within_rel_tol(observed: float, expected: float, rel_tol: float) -> bool:
	return abs(observed - expected) <= max(abs(expected) * rel_tol, 1e-9)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpectedFilesCheck(BaseCheck):
	root: OraclePath
	pattern: str
	expected_relative_paths: tuple[str, ...]
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		try:
			observed_paths = glob(self.root, self.pattern, executor=self.executor)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"could not inspect generated files: {exc}")

		expected = set(self.expected_relative_paths)
		depths = {len(Path(relative).parts) for relative in expected}
		if len(depths) != 1:
			return CheckResult.failure("expected paths must have a uniform depth")
		depth = depths.pop()
		observed = {"/".join(path.parts[-depth:]) for path in observed_paths}
		missing = sorted(expected - observed)
		if missing:
			return CheckResult.failure(
				f"missing {len(missing)}/{len(expected)} expected files: {missing[:8]}"
			)
		return CheckResult.success(message=f"all {len(expected)} expected files are present")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultSetCheck(BaseCheck):
	logs_dir: OraclePath
	expected_names: tuple[str, ...]
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		try:
			observed = {
				path.name for path in glob(self.logs_dir, "*-final", executor=self.executor)
			}
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"could not list final benchmark results: {exc}")

		expected = set(self.expected_names)
		missing = sorted(expected - observed)
		if missing:
			return CheckResult.failure(
				f"missing {len(missing)}/{len(expected)} final results: {missing[:8]}"
			)
		return CheckResult.success(
			message=f"complete {len(expected)}-result benchmark matrix is present"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationCheck(BaseCheck):
	logs_dir: OraclePath
	reference_path: Path
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def _read_metrics(self, filename: str) -> dict[str, float]:
		text = check_read_file_text(
			Path(str(self.logs_dir)) / filename,
			executor=self.executor,
		)
		return _parse_metrics(text, filename)

	def check(self) -> CheckResult:
		try:
			reference = json.loads(self.reference_path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			return CheckResult.failure(f"could not read evaluation reference: {exc}")

		rel_tol = float(reference["relative_tolerance"])
		observed_runs: dict[str, dict[str, dict[str, float]]] = {}
		errors: list[str] = []

		for benchmark, systems in reference["runs"].items():
			observed_runs[benchmark] = {}
			for system, expected_metrics in systems.items():
				filename = reference_result_name(benchmark, system)
				try:
					metrics = self._read_metrics(filename)
				except (OSError, ValueError) as exc:
					errors.append(str(exc))
					continue
				observed_runs[benchmark][system] = metrics
				for metric, expected in expected_metrics.items():
					observed = metrics.get(metric)
					if observed is None:
						errors.append(f"{filename}: missing {metric}")
					elif not _within_rel_tol(observed, float(expected), rel_tol):
						errors.append(
							f"{filename}: {metric} {observed:g} != {expected} (rel_tol {rel_tol})"
						)
				if len(errors) >= 8:
					return CheckResult.failure("evaluation mismatch: " + "; ".join(errors))

		try:
			mean_normalized_cycles = {
				system: sum(
					observed_runs[benchmark][system]["cycles"]
					/ observed_runs[benchmark]["plain_c"]["cycles"]
					for benchmark in observed_runs
				)
				/ len(observed_runs)
				for system in ("clank", "prowl", "replay_cache", "nacho", "nacho_oracle")
			}
		except KeyError as exc:
			return CheckResult.failure(f"could not evaluate runtime trend: missing {exc}")

		for baseline in ("clank", "prowl", "replay_cache"):
			if mean_normalized_cycles["nacho"] >= mean_normalized_cycles[baseline]:
				errors.append(
					f"NACHO mean normalized cycles {mean_normalized_cycles['nacho']:.3f} "
					f"not below {baseline} {mean_normalized_cycles[baseline]:.3f}"
				)

		power_abs_tol = float(reference["power_reexecution_abs_tolerance"])
		for benchmark, durations in reference["power_reexecution_percent"].items():
			base = observed_runs[benchmark]["nacho"]["cycles"]
			for raw_duration, expected in durations.items():
				duration = int(raw_duration)
				filename = nacho_power_result_name(benchmark, duration)
				try:
					observed = (self._read_metrics(filename)["cycles"] / base - 1) * 100
				except (OSError, ValueError, KeyError) as exc:
					errors.append(f"{filename}: {exc}")
					continue
				if abs(observed - float(expected)) > power_abs_tol:
					errors.append(
						f"{filename}: re-execution {observed:.3f}% != {expected}% "
						f"(abs_tol {power_abs_tol})"
					)
				if len(errors) >= 8:
					break

		if errors:
			return CheckResult.failure("evaluation mismatch: " + "; ".join(errors[:8]))
		return CheckResult.success(
			message=(
				"main -Os metrics and power-failure results match reference; "
				"NACHO preserves the lower mean-runtime trend"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutedNotebooksCheck(BaseCheck):
	paths: tuple[tuple[str, OraclePath], ...]
	executor: RuntimeCheckExecutor | None = field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		errors: list[str] = []
		for label, path in self.paths:
			try:
				notebook = json.loads(check_read_file_text(path, executor=self.executor))
			except (OSError, json.JSONDecodeError) as exc:
				errors.append(f"{label}: {exc}")
				continue
			code_cells = [
				cell
				for cell in notebook.get("cells", [])
				if cell.get("cell_type") == "code" and "".join(cell.get("source", [])).strip()
			]
			if not code_cells or any(cell.get("execution_count") is None for cell in code_cells):
				errors.append(f"{label}: contains unexecuted code cells")
			if any(
				output.get("output_type") == "error"
				for cell in code_cells
				for output in cell.get("outputs", [])
			):
				errors.append(f"{label}: contains an error output")

		if errors:
			return CheckResult.failure("notebook execution incomplete: " + "; ".join(errors))
		return CheckResult.success(
			message=f"all {len(self.paths)} notebooks executed without errors"
		)
