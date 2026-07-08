"""Case manifest serialization tests."""

from __future__ import annotations

from evaluator.authoring.case_spec import case_spec_to_toml
from models import CaseConfig


def _case(required_evidence: list[str] | None = None) -> CaseConfig:
	payload = {
		"id": "test_case",
		"case_brief": {
			"core_claim": "Verify the artifact builds correctly.",
			"acceptable_evidence": "The binary exists and runs.",
			"allowed_tolerance": "None.",
		},
		"run": {
			"id": "test_case",
			"runtime": {"mode": "local"},
		},
		"paper": {
			"url": "https://example.com/paper.pdf",
			"sha256": "0" * 64,
		},
	}
	if required_evidence is not None:
		payload["run"]["required_evidence"] = required_evidence
	return CaseConfig.model_validate(payload)


def test_case_spec_serializes_required_evidence_when_present() -> None:
	toml = case_spec_to_toml(_case(["Save stdout to table.txt", "Keep logs under logs/"]))

	assert 'required_evidence = ["Save stdout to table.txt", "Keep logs under logs/"]' in toml
	assert "[run]\n" in toml


def test_case_spec_omits_empty_required_evidence() -> None:
	toml = case_spec_to_toml(_case())

	assert "required_evidence" not in toml
