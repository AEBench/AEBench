"""Layer 4, P0: hand-written scenarios and metamorphic tests (spec §0.6).

One YAML file per scenario in ``tests/oracle_inputs_scenarios/``; the
format and the defaults are in ``oracle_inputs_helpers.py``. Each
scenario's expectations are checked, then the independent invariants of
``input_state_invariants.check_oracle_inputs``, then the metamorphic
relations of §0.6. The rule coverage checks, which need no layer-4 code,
are in ``test_oracle_inputs_rules.py``.
"""

import copy
import json
import pathlib
import random
from collections.abc import Mapping
from typing import Any

import conftest
import input_state_invariants
import oracle_inputs_helpers as helpers
import pytest

SCENARIOS = helpers.scenario_paths()
SCENARIO_PARAMS = helpers.scenario_params()


def _row_problems(
    rows: list[Mapping[str, Any]], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    wanted = expect.get("oracle_inputs")
    for want in wanted or []:
        hits = [r for r in rows if not conftest.partial_mismatches(r, want)]
        if len(hits) != 1:
            near = [
                conftest.partial_mismatches(r, want)
                for r in rows
                if r["path"] == want.get("path", r["path"])
            ]
            problems.append(f"{len(hits)} rows match {want}; {near}")
    if expect.get("oracle_inputs_exact") and len(rows) != len(wanted or []):
        paths = [r["path"] for r in rows]
        problems.append(f"{len(rows)} rows {paths}, expected {len(wanted)}")
    present = {row["path"] for row in rows}
    for path in expect.get("absent") or []:
        if path in present:
            problems.append(f"{path}: has a row, expected none")
    return problems


def _block_problems(
    block: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for key, want in (expect.get("block") or {}).items():
        try:
            got = helpers.block_value(block, key)
        except KeyError:
            problems.append(f"block {key}: missing")
            continue
        if not conftest.close(got, want):
            problems.append(f"block {key}: {got!r} != expected {want!r}")
    warnings = block["warnings"]
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
    result = helpers.run_derive(scenario)
    assert set(result) == {"oracle_inputs", "oracle_inputs_run"}
    expect = scenario["expect"]
    problems = _row_problems(result["oracle_inputs"], expect)
    problems += _block_problems(result["oracle_inputs_run"], expect)
    problems += input_state_invariants.check_oracle_inputs(scenario, result)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_record_fields(path: pathlib.Path) -> None:
    """Every row and the block have exactly the fields of spec §0.3.

    Every key is written in every run, as null when its precondition
    fails; capabilities are copied from coverage.json.
    """
    scenario = helpers.load_scenario(path)
    result = helpers.run_derive(scenario)
    for row in result["oracle_inputs"]:
        assert set(row) == helpers.ROW_FIELDS, sorted(set(row))
    block = result["oracle_inputs_run"]
    assert set(block) == helpers.BLOCK_FIELDS
    assert block["schema_version"] == "1"
    assert block["run_key"] == scenario["run"]["run_key"]
    assert block["family"] == scenario["run"]["family"]
    assert (
        block["capabilities"] == scenario["linked"]["coverage"]["capabilities"]
    )
    assert set(block["bases"]) == helpers.BASES_KEYS
    assert set(block["preconditions"]) == set(helpers.PRECONDITION_LABELS)
    assert set(block["preconditions"].values()) <= {"holds", "vacuous", "fails"}
    assert set(block["metrics"]) == {"M3.1", "M3.5"}
    assert set(block["metrics"]["M3.1"]) == helpers.M31_KEYS
    assert set(block["metrics"]["M3.5"]) == helpers.M35_KEYS


def _canonical(result: Mapping[str, Any]) -> str:
    return conftest.canonical(
        {
            "rows": result["oracle_inputs"],
            "block": {
                **result["oracle_inputs_run"],
                "warnings": sorted(result["oracle_inputs_run"]["warnings"]),
            },
        }
    )


def _with_manifests(scenario: Mapping[str, Any], change: Any) -> dict[str, Any]:
    changed = copy.deepcopy(dict(scenario))
    for key in ("graded_manifest", "null_manifest"):
        if changed[key] is not None:
            changed[key] = change(changed[key])
    return changed


def _key(record: Mapping[str, Any]) -> tuple:
    return (
        record["resolved_workspace_path"] or record["path"],
        record["glob_pattern"],
    )


def _shuffle_keeping_first(
    records: list[dict[str, Any]], rng: random.Random
) -> list[dict[str, Any]]:
    """Shuffles a manifest so each key's first record stays its first."""
    first: dict[tuple, int] = {}
    for index, record in enumerate(records):
        if record["path"] is not None:
            first.setdefault(_key(record), index)
    order = list(range(len(records)))
    rng.shuffle(order)
    for index in first.values():
        key = _key(records[index])
        same = [
            pos
            for pos, i in enumerate(order)
            if records[i]["path"] is not None and _key(records[i]) == key
        ]
        here = order.index(index)
        order[here], order[same[0]] = order[same[0]], order[here]
    return [copy.deepcopy(records[i]) for i in order]


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_shuffled_records_keep_the_output(path: pathlib.Path) -> None:
    """Shuffling records keeping each path's first record gives the output.

    No output depends on where a run_process record falls, nor on the
    order of a path's later records (warnings compared as a multiset).
    Rules: OI3, OI6, OI7.
    """
    scenario = helpers.load_scenario(path)
    base = _canonical(helpers.run_derive(scenario))
    for seed in range(3):
        rng = random.Random(seed)
        changed = _with_manifests(
            scenario, lambda m, r=rng: _shuffle_keeping_first(m, r)
        )
        assert _canonical(helpers.run_derive(changed)) == base, seed


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_duplicated_record_keeps_the_output(path: pathlib.Path) -> None:
    """Duplicating a record with a path gives identical output.

    A run_process record is not duplicated: it counts in command_checks.
    Rules: OI3, OI4, OI8.
    """
    scenario = helpers.load_scenario(path)
    base = _canonical(helpers.run_derive(scenario))

    def duplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with_path = [i for i, r in enumerate(records) if r["path"]]
        if not with_path:
            return records
        index = with_path[-1]
        return [*records, copy.deepcopy(records[index])]

    changed = _with_manifests(scenario, duplicate)
    assert _canonical(helpers.run_derive(changed)) == base


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_renamed_checks_are_renamed(path: pathlib.Path) -> None:
    """Renaming check ids by an order-keeping bijection renames them.

    Rules: OI3, OI6.
    """
    scenario = helpers.load_scenario(path)
    base = helpers.run_derive(scenario)

    def rename(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for record in records:
            record["check_id"] = "z_" + record["check_id"]
        return records

    changed = helpers.run_derive(_with_manifests(scenario, rename))
    expected = copy.deepcopy(base["oracle_inputs"])
    for row in expected:
        row["checks"] = ["z_" + check for check in row["checks"]]
    assert changed["oracle_inputs"] == expected
    assert (
        changed["oracle_inputs_run"]["metrics"]
        == (base["oracle_inputs_run"]["metrics"])
    )


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_shuffled_paths_and_final_files(path: pathlib.Path) -> None:
    """Shuffling final_files and paths.jsonl gives identical output.

    Rules: OI5, OI10.
    """
    scenario = helpers.load_scenario(path)
    base = _canonical(helpers.run_derive(scenario))
    for seed in range(3):
        changed = copy.deepcopy(scenario)
        rng = random.Random(seed)
        rng.shuffle(changed["linked"]["paths"])
        if changed["final_files"]:
            rng.shuffle(changed["final_files"])
        assert _canonical(helpers.run_derive(changed)) == base


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_twice_byte_identical(path: pathlib.Path) -> None:
    """Running twice gives byte-identical output; inputs are not modified.

    Rules: OI7.
    """
    scenario = helpers.load_scenario(path)
    before = copy.deepcopy(scenario)
    own = {
        key: scenario[key] for key in ("linked", "run", *helpers.DERIVE_ARGS)
    }
    first = json.dumps(helpers.run_derive(scenario, **own), sort_keys=True)
    second = json.dumps(helpers.run_derive(scenario, **own), sort_keys=True)
    assert first == second
    assert scenario == before
