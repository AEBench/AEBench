"""Tests for execution evidence oracle checks."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evaluator.oracles import ExecutionEvidenceFileCheck


def test_execution_evidence_file_check_accepts_nonempty_file(tmp_path: Path) -> None:
	evidence = tmp_path / "table.txt"
	evidence.write_text("Table 5\nbugs reproduced: 56\n", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(name="table", path=evidence).check()

	assert result.ok is True
	assert "satisfied" in result.message


def test_execution_evidence_file_check_reports_missing_file(tmp_path: Path) -> None:
	result = ExecutionEvidenceFileCheck(
		name="missing",
		path=tmp_path / "missing.log",
	).check()

	assert result.ok is False
	assert "not found" in result.message


def test_execution_evidence_file_check_rejects_empty_file(tmp_path: Path) -> None:
	evidence = tmp_path / "empty.log"
	evidence.write_text("", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(name="empty", path=evidence).check()

	assert result.ok is False
	assert "too small" in result.message


def test_execution_evidence_file_check_requires_text(tmp_path: Path) -> None:
	evidence = tmp_path / "run.log"
	evidence.write_text("completed table generation\n", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(
		name="run_log",
		path=evidence,
		required_text="table generation",
	).check()

	assert result.ok is True


def test_execution_evidence_file_check_reports_missing_text(tmp_path: Path) -> None:
	evidence = tmp_path / "run.log"
	evidence.write_text("setup only\n", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(
		name="run_log",
		path=evidence,
		required_text="table generation",
	).check()

	assert result.ok is False
	assert "required evidence text" in result.message


def test_execution_evidence_file_check_requires_regex(tmp_path: Path) -> None:
	evidence = tmp_path / "table.txt"
	evidence.write_text("bugs reproduced: 56\n", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(
		name="table",
		path=evidence,
		required_regex=r"bugs reproduced:\s+56",
	).check()

	assert result.ok is True


def test_execution_evidence_file_check_reports_missing_regex(tmp_path: Path) -> None:
	evidence = tmp_path / "table.txt"
	evidence.write_text("bugs reproduced: 12\n", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(
		name="table",
		path=evidence,
		required_regex=r"bugs reproduced:\s+56",
	).check()

	assert result.ok is False
	assert "regex did not match" in result.message


def test_execution_evidence_file_check_can_require_fresh_mtime(tmp_path: Path) -> None:
	evidence = tmp_path / "fresh.log"
	evidence.write_text("generated after run start\n", encoding="utf-8")
	threshold = datetime(2026, 1, 1, tzinfo=timezone.utc)
	fresh_timestamp = threshold.timestamp() + 60
	os.utime(evidence, (fresh_timestamp, fresh_timestamp))

	result = ExecutionEvidenceFileCheck(
		name="fresh",
		path=evidence,
		modified_after=threshold,
	).check()

	assert result.ok is True


def test_execution_evidence_file_check_rejects_stale_mtime(tmp_path: Path) -> None:
	evidence = tmp_path / "stale.log"
	evidence.write_text("old copied output\n", encoding="utf-8")
	threshold = datetime(2026, 1, 1, tzinfo=timezone.utc)
	stale_timestamp = threshold.timestamp() - 60
	os.utime(evidence, (stale_timestamp, stale_timestamp))

	result = ExecutionEvidenceFileCheck(
		name="stale",
		path=evidence,
		modified_after=threshold,
	).check()

	assert result.ok is False
	assert "older than required threshold" in result.message


def test_execution_evidence_file_check_reports_missing_mtime_threshold(
	tmp_path: Path,
) -> None:
	evidence = tmp_path / "run.log"
	evidence.write_text("freshness requested but no runtime metadata\n", encoding="utf-8")

	result = ExecutionEvidenceFileCheck(
		name="run_log",
		path=evidence,
		modified_after_required=True,
	).check()

	assert result.ok is False
	assert "mtime threshold unavailable" in result.message


def test_execution_evidence_file_check_validates_regex() -> None:
	with pytest.raises(ValueError, match="invalid required_regex"):
		ExecutionEvidenceFileCheck(
			name="bad_regex",
			path="evidence.log",
			required_regex="[",
		)
