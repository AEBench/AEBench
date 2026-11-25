"""Layer 4, P1-P6: every rule of the spec is exercised by a test.

This test imports no layer-4 code, so it runs before ``input_state.py``
exists (D44). It reads the ``rules`` lists of the scenario files and the
``Rules:`` lines of the other tests' docstrings, checks the capability
pairs (†) of spec §6.9, and checks that every scenario loads with the
helper's defaults and is internally consistent.
"""

import pathlib

import input_state_helpers as helpers
import oracle_inputs_helpers
import pytest

SCENARIOS = helpers.scenario_paths()
TEST_FILES = (
    "test_input_state_functions.py",
    "test_input_state_scenarios.py",
    "test_input_state_invariants.py",
    "test_input_state_cli.py",
)
SPEC_IDS = (
    *("P01", "P02", "P02b", "P03", "P03b", "P04", "P05", "P06", "P07"),
    *("P08", "P09", "P10", "P11", "P12", "P12b", "P13", "P14", "P15"),
    *("P15b", "P16", "P17", "P18", "P18b", "P19", "P19b", "P20", "P21"),
    *("P22", "P23", "P24", "P25", "P26", "P27", "P28", "P28b", "P29"),
    *("P30", "P31", "P32", "P32b", "P33", "P34", "P35", "P36", "P36b"),
    *("P37", "P37b", "P38", "P39", "P40", "P40b"),
    *("P41", "P42", "P43", "P44"),  # cycle-1 critic: A1, A5
    *("P45", "P45b"),  # cycle 2: late writes after the final diff (D73)
    *("P46", "P47"),  # partial snapshots: blind for the paths they cover
)


def _scenario_rules() -> dict[str, set[str]]:
    cited: dict[str, set[str]] = {}
    for path in SCENARIOS:
        for rule in helpers.read_raw(path).get("rules") or []:
            cited.setdefault(rule, set()).add(path.stem)
    return cited


def _test_rules() -> dict[str, set[str]]:
    return oracle_inputs_helpers.rule_citations(TEST_FILES)


def test_scenarios_exist() -> None:
    """The scenarios P01 to P45b of spec §2.2.2, then P46 and P47."""
    ids = [path.stem.split("_", 1)[0] for path in SCENARIOS]
    assert sorted(ids) == sorted(SPEC_IDS)


def test_scenarios_cite_known_rules() -> None:
    """No scenario cites an id the spec does not define as a rule."""
    unknown = set(_scenario_rules()) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_tests_cite_known_rules() -> None:
    """No test docstring cites an unknown rule id of layer 4.

    The CLI tests also cite §0's ids (OI, NS), which are layer 4's too.
    """
    known = set(helpers.RULE_IDS) | set(oracle_inputs_helpers.RULE_IDS)
    unknown = set(_test_rules()) - known
    assert not unknown, sorted(unknown)


def test_retired_rules_not_defined() -> None:
    """LWR1-LWR3 and LWR5-LWR7 are withdrawn or moved, CON10 reserved."""
    for rule in helpers.RETIRED_RULE_IDS:
        assert rule not in helpers.RULE_IDS


def test_every_section6_rule_is_cited_by_a_scenario() -> None:
    """Spec §2.2.2: a rule id of §6 that no scenario lists fails."""
    cited = _scenario_rules()
    missing = [r for r in helpers.SECTION6_RULES if r not in cited]
    assert missing == [], missing


def test_every_rule_is_cited() -> None:
    """Every rule, precondition and assumption is cited by some test.

    By a scenario or a docstring Rules: line; S1-S5 by the model-based
    test at least.
    """
    cited = set(_scenario_rules()) | set(_test_rules())
    missing = [r for r in helpers.RULE_IDS if r not in cited]
    assert missing == [], missing


def test_assumptions_are_cited_by_the_model_test() -> None:
    """The soundness lemmas name the assumptions they rest on (§8)."""
    model = oracle_inputs_helpers.rule_citations(
        ("test_input_state_invariants.py",)
    )
    for assumption in ("S1", "S2", "S3", "S4", "S5"):
        assert assumption in model, assumption


def test_every_scenario_cites_a_rule() -> None:
    """A scenario that traces to no rule has no reason to exist."""
    empty = [p.stem for p in SCENARIOS if not helpers.read_raw(p).get("rules")]
    assert empty == [], empty


@pytest.mark.parametrize(
    ("basis", "condition", "rule"),
    helpers.CAPABILITY_RULES,
    ids=[row[0] for row in helpers.CAPABILITY_RULES],
)
def test_capability_pairs(basis: str, condition: str, rule: str) -> None:
    """Spec §6.9: each capability-dependent rule has both halves (†).

    A scenario citing the rule with its condition true, and one with it
    false (D30).
    """
    halves: dict[bool, list[str]] = {True: [], False: []}
    for path in SCENARIOS:
        raw = helpers.read_raw(path)
        if rule in (raw.get("rules") or []):
            halves[helpers.condition_holds(raw, condition)].append(path.stem)
    assert halves[True], f"{basis}: no {rule} scenario with {condition}"
    assert halves[False], f"{basis}: no {rule} scenario without {condition}"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_is_consistent(path: pathlib.Path) -> None:
    """Each scenario loads and is internally consistent.

    Ids resolve, each true capability's fields are set somewhere and each
    false one's nowhere, graded rows only with oracle_reads.
    """
    scenario = helpers.load_scenario(path)
    assert helpers.validate(scenario, path.stem) == []
