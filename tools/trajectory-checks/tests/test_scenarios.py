"""Kind 2: hand-written scenarios for T2-T6 (spec §2.2).

One YAML file per scenario in ``tests/scenarios/``; the format and the
defaults are documented in ``conftest.py``.
"""

import pathlib
from collections.abc import Mapping
from typing import Any

import conftest
import pytest

SCENARIOS = conftest.scenario_paths()
LINK_TYPES = ("call_exec", "wrapper_duplicate", "exec_parent", "write_event")


def _link_problems(
    links: list[dict[str, Any]], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    wanted = expect.get("links") or []
    for want in wanted:
        if not any(not conftest.partial_mismatches(lk, want) for lk in links):
            same_type = [
                lk
                for lk in links
                if lk.get(conftest.LINK_TYPE_FIELD)
                == want[conftest.LINK_TYPE_FIELD]
            ]
            problems.append(f"no link matches {want}; same type: {same_type}")
    if expect.get("links_exact"):
        for link_type in {w[conftest.LINK_TYPE_FIELD] for w in wanted}:
            got = [
                lk for lk in links if lk[conftest.LINK_TYPE_FIELD] == link_type
            ]
            count = sum(
                1 for w in wanted if w[conftest.LINK_TYPE_FIELD] == link_type
            )
            if len(got) != count:
                problems.append(
                    f"{len(got)} {link_type} links, expected {count}: {got}"
                )
    for absent in expect.get("links_absent") or []:
        hits = [
            lk for lk in links if not conftest.partial_mismatches(lk, absent)
        ]
        if hits:
            problems.append(f"unexpected links matching {absent}: {hits}")
    return problems


def _disposition_problems(
    rows: list[dict[str, Any]], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(row["id"], []).append(row)
    for item_id, want in (expect.get("dispositions") or {}).items():
        got = by_id.get(item_id, [])
        if len(got) != 1:
            problems.append(f"{item_id}: {len(got)} dispositions rows")
            continue
        if isinstance(want, str):
            want = {"disposition": want}
        for problem in conftest.partial_mismatches(got[0], want):
            problems.append(f"{item_id}: {problem}")
    return problems


def _path_problems(
    rows: list[dict[str, Any]], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    by_path = {}
    for row in rows:
        by_path.setdefault(row["path"], []).append(row)
    for want in expect.get("paths") or []:
        got = by_path.get(want["path"], [])
        if len(got) != 1:
            problems.append(f"{want['path']}: {len(got)} paths rows")
            continue
        for problem in conftest.partial_mismatches(got[0], want):
            problems.append(f"{want['path']}: {problem}")
    return problems


def _coverage_problems(
    coverage: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for key, want in (expect.get("coverage") or {}).items():
        try:
            got = conftest.dotted(coverage, key)
        except (KeyError, TypeError):
            problems.append(f"{key}: missing")
            continue
        if want is None:
            if got is not None:
                problems.append(f"{key}: {got!r}, expected null")
        elif not conftest.close(got, want):
            problems.append(f"{key}: {got!r} != expected {want!r}")
    return problems


def test_scenarios_exist() -> None:
    """The scenario directory is not empty and names match file stems."""
    assert len(SCENARIOS) >= 30
    for path in SCENARIOS:
        assert conftest.load_scenario(path)["name"] == path.stem


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario(path: pathlib.Path) -> None:
    """``match.link`` reproduces every expectation of the scenario."""
    scenario = conftest.load_scenario(path)
    result = conftest.run_link(scenario)
    assert set(result) == conftest.RESULT_KEYS
    expect = scenario["expect"]
    problems = (
        _link_problems(result["links"], expect)
        + _disposition_problems(result["dispositions"], expect)
        + _path_problems(result["paths"], expect)
        + _coverage_problems(result["coverage"], expect)
    )
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_is_deterministic(path: pathlib.Path) -> None:
    """Two runs on the same input give identical output (§1 rules)."""
    scenario = conftest.load_scenario(path)
    first = conftest.canonical(conftest.run_link(scenario))
    assert first == conftest.canonical(conftest.run_link(scenario))
