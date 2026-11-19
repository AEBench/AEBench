"""Layer 4, P0: every rule of spec §0.4 is exercised by a test.

This test imports no layer-4 code, so it runs before ``oracle_inputs.py``
exists (D44). It reads the ``rules`` lists of the scenario files and the
``Rules:`` lines of the unit and metamorphic tests' docstrings, checks the
capability pairs (†) of spec §0.6, and checks that every scenario loads
with the helper's defaults and is internally consistent.
"""

import pathlib

import oracle_inputs_helpers as helpers
import pytest

SCENARIOS = helpers.scenario_paths()
TEST_FILES = (
    "test_oracle_inputs_functions.py",
    "test_oracle_inputs_scenarios.py",
)


def _scenario_rules() -> dict[str, set[str]]:
    cited: dict[str, set[str]] = {}
    for path in SCENARIOS:
        for rule in helpers.read_raw(path).get("rules") or []:
            cited.setdefault(rule, set()).add(path.stem)
    return cited


def test_scenarios_exist() -> None:
    """OS01 to OS31 are present (OS23 in two halves)."""
    numbers = {path.stem[:4] for path in SCENARIOS}
    assert numbers == {f"OS{n:02d}" for n in range(1, 32)}


def test_scenarios_cite_known_rules() -> None:
    """No scenario cites a rule id spec §0.4 does not define."""
    unknown = set(_scenario_rules()) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_tests_cite_known_rules() -> None:
    """No test docstring cites an unknown rule id."""
    unknown = set(helpers.rule_citations(TEST_FILES)) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_every_rule_is_cited_by_a_scenario() -> None:
    """Spec §0.6: a rule of §0.4 that no scenario lists fails the runner."""
    cited = _scenario_rules()
    missing = [r for r in helpers.RULE_IDS if r not in cited]
    assert missing == [], missing


def test_every_rule_is_cited_by_a_test() -> None:
    """Each rule id is also cited by a unit or metamorphic test."""
    cited = helpers.rule_citations(TEST_FILES)
    missing = [r for r in helpers.RULE_IDS if r not in cited]
    assert missing == [], missing


def test_every_scenario_cites_a_rule() -> None:
    """A scenario that traces to no rule has no reason to exist."""
    empty = [p.stem for p in SCENARIOS if not helpers.read_raw(p).get("rules")]
    assert empty == [], empty


@pytest.mark.parametrize(("condition", "rule"), helpers.CAPABILITY_RULES)
def test_capability_pairs(condition: str, rule: str) -> None:
    """Spec §0.6 †: each capability rule has both halves of a pair.

    A scenario citing the rule with its condition true, and one with it
    false (D30).
    """
    halves: dict[bool, list[str]] = {True: [], False: []}
    for path in SCENARIOS:
        raw = helpers.read_raw(path)
        if rule in (raw.get("rules") or []):
            halves[helpers.condition_holds(raw, condition)].append(path.stem)
    assert halves[True], f"{rule}: no scenario with {condition} true"
    assert halves[False], f"{rule}: no scenario with {condition} false"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_is_consistent(path: pathlib.Path) -> None:
    """Each scenario loads with the defaults and is internally consistent."""
    scenario = helpers.load_scenario(path)
    assert helpers.validate(scenario, path.stem) == []


def test_scenario_names_are_unique() -> None:
    """One scenario per OS number and half."""
    stems = [path.stem.split("_", 1)[0] for path in SCENARIOS]
    assert len(stems) == len(set(stems))
