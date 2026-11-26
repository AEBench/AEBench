"""Layer 6: every rule of the spec is exercised, every pair is present.

This test imports no layer-6 code (neither ``declared`` nor ``chain``),
so it passes before either module exists (D46). It reads the ``rules``
lists of the scenario files and the ``Rules:`` lines of the other tests'
docstrings, checks the capability pairs of spec §2.2.3, and checks that
every scenario loads with the helpers' defaults and is consistent.
"""

import pathlib

import chain_helpers as helpers
import oracle_inputs_helpers
import pytest

SCENARIOS = helpers.scenario_paths()
TEST_FILES = (
    "test_declared_functions.py",
    "test_declared_fixtures.py",
    "test_chain_functions.py",
    "test_chain_scenarios.py",
    "test_chain_invariants.py",
    "test_chain_cli.py",
    "test_chain_guard.py",
)
# The tests whose Rules: lines count for §2.2.6 besides the scenarios.
FIXTURE_AND_CLI_FILES = ("test_declared_fixtures.py", "test_chain_cli.py")
# The fixture repositories of §2.2.1.
DECLARED_FIXTURES = (
    *("fences", "cwd", "links", "placeholders", "argparse", "make"),
    *("ci_excluded", "case_lines", "none"),
)


def _scenario_rules() -> dict[str, set[str]]:
    cited: dict[str, set[str]] = {}
    for path in SCENARIOS:
        for raw in helpers.run_raws(helpers.read_raw(path)):
            for rule in raw.get("rules") or []:
                cited.setdefault(rule, set()).add(path.stem)
    return cited


def _by_id() -> dict[str, pathlib.Path]:
    return {helpers.scenario_id(path): path for path in SCENARIOS}


def test_scenarios_exist() -> None:
    """Exactly the scenarios H01 to H44, H45a to H52b and H53 to H78."""
    ids = [helpers.scenario_id(path) for path in SCENARIOS]
    assert sorted(ids) == sorted(helpers.SPEC_IDS)
    assert len(ids) == len(set(ids))


def test_scenarios_cite_known_rules() -> None:
    """No scenario cites an id the spec does not define as a rule."""
    unknown = set(_scenario_rules()) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_tests_cite_known_rules() -> None:
    """No test docstring cites an unknown rule id of layer 6."""
    cited = oracle_inputs_helpers.rule_citations(TEST_FILES)
    unknown = set(cited) - set(helpers.RULE_IDS)
    assert not unknown, sorted(unknown)


def test_every_section6_rule_is_cited() -> None:
    """Every rule of §6 and §7 (but ARG6, TM4) is cited (§2.2.6).

    By a scenario, or by the Rules: line of a fixture or CLI test (AGG2
    by test_chain_peer_ratios, the DEC rules by the fixtures).
    """
    cited = set(_scenario_rules()) | set(
        oracle_inputs_helpers.rule_citations(FIXTURE_AND_CLI_FILES)
    )
    missing = [r for r in helpers.SECTION6_RULES if r not in cited]
    assert missing == [], missing


def test_aggregation_peer_ratio_is_cited_by_the_cli_test() -> None:
    """AGG2 is cited by test_chain_peer_ratios (§2.2.6)."""
    cited = oracle_inputs_helpers.rule_citations(("test_chain_cli.py",))
    assert "test_chain_cli.py::test_chain_peer_ratios" in cited.get(
        "AGG2", set()
    )


def test_every_rule_is_cited() -> None:
    """Every rule id, ARG6 and TM4 too, is cited by a scenario or a test."""
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
    ("capability", "basis", "true_id", "false_id", "rule"),
    helpers.CAPABILITY_PAIRS,
    ids=[f"{row[2]}-{row[3]}" for row in helpers.CAPABILITY_PAIRS],
)
def test_capability_pairs(
    capability: str, basis: str, true_id: str, false_id: str, rule: str
) -> None:
    """Spec §2.2.3: each capability-dependent rule has both halves (D29).

    Both scenarios exist and cite the rule; the first runs with the
    capability true, the second with it false, except H52b, which keeps
    the capability and drops one record's field (PRE3); each lists the
    row's bases key in its expect.
    """
    found = _by_id()
    assert true_id in found and false_id in found
    for half, scenario_id in ((True, true_id), (False, false_id)):
        raw = helpers.read_raw(found[scenario_id])
        assert rule in (raw.get("rules") or []), (scenario_id, rule)
        assert basis in ((raw.get("expect") or {}).get("bases") or {}), (
            scenario_id,
            basis,
        )
        scenario = helpers.build_scenario(raw)
        value = scenario["run"]["capabilities"][capability]
        if scenario_id == "H52b":
            assert value is True
            assert any(e["reads"] is None for e in scenario["execs"])
        else:
            assert value is half, (scenario_id, capability)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_is_consistent(path: pathlib.Path) -> None:
    """Each scenario (each of its runs) loads and is internally consistent.

    Ids are unique and resolve, every input_states and last_writers row
    names an oracle-input row, every decl_id is unique.
    """
    for raw in helpers.run_raws(helpers.read_raw(path)):
        scenario = helpers.build_scenario(raw)
        assert helpers.validate(scenario, path.stem) == []


def test_scenario_spec_parse() -> None:
    """The helpers parse a [declared] table as CT1 and CT5 say."""
    spec = helpers.parse_spec(
        {
            "lines": ["./a.sh"],
            "options": {"cargo bench --bench": "exact", "x": "min"},
            "allowed_diff_classes": ["whitespace_only", "prettier"],
            "other": 1,
        }
    )
    assert spec["markers"] == {"cargo bench": {"--bench": "exact"}}
    assert spec["allowed_diff_classes"] == ["whitespace_only"]
    assert spec["lines"] == ["./a.sh"]
    assert len(spec["warnings"]) == 3
    assert all(w.startswith("declared ") for w in spec["warnings"])


def test_fixtures_are_documented() -> None:
    """Every fixture has its two expected files and EDITS.md names it."""
    assert (helpers.DECLARED_FIXTURES_DIR / "EDITS.md").is_file()
    found = sorted(
        p.name for p in helpers.DECLARED_FIXTURES_DIR.iterdir() if p.is_dir()
    )
    assert found == sorted(DECLARED_FIXTURES)
    text = (helpers.DECLARED_FIXTURES_DIR / "EDITS.md").read_text()
    for name in found:
        assert (
            helpers.DECLARED_FIXTURES_DIR / name / "expected_invocations.json"
        ).is_file()
        assert (
            helpers.DECLARED_FIXTURES_DIR / name / "expected_options.json"
        ).is_file()
        assert f"`{name}`" in text, name
