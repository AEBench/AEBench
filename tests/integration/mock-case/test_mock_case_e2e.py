"""End-to-end mock artifact case test."""

from __future__ import annotations

import json
from pathlib import Path

from evaluator.loader import load_case_spec
from evaluator.oracles.execution import run_oracle
from models import OracleStatus

_FIXTURE_DIR = Path(__file__).parent / "fixture"
_CASE_ID = "mock_apt_case"


def test_case_run_end_to_end(tmp_path: Path) -> None:
	bundle = _FIXTURE_DIR / "bundles" / _CASE_ID
	spec = load_case_spec(bundle)
	output_dir = tmp_path / "case_output"
	workspace_dir = bundle / "artifact"

	result = run_oracle(
		bundle,
		runtime_result=None,
		output_dir=output_dir,
		case=spec,
		workspace_dir=workspace_dir,
	)

	assert result.status == OracleStatus.SUCCESS
	assert result.score == 4
	assert len(result.phases) == 4
	assert all(phase.status == OracleStatus.SUCCESS for phase in result.phases)

	oracle_result_path = output_dir / "oracle_result.json"
	assert oracle_result_path.is_file()
	payload = json.loads(oracle_result_path.read_text(encoding="utf-8"))
	assert payload["score"] == 4
	assert payload["status"] == "success"
	assert all(phase["status"] == "success" for phase in payload["phases"])
