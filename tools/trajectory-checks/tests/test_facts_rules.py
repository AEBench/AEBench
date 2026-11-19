"""Layer 2: every rule of the facts spec (§6) is exercised by some test.

This test imports no layer-2 code. It reads the ``rules`` lists of the
scenario files and the rules the unit tests declare in
``facts_helpers.UNIT_TEST_RULES``.
"""

import facts_helpers
import yaml


def _scenario_rules() -> dict[str, set[str]]:
    cited: dict[str, set[str]] = {}
    for path in facts_helpers.scenario_paths():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for rule in raw.get("rules") or []:
            cited.setdefault(rule, set()).add(path.stem)
    return cited


def test_scenarios_cite_known_rules() -> None:
    """No scenario cites a rule id the spec does not define."""
    unknown = set(_scenario_rules()) - set(facts_helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_every_rule_is_exercised() -> None:
    """Each rule id is cited by a scenario or covered by a unit test."""
    covered = set(_scenario_rules()) | facts_helpers.UNIT_TEST_RULES
    missing = [r for r in facts_helpers.RULE_IDS if r not in covered]
    assert missing == [], missing


def test_every_scenario_cites_a_rule() -> None:
    """A scenario that traces to no rule has no reason to exist."""
    empty = [
        path.stem
        for path in facts_helpers.scenario_paths()
        if not yaml.safe_load(path.read_text(encoding="utf-8")).get("rules")
    ]
    assert empty == [], empty
