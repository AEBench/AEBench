"""Oracle phase discovery tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from evaluator.oracles.discovery import (
	ARTIFACT_BUILD,
	BENCHMARK_PREP,
	ENV_SETUP,
	EXPERIMENT_RUNS,
	DiscoveredPhase,
	OracleLoadError,
	discover_oracle_phases,
	phase_key_to_string,
	phase_string_to_key,
)


def _write_oracle(oracle_dir: Path, filename: str, content: str) -> None:
	(oracle_dir / filename).write_text(textwrap.dedent(content), encoding="utf-8")


def _make_case(tmp_path: Path) -> tuple[Path, Path]:
	"""Return (case_dir, oracle_dir) with oracles/ created."""
	case_dir = tmp_path / "case"
	case_dir.mkdir()
	oracle_dir = case_dir / "oracles"
	oracle_dir.mkdir()
	return case_dir, oracle_dir


_ENV_SETUP = """\
	from evaluator.oracles.case_base import CaseOracleEnvSetupBase
	class OracleEnvSetup(CaseOracleEnvSetupBase):
		def requirements(self): return []
"""

_ARTIFACT_BUILD = """\
	from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
	class OracleArtifactBuild(CaseOracleArtifactBuildBase):
		def requirements(self): return []
"""

_BENCHMARK_PREP = """\
	from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
	class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
		def requirements(self): return []
"""

_EXPERIMENT_RUNS = """\
	from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
	class OracleExperimentRuns(CaseOracleExperimentRunsBase):
		def requirements(self): return []
"""


def test_discover_all_four_phases(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "env_setup.py", _ENV_SETUP)
	_write_oracle(oracle_dir, "artifact_build.py", _ARTIFACT_BUILD)
	_write_oracle(oracle_dir, "benchmark_prep.py", _BENCHMARK_PREP)
	_write_oracle(oracle_dir, "experiment_runs.py", _EXPERIMENT_RUNS)

	phases = discover_oracle_phases(case_dir)

	assert len(phases) == 4
	assert [p.key for p in phases] == [ENV_SETUP, ARTIFACT_BUILD, BENCHMARK_PREP, EXPERIMENT_RUNS]


def test_discover_subset_of_phases(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "env_setup.py", _ENV_SETUP)
	_write_oracle(oracle_dir, "artifact_build.py", _ARTIFACT_BUILD)

	phases = discover_oracle_phases(case_dir)

	assert len(phases) == 2
	assert [p.key for p in phases] == [ENV_SETUP, ARTIFACT_BUILD]


def test_phase_priority_ordering(tmp_path: Path) -> None:
	"""Phases sort by priority regardless of file order."""
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "a_experiment_runs.py", _EXPERIMENT_RUNS)
	_write_oracle(oracle_dir, "b_benchmark_prep.py", _BENCHMARK_PREP)
	_write_oracle(oracle_dir, "c_artifact_build.py", _ARTIFACT_BUILD)
	_write_oracle(oracle_dir, "d_env_setup.py", _ENV_SETUP)

	phases = discover_oracle_phases(case_dir)

	priorities = [p.priority for p in phases]
	assert priorities == sorted(priorities)
	assert [p.key for p in phases] == [ENV_SETUP, ARTIFACT_BUILD, BENCHMARK_PREP, EXPERIMENT_RUNS]


def test_non_oracle_class_ignored(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	content = """\
		from evaluator.oracles.case_base import CaseOracleEnvSetupBase

		class CommonUtils:
			def helper(self): pass

		class OracleEnvSetup(CaseOracleEnvSetupBase):
			def requirements(self): return []
	"""
	_write_oracle(oracle_dir, "env_setup.py", content)

	phases = discover_oracle_phases(case_dir)

	assert len(phases) == 1
	assert phases[0].key == ENV_SETUP
	assert phases[0].qualname == "OracleEnvSetup"


def test_abstract_base_not_discovered(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	content = """\
		from evaluator.oracles.case_base import CaseOracleEnvSetupBase

		class OracleEnvSetup(CaseOracleEnvSetupBase):
			def requirements(self): return []
	"""
	_write_oracle(oracle_dir, "env_setup.py", content)

	phases = discover_oracle_phases(case_dir)

	class_names = {p.qualname for p in phases}
	assert "CaseOracleEnvSetupBase" not in class_names
	assert "OracleEnvSetup" in class_names


def test_no_oracle_dir_raises(tmp_path: Path) -> None:
	case_dir = tmp_path / "case"
	case_dir.mkdir()

	with pytest.raises(OracleLoadError, match="oracle directory is missing"):
		discover_oracle_phases(case_dir)


def test_no_oracle_implementations_raises(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "common.py", "class CommonUtils: pass\n")

	with pytest.raises(OracleLoadError, match="no oracle phase implementations"):
		discover_oracle_phases(case_dir)


def test_empty_oracle_dir_raises(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)

	with pytest.raises(OracleLoadError, match="no Python modules"):
		discover_oracle_phases(case_dir)


def test_phase_key_to_string_single_segment() -> None:
	assert phase_key_to_string(("env_setup",)) == "env_setup"


def test_phase_key_to_string_multi_segment() -> None:
	assert phase_key_to_string(("benchmark", "prep")) == "benchmark.prep"


def test_phase_key_to_string_empty_raises() -> None:
	with pytest.raises(ValueError):
		phase_key_to_string(())


def test_phase_string_to_key_valid() -> None:
	assert phase_string_to_key("env_setup") == ("env_setup",)
	assert phase_string_to_key("artifact_build") == ("artifact_build",)
	assert phase_string_to_key("benchmark_prep") == ("benchmark_prep",)
	assert phase_string_to_key("experiment_runs") == ("experiment_runs",)


@pytest.mark.parametrize("bad", ["UPPERCASE", "", "has spaces", "123starts_digit", "dot..double"])
def test_phase_string_to_key_invalid(bad: str) -> None:
	with pytest.raises(ValueError):
		phase_string_to_key(bad)


# Decorator-based discovery

_DECORATOR_ENV_SETUP = """\
	from evaluator.oracles.discovery import env_setup
	from models import OracleInput
	from evaluator.oracles.utils import Checkable
	from collections.abc import Sequence

	@env_setup
	def _(context: OracleInput) -> Sequence[Checkable]:
		return []
