"""Layer 3: invariants and metamorphic tests on every scenario (§2.2.3).

The invariants are checked by the independent oracle
``structure_invariants.py``; here they run on every scenario and on a
shuffled copy, and each metamorphic transformation of spec §2.2.3 is
applied to every scenario and its output compared with the original's.
"""

import copy
import json
import pathlib
import random
import re
import types
from collections.abc import Mapping
from typing import Any

import conftest
import pytest
import structure_helpers
import structure_invariants

SCENARIOS = structure_helpers.scenario_paths()
# D51: the transformations that need a path or an exec run only on the
# scenarios that have one, decided from the YAML at collection (no layer-3
# import, D36). The other two decide by derive and skip instead.
WITH_PATHS = [
    p
    for p in SCENARIOS
    if structure_helpers.has_paths(structure_helpers.read_raw(p))
]
WITH_EXECS = [
    p
    for p in SCENARIOS
    if structure_helpers.has_execs(structure_helpers.read_raw(p))
]
# Floors on the number of scenarios each metamorphic transformation
# applies to (test_transformations_apply): a scenario edit that silently
# drops a transformation's reach fails a test instead of adding skips.
MIN_APPLICABLE = types.MappingProxyType(
    {"paths": 25, "execs": 55, "shape_edge": 40, "numeral": 20}
)
SHIFT_KEYS = frozenset({"t_start", "t_end", "released_at", "ended_at"}) | {
    "order_t"
}
SHIFT = 1024.0  # a power of two, so shifted times keep their order exactly
METRIC_SKIP = {
    # Fields a numeral change may alter (spec §2.2.3, fourth row).
    "M2.1": {"exact_repeat_max_act"},
    "M2.3": {
        *("args_unchanged", "numeric_changed", "numeric_decreased"),
        "numeric_increased",
    },
}
EDGE_NUMERAL_FIELDS = frozenset(
    {"numeric_changes", "numeric_direction", "arg_change_type", "identical"}
)
NAME_RULES = frozenset({"FC1", "FC2", "FC4", "FC6"})  # rules that read names


def _canonical(result: Any) -> str:
    return json.dumps(result, sort_keys=True)


def _derive(scenario: Mapping[str, Any]) -> Any:
    return structure_helpers.run_derive(scenario)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_invariants_hold(path: pathlib.Path) -> None:
    """The oracle's invariants hold on every scenario and on a shuffle."""
    scenario = structure_helpers.load_scenario(path)
    for variant in (scenario, _shuffled(scenario, 3)):
        problems = structure_invariants.check(variant, _derive(variant))
        assert problems == [], "\n".join(problems)


def _shuffled(scenario: Mapping[str, Any], seed: int) -> dict[str, Any]:
    # path_facts keeps its order: file_classes follows it (spec §5.3).
    out = copy.deepcopy(dict(scenario))
    rng = random.Random(seed)
    for key in ("calls", "execs"):
        rng.shuffle(out[key])
    for key in ("links", "dispositions", "paths"):
        rng.shuffle(out["linked"][key])
    for key in ("call_facts", "exec_facts"):
        rng.shuffle(out["facts_result"][key])
    if out["baseline"]:
        files = list(out["baseline"]["files"].items())
        rng.shuffle(files)
        out["baseline"]["files"] = dict(files)
    return out


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
@pytest.mark.parametrize("seed", [1, 2])
def test_shuffle_inputs(path: pathlib.Path, seed: int) -> None:
    """Shuffling every input list gives an identical output."""
    scenario = structure_helpers.load_scenario(path)
    assert _canonical(_derive(_shuffled(scenario, seed))) == _canonical(
        _derive(scenario)
    )


