"""Regression coverage for the PLDI Herbie case's evidence checks."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator.authoring.validate import validate_case_bundle
from evaluator.loader import load_case_spec
from evaluator.oracles.discovery import oracle_import_scope
from evaluator.oracles.oracle_checks_runtime import LocalRuntimeCheckExecutor

CASE = Path(__file__).resolve().parents[2] / "cases" / "pldi15_herbie"


@pytest.fixture
def experiment():
	with oracle_import_scope(CASE, "oracles"):
		yield importlib.import_module("oracles.experiment_runs")


def report_data():
	return {
		"iterations": 4,
		"seed": "286210900",
		"tests": [
			{"name": "a", "start": 8, "end": 1, "time": 1, "status": "imp-start"},
			{"name": "b", "start": 16, "end": 2, "time": 1, "status": "imp-start"},
		],
	}


@pytest.mark.parametrize(
	"failure",
	[
		"zero_iterations",
		"wrong_seed",
		"unchanged",
		"regressed",
		"duplicate",
		"missing",
		"nan",
		"infinity",
		"boolean",
		"null",
		"timeout",
		"non_object",
		"empty",
		"invalid_status",
		"oversized_number",
	],
)
def test_invalid_evidence_fails(experiment, tmp_path, failure):
	data = report_data()
	reference = tmp_path / "reference.json"
	reference.write_text(json.dumps(data))
	if failure == "zero_iterations":
		data["iterations"] = 0
	elif failure == "wrong_seed":
		data["seed"] = "1"
	elif failure == "unchanged":
		for test in data["tests"]:
			test["end"] = test["start"]
	elif failure == "regressed":
		data["tests"][0]["end"] = 9
	elif failure == "duplicate":
		data["tests"][1]["name"] = "a"
	elif failure == "missing":
		data["tests"][1]["name"] = "unexpected"
	elif failure in {"nan", "infinity", "boolean", "null"}:
		data["tests"][0]["end"] = {
			"nan": float("nan"),
			"infinity": float("inf"),
			"boolean": True,
			"null": None,
		}[failure]
	elif failure == "invalid_status":
		data["tests"][0]["status"] = []
	elif failure == "oversized_number":
		data["tests"][0]["start"] = 10**400
	elif failure == "timeout":
		data["tests"][0]["status"] = "timeout"
	elif failure == "non_object":
		data["tests"][0] = None
	elif failure == "empty":
		data["tests"] = []
	results = tmp_path / "results.json"
	results.write_text(json.dumps(data))
	executor = LocalRuntimeCheckExecutor(default_cwd=tmp_path)
	checks = [
		experiment.ResultsJSONStructureCheck(name="structure", results_path=results),
		experiment.HerbieNoRegressionCheck(name="accuracy", results_path=results),
		experiment.BenchmarkCountCheck(
			name="benchmarks", results_path=results, reference_path=reference
		),
	]
	assert not all([check.check(executor).ok for check in checks])


def test_json_without_html_still_checks_accuracy(experiment, tmp_path):
	static = tmp_path / "www"
	static.mkdir()
	(static / "index.html").write_text("bundled upstream report")
	(static / "results.json").write_text(json.dumps(report_data()))
	graphs = tmp_path / "graphs"
	graphs.mkdir()
	(graphs / "results.json").write_text(json.dumps(report_data()))
	executor = LocalRuntimeCheckExecutor(default_cwd=tmp_path)
	oracle = object.__new__(experiment.OracleExperimentRuns)
	oracle._workspace_dir = tmp_path
	oracle._refs_dir = CASE / "refs"
	oracle._runtime_registry = SimpleNamespace(executor_for=lambda _: executor)
	oracle._default_target_name = "local"
	checks = oracle.requirements()
	assert "no_accuracy_regression" in {check.name for check in checks}
	assert (
		next(check for check in checks if check.name == "results_json_structure").results_path
		== graphs / "results.json"
	)
	assert (
		not next(check for check in checks if check.name == "herbie_report_html").check(executor).ok
	)


def test_case_identity():
	assert validate_case_bundle(CASE).ok
	case = load_case_spec(CASE)
	assert case.id == case.run.id == "pldi15_herbie"
	assert case.paper.venue == "PLDI 2015"
	assert "--num-iters 4" in case.run.prompt.append
	registry = json.loads((CASE.parents[1] / "cases.json").read_text())
	assert registry["cases"][case.id]["path"] == "cases/pldi15_herbie"


def test_optimized_reference(experiment):
	results = CASE / "refs" / "results.ref.json"
	executor = LocalRuntimeCheckExecutor(default_cwd=CASE)
	assert len(json.loads(results.read_text())["tests"]) == 28
	assert (
		experiment.ResultsJSONStructureCheck(name="structure", results_path=results)
		.check(executor)
		.ok
	)
	assert (
		experiment.HerbieNoRegressionCheck(name="accuracy", results_path=results).check(executor).ok
	)
	assert (
		experiment.BenchmarkCountCheck(
			name="benchmarks", results_path=results, reference_path=results
		)
		.check(executor)
		.ok
	)

	for field in ("start", "end"):
		assert (
			experiment.HerbieValueSimilarityCheck(
				name=field,
				results_path=results,
				reference_path=results,
				field=field,
				threshold=0.9,
			)
			.check(executor)
			.ok
		)