"""

_DECORATOR_ARTIFACT_BUILD = """\
	from evaluator.oracles.discovery import artifact_build
	from models import OracleInput
	from evaluator.oracles.utils import Checkable
	from collections.abc import Sequence

	@artifact_build
	def _(context: OracleInput) -> Sequence[Checkable]:
		return []
"""


def test_discover_decorator_based_phase(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "env_setup.py", _DECORATOR_ENV_SETUP)

	phases = discover_oracle_phases(case_dir)

	assert len(phases) == 1
	assert phases[0].key == ENV_SETUP


def test_discover_mixed_class_and_decorator(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "env_setup.py", _DECORATOR_ENV_SETUP)
	_write_oracle(oracle_dir, "artifact_build.py", _ARTIFACT_BUILD)

	phases = discover_oracle_phases(case_dir)

	assert len(phases) == 2
	assert [p.key for p in phases] == [ENV_SETUP, ARTIFACT_BUILD]


def test_duplicate_decorator_and_class_for_same_phase_raises(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "env_setup_cls.py", _ENV_SETUP)
	_write_oracle(oracle_dir, "env_setup_dec.py", _DECORATOR_ENV_SETUP)

	with pytest.raises(OracleLoadError, match="duplicate"):
		discover_oracle_phases(case_dir)


def test_discover_multiple_decorator_phases(tmp_path: Path) -> None:
	case_dir, oracle_dir = _make_case(tmp_path)
	_write_oracle(oracle_dir, "env_setup.py", _DECORATOR_ENV_SETUP)
	_write_oracle(oracle_dir, "artifact_build.py", _DECORATOR_ARTIFACT_BUILD)

	phases = discover_oracle_phases(case_dir)

	assert len(phases) == 2
	assert [p.key for p in phases] == [ENV_SETUP, ARTIFACT_BUILD]


# Phase key enforcement


def test_unknown_phase_key_rejected() -> None:
	"""DiscoveredPhase rejects phase keys outside the four standard phases."""
	from collections.abc import Sequence

	from evaluator.oracles.utils import Checkable
	from models import OracleInput

	def _noop(context: OracleInput) -> Sequence[Checkable]:
		return []

	with pytest.raises(ValueError, match="unknown oracle phase key"):
		DiscoveredPhase(
			key=("custom_extra_phase",),
			priority=999,
			requirements=_noop,
			qualname="custom_extra_phase",
		)


def test_known_phase_keys_accepted() -> None:
	"""DiscoveredPhase accepts all four standard phase keys."""
	from collections.abc import Sequence

	from evaluator.oracles.utils import Checkable
	from models import OracleInput

	def _noop(context: OracleInput) -> Sequence[Checkable]:
		return []

	for key in (ENV_SETUP, ARTIFACT_BUILD, BENCHMARK_PREP, EXPERIMENT_RUNS):
		phase = DiscoveredPhase(
			key=key,
			priority=100,
			requirements=_noop,
			qualname="test",
		)
		assert phase.key == key


def test_manually_constructed_extra_oracle_phase_rejected(tmp_path: Path) -> None:
	"""A module-level DiscoveredPhase with an unknown key fails at import time."""
	case_dir, oracle_dir = _make_case(tmp_path)
	# Write all four standard phases so discovery doesn't fail for other reasons
	_write_oracle(oracle_dir, "env_setup.py", _ENV_SETUP)
	_write_oracle(oracle_dir, "artifact_build.py", _ARTIFACT_BUILD)
	_write_oracle(oracle_dir, "benchmark_prep.py", _BENCHMARK_PREP)
	_write_oracle(oracle_dir, "experiment_runs.py", _EXPERIMENT_RUNS)
	# Write a module that manually constructs a DiscoveredPhase with a bad key
	_write_oracle(
		oracle_dir,
		"extra.py",
		"""\
		from evaluator.oracles.discovery import DiscoveredPhase
		from collections.abc import Sequence
		from evaluator.oracles.utils import Checkable
		from models import OracleInput

		def _noop(context: OracleInput) -> Sequence[Checkable]:
			return []

		# This should fail at construction time
		custom = DiscoveredPhase(
			key=("my_custom_phase",),
			priority=500,
			requirements=_noop,
			qualname="custom",
		)
	""",
	)

	with pytest.raises(OracleLoadError, match="failed to import"):
		discover_oracle_phases(case_dir)