def _shift(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _shift(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift(v) for v in value]
    if key in SHIFT_KEYS and isinstance(value, int | float):
        if not isinstance(value, bool):
            return value + SHIFT
    return value


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_time_shift(path: pathlib.Path) -> None:
    """A common time shift changes nothing.

    A common time shift changes nothing: no output holds an absolute
    time.
    """
    scenario = structure_helpers.load_scenario(path)
    moved = copy.deepcopy(scenario)
    for key in ("calls", "execs", "run_info", "linked", "facts_result"):
        moved[key] = _shift(moved[key])
    assert _canonical(_derive(moved)) == _canonical(_derive(scenario))


def _rename(value: Any, names: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {names.get(k, k): _rename(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename(v, names) for v in value]
    if isinstance(value, tuple):
        return tuple(_rename(v, names) for v in value)
    if isinstance(value, str):
        return names.get(value, value)
    return value


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_rename_ids(path: pathlib.Path) -> None:
    """Renaming ids by a bijection renames the output only.

    Renaming call and command ids by a bijection renames the output and
    nothing else; class ids are unchanged.
    """
    scenario = structure_helpers.load_scenario(path)
    names = {
        c["call_id"]: f"call-{c['call_id'][::-1]}-x" for c in scenario["calls"]
    }
    names |= {
        e["command_id"]: f"exec-{e['command_id'][::-1]}"
        for e in scenario["execs"]
    }
    renamed = copy.deepcopy(scenario)
    for key in ("calls", "execs", "linked", "facts_result"):
        renamed[key] = _rename(renamed[key], names)
    expected = _rename(_derive(scenario), names)
    assert _canonical(_derive(renamed)) == _canonical(expected)


def _numeral_variant(
    path: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(original, variant) with one numeral of one act command replaced."""
    raw = structure_helpers.read_raw(path)
    base = structure_helpers.build_scenario(raw)
    rows = {r["call_id"]: r for r in _derive(base)["call_structure"]}
    overridden = set(raw.get("call_facts") or {})
    for index, call in enumerate(raw.get("calls") or []):
        text = call.get("command")
        row = rows.get(call["call_id"])
        if not text or row is None or row["call_class"] != "act":
            continue
        if call["call_id"] in overridden or "<<" in text:
            continue
        # A standalone numeral, not a file descriptor of a redirection.
        found = re.search(r"(?<![\w.$/&<>-])\d+(?![\w.&<>])", text)
        if not found:
            continue
        changed = text[: found.start()] + str(int(found[0]) + 7)
        changed += text[found.end() :]
        variant = copy.deepcopy(raw)
        variant["calls"][index]["command"] = changed
        variant["calls"][index].pop("command_norm", None)
        for record in variant.get("execs") or []:
            if record.get("call") == call["call_id"]:
                record.setdefault("command", text)
        return base, structure_helpers.build_scenario(variant)
    return None


def _without(value: Mapping[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in keys}


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_numeral_change(path: pathlib.Path) -> None:
    """Replacing one numeral keeps the shape and program classes.

    Replacing one numeral in one act command keeps the shape and
    program classes; only numeric_changes and the M2.3 numeric counts (and
    the text-equality fields that follow) may change.
    """
    pair = _numeral_variant(path)
    if pair is None:
        pytest.skip("no act command with a standalone numeral")
    before, after = (_derive(s) for s in pair)
    for old, new in zip(
        before["call_structure"], after["call_structure"], strict=True
    ):
        for field in ("shape_class", "program_class", "program_key"):
            assert new[field] == old[field], (old["call_id"], field)
        assert _without(new, {"exact_class"}) == _without(old, {"exact_class"})
    assert len(after["reruns"]) == len(before["reruns"])
    for old, new in zip(before["reruns"], after["reruns"], strict=True):
        assert _without(new, EDGE_NUMERAL_FIELDS) == _without(
            old, EDGE_NUMERAL_FIELDS
        )
    for block in ("M2.1", "M2.2", "M2.3", "M4.2"):
        skip = METRIC_SKIP.get(block, set())
        assert _without(after["structure"]["metrics"][block], skip) == (
            _without(before["structure"]["metrics"][block], skip)
        ), block


def _with_observe_call(
    raw: Mapping[str, Any], edge: Mapping[str, Any]
) -> dict[str, Any]:
    variant = copy.deepcopy(dict(raw))
    calls = variant["calls"]
    index = next(
        i for i, c in enumerate(calls) if c["call_id"] == edge["call_id"]
    )
    calls.insert(
        index,
        {
            "call_id": "zz-observe",
            "command": "tail -1 zz.log",
            "t_start": calls[index]["t_start"] - 0.001,
        },
    )
    return variant


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_insert_observe_call(path: pathlib.Path) -> None:
    """An inserted observe call only adds to calls_between.

    An observe call inserted between the two calls of an edge changes
    only calls_between (+1 for the edges around it) and the seqs.
    """
    raw = structure_helpers.read_raw(path)
    if any("seq" in c for c in raw.get("calls") or []):
        pytest.skip("explicit seq values")
    before = _derive(structure_helpers.build_scenario(raw))
    edges = [e for e in before["reruns"] if e["key_type"] == "shape"]
    if not edges:
        pytest.skip("no shape edge")
    after = _derive(
        structure_helpers.build_scenario(_with_observe_call(raw, edges[0]))
    )
    inserted = next(
        r for r in after["call_structure"] if r["call_id"] == "zz-observe"
    )
    assert inserted["call_class"] == "observe"
    new_seq = inserted["seq"]
    ignore = {"rerun_id", "class_id", "prev_seq", "seq", "calls_between"}
    by_pair = {
        (e["key_type"], e["prev_call_id"], e["call_id"]): e
        for e in after["reruns"]
    }
    assert len(by_pair) == len(before["reruns"])
    for old in before["reruns"]:
        new = by_pair[(old["key_type"], old["prev_call_id"], old["call_id"])]
        assert _without(new, ignore) == _without(old, ignore)
        inside = new["prev_seq"] < new_seq < new["seq"]
        assert new["calls_between"] == old["calls_between"] + int(inside)


def _renamed_paths(scenario: Mapping[str, Any]) -> tuple[dict, dict]:
    """Renames every path's basename, keeping extension and folders.

    A copy with every path's basename renamed, extension and folders
    kept; and the old-to-new map.
    """
    paths = {p["path"] for p in scenario["linked"]["paths"]}
    if scenario["baseline"]:
        paths |= set(scenario["baseline"]["files"])
    names = {}
    for i, old in enumerate(sorted(paths)):
        head, _, base = old.rpartition("/")
        stem, dot, ext = base.rpartition(".")
        suffix = f".{ext}" if dot and stem else ""
        new = f"renamed{i}{suffix}"
        names[old] = f"{head}/{new}" if head else new
    out = copy.deepcopy(dict(scenario))
    for link in out["linked"]["links"]:
        if link["link_type"] == "write_event":
            link["path"] = names.get(link["path"], link["path"])
    for row in (*out["linked"]["paths"], *out["facts_result"]["path_facts"]):
        row["path"] = names[row["path"]]
    out["facts_result"]["path_facts"].sort(key=lambda p: p["path"])
    if out["baseline"]:
        out["baseline"]["files"] = {
            names[k]: v for k, v in out["baseline"]["files"].items()
        }
    for record in out["execs"]:
        snapshot = record["snapshot"]
        for key in ("created", "modified", "deleted"):
            snapshot[key] = [names.get(p, p) for p in snapshot[key]]
    text = out["final_diff_text"]
    if text:
        for old in sorted(names, key=len, reverse=True):
            text = text.replace(f"a/{old}", f"a/{names[old]}")
            text = text.replace(f"b/{old}", f"b/{names[old]}")
        out["final_diff_text"] = text
    return out, names


@pytest.mark.parametrize("path", WITH_PATHS, ids=lambda p: p.stem)
def test_rename_basenames(path: pathlib.Path) -> None:
    """Renaming basenames keeps file classes and binary content.

    Renaming a path's basename, keeping its extension and folders,
    keeps its file class and binary content (rules that read the file name
    itself, FC1, FC2, FC4 and FC6, excepted).
    """
    scenario = structure_helpers.load_scenario(path)
    assert scenario["linked"]["paths"], "WITH_PATHS disagrees with the build"
    renamed, names = _renamed_paths(scenario)
    before = {r["path"]: r for r in _derive(scenario)["file_classes"]}
    after = {r["path"]: r for r in _derive(renamed)["file_classes"]}
    for old, row in before.items():
        if row["class_rule"] in NAME_RULES:
            continue
        new = after[names[old]]
        for field in (
            "file_class",
            "class_rule",
            "extension",
            "binary_content",
        ):
            assert new[field] == row[field], (old, field)


def _capabilities_off(raw: Mapping[str, Any]) -> dict[str, Any]:
    variant = copy.deepcopy(dict(raw))
    run = dict(variant.get("run") or {})
    run.pop("capabilities", None)
    for key in ("final_diff_content", "oracle_reads"):
        run.pop(key, None)
    variant["run"] = run
    variant.pop("final_diff_text", None)
    for record in variant.get("execs") or []:
        for field in structure_helpers.NEW_EXEC_FIELDS:
            record.pop(field, None)
        snapshot = record.get("snapshot")
        for field in structure_helpers.NEW_SNAPSHOT_FIELDS:
            if snapshot:
                snapshot.pop(field, None)
    return variant


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_capabilities_off(path: pathlib.Path) -> None:
    """Every capability off gives the fallback form (D30, D31).

    Every capability false and every new field None gives the fallback
    form: the bases fall back (unchanged_under stays exact only by scope),
    binary content and untracked counts are null, and no file changes
    class (D31). A scenario with every capability already false is
    unchanged.
    """
    raw = structure_helpers.read_raw(path)
    before = _derive(structure_helpers.build_scenario(raw))
    off = structure_helpers.build_scenario(_capabilities_off(raw))
    assert structure_helpers.validate(off, path.stem) == []
    after = _derive(off)
    structure = after["structure"]
    assert set(structure["capabilities"].values()) == {False}
    visible = bool((raw.get("coverage") or {}).get("rates.untracked_visible"))
    assert structure["bases"] == {
        "writes_between": "fallback",
        "gap": "fallback",
        "unchanged_under": "exact" if visible else "fallback",
    }
    assert not [w for w in structure["warnings"] if w.startswith("capability")]
    assert all(r["binary_content"] is None for r in after["file_classes"])
    assert all(r["untracked_writes_between"] is None for r in after["reruns"])
    for old, new in zip(
        before["file_classes"], after["file_classes"], strict=True
    ):
        assert (new["file_class"], new["class_rule"]) == (
            old["file_class"],
            old["class_rule"],
        )
    if not any((raw.get("run") or {}).get("capabilities", {}).values()):
        assert _canonical(after) == _canonical(before)


def _with_noise(scenario: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(scenario))
    template = out["execs"][0]
    noise = copy.deepcopy(template)
    noise.update(
        command_id="zz-noise",
        command="true",
        command_norm="true",
        noise="shim_probe",
        t_start=0.5,
        t_end=9999.0,
        duration_s=9998.5,
        seq=len(out["execs"]),
    )
    noise["snapshot"].update(status="skipped", skip_reason="too many files")
    out["execs"].append(noise)
    out["linked"]["dispositions"].append(
        {
            "item_type": "exec",
            "id": "zz-noise",
            "disposition": "noise",
            "reason": None,
            "matched": None,
            "match_under": None,
            "snapshot_ok": False,
            "root_under": False,
        }
    )
    return out


@pytest.mark.parametrize("path", WITH_EXECS, ids=lambda p: p.stem)
def test_noise_exec_changes_nothing(path: pathlib.Path) -> None:
    """A noise exec changes nothing.

    A noise exec spanning the run with a skipped snapshot changes
    nothing: RR5 and RR6 read non-noise execs only.
    """
    scenario = structure_helpers.load_scenario(path)
    assert scenario["execs"], "WITH_EXECS disagrees with the build"
    assert _canonical(_derive(_with_noise(scenario))) == _canonical(
        _derive(scenario)
    )


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_deterministic(path: pathlib.Path) -> None:
    """The same input twice gives byte-identical output.

    The same input twice gives byte-identical output, and derive does
    not modify its inputs.
    """
    scenario = structure_helpers.load_scenario(path)
    frozen = copy.deepcopy(scenario)
    first = _canonical(_derive(scenario))
    assert _canonical(_derive(scenario)) == first
    assert conftest.canonical(scenario) == conftest.canonical(frozen)


def test_derive_leaves_inputs_unchanged() -> None:
    """Derive never modifies the records it is given."""
    from trajectory_checks import structure

    scenario = structure_helpers.load_scenario(SCENARIOS[0])
    args = copy.deepcopy(scenario)
    structure.derive(
        args["calls"],
        args["execs"],
        args["linked"],
        args["facts_result"],
        run_info=args["run_info"],
        baseline=args["baseline"],
        final_diff_text=args["final_diff_text"],
        config=args["config"],
    )
    for key in ("calls", "execs", "linked", "facts_result", "run_info"):
        assert conftest.canonical(args[key]) == conftest.canonical(
            scenario[key]
        )


def _has_shape_edge(path: pathlib.Path) -> bool:
    raw = structure_helpers.read_raw(path)
    if any("seq" in c for c in raw.get("calls") or []):
        return False
    result = _derive(structure_helpers.build_scenario(raw))
    return any(e["key_type"] == "shape" for e in result["reruns"])


def test_transformations_apply() -> None:
    """Each metamorphic transformation reaches at least a floor of scenarios.

    D51: the path and exec transformations are filtered at collection and
    the shape-edge and numeral ones skip; either way a transformation that
    reaches too few scenarios tests too little.
    """
    reach = {
        "paths": len(WITH_PATHS),
        "execs": len(WITH_EXECS),
        "shape_edge": sum(map(_has_shape_edge, SCENARIOS)),
        "numeral": sum(_numeral_variant(p) is not None for p in SCENARIOS),
    }
    low = {
        k: (v, MIN_APPLICABLE[k])
        for k, v in reach.items()
        if v < MIN_APPLICABLE[k]
    }
    assert low == {}, f"(reach, floor) below the floor: {low}"
