"""Layer 2: metamorphic tests on every scenario (facts spec §2.2.3).

The invariants themselves run inside ``test_facts_scenarios.py`` through
the independent oracle ``facts_invariants.py``; here each scenario is
transformed and the output compared with the original's.
"""

import copy
import json
import pathlib
import random
import re
from collections.abc import Callable
from typing import Any

import facts_helpers
import facts_invariants
import pytest

SCENARIOS = facts_helpers.scenario_paths()
TIME_KEYS = frozenset({"t_start", "t_end"})
# Input fields holding absolute times (layer 1's universal Exec fields and
# the write-event ordering key included).
SHIFT_KEYS = TIME_KEYS | {"released_at", "ended_at", "order_t"}
SHIFT = 1000.0


def _canonical(result: Any) -> str:
    return json.dumps(result, sort_keys=True)


def _shuffled(scenario: dict[str, Any], seed: int) -> dict[str, Any]:
    out = copy.deepcopy(scenario)
    rng = random.Random(seed)
    for key in ("calls", "execs"):
        rng.shuffle(out[key])
    for key in ("links", "dispositions", "paths"):
        rng.shuffle(out["linked"][key])
    return out


def _shift(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _shift(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift(v) for v in value]
    if key in SHIFT_KEYS and isinstance(value, int | float):
        return value + SHIFT
    return value


def _shifted(scenario: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(scenario)
    for key in ("calls", "execs", "run"):
        out[key] = _shift(out[key])
    out["linked"]["links"] = _shift(out["linked"]["links"])
    return out


def _without_times(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _without_times(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_times(v) for v in value]
    return None if key in TIME_KEYS else value


def _rename(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {names.get(k, k): _rename(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename(v, names) for v in value]
    if isinstance(value, str):
        return names.get(value, value)
    return value


def _id_map(scenario: dict[str, Any]) -> dict[str, str]:
    names = {}
    for call in scenario["calls"]:
        names[call["call_id"]] = "call-" + call["call_id"][::-1] + "-x"
    for record in scenario["execs"]:
        names[record["command_id"]] = "exec-" + record["command_id"][::-1]
    return names


def _with_noise(scenario: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(scenario)
    noise = copy.deepcopy(out["execs"][0]) if out["execs"] else None
    if noise is None:
        return out
    noise.update(
        command_id="zz-noise",
        command="true",
        command_norm="true",
        noise="shim_probe",
        seq=len(out["execs"]),
    )
    out["execs"].append(noise)
    out["linked"]["dispositions"].append(
        {
            "item_type": "exec",
            "id": "zz-noise",
            "disposition": "noise",
            "reason": None,
            "matched": None,
            "match_under": None,
            "snapshot_ok": True,
            "root_under": False,
        }
    )
    if noise["env_keys"] is not None:
        noise["env_keys"] = ["ONLY_IN_NOISE"]
    return out


def _derive(scenario: dict[str, Any]) -> Any:
    return facts_helpers.run_derive(scenario)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
@pytest.mark.parametrize("seed", [1, 2])
def test_shuffle_inputs(path: pathlib.Path, seed: int) -> None:
    """Shuffling every input list gives an identical output."""
    scenario = facts_helpers.load_scenario(path)
    assert _canonical(_derive(_shuffled(scenario, seed))) == _canonical(
        _derive(scenario)
    )


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_time_shift(path: pathlib.Path) -> None:
    """A common time shift changes only absolute times."""
    scenario = facts_helpers.load_scenario(path)
    base = _without_times(_derive(scenario))
    moved = _without_times(_derive(_shifted(scenario)))
    assert _canonical(moved) == _canonical(base)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_rename_ids(path: pathlib.Path) -> None:
    """Renaming call and command ids renames the output and nothing else."""
    scenario = facts_helpers.load_scenario(path)
    names = _id_map(scenario)
    renamed = copy.deepcopy(scenario)
    for key in ("calls", "execs", "linked"):
        renamed[key] = _rename(renamed[key], names)
    expected = _rename(_derive(scenario), names)
    assert _canonical(_derive(renamed)) == _canonical(expected)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_add_noise(path: pathlib.Path) -> None:
    """A noise exec changes nothing."""
    scenario = facts_helpers.load_scenario(path)
    assert _canonical(_derive(_with_noise(scenario))) == _canonical(
        _derive(scenario)
    )


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_deterministic(path: pathlib.Path) -> None:
    """The same input twice gives byte-identical output."""
    scenario = facts_helpers.load_scenario(path)
    assert _canonical(_derive(scenario)) == _canonical(_derive(scenario))


def _replace_number(command: str) -> str:
    return re.sub(
        r"(?<![\w.-])\d+(?![\w.])",
        lambda m: str(int(m.group(0)) + 7),
        command,
        count=1,
    )


NUMBER_CASES: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("./bench --n 100 /repo/out/a", _replace_number),
    ("sleep 60; tail -3 out.log", _replace_number),
    ("python3 plot.py --iters 50", _replace_number),
)


@pytest.mark.parametrize(("command", "change"), NUMBER_CASES)
def test_number_change_keeps_shape(
    command: str, change: Callable[[str], str]
) -> None:
    """Replacing one numeral keeps the shape; only numeric_args changes."""
    from trajectory_checks import facts

    before = facts.command_shape(command)
    after = facts.command_shape(change(command))
    assert after[0] == before[0]
    assert after[2] == before[2]
    assert after[1] != before[1]


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_invariants_hold(path: pathlib.Path) -> None:
    """The oracle's invariants hold on every scenario (also on shuffles)."""
    scenario = facts_helpers.load_scenario(path)
    for variant in (scenario, _shuffled(scenario, 3)):
        problems = facts_invariants.check(variant, _derive(variant))
        assert problems == [], "\n".join(problems)


# (capability, bases key, the Exec fields its rules read).
CAPABILITY_FIELDS = (
    ("exact_timing", "timing", ("released_at", "ended_at")),
    ("parent_links", "depth", ("depth",)),
    ("start_events", "running_at_stop", ("record_type",)),
)
TIME_COUNTS = (
    "command_time_under_s",
    "command_time_over_s",
    "longest_span_under_s",
    "longest_span_over_s",
)


def _capability_off(scenario: dict[str, Any], name: str) -> dict[str, Any]:
    out = copy.deepcopy(scenario)
    fields = next(f for c, _, f in CAPABILITY_FIELDS if c == name)
    out["run"]["capabilities"][name] = False
    out["linked"]["coverage"]["capabilities"][name] = False
    for record in out["execs"]:
        record.update(dict.fromkeys(fields))
    return out


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_capability_off(path: pathlib.Path) -> None:
    """Turning a capability off switches its basis to the fallback.

    With exact_timing off no time count or duration decreases, since the
    fallback interval includes the snapshot time.
    """
    scenario = facts_helpers.load_scenario(path)
    for name, key, _ in CAPABILITY_FIELDS:
        if not scenario["run"]["capabilities"][name]:
            continue
        before = _derive(scenario)
        after = _derive(_capability_off(scenario, name))
        assert after["facts"]["bases"][key] == "fallback"
        assert after["facts"]["capabilities"][name] is False
        if name != "exact_timing":
            continue
        for field in TIME_COUNTS:
            old = before["facts"]["metrics"]["M1.1"][field]
            new = after["facts"]["metrics"]["M1.1"][field]
            assert old is None or new + 1e-9 >= old, field
        for old, new in zip(
            before["call_facts"], after["call_facts"], strict=True
        ):
            if old["duration_s"] is not None:
                assert new["duration_s"] + 1e-9 >= old["duration_s"]


def _rename_paths(scenario: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(scenario)

    def new(path: str) -> str:
        head, _, base = path.rpartition("/")
        return f"{head}/moved_{base}" if head else f"moved_{base}"

    for link in out["linked"]["links"]:
        if link["link_type"] == "write_event":
            link["path"] = new(link["path"])
    for row in out["linked"]["paths"]:
        row["path"] = new(row["path"])
    for record in out["execs"]:
        for key in ("created", "modified", "deleted"):
            record["snapshot"][key] = [new(p) for p in record["snapshot"][key]]
    return out


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_rename_paths(path: pathlib.Path) -> None:
    """Renaming every path (basenames kept distinct) keeps every count."""
    scenario = facts_helpers.load_scenario(path)
    before = _derive(scenario)["facts"]
    after = _derive(_rename_paths(scenario))["facts"]
    assert _canonical(after) == _canonical(before)
