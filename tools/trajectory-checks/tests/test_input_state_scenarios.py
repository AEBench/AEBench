"""Layer 4, P1-P6: hand-written scenarios (spec §2.2.2).

One YAML file per scenario in ``tests/input_state_scenarios/``; the format
and the defaults are in ``input_state_helpers.py``. Each scenario's
expectations are checked, then the independent invariants of
``input_state_invariants.check_input_states``. The rule coverage checks,
which need no layer-4 code, are in ``test_input_state_rules.py``.
"""

import pathlib
from collections.abc import Mapping
from typing import Any

import conftest
import input_state_helpers as helpers
import input_state_invariants
import pytest

SCENARIOS = helpers.scenario_paths()
SCENARIO_PARAMS = helpers.scenario_params()


def _row_problems(
    rows: list[Mapping[str, Any]], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    index = helpers.row_index(rows)
    for key, want in (expect.get("input_states") or {}).items():
        got = index.get(key, [])
        if len(got) != 1:
            problems.append(f"input_states[{key}]: {len(got)} rows")
            continue
        for problem in conftest.partial_mismatches(got[0], want):
            problems.append(f"input_states[{key}]: {problem}")
    for path in expect.get("absent") or []:
        if path in index:
            problems.append(f"{path}: has a row, expected none")
    return problems


def _count_problems(
    counts: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for key, want in (expect.get("counts") or {}).items():
        try:
            got = helpers.counts_value(counts, key)
        except KeyError:
            problems.append(f"counts {key}: missing")
            continue
        if not conftest.close(got, want):
            problems.append(f"counts {key}: {got!r} != expected {want!r}")
    warnings = counts["warnings"]
    wanted = expect.get("warnings") or []
    for text in wanted:
        hits = [w for w in warnings if text in w]
        if len(hits) != 1:
            problems.append(
                f"{len(hits)} warnings contain {text!r}: {warnings}"
            )
    if expect.get("warnings_exact") and len(warnings) != len(wanted):
        problems.append(f"{len(warnings)} warnings, expected {len(wanted)}")
    return problems


@pytest.mark.parametrize("path", SCENARIO_PARAMS)
def test_scenario(path: pathlib.Path) -> None:
    """Each scenario's expectations hold, and so do the invariants."""
    scenario = helpers.load_scenario(path)
    assert helpers.validate(scenario, path.stem) == []
    result = helpers.run_classify(scenario)
    assert set(result) == {"input_states", "state_counts"}
    expect = scenario["expect"]
    problems = _row_problems(result["input_states"], expect)
    problems += _count_problems(result["state_counts"], expect)
    problems += input_state_invariants.check_input_states(scenario, result)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_record_fields(path: pathlib.Path) -> None:
    """Every row and state_counts.json have exactly the fields of §5.

    Every M3.2 key is written in every run; capabilities are copied from
    coverage.json; bases has one key per row of §6.9 that is not §0's or
    per row; preconditions has the four labels of §7.
    """
    scenario = helpers.load_scenario(path)
    result = helpers.run_classify(scenario)
    for row in result["input_states"]:
        assert set(row) == helpers.ROW_FIELDS, sorted(set(row))
        for root in row["writer_roots"]:
            assert set(root) == helpers.WRITER_ROOT_FIELDS
        assert set(row["predicates_under"]) == set(helpers.PREDICATE_NAMES)
        assert set(row["predicates_over"]) == set(helpers.PREDICATE_NAMES)
        assert row["under_reason"] in (None, *helpers.UNDER_REASONS)
    counts = result["state_counts"]
    assert set(counts) == helpers.COUNTS_FIELDS
    assert counts["run_key"] == scenario["run"]["run_key"]
    assert (
        counts["capabilities"] == scenario["linked"]["coverage"]["capabilities"]
    )
    assert set(counts["bases"]) == helpers.BASES_KEYS
    assert set(counts["bases"].values()) <= {"exact", "fallback"}
    assert set(counts["preconditions"]) == set(helpers.PRECONDITION_LABELS)
    assert set(counts["metrics"]) == {"M3.2"}
    m32 = counts["metrics"]["M3.2"]
    assert set(m32) == helpers.M32_KEYS
    if m32["by_state_under"] is not None:
        assert set(m32["by_state_under"]) == set(helpers.STATES)
        assert set(m32["by_state_over"]) == set(helpers.STATES)
        for phase in m32["phases"].values():
            assert set(phase) == set(helpers.PHASE_FLAGS)
