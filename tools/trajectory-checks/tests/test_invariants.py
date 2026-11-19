"""Kind 3: invariants and metamorphic tests on the scenarios (spec §2.3).

``invariants.check_invariants`` is the independent oracle; the last group of
tests checks that the oracle itself catches broken outputs.
"""

import copy
import pathlib
import random
from collections.abc import Callable, Mapping
from typing import Any

import conftest
import invariants
import pytest

SCENARIOS = conftest.scenario_paths()
SHIFT_S = 3600.0
NOISE_COUNT_KEYS = (
    "execs_by_disposition",
    "execs_total",
    "snapshot_status_counts",
)


def _check(scenario: Mapping[str, Any], result: Mapping[str, Any], **kw: Any):
    return invariants.check_invariants(
        kw.get("calls", scenario["calls"]),
        kw.get("execs", scenario["execs"]),
        result,
        run=scenario["run"],
        final_diff=scenario["final_diff"],
    )


def _with_trace(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    return [p for p in paths if conftest.load_scenario(p)["run"]["trace_ok"]]


def _with_matches(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    selected = []
    for path in paths:
        scenario = conftest.load_scenario(path)
        listed = scenario["expect"].get("links") or []
        matched = {
            lk["command_id"] for lk in listed if lk["link_type"] == "call_exec"
        }
        busy = {lk.get("parent") for lk in listed} | {
            lk.get("of") for lk in listed
        }
        if scenario["run"]["trace_ok"] and matched - busy:
            selected.append(path)
    return selected


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_invariants_hold(path: pathlib.Path) -> None:
    """Every invariant of §2.3 holds on every scenario."""
    scenario = conftest.load_scenario(path)
    problems = _check(scenario, conftest.run_link(scenario))
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_shuffled_execs_give_identical_output(path: pathlib.Path) -> None:
    """The list order of the execs does not matter."""
    scenario = conftest.load_scenario(path)
    expected = conftest.canonical(conftest.run_link(scenario))
    for seed in (1, 2, 3):
        execs = copy.deepcopy(scenario["execs"])
        random.Random(seed).shuffle(execs)
        assert (
            conftest.canonical(conftest.run_link(scenario, execs=execs))
            == expected
        )


def _shift(records: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
    for record in records:
        for key in keys:
            if record.get(key) is not None:
                record[key] += SHIFT_S


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_time_shift_changes_only_absolute_times(path: pathlib.Path) -> None:
    """Shifting every time by one hour changes only absolute times."""
    scenario = conftest.load_scenario(path)
    expected = conftest.run_link(scenario)
    calls = copy.deepcopy(scenario["calls"])
    execs = copy.deepcopy(scenario["execs"])
    run = dict(scenario["run"])
    _shift(calls, ("t_start", "t_end"))
    _shift(execs, ("t_start", "t_end"))
    run["t_start"] += SHIFT_S
    run["t_end"] += SHIFT_S
    shifted = conftest.run_link(scenario, calls=calls, execs=execs, run=run)
    for link in shifted["links"]:
        if link.get(conftest.LINK_TYPE_FIELD) == "write_event":
            link["t_start"] -= SHIFT_S
            link["t_end"] -= SHIFT_S
            link["order_t"] -= SHIFT_S
    assert conftest.close(shifted, expected, tolerance=1e-5)


def _drop_noise_counts(coverage: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(coverage))
    for key in NOISE_COUNT_KEYS:
        out.get("counts", {}).pop(key, None)
    return out


@pytest.mark.parametrize("path", _with_trace(SCENARIOS), ids=lambda p: p.stem)
def test_added_noise_changes_only_noise_counts(path: pathlib.Path) -> None:
    """A new harness-noise record adds one noise row and nothing else."""
    scenario = conftest.load_scenario(path)
    expected = conftest.run_link(scenario)
    # A skipped snapshot, so the probe cannot change the run's snapshot
    # scope (C0.4 reads the first non-skipped snapshot).
    probe = conftest.make_exec(
        {
            "command_id": "noise-added",
            "t_start": scenario["run"]["t_start"] + 0.5,
            "duration_s": 0.01,
            "command": "echo AEBENCH_SHIM_PROBE_OK",
            "noise": "shim_probe",
            "snapshot": {
                "status": "skipped",
                "scope": None,
                "skip_reason": "workspace exceeds 20000 files",
            },
        },
        len(scenario["execs"]),
        scenario["run"]["workspace_root"],
    )
    execs = copy.deepcopy(scenario["execs"]) + [probe]
    conftest.assign_exec_seq(execs)
    result = conftest.run_link(scenario, execs=execs)
    assert conftest.canonical(result["links"]) == conftest.canonical(
        expected["links"]
    )
    assert conftest.canonical(result["paths"]) == conftest.canonical(
        expected["paths"]
    )
    rows = [r for r in result["dispositions"] if r["id"] != "noise-added"]
    assert conftest.canonical(rows) == conftest.canonical(
        expected["dispositions"]
    )
    if scenario["run"]["trace_ok"]:
        added = [r for r in result["dispositions"] if r["id"] == "noise-added"]
        assert [r["disposition"] for r in added] == ["noise"]
        before = expected["coverage"]["counts"]["execs_by_disposition"]
        after = result["coverage"]["counts"]["execs_by_disposition"]
        assert after["noise"] == before["noise"] + 1
    assert conftest.close(
        _drop_noise_counts(result["coverage"]),
        _drop_noise_counts(expected["coverage"]),
    )
    assert _check(scenario, result, execs=execs) == []


def _matches(result: Mapping[str, Any]) -> dict[str, str]:
    return {
        lk["call_id"]: lk["command_id"]
        for lk in result["links"]
        if lk[conftest.LINK_TYPE_FIELD] == "call_exec"
    }


@pytest.mark.parametrize("path", _with_matches(SCENARIOS), ids=lambda p: p.stem)
def test_deleting_a_matched_exec(path: pathlib.Path) -> None:
    """Deleting a matched record unmatches one call or re-matches it."""
    scenario = conftest.load_scenario(path)
    result = conftest.run_link(scenario)
    before = _matches(result)
    busy = {
        lk.get("parent")
        for lk in result["links"]
        if lk[conftest.LINK_TYPE_FIELD] == "exec_parent"
    } | {
        lk.get("of")
        for lk in result["links"]
        if lk[conftest.LINK_TYPE_FIELD] == "wrapper_duplicate"
    }
    victims = sorted(e for e in before.values() if e not in busy)
    if not victims:
        pytest.skip("every matched record has children or a duplicate")
    victim = victims[0]
    execs = [e for e in scenario["execs"] if e["command_id"] != victim]
    execs = copy.deepcopy(execs)
    conftest.assign_exec_seq(execs)
    after = _matches(conftest.run_link(scenario, execs=execs))
    assert len(after) in (len(before), len(before) - 1)
    norm = {e["command_id"]: e["command_norm"] for e in scenario["execs"]}
    for call_id, command_id in after.items():
        old = before.get(call_id)
        if old != command_id:
            assert old is not None, f"{call_id} matched only after deletion"
            assert norm[command_id] == norm[old], (call_id, old, command_id)
    assert (
        _check(scenario, conftest.run_link(scenario, execs=execs), execs=execs)
        == []
    )


# ---- The oracle itself must catch broken outputs. --------------------------

ORACLE_CALLS = (
    {
        "call_id": "c1",
        "tool_type": "shell",
        "t_start": 100.0,
        "t_end": 101.0,
        "command": "./run.sh",
        "command_norm": "./run.sh",
    },
)
ORACLE_EXECS = (
    {
        "command_id": "e1",
        "t_start": 100.3,
        "t_end": 100.9,
        "complete": True,
        "command": "./run.sh",
        "command_norm": "./run.sh",
        "noise": None,
        "snapshot": {"status": "ok"},
    },
    {
        "command_id": "n1",
        "t_start": 100.4,
        "t_end": 100.5,
        "complete": True,
        "command": "touch a.txt",
        "command_norm": "touch a.txt",
        "noise": None,
        "snapshot": {"status": "ok"},
    },
)
ORACLE_RUN = {
    "t_start": 0.0,
    "t_end": 1000.0,
    "workspace_root": "/repo",
    "trace_ok": True,
}
ORACLE_DIFF = {"modified": ["a.txt"], "deleted": [], "mode_changed": []}


def _good_result() -> dict[str, Any]:
    return {
        "links": [
            {
                "link_type": "call_exec",
                "call_id": "c1",
                "command_id": "e1",
                "rule": "exact",
                "dt_s": 0.3,
            },
            {
                "link_type": "exec_parent",
                "command_id": "n1",
                "parent": "e1",
                "root_call_id": "c1",
                "rule": "time_containment",
                "root_ambiguous": False,
                "root_candidates": [],
            },
            {
                "link_type": "write_event",
                "event_id": "w0",
                "path": "a.txt",
                "in_workspace": True,
                "op": "modify",
                "scope": "tracked",
                "writer_type": "exec",
                "writer_id": "n1",
                "t_start": 100.4,
                "t_end": 100.5,
                "order_t": 100.5,
                "root_call_id": "c1",
                "root_ambiguous": False,
                "root_candidates": [],
                "depth_rule": "none",
                "possibly_edit_call": None,
                "overlaps_sibling": False,
                "source": "snapshot",
                "writer_under": True,
            },
        ],
        "dispositions": [
            {
                "item_type": "call",
                "id": "c1",
                "disposition": "matched",
                "reason": None,
                "matched": True,
                "match_under": True,
            },
            {
                "item_type": "exec",
                "id": "e1",
                "disposition": "matched_root",
                "reason": None,
                "snapshot_ok": True,
                "root_under": True,
            },
            {
                "item_type": "exec",
                "id": "n1",
                "disposition": "nested",
                "reason": None,
                "snapshot_ok": True,
                "root_under": True,
            },
        ],
        "paths": [
            {
                "path": "a.txt",
                "in_workspace": True,
                "tracked": True,
                "events": ["w0"],
                "final_writer": "w0",
                "final_writer_under": True,
                "final_state": "changed",
                "unattributed_candidates": [],
            },
        ],
        "coverage": {
            "rates": {"call_match_rate_under": 1.0, "reverted_changes": 0},
            "preconditions": {
                "C1_under": "holds",
                "C2_under": "vacuous",
                "C6": "vacuous",
                "C7_under": "vacuous",
            },
            "warnings": [],
        },
    }


def _oracle(result: Mapping[str, Any]) -> list[str]:
    return invariants.check_invariants(
        ORACLE_CALLS,
        ORACLE_EXECS,
        result,
        run=ORACLE_RUN,
        final_diff=ORACLE_DIFF,
    )


def test_oracle_accepts_good_result() -> None:
    """The hand-made correct result has no violations."""
    assert _oracle(_good_result()) == []


def _link(result: dict[str, Any], link_type: str) -> dict[str, Any]:
    return next(lk for lk in result["links"] if lk["link_type"] == link_type)


def _duplicate_row(r: dict[str, Any]) -> None:
    r["dispositions"].append(dict(r["dispositions"][1]))


def _drop_exec_row(r: dict[str, Any]) -> None:
    del r["dispositions"][2]


def _match_twice(r: dict[str, Any]) -> None:
    r["links"].insert(
        1,
        {
            "link_type": "call_exec",
            "call_id": "c1",
            "command_id": "n1",
            "rule": "exact",
            "dt_s": 0.4,
        },
    )


def _wrong_dt(r: dict[str, Any]) -> None:
    _link(r, "call_exec")["dt_s"] = 1.3


def _parent_cycle(r: dict[str, Any]) -> None:
    r["links"].insert(
        2,
        {
            "link_type": "exec_parent",
            "command_id": "e1",
            "parent": "n1",
            "root_call_id": "c1",
            "rule": "time_containment",
            "root_ambiguous": False,
            "root_candidates": [],
        },
    )


def _start_outside_parent(r: dict[str, Any]) -> None:
    _link(r, "exec_parent")["parent"] = "missing"


def _ambiguous_without_candidates(r: dict[str, Any]) -> None:
    _link(r, "exec_parent")["root_ambiguous"] = True


def _final_writer_not_event(r: dict[str, Any]) -> None:
    r["paths"][0]["final_writer"] = "w9"


def _missing_final_diff_row(r: dict[str, Any]) -> None:
    r["paths"] = []


def _bad_rate(r: dict[str, Any]) -> None:
    r["coverage"]["rates"]["call_match_rate_under"] = 1.5


def _unknown_writer(r: dict[str, Any]) -> None:
    _link(r, "write_event")["writer_id"] = "ghost"


def _bad_event_id(r: dict[str, Any]) -> None:
    _link(r, "write_event")["event_id"] = "we0000"
    r["paths"][0]["events"] = ["we0000"]
    r["paths"][0]["final_writer"] = "we0000"


def _links_out_of_order(r: dict[str, Any]) -> None:
    r["links"].reverse()


def _noise_disposition_on_agent_exec(r: dict[str, Any]) -> None:
    r["dispositions"][2]["disposition"] = "noise"


def _matched_without_link(r: dict[str, Any]) -> None:
    r["links"] = [lk for lk in r["links"] if lk["link_type"] != "call_exec"]


def _extended_interval(r: dict[str, Any]) -> None:
    _link(r, "write_event")["t_end"] = 1000.0


def _delete_final_on_existing(r: dict[str, Any]) -> None:
    _link(r, "write_event")["op"] = "delete"


def _bad_precondition_state(r: dict[str, Any]) -> None:
    r["coverage"]["preconditions"]["C6"] = "maybe"


def _holds_on_null_rate(r: dict[str, Any]) -> None:
    r["coverage"]["preconditions"]["C7_under"] = "holds"


def _vacuous_with_warning(r: dict[str, Any]) -> None:
    r["coverage"]["warnings"] = ["precondition C6 = 1 failed (null): M4 null"]


def _parent_id_not_recorded(r: dict[str, Any]) -> None:
    _link(r, "exec_parent")["rule"] = "parent_id"


BREAKERS: tuple[Callable[[dict[str, Any]], None], ...] = (
    _duplicate_row,
    _drop_exec_row,
    _match_twice,
    _wrong_dt,
    _parent_cycle,
    _start_outside_parent,
    _ambiguous_without_candidates,
    _final_writer_not_event,
    _missing_final_diff_row,
    _bad_rate,
    _unknown_writer,
    _bad_event_id,
    _links_out_of_order,
    _noise_disposition_on_agent_exec,
    _matched_without_link,
    _extended_interval,
    _delete_final_on_existing,
    _bad_precondition_state,
    _holds_on_null_rate,
    _vacuous_with_warning,
    _parent_id_not_recorded,
)


@pytest.mark.parametrize("breaker", BREAKERS, ids=lambda f: f.__name__[1:])
def test_oracle_catches_broken_result(
    breaker: Callable[[dict[str, Any]], None],
) -> None:
    """Each hand-made violation is reported."""
    result = _good_result()
    breaker(result)
    assert _oracle(result) != []


def test_oracle_catches_window_violation() -> None:
    """A pair whose exec starts 6 s before the call breaks invariant 3."""
    execs = copy.deepcopy(list(ORACLE_EXECS))
    execs[0]["t_start"] = 94.0
    result = _good_result()
    _link(result, "call_exec")["dt_s"] = -6.0
    problems = invariants.check_invariants(
        ORACLE_CALLS, execs, result, run=ORACLE_RUN, final_diff=ORACLE_DIFF
    )
    assert any("window" in p for p in problems)


def test_oracle_catches_reparenting_to_non_background_root() -> None:
    """A reparenting link to a root that backgrounds nothing is reported."""
    execs = copy.deepcopy(list(ORACLE_EXECS))
    execs[1]["t_start"], execs[1]["t_end"] = 200.0, 201.0
    result = _good_result()
    link = _link(result, "exec_parent")
    link["rule"], link["parent"] = "reparenting", None
    result["links"] = [
        lk for lk in result["links"] if lk["link_type"] != "write_event"
    ]
    result["paths"] = [
        {
            "path": "a.txt",
            "events": [],
            "final_writer": None,
            "final_writer_under": False,
        }
    ]
    problems = invariants.check_invariants(
        ORACLE_CALLS, execs, result, run=ORACLE_RUN, final_diff=ORACLE_DIFF
    )
    assert any("backgrounds nothing" in p for p in problems)


# ---- Universal records (D26, D28): old fields only give the fallback. -----

NEW_EXEC_FIELDS = (
    *("schema_version", "parent_command_id", "root_command_id", "depth"),
    *("ppid", "pgid", "sid", "parent_link", "record_type", "released_at"),
    *("ended_at", "snapshot_before_s", "snapshot_after_s", "reads"),
    "orphans",
)
NEW_SNAPSHOT_FIELDS = (
    *("hashes", "created_untracked", "modified_untracked"),
    "deleted_untracked",
)


def _strip(execs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Removes every D26 field, as if the trace were in the old format."""
    stripped = copy.deepcopy(execs)
    for record in stripped:
        record.update(dict.fromkeys(NEW_EXEC_FIELDS))
        record["env_keys"] = None
        record["snapshot"].update(dict.fromkeys(NEW_SNAPSHOT_FIELDS))
    return stripped


def _parent_links(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    return [
        p
        for p in paths
        if conftest.load_scenario(p)["run"]["capabilities"]["parent_links"]
    ]


@pytest.mark.parametrize("path", _parent_links(SCENARIOS), ids=lambda p: p.stem)
def test_old_fields_give_the_time_based_fallback(path: pathlib.Path) -> None:
    """Stripping D26 fields equals keeping them with every parent unknown.

    Both must give exactly the links of time containment and reparenting:
    ``parent_id`` falls back per exec when ``parent_command_id`` is None.
    """
    scenario = conftest.load_scenario(path)
    old_run = copy.deepcopy(scenario["run"])
    old_run["capabilities"] = dict.fromkeys(old_run["capabilities"], False)
    stripped = conftest.run_link(
        scenario, execs=_strip(scenario["execs"]), run=old_run
    )
    unknown = copy.deepcopy(scenario["execs"])
    for record in unknown:
        record["parent_command_id"] = None
    fallback = conftest.run_link(scenario, execs=unknown)
    assert conftest.canonical(fallback["links"]) == conftest.canonical(
        stripped["links"]
    )
    assert conftest.canonical(fallback["paths"]) == conftest.canonical(
        stripped["paths"]
    )
    assert not [lk for lk in stripped["links"] if lk.get("rule") == "parent_id"]
    with_ids = conftest.run_link(scenario)
    assert [lk for lk in with_ids["links"] if lk.get("rule") == "parent_id"]
