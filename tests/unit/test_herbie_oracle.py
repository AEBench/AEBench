"""The Herbie oracle must score the pinned Hamming run, not a stale or full-suite report."""

from __future__ import annotations

import json
from pathlib import Path

from cases.asplos25_herbie.oracles.experiment_runs import (
	HerbieAccuracyImprovementCheck,
	_discover_herbie_report,
)
from evaluator.oracles.oracle_checks_runtime import LocalRuntimeCheckExecutor


def _write_report(root: Path, name: str, *, commit: str, date: int, tests: list[dict]) -> Path:
	report = root / name
	report.mkdir()
	(report / "report.html").write_text("<html></html>")
	results = report / "results.json"
	results.write_text(json.dumps({"commit": commit, "date": date, "tests": tests}))
	return results


def test_herbie_oracle_selects_current_hamming_report(tmp_path: Path) -> None:
	reference = tmp_path / "reference.json"
	baseline = [{"name": name, "start": 10, "end": 10} for name in ("first", "second", "third")]
	reference.write_text(json.dumps({"commit": "pinned", "tests": baseline}))
	executor = LocalRuntimeCheckExecutor(default_cwd=tmp_path)
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	_write_report(workspace, "www", commit="old", date=100, tests=baseline)
	_write_report(
		workspace,
		"full-suite",
		commit="pinned",
		date=300,
		tests=baseline + [{"name": "unrelated", "start": 1, "end": 1}],
	)
	chosen = _write_report(
		workspace,
		"hamming",
		commit="pinned",
		date=200,
		tests=[
			{"name": "first", "start": 10, "end": 2},
			{"name": "second", "start": 10, "end": 3},
			{"name": "third", "start": 10, "end": 10},
		],
	)

	report = _discover_herbie_report(workspace, reference, executor=executor)
	assert report is not None
	assert str(report[1]) == str(chosen)
	assert (
		HerbieAccuracyImprovementCheck(name="accuracy_improvement", results_path=report[1])
		.check(executor)
		.ok
	)
	assert (
		not HerbieAccuracyImprovementCheck(name="accuracy_improvement", results_path=reference)
		.check(executor)
		.ok
	)
	regressed = _write_report(
		workspace,
		"regressed",
		commit="pinned",
		date=400,
		tests=[
			{"name": "first", "start": 10, "end": 2},
			{"name": "second", "start": 10, "end": 3},
			{"name": "third", "start": 10, "end": 11},
		],
	)
	assert (
		not HerbieAccuracyImprovementCheck(name="accuracy_improvement", results_path=regressed)
		.check(executor)
		.ok
	)


def test_herbie_oracle_rejects_only_stale_report(tmp_path: Path) -> None:
	reference = tmp_path / "reference.json"
	reference.write_text(json.dumps({"commit": "pinned", "tests": [{"name": "test"}]}))
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	_write_report(workspace, "www", commit="old", date=100, tests=[{"name": "test"}])
	assert (
		_discover_herbie_report(
			workspace, reference, executor=LocalRuntimeCheckExecutor(default_cwd=tmp_path)
		)
		is None
	)
