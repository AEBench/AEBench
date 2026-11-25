"""Layer 3: every rule of the structure spec (§6) is exercised by a test.

This test imports no layer-3 code, so it runs before ``structure.py``
exists. It reads the ``rules`` lists of the scenario files and the rules
the unit tests declare in ``structure_helpers.UNIT_TEST_RULES``, checks
the capability pairs of spec §1 (D30), and checks that every scenario
loads with the helper's defaults and is internally consistent.
"""

import pathlib

import pytest
import structure_helpers

SCENARIOS = structure_helpers.scenario_paths()


def _scenario_rules() -> dict[str, set[str]]:
    cited: dict[str, set[str]] = {}
    for path in SCENARIOS:
        for rule in structure_helpers.read_raw(path).get("rules") or []:
            cited.setdefault(rule, set()).add(path.stem)
    return cited


def test_scenarios_exist() -> None:
    """The scenario folder is not empty."""
    assert len(SCENARIOS) >= 40


def test_scenarios_cite_known_rules() -> None:
    """No scenario cites a rule id the spec does not define."""
    unknown = set(_scenario_rules()) - set(structure_helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_retired_rules_not_defined() -> None:
    """PRE6 moved to oracle_inputs.py and FC7 is retired (D29, D31)."""
    for rule in structure_helpers.RETIRED_RULE_IDS:
        assert rule not in structure_helpers.RULE_IDS
        assert rule not in structure_helpers.UNIT_TEST_RULES


def test_every_rule_is_exercised() -> None:
    """Each rule id is cited by a scenario or covered by a unit test."""
    covered = set(_scenario_rules()) | structure_helpers.UNIT_TEST_RULES
    missing = [r for r in structure_helpers.RULE_IDS if r not in covered]
    assert missing == [], missing


def test_every_rule_is_cited_by_a_scenario() -> None:
    """Spec §2.2.2: every rule id is cited by at least one scenario."""
    cited = _scenario_rules()
    missing = [r for r in structure_helpers.RULE_IDS if r not in cited]
    assert missing == [], missing


def test_every_scenario_cites_a_rule() -> None:
    """A scenario that traces to no rule has no reason to exist."""
    empty = [
        path.stem
        for path in SCENARIOS
        if not structure_helpers.read_raw(path).get("rules")
    ]
    assert empty == [], empty


@pytest.mark.parametrize(
    ("condition", "rule"),
    [
        (condition, rule)
        for condition, rules in structure_helpers.CAPABILITY_RULES
        for rule in rules
    ],
)
def test_capability_pairs(condition: str, rule: str) -> None:
    """D30: each capability rule is tested in both halves of a pair.

    D30: each capability rule has a scenario with its condition true and
    one with it false.
    """
    halves = {True: [], False: []}
    for path in SCENARIOS:
        raw = structure_helpers.read_raw(path)
        if rule in (raw.get("rules") or []):
            held = structure_helpers.condition_holds(raw, condition)
            halves[held].append(path.stem)
    assert halves[True], f"{rule}: no scenario with {condition} true"
    assert halves[False], f"{rule}: no scenario with {condition} false"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_is_consistent(path: pathlib.Path) -> None:
    """Each scenario loads and is internally consistent.

    Each scenario loads with the defaults and is internally consistent:
    ids resolve, each true capability's fields are set somewhere and each
    false one's nowhere, the baseline agrees with inputs.tracked_files.
    """
    scenario = structure_helpers.load_scenario(path)
    assert structure_helpers.validate(scenario, path.stem) == []


def test_scenario_names_are_unique_by_number() -> None:
    """One scenario per G number."""
    numbers = [path.stem.split("_", 1)[0] for path in SCENARIOS]
    assert len(numbers) == len(set(numbers))
