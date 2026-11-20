"""Layer 5: every rule of the spec is exercised, every pair is present.

This test imports no layer-5 code, so it runs before ``writer.py``
exists (D41). It reads the ``rules`` lists of the scenario files and the
``Rules:`` lines of the other tests' docstrings, checks the capability
pairs of spec §2.2.2, and checks that every scenario loads with the
helper's defaults and is internally consistent.
"""

import pathlib

import oracle_inputs_helpers
import pytest
import writer_helpers as helpers

SCENARIOS = helpers.scenario_paths()
TEST_FILES = (
    "test_writer_functions.py",
    "test_writer_scenarios.py",
    "test_writer_invariants.py",
    "test_writer_cli.py",
)


def _scenario_rules() -> dict[str, set[str]]:
    cited: dict[str, set[str]] = {}
    for path in SCENARIOS:
        for rule in helpers.read_raw(path).get("rules") or []:
            cited.setdefault(rule, set()).add(path.stem)
    return cited


def _by_id() -> dict[str, pathlib.Path]:
    return {path.stem.split("_", 1)[0]: path for path in SCENARIOS}


def test_scenarios_exist() -> None:
    """Exactly the scenarios W01 to W54 with W23b, then W55 to W70."""
    ids = [path.stem.split("_", 1)[0] for path in SCENARIOS]
    assert sorted(ids) == sorted(helpers.SPEC_IDS)
    assert len(ids) == len(set(ids))


def test_scenarios_cite_known_rules() -> None:
    """No scenario cites an id the spec does not define as a rule."""
    unknown = set(_scenario_rules()) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_tests_cite_known_rules() -> None:
    """No test docstring cites an unknown rule id of layer 5."""
    cited = oracle_inputs_helpers.rule_citations(TEST_FILES)
    unknown = set(cited) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_retired_rule_not_defined() -> None:
    """LW7 is removed (D23) and its id is not reused."""
    for rule in helpers.RETIRED_RULE_IDS:
        assert rule not in helpers.RULE_IDS
        assert rule not in _scenario_rules()


def test_every_section6_rule_is_cited_by_a_scenario() -> None:
    """Every rule of §6, but RW7 and the steps, is cited by a scenario."""
    cited = _scenario_rules()
    missing = [r for r in helpers.SECTION6_RULES if r not in cited]
    assert missing == [], missing


def test_every_rule_is_cited() -> None:
    """Every rule id, RW7 and OUT1 too, is cited by a scenario or a test."""
    cited = set(_scenario_rules()) | set(
        oracle_inputs_helpers.rule_citations(TEST_FILES)
    )
    missing = [r for r in helpers.RULE_IDS if r not in cited]
    assert missing == [], missing


def test_every_scenario_cites_a_rule() -> None:
    """A scenario that traces to no rule has no reason to exist."""
    empty = [p.stem for p in SCENARIOS if not helpers.read_raw(p).get("rules")]
    assert empty == [], empty


@pytest.mark.parametrize(
    ("capability", "rule", "basis", "true_id", "false_id"),
    helpers.CAPABILITY_PAIRS,
    ids=[f"{row[0]}-{row[1]}" for row in helpers.CAPABILITY_PAIRS],
)
def test_capability_pairs(
    capability: str, rule: str, basis: str | None, true_id: str, false_id: str
) -> None:
    """Spec §2.2.2: each capability-dependent rule has both halves (D24).

    Both scenarios exist and cite the rule; the first runs with the
    capability true, the second with it false (W57 holds both halves,
    as its own two runs); each lists the row's bases key in its expect.
    """
    found = _by_id()
    assert true_id in found and false_id in found
    halves = {}
    for half, scenario_id in ((True, true_id), (False, false_id)):
        raw = helpers.read_raw(found[scenario_id])
        assert rule in (raw.get("rules") or []), (scenario_id, rule)
        values = {
            bool(helpers.build_scenario(r)["run"]["capabilities"][capability])
            for r in helpers.run_raws(raw)
        }
        halves[half] = values
        if basis is not None:
            assert basis in ((raw.get("expect") or {}).get("bases") or {}), (
                scenario_id,
                basis,
            )
    assert True in halves[True], (true_id, capability)
    assert False in halves[False], (false_id, capability)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_is_consistent(path: pathlib.Path) -> None:
    """Each scenario (each of its runs) loads and is internally consistent.

    Ids resolve, event ids follow order_t, each paths row's final writer
    is layer 1's, every input_states row names an oracle-input row.
    """
    for scenario in helpers.load_runs(path):
        assert helpers.validate(scenario, path.stem) == []


def test_diff_fixtures_have_expectations() -> None:
    """Each diff fixture has its hand-written hunks and EDITS.md exists."""
    fixtures = sorted(helpers.DIFF_FIXTURES_DIR.glob("*.diff"))
    assert fixtures
    assert (helpers.DIFF_FIXTURES_DIR / "EDITS.md").is_file()
    for path in fixtures:
        expected = path.with_name(path.stem.split(".")[0] + ".hunks.json")
        assert expected.is_file(), expected.name
