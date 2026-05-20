from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.discovery import experiment_runs
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.experiment_runs_checks import (
	ElementwiseSimilarityThresholdCheck,
	ListSimilarityCheck,
	SimilarityMetric,
)
from evaluator.oracles.utils import Checkable
from models import OracleInput

from . import custom


def _controller_id_as_float(controller: str) -> float:
	digest = hashlib.sha256(controller.encode("utf-8")).digest()
	raw64 = int.from_bytes(digest[:8], "big", signed=False)
	return float(raw64 % (2**53))


def _load_reference_ratios(path: Path) -> dict[str, tuple[float, float]]:
	try:
		raw = json.loads(path.read_text(encoding="utf-8"))
	except OSError as exc:
		raise ValueError(f"failed to read reference JSON {path}: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid reference JSON {path}: {exc}") from exc

	if not isinstance(raw, list):
		raise ValueError(f"reference JSON must contain a list, got {type(raw).__name__}")

	rows: dict[str, tuple[float, float]] = {}
	for index, entry in enumerate(raw):
		if not isinstance(entry, dict):
			raise ValueError(f"reference row #{index} is not an object")
		try:
			controller = str(entry["controller"])
			ratios = custom._compute_ratios(entry)
		except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
			raise ValueError(f"malformed reference row #{index}: {exc}") from exc
		if controller in rows:
			raise ValueError(f"duplicate controller in reference JSON: {controller!r}")
		rows[controller] = ratios
	return rows


@experiment_runs
def oracle_experiment_runs(context: OracleInput) -> Sequence[Checkable]:
	anvil_root = context.workspace_dir
	results_path = anvil_root / "results" / "table3.md"
	reference_path = context.case_dir / "refs" / "anvil-table-3.ref.json"
	cache: dict[str, tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]] = {}

	def _load_data() -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
		data = cache.get("table3")
		if data is not None:
			return data
		observed = custom._parse_table3(results_path)
		reference = _load_reference_ratios(reference_path)
		data = (observed, reference)
		cache["table3"] = data
		return data

	def _check_controllers() -> utils.CheckResult:
		try:
			observed, reference = _load_data()
		except ValueError as exc:
			return utils.CheckResult.failure(str(exc))

		observed_ids = [_controller_id_as_float(controller) for controller in observed]
		reference_ids = [_controller_id_as_float(controller) for controller in reference]
		return ListSimilarityCheck(
			name="table3_controllers",
			observed=observed_ids,
			reference=reference_ids,
			metric=SimilarityMetric.JACCARD_SET,
			min_similarity=1.0,
		).check()

	def _check_ratio(index: int, label: str) -> utils.CheckResult:
		try:
			observed, reference = _load_data()
		except ValueError as exc:
			return utils.CheckResult.failure(str(exc))

		if set(observed) != set(reference):
			return utils.CheckResult.failure(
				"controller sets differ between observed and reference tables"
			)

		controllers = sorted(reference)
		observed_values = [observed[controller][index] for controller in controllers]
		reference_values = [reference[controller][index] for controller in controllers]
		return ElementwiseSimilarityThresholdCheck(
			name=label,
			observed=observed_values,
			reference=reference_values,
			threshold=0.75,
		).check()

	return (
		FilesystemPathCheck(
			name="results_table3_exists",
			path=results_path,
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="reference_table3_exists",
			path=reference_path,
			path_type=PathType.FILE,
		),
		utils.Check(
			name="table3_controllers",
			fn=_check_controllers,
		),
		utils.Check(
			name="table3_mean_ratio",
			fn=lambda: _check_ratio(0, "table3_mean_ratio"),
		),
		utils.Check(
			name="table3_max_ratio",
			fn=lambda: _check_ratio(1, "table3_max_ratio"),
		),
	)
