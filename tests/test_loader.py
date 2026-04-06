"""Case bundle loading and validation tests."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from artevalbench.evaluator.loader import CaseBundleError, load_case_spec


def _write(path: Path, content: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(textwrap.dedent(content), encoding="utf-8")


_MINIMAL_TOML = """\
	id = "test_case"

	[case_brief]
	core_claim = "Verify the artifact builds correctly."
	acceptable_evidence = "The binary exists and runs."
	allowed_tolerance = "None."

	[run]
	id = "test_case"
	[run.runtime]
	mode = "local"

	[oracle]
	phases = ["env_setup"]
"""

_ORACLE_STUB = """\
	from artevalbench.evaluator.oracles.case_base import CaseOracleEnvSetupBase
	class OracleEnvSetup(CaseOracleEnvSetupBase):
		def requirements(self): return []
"""


def _setup_valid_case(case_dir: Path) -> None:
	_write(case_dir / "case.toml", _MINIMAL_TOML)
	(case_dir / "refs").mkdir(exist_ok=True)
	oracle_dir = case_dir / "oracles"
	oracle_dir.mkdir(exist_ok=True)
	_write(oracle_dir / "env_setup.py", _ORACLE_STUB)
	_write(case_dir / "artifact" / "README.md", "# test artifact\n")


def test_load_valid_bundle(tmp_path: Path) -> None:
	case_dir = tmp_path / "test_case"
	case_dir.mkdir()
	_setup_valid_case(case_dir)
	spec = load_case_spec(case_dir)
	assert spec.id == "test_case"


def test_case_toml_id_field(tmp_path: Path) -> None:
	case_dir = tmp_path / "test_case"
	case_dir.mkdir()
	_setup_valid_case(case_dir)
	spec = load_case_spec(case_dir)
	assert spec.id == "test_case"


def test_case_brief_fields_parsed(tmp_path: Path) -> None:
	case_dir = tmp_path / "test_case"
	case_dir.mkdir()
	_setup_valid_case(case_dir)
	spec = load_case_spec(case_dir)
	assert spec.case_brief.core_claim == "Verify the artifact builds correctly."
	assert spec.case_brief.acceptable_evidence == "The binary exists and runs."
	assert spec.case_brief.allowed_tolerance == "None."


def test_oracle_phases_field_parsed(tmp_path: Path) -> None:
	case_dir = tmp_path / "test_case"
	case_dir.mkdir()
	_setup_valid_case(case_dir)
	spec = load_case_spec(case_dir)
	assert "env_setup" in spec.oracle.phases


def test_runtime_mode_parsed(tmp_path: Path) -> None:
	case_dir = tmp_path / "test_case"
	case_dir.mkdir()
	_setup_valid_case(case_dir)
	spec = load_case_spec(case_dir)
	assert spec.run.runtime.mode.value == "local"


def test_nonexistent_dir_raises(tmp_path: Path) -> None:
	with pytest.raises(CaseBundleError, match="does not exist"):
		load_case_spec(tmp_path / "does_not_exist")


def test_missing_case_toml_raises(tmp_path: Path) -> None:
	case_dir = tmp_path / "no_toml"
	case_dir.mkdir()
	(case_dir / "refs").mkdir()
	oracle_dir = case_dir / "oracles"
	oracle_dir.mkdir()
	_write(oracle_dir / "env_setup.py", _ORACLE_STUB)

	with pytest.raises(CaseBundleError, match="case.toml not found"):
		load_case_spec(case_dir)


def test_missing_refs_dir_raises(tmp_path: Path) -> None:
	case_dir = tmp_path / "no_refs"
	case_dir.mkdir()
	_write(case_dir / "case.toml", _MINIMAL_TOML)
	oracle_dir = case_dir / "oracles"
	oracle_dir.mkdir()
	_write(oracle_dir / "env_setup.py", _ORACLE_STUB)

	with pytest.raises(CaseBundleError, match="refs"):
		load_case_spec(case_dir)


def test_missing_oracle_dir_raises(tmp_path: Path) -> None:
	case_dir = tmp_path / "no_oracle"
	case_dir.mkdir()
	_write(case_dir / "case.toml", _MINIMAL_TOML)
	(case_dir / "refs").mkdir()

	with pytest.raises(CaseBundleError, match="oracle"):
		load_case_spec(case_dir)


def test_empty_oracle_dir_raises(tmp_path: Path) -> None:
	case_dir = tmp_path / "empty_oracle"
	case_dir.mkdir()
	_write(case_dir / "case.toml", _MINIMAL_TOML)
	(case_dir / "refs").mkdir()
	(case_dir / "oracles").mkdir()
	with pytest.raises(CaseBundleError, match="no Python files"):
		load_case_spec(case_dir)


def test_singular_oracle_dir_rejected(tmp_path: Path) -> None:
	case_dir = tmp_path / "singular_case"
	case_dir.mkdir()
	_write(case_dir / "case.toml", _MINIMAL_TOML)
	(case_dir / "refs").mkdir()
	singular_dir = case_dir / "oracle"
	singular_dir.mkdir()
	_write(singular_dir / "env_setup.py", _ORACLE_STUB)
	_write(case_dir / "artifact" / "README.md", "# test\n")

	with pytest.raises(CaseBundleError, match="oracles"):
		load_case_spec(case_dir)
