"""Layer 2: hand-written scenarios (facts spec §2.2.2).

One YAML file per scenario in ``tests/facts_scenarios/``, written as
layer-1 output records; the format and the defaults are documented in
``facts_helpers.py``.
"""

import pathlib
from collections.abc import Mapping
from typing import Any

import conftest
import facts_helpers
import facts_invariants
import pytest

SCENARIOS = facts_helpers.scenario_params()
ROW_KEYS = (
    ("call_facts", "call_id"),
    ("exec_facts", "command_id"),
    ("path_facts", "path"),
)


def _row_problems(
    result: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for block, id_key in ROW_KEYS:
        rows = {}
        for row in result[block]:
            rows.setdefault(row[id_key], []).append(row)
        for item_id, want in (expect.get(block) or {}).items():
            got = rows.get(item_id, [])
            if len(got) != 1:
                problems.append(f"{block}[{item_id}]: {len(got)} rows")
                continue
            for problem in conftest.partial_mismatches(got[0], want):
                problems.append(f"{block}[{item_id}]: {problem}")
    present = {
        row[id_key] for block, id_key in ROW_KEYS for row in result[block]
    }
    for item_id in expect.get("absent") or []:
        if item_id in present:
            problems.append(f"{item_id}: has a row, expected none")
    return problems


def _facts_problems(
    facts: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for key, want in (expect.get("facts") or {}).items():
        try:
            got = facts_helpers.facts_value(facts, key)
        except KeyError:
            problems.append(f"facts {key}: missing")
            continue
        if not conftest.close(got, want):
            problems.append(f"facts {key}: {got!r} != expected {want!r}")
    warnings = facts["warnings"]
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


@pytest.mark.parametrize("path", SCENARIOS)
def test_scenario(path: pathlib.Path) -> None:
    """Each scenario's expectations hold, and so do the invariants."""
    scenario = facts_helpers.load_scenario(path)
    assert scenario["name"] == path.stem
    unknown = set(scenario["rules"]) - set(facts_helpers.RULE_IDS)
    assert not unknown, f"unknown rule ids {sorted(unknown)}"
    result = facts_helpers.run_derive(scenario)
    expect = scenario["expect"]
    problems = _row_problems(result, expect)
    problems += _facts_problems(result["facts"], expect)
    problems += facts_invariants.check(scenario, result)
    assert problems == [], "\n".join(problems)
