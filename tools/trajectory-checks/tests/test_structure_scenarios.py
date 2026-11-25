"""Layer 3: hand-written scenarios (structure spec §2.2.2).

One YAML file per scenario in ``tests/structure_scenarios/``, written as
layer-1 and layer-2 output records; the format and the defaults are
documented in ``structure_helpers.py``. Each scenario's expectations are
checked, then the independent oracle ``structure_invariants.py``. The rule
coverage checks, which need no layer-3 code, are in
``test_structure_rules.py``.
"""

import pathlib
from collections.abc import Mapping
from typing import Any

import conftest
import pytest
import structure_helpers
import structure_invariants

SCENARIOS = structure_helpers.scenario_paths()
SCENARIO_PARAMS = structure_helpers.scenario_params()
ROW_KEYS = (("call_structure", "call_id"), ("file_classes", "path"))


def _row_problems(
    result: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for block, id_key in ROW_KEYS:
        rows: dict[str, list[Mapping[str, Any]]] = {}
        for row in result[block]:
            rows.setdefault(row[id_key], []).append(row)
        for item_id, want in (expect.get(block) or {}).items():
            got = rows.get(item_id, [])
            if len(got) != 1:
                problems.append(f"{block}[{item_id}]: {len(got)} rows")
                continue
            for problem in conftest.partial_mismatches(got[0], want):
                problems.append(f"{block}[{item_id}]: {problem}")
    edges = result["reruns"]
    wanted = expect.get("reruns")
    for want in wanted or []:
        hits = [e for e in edges if not conftest.partial_mismatches(e, want)]
        if len(hits) != 1:
            near = [
                conftest.partial_mismatches(e, want)
                for e in edges
                if e["prev_call_id"] == want.get("prev_call_id")
                and e["call_id"] == want.get("call_id")
                and e["key_type"] == want.get("key_type", e["key_type"])
            ]
            problems.append(f"reruns: {len(hits)} rows match {want}; {near}")
    if expect.get("reruns_exact") and len(edges) != len(wanted or []):
        problems.append(f"{len(edges)} reruns, expected {len(wanted or [])}")
    present = {
        row[id_key] for block, id_key in ROW_KEYS for row in result[block]
    } | {edge["rerun_id"] for edge in edges}
    for item_id in expect.get("absent") or []:
        if item_id in present:
            problems.append(f"{item_id}: has a row, expected none")
    return problems


def _structure_problems(
    structure: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    problems = []
    for key, want in (expect.get("structure") or {}).items():
        try:
            got = structure_helpers.structure_value(structure, key)
        except KeyError:
            problems.append(f"structure {key}: missing")
            continue
        if not conftest.close(got, want):
            problems.append(f"structure {key}: {got!r} != expected {want!r}")
    warnings = structure["warnings"]
    wanted = expect.get("warnings") or []
    for text in wanted:
        hits = [w for w in warnings if text in w]
        if len(hits) != 1:
            problems.append(
                f"{len(hits)} warnings contain {text!r}: {warnings}"
            )
    if expect.get("warnings_exact") and len(warnings) != len(wanted):
        problems.append(
            f"{len(warnings)} warnings, expected {len(wanted)}: {warnings}"
        )
    return problems


@pytest.mark.parametrize("path", SCENARIO_PARAMS)
def test_scenario(path: pathlib.Path) -> None:
    """Each scenario's expectations hold, and so do the invariants."""
    scenario = structure_helpers.load_scenario(path)
    assert structure_helpers.validate(scenario, path.stem) == []
    result = structure_helpers.run_derive(scenario)
    assert set(result) == {
        "call_structure",
        "reruns",
        "file_classes",
        "structure",
    }
    expect = scenario["expect"]
    problems = _row_problems(result, expect)
    problems += _structure_problems(result["structure"], expect)
    problems += structure_invariants.check(scenario, result)
    assert problems == [], "\n".join(problems)


# Spec §5.1 to §5.3: the fields of every per-item row.
CALL_STRUCTURE_FIELDS = frozenset(
    {
        *("call_id", "seq", "call_class", "class_reason", "exact_class"),
        *("shape_class", "program_class", "program_key", "shape_index"),
        *("program_index", "prev_shape_call", "prev_program_call"),
    }
)
RERUN_FIELDS = frozenset(
    {
        *("rerun_id", "key_type", "class_id", "prev_call_id", "call_id"),
        *("prev_seq", "seq", "prev_outcome", "prev_match_under", "identical"),
        *("arg_change_type", "arg_count_delta", "numeric_changes"),
        *("numeric_direction", "words_removed", "words_added", "narrowed"),
        *("calls_between", "act_calls_between", "edit_calls_between"),
        *("writes_between_under", "writes_between_over"),
        *("untracked_writes_between", "gap_observed", "unchanged_under"),
        "unchanged_over",
    }
)
FILE_CLASS_FIELDS = frozenset(
    {
        *("path", "in_workspace", "extension", "file_class", "class_rule"),
        *("mode_source", "binary_content"),
    }
)
STRUCTURE_FIELDS = frozenset(
    {
        *("run_key", "family", "preconditions"),
        *("capabilities", "bases", "metrics", "warnings"),
    }
)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_record_fields(path: pathlib.Path) -> None:
    """Every row has exactly the fields of spec §5 (universal records)."""
    result = structure_helpers.run_derive(structure_helpers.load_scenario(path))
    for row in result["call_structure"]:
        assert set(row) == CALL_STRUCTURE_FIELDS, row
    for row in result["reruns"]:
        assert set(row) == RERUN_FIELDS, row
        assert all(
            set(change) == {"index", "from", "to"}
            for change in row["numeric_changes"]
        )
    for row in result["file_classes"]:
        assert set(row) == FILE_CLASS_FIELDS, row
    structure = result["structure"]
    assert set(structure) == STRUCTURE_FIELDS
    assert set(structure["metrics"]) == {"M2.1", "M2.2", "M2.3", "M4.2"}
    assert list(structure["capabilities"]) == sorted(structure["capabilities"])
    assert list(structure["bases"]) == sorted(structure["bases"])
