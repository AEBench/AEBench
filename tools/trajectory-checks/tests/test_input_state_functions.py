"""Layer 4, P1-P6: table-driven tests of the pure functions (§2.2.1).

Every expected value is written by hand from the rules of spec §6. Each
test names the rules it exercises on a ``Rules:`` docstring line, which
``test_input_state_rules.py`` reads without importing this file. The
module under test is imported inside each test (D44).
"""

import builtins
import itertools
import os
import pathlib
import subprocess
import sys
from typing import Any

import conftest
import input_state_helpers as helpers
import pytest

FULL = frozenset(helpers.STATES)
NO_ORACLE = FULL - {"oracle_written"}


def _module() -> Any:
    from trajectory_checks import input_state

    return input_state


# ----------------------------------------------------------- lookup tables


def test_tables() -> None:
    """The lookup tables of §3.3 hold the spec's values, in its order.

    Rules: CNT1, CON13, CNT3, PHS1, PHS3.
    """
    module = _module()
    assert tuple(module.STATES) == helpers.STATES
    assert tuple(module.UNDER_REASONS) == helpers.UNDER_REASONS
    assert tuple(module.PHASE_FLAGS) == helpers.PHASE_FLAGS
    assert set(module.CONTENT_ACCESS) == {"read", "command"}
    assert {k: set(v) for k, v in module.PREDICATES.items()} == {
        "baseline_content": {"pristine", "rewritten_identical"},
        "fresh": {
            *("rewritten_identical", "edit_written", "command_written"),
            "oracle_written",
        },
        "agent_written": {"edit_written", "command_written"},
        "edit_written": {"edit_written"},
        "command_written": {"command_written"},
        "absent": {"deleted", "missing"},
    }
    assert dict(module.DEFAULTS) == {"passing_statuses": ("success",)}
    assert {k: tuple(v) for k, v in module.DICT_FIELDS.items()} == {
        "by_state_under": helpers.STATES,
        "by_state_over": helpers.STATES,
    }
    keys = {row[0] for row in module.CAPABILITY_RULES}
    assert keys >= helpers.BASES_KEYS


# -------------------------------------------------------------- input_rows


def _source(**fields: Any) -> dict[str, Any]:
    return helpers.make_input_row(fields, 0, None, "/repo")


def _input_rows_case() -> list[dict[str, Any]]:
    rows = [
        _source(
            path="/refs/x.json",
            input_scope="reference",
            oracle_writes=None,
            oracle_writes_basis=None,
        ),
        _source(path="out", pattern="**/*.csv", checks=["all"]),
        _source(path="out/sub", pattern="*.csv", checks=["sub"]),
        _source(
            path="out/sub/a.csv",
            listed_by=["i1", "i2"],
            access_types=["list", "read"],
            checks=["all", "read_a", "sub"],
            phases=["benchmark_prep", "experiment_runs"],
        ),
        _source(
            path="out/sub/b.csv",
            match_type="pattern_match",
            listed_by=["i2"],
            checks=["sub"],
        ),
        _source(path="out/sub", access_types=["stat"], checks=["dir"]),
    ]
    for index, row in enumerate(rows):
        row["input_id"] = f"i{index}"
    return rows


def test_input_rows() -> None:
    """P2: references dropped and counted, listings and their members.

    A pattern row with a literal and a pattern_match member; a literal
    listed by two patterns (member_of both); a folder that is both a
    pattern row and a stat-ed file (two rows); checks copied as check_ids.
    Rules: INP1, INP2, INP3, INP4.
    """
    rows, dropped = _module().input_rows(_input_rows_case())
    assert dropped == 1
    got = {(r["path"], r["input_type"], r["pattern"]): r for r in rows}
    assert set(got) == {
        ("out", "listing", "**/*.csv"),
        ("out/sub", "listing", "*.csv"),
        ("out/sub", "file", None),
        ("out/sub/a.csv", "file", None),
        ("out/sub/b.csv", "file", None),
    }
    a = got[("out/sub/a.csv", "file", None)]
    assert a["member_of"] == ["out", "out/sub"]
    assert a["members"] == []
    assert a["check_ids"] == ["all", "read_a", "sub"]
    assert a["access_types"] == ["list", "read"]
    assert a["phases"] == ["benchmark_prep", "experiment_runs"]
    assert (a["input_id"], a["in_under"], a["source_type"]) == (
        "i3",
        True,
        "null_agent",
    )
    assert a["input_scope"] == "workspace"
    b = got[("out/sub/b.csv", "file", None)]
    assert (b["member_of"], b["in_under"]) == (["out/sub"], False)
    folder = got[("out/sub", "file", None)]
    assert (folder["member_of"], folder["members"]) == ([], [])
    assert folder["access_types"] == ["stat"]
    assert folder["check_ids"] == ["dir"]
    top = got[("out", "listing", "**/*.csv")]
    assert top["members"] == ["out/sub/a.csv"]
    assert top["input_id"] == "i1"
    assert top["check_ids"] == ["all"]
    sub = got[("out/sub", "listing", "*.csv")]
    assert sub["members"] == ["out/sub/a.csv", "out/sub/b.csv"]
    assert sub["member_of"] == []
    for row in rows:
        assert row["input_scope"] != "reference"


# A pattern row listing every second one of 20,000 file rows (S1).
_MANY_INPUTS = """
import input_state_helpers as helpers
from trajectory_checks import input_state

rows = []
for n in range(20000):
    fields = {"path": f"out/f{n:05d}.csv", "listed_by": ["i0"] if n % 2 else []}
    if n == 0:
        fields = {"path": "out", "pattern": "*.csv", "access_types": ["list"]}
    row = helpers.make_input_row(fields, n, None, "/repo")
    row["input_id"] = f"i{n}"
    rows.append(row)
out, _ = input_state.input_rows(rows)
assert len(out) == 20000 and len(out[-1]["members"]) == 10000
"""


def test_input_rows_scales() -> None:
    """20,000 inputs go through input_rows in well under 5 s (S1).

    members is built in one pass over listed_by, not once per row (today
    quadratic: about 30 s here). Run in a child process stopped at 5 s.
    Rules: INP3, INP4.
    """
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(conftest.TESTS_DIR.parent / d) for d in ("src", "tests")
        ),
    }
    try:
        subprocess.run(
            [sys.executable, "-c", _MANY_INPUTS],
            env=env,
            cwd=conftest.TESTS_DIR,
            check=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("input_rows took more than 5 s on 20,000 inputs")


# --------------------------------------------------------- content_compare

COMPARES = (
    ((True, "aa", True, "aa"), None, "equal"),
    ((True, "aa", True, "AA"), None, "equal"),
    ((True, "aa", True, "ab"), None, "differs"),
    ((False, None, True, "ab"), None, "differs"),
    ((True, "aa", False, None), None, "differs"),
    ((False, None, False, None), None, None),
    ((True, "aa", True, None), None, None),
    ((True, None, True, "ab"), None, None),
    ((None, None, True, "ab"), None, None),
    ((True, "aa", True, None), "equal", "equal"),
    ((True, "aa", True, None), "differs", "differs"),
)


@pytest.mark.parametrize(("args", "diff", "want"), COMPARES)
def test_content_compare(
    args: tuple, diff: str | None, want: str | None
) -> None:
    """EVD3: equal, differs or null; the final diff's answer when given.

    Hashes are compared as lowercase hex (§6.8).
    Rules: EVD3.
    """
    assert _module().content_compare(*args, diff_cmp=diff) == want


# ---------------------------------------------------------- detached_execs


def _execs(*raw: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        conftest.make_exec({"t_start": 10.0 * (i + 1), **r}, i, "/repo")
        for i, r in enumerate(raw)
    ]
    conftest.assign_exec_seq(records)
    return records


def _caps(**on: bool) -> dict[str, bool]:
    return {**dict.fromkeys(conftest.CAPABILITIES, False), **on}


def test_detached_fallback() -> None:
    """EVD8 fallback: syntax, incomplete and reparented execs.

    A command ending in &, setsid nohup ... &, nohup after &&, an
    incomplete exec, a reparented exec; not a plain command, never a noise
    exec. Ids in exec seq order.
    Rules: EVD8.
    """
    execs = _execs(
        {"command_id": "e1", "command": "./serve.sh &"},
        {"command_id": "e2", "command": "setsid nohup ./x.sh > l 2>&1 &"},
        {"command_id": "e3", "command": "make && nohup ./y.sh"},
        {"command_id": "e4", "command": "./run.sh", "complete": False},
        {"command_id": "e5", "command": "worker"},
        {"command_id": "e6", "command": "make"},
        {"command_id": "e7", "command": "./bg.sh &", "noise": "probe"},
    )
    links = [
        {
            "link_type": "exec_parent",
            "command_id": "e5",
            "parent": None,
            "root_call_id": "c1",
            "rule": "reparenting",
            "root_ambiguous": False,
            "root_candidates": [],
        },
        {
            "link_type": "exec_parent",
            "command_id": "e6",
            "parent": "e1",
            "root_call_id": "c1",
            "rule": "time_containment",
            "root_ambiguous": False,
            "root_candidates": [],
        },
    ]
    ids, basis = _module().detached_execs(execs, links, capabilities=_caps())
    assert (list(ids), basis) == (["e1", "e2", "e3", "e4", "e5"], "fallback")


def test_detached_exact() -> None:
    """EVD8 exact: parent_links and every non-noise exec's orphans a list.

    An exec with orphans, one with orphans [] (its & no longer counts), an
    incomplete one; a noise exec's orphans need not be set.
    Rules: EVD8.
    """
    execs = _execs(
        {"command_id": "e1", "command": "./serve.sh &", "orphans": []},
        {
            "command_id": "e2",
            "command": "./x.sh",
            "orphans": [{"pid": 7, "comm": "x"}],
        },
        {
            "command_id": "e3",
            "command": "./y",
            "complete": False,
            "orphans": [],
        },
        {"command_id": "e4", "command": "probe &", "noise": "probe"},
    )
    caps = _caps(parent_links=True)
    ids, basis = _module().detached_execs(execs, [], capabilities=caps)
    assert (list(ids), basis) == (["e2", "e3"], "exact")


def test_detached_exact_inherited_orphans() -> None:
    """EVD8 exact: an orphan an inner command listed first counts there.

    The outer exec's entry carries inherited_from, so only the inner exec
    is detached by it.
    Rules: EVD8.
    """
    orphan = {"pid": 7, "comm": "x", "start_ticks": 50}
    execs = _execs(
        {
            "command_id": "e1",
            "command": "./run.sh",
            "orphans": [{**orphan, "inherited_from": "e2"}],
        },
        {"command_id": "e2", "command": "./x.sh", "orphans": [orphan]},
    )
    caps = _caps(parent_links=True)
    ids, basis = _module().detached_execs(execs, [], capabilities=caps)
    assert (list(ids), basis) == (["e2"], "exact")


def test_detached_exact_unconfirmed_inherited_orphan() -> None:
    """EVD8 exact: inherited_from counts only when that command lists it.

    e1's orphan is e2's own shim (a recorded command, so it carries
    command_id); e2 lists no orphans, so e1 keeps it. e3 names a command
    that lists another start_ticks, and e4 one that does not exist.
    Rules: EVD8.
    """
    shim = {"pid": 7, "start_ticks": 50, "comm": "bash", "command_id": "e2"}
    execs = _execs(
        {
            "command_id": "e1",
            "command": "./run.sh",
            "orphans": [{**shim, "inherited_from": "e2"}],
        },
        {"command_id": "e2", "command": "./x.sh", "orphans": []},
        {
            "command_id": "e3",
            "command": "./y.sh",
            "orphans": [{"pid": 8, "start_ticks": 60, "inherited_from": "e5"}],
        },
        {
            "command_id": "e4",
            "command": "./z.sh",
            "orphans": [{"pid": 9, "start_ticks": 70, "inherited_from": "e9"}],
        },
        {
            "command_id": "e5",
            "command": "./w.sh",
            "orphans": [{"pid": 8, "start_ticks": 61}],
        },
    )
    caps = _caps(parent_links=True)
    ids, basis = _module().detached_execs(execs, [], capabilities=caps)
    assert (list(ids), basis) == (["e1", "e3", "e4", "e5"], "exact")


def test_detached_exact_incomplete_without_orphans() -> None:
    """EVD8 exact: an incomplete exec needs no orphans list.

    A killed command never writes its end record, so its orphans are
    null; it is detached by being incomplete, and the basis stays exact.
    Rules: EVD8.
    """
    execs = _execs(
        {"command_id": "e1", "command": "./serve.sh &", "orphans": []},
        {"command_id": "e2", "command": "./train.py", "complete": False},
        {
            "command_id": "e3",
            "command": "./x.sh",
            "orphans": [{"pid": 7, "comm": "x"}],
        },
    )
    caps = _caps(parent_links=True)
    ids, basis = _module().detached_execs(execs, [], capabilities=caps)
    assert (list(ids), basis) == (["e2", "e3"], "exact")


def test_detached_partial_orphans() -> None:
    """parent_links with some orphans null: the fallback plus adopted parents.

    Without parent_links an adopted record's parent is not added.
    Rules: EVD8.
    """
    execs = _execs(
        {"command_id": "e1", "command": "./launch.sh"},
        {
            "command_id": "e2",
            "command": "worker",
            "orphans": [],
            "parent_link": "adopted",
            "parent_command_id": "e1",
        },
        {"command_id": "e3", "command": "./bg &", "orphans": []},
    )
    module = _module()
    ids, basis = module.detached_execs(
        execs, [], capabilities=_caps(parent_links=True)
    )
    assert (list(ids), basis) == (["e1", "e3"], "fallback")
    ids, basis = module.detached_execs(execs, [], capabilities=_caps())
    assert (list(ids), basis) == (["e3"], "fallback")


# ------------------------------------------------------------ rewrite_type

REWRITES = (
    (["aa", "aa"], "aa", True, False, ("same_content", "exact")),
    (["AA"], "aa", True, False, ("same_content", "exact")),
    (["ab", "aa"], "aa", True, False, ("restored", "exact")),
    (["aa", None], "aa", True, False, (None, "exact")),
    (["aa"], "aa", True, True, (None, "exact")),
    (["aa"], "aa", False, False, (None, "fallback")),
    (["ab"], "aa", False, False, (None, "fallback")),
)


REWRITES_CYCLE2 = (
    # A6: an unknown baseline hash decides nothing (as D57).
    pytest.param(["ab"], None, id="unknown baseline, other hash"),
    pytest.param(["aa", "ab"], None, id="unknown baseline, two hashes"),
)


@pytest.mark.parametrize(("hashes", "base", "caps", "edit", "want"), REWRITES)
def test_rewrite_type(
    hashes: list, base: str, caps: bool, edit: bool, want: tuple
) -> None:
    """CON7's split of rewritten_identical, and its basis.

    Rules: CON7.
    """
    got = _module().rewrite_type(
        hashes, base, content_hashes=caps, has_edit=edit
    )
    assert tuple(got) == want


@pytest.mark.parametrize(("hashes", "base"), REWRITES_CYCLE2)
def test_rewrite_type_unknown_baseline(hashes: list, base: None) -> None:
    """With baseline_sha256 null the split is null, not restored (A6).

    Rules: CON7.
    """
    got = _module().rewrite_type(
        hashes, base, content_hashes=True, has_edit=False
    )
    assert got[0] is None


# ---------------------------------------------------------------- intersect


def _evidence(**fields: Any) -> dict[str, Any]:
    """An Evidence dict with no evidence.

    No constraint applies but CON1's null, which removes oracle_written in
    the strict model.
    """
    evidence = {
        "log": False,
        "baseline_exists": None,
        "baseline_sha256": None,
        "grading_exists": None,
        "content_cmp": None,
        "content_sources_disagree": False,
        "oracle_writes": None,
        "oracle_writes_basis": None,
        "events": [],
        "edit_events": [],
        "exec_events": [],
        "final_writer_type": None,
        "final_writer_under": False,
        "final_writer_op": None,
        "final_writer_hash": None,
        "content_hashes": False,
        "content_late": False,
        "observable": False,
        "blind_execs": [],
        "unmonitored_calls": [],
    }
    evidence.update(fields)
    return evidence


S = frozenset
EXEC_EVENT = {"events": ["w1"], "exec_events": ["w1"]}
EDIT_EVENT = {"events": ["w0"], "edit_events": ["w0"]}
WATCHED = {"observable": True}
CLEAR_EXEC = {
    **WATCHED,
    **EXEC_EVENT,
    "final_writer_under": True,
    "final_writer_type": "exec",
}
# (name, evidence, states_under, states_over): each constraint alone on
# the full domain, under each model.
CONSTRAINTS = (
    ("none", {}, NO_ORACLE, FULL),
    # CON1 with each (oracle_writes, oracle_writes_basis) pair (D33, D5).
    (
        "CON1 true exact",
        {"oracle_writes": True, "oracle_writes_basis": "exact"},
        S({"oracle_written"}),
        S({"oracle_written"}),
    ),
    (
        "CON1 true fallback",
        {"oracle_writes": True, "oracle_writes_basis": "fallback"},
        S({"oracle_written"}),
        FULL,
    ),
    (
        "CON1 false exact",
        {"oracle_writes": False, "oracle_writes_basis": "exact"},
        NO_ORACLE,
        NO_ORACLE,
    ),
    (
        "CON1 false fallback",
        {"oracle_writes": False, "oracle_writes_basis": "fallback"},
        NO_ORACLE,
        FULL,
    ),
    ("CON1 null", {}, NO_ORACLE, FULL),
    (
        "CON1 true exact wins",
        {
            "oracle_writes": True,
            "oracle_writes_basis": "exact",
            "baseline_exists": False,
            "grading_exists": False,
            "content_sources_disagree": True,
        },
        S({"oracle_written"}),
        S({"oracle_written"}),
    ),
    # CON2.
    (
        "CON2 true",
        {"baseline_exists": True},
        NO_ORACLE - {"missing"},
        FULL - {"missing"},
    ),
    (
        "CON2 false",
        {"baseline_exists": False},
        S({"edit_written", "command_written", "missing"}),
        S({"edit_written", "command_written", "oracle_written", "missing"}),
    ),
    # CON3.
    (
        "CON3 false",
        {"grading_exists": False},
        S({"deleted", "missing"}),
        S({"deleted", "missing", "oracle_written"}),
    ),
    (
        "CON3 true",
        {"grading_exists": True},
        NO_ORACLE - {"deleted", "missing"},
        FULL - {"deleted", "missing"},
    ),
    # CON4.
    (
        "CON4 equal",
        {"content_cmp": "equal"},
        S({"pristine", "rewritten_identical"}),
        S({"pristine", "rewritten_identical", "oracle_written"}),
    ),
    (
        "CON4 differs",
        {"content_cmp": "differs"},
        NO_ORACLE - {"pristine", "rewritten_identical"},
        FULL - {"pristine", "rewritten_identical"},
    ),
    # CON5.
    ("CON5", {"events": ["w1"]}, NO_ORACLE - {"pristine"}, FULL - {"pristine"}),
    # CON6.
    (
        "CON6 certain exec",
        {
            "log": True,
            "final_writer_under": True,
            "final_writer_type": "exec",
            **EXEC_EVENT,
        },
        NO_ORACLE - {"pristine", "edit_written"},
        FULL - {"pristine", "edit_written"},
    ),
    (
        "CON6 certain edit",
        {
            "log": True,
            "final_writer_under": True,
            "final_writer_type": "edit_call",
            **EDIT_EVENT,
        },
        NO_ORACLE - {"pristine"},
        FULL - {"pristine"},
    ),
    (
        "CON6 uncertain, no edit",
        {"log": True},
        NO_ORACLE - {"edit_written"},
        FULL - {"edit_written"},
    ),
    (
        "CON6 uncertain, an edit",
        {"log": True, **EDIT_EVENT},
        NO_ORACLE - {"pristine"},
        FULL - {"pristine"},
    ),
    (
        "CON6 without the log",
        {"final_writer_under": True, "final_writer_type": "exec", **EXEC_EVENT},
        NO_ORACLE - {"pristine"},
        FULL - {"pristine"},
    ),
    # CON7 (strict only): the final writer's recorded hash, applied only
    # when no write after it can have gone unseen (observable, no blind
    # window, no unmonitored call: D61).
    (
        "CON7 equal",
        {
            **CLEAR_EXEC,
            "content_hashes": True,
            "final_writer_hash": "hh",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        S({"rewritten_identical"}),
        FULL - {"missing", "pristine"},
    ),
    (
        "CON7 differs",
        {
            **CLEAR_EXEC,
            "content_hashes": True,
            "final_writer_hash": "hg",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        S({"edit_written", "command_written"}),
        FULL - {"missing", "pristine"},
    ),
    (
        "CON7 new file",
        {
            **CLEAR_EXEC,
            "content_hashes": True,
            "final_writer_hash": "hg",
            "baseline_exists": False,
        },
        S({"edit_written", "command_written"}),
        S({"edit_written", "command_written", "oracle_written", "missing"}),
    ),
    (
        "CON7 no capability",
        {
            **CLEAR_EXEC,
            "final_writer_hash": "hg",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        NO_ORACLE - {"missing", "pristine"},
        FULL - {"missing", "pristine"},
    ),
    (
        "CON7 content known",
        {
            **CLEAR_EXEC,
            "content_hashes": True,
            "final_writer_hash": "hg",
            "content_cmp": "equal",
            "baseline_sha256": "hh",
        },
        S({"rewritten_identical"}),
        S({"rewritten_identical", "oracle_written"}),
    ),
    (
        "CON7 no hash",
        {
            **CLEAR_EXEC,
            "content_hashes": True,
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        NO_ORACLE - {"missing", "pristine"},
        FULL - {"missing", "pristine"},
    ),
    (
        "CON7 off: blind",
        {
            **CLEAR_EXEC,
            "blind_execs": ["e2"],
            "content_hashes": True,
            "final_writer_hash": "hg",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        NO_ORACLE - {"missing", "pristine"},
        FULL - {"missing", "pristine"},
    ),
    (
        "CON7 off: not observable",
        {
            **CLEAR_EXEC,
            "observable": False,
            "content_hashes": True,
            "final_writer_hash": "hg",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        NO_ORACLE - {"missing", "pristine"},
        FULL - {"missing", "pristine"},
    ),
    # CON8 and CON9 (strict only): observable, no window.
    (
        "CON8+CON9 nothing seen",
        WATCHED,
        NO_ORACLE - {"rewritten_identical", "deleted", "command_written"},
        FULL,
    ),
    ("CON8 off: blind", {**WATCHED, "blind_execs": ["e1"]}, NO_ORACLE, FULL),
    (
        "CON8 off: unmonitored",
        {**WATCHED, "unmonitored_calls": ["c1"]},
        NO_ORACLE,
        FULL,
    ),
    ("CON8 off: not observable", {}, NO_ORACLE, FULL),
    (
        "CON9 certain edit",
        {
            **WATCHED,
            "final_writer_under": True,
            "final_writer_type": "edit_call",
            **EDIT_EVENT,
        },
        NO_ORACLE - {"pristine", "command_written"},
        FULL - {"pristine"},
    ),
    (
        "CON9 certain exec",
        {
            **WATCHED,
            "final_writer_under": True,
            "final_writer_type": "exec",
            **EXEC_EVENT,
        },
        NO_ORACLE - {"pristine"},
        FULL - {"pristine"},
    ),
    (
        "CON9 uncertain, an exec event",
        {**WATCHED, **EXEC_EVENT},
        NO_ORACLE - {"pristine"},
        FULL - {"pristine"},
    ),
    (
        "CON9 uncertain, edits only",
        {**WATCHED, **EDIT_EVENT},
        NO_ORACLE - {"pristine", "command_written"},
        FULL - {"pristine"},
    ),
    (
        "CON9 off: blind",
        {**WATCHED, "blind_execs": ["e1"], **EDIT_EVENT},
        NO_ORACLE - {"pristine"},
        FULL - {"pristine"},
    ),
    # CON12 (strict only).
    ("CON12", {"content_sources_disagree": True}, S(), FULL),
    # CON15 (strict only), under the same condition as CON7 (D61).
    (
        "CON15 delete exists",
        {**CLEAR_EXEC, "final_writer_op": "delete", "grading_exists": True},
        S(),
        FULL - {"pristine", "deleted", "missing"},
    ),
    (
        "CON15 create absent",
        {**CLEAR_EXEC, "final_writer_op": "create", "grading_exists": False},
        S(),
        S({"deleted", "missing", "oracle_written"}),
    ),
    (
        "CON15 modify absent",
        {**CLEAR_EXEC, "final_writer_op": "modify", "grading_exists": False},
        S(),
        S({"deleted", "missing", "oracle_written"}),
    ),
    (
        "CON15 consistent",
        {**CLEAR_EXEC, "final_writer_op": "modify", "grading_exists": True},
        NO_ORACLE - {"pristine", "deleted", "missing"},
        FULL - {"pristine", "deleted", "missing"},
    ),
    (
        "CON15 delete absent",
        {**CLEAR_EXEC, "final_writer_op": "delete", "grading_exists": False},
        S({"deleted", "missing"}),
        S({"deleted", "missing", "oracle_written"}),
    ),
    (
        "CON15 mode",
        {**CLEAR_EXEC, "final_writer_op": "mode", "grading_exists": False},
        S({"deleted", "missing"}),
        S({"deleted", "missing", "oracle_written"}),
    ),
    (
        "CON15 off: blind",
        {
            **CLEAR_EXEC,
            "blind_execs": ["e2"],
            "final_writer_op": "delete",
            "grading_exists": True,
        },
        NO_ORACLE - {"pristine", "deleted", "missing"},
        FULL - {"pristine", "deleted", "missing"},
    ),
    (
        "CON15 off: unmonitored",
        {
            **CLEAR_EXEC,
            "unmonitored_calls": ["c2"],
            "final_writer_op": "create",
            "grading_exists": False,
        },
        S({"deleted", "missing"}),
        S({"deleted", "missing", "oracle_written"}),
    ),
    (
        "CON15 off: not observable",
        {
            **CLEAR_EXEC,
            "observable": False,
            "final_writer_op": "delete",
            "grading_exists": True,
        },
        NO_ORACLE - {"pristine", "deleted", "missing"},
        FULL - {"pristine", "deleted", "missing"},
    ),
)


# Cycle-1 critic, A1 (extends D61): CON7 and CON15 also need layer 1's
# final writer to be certain, as CON6 and CON9 already do.
UNCERTAIN = {**CLEAR_EXEC, "final_writer_under": False}
CONSTRAINTS_CYCLE2 = (
    (
        "CON7 off: final writer uncertain",
        {
            **UNCERTAIN,
            "content_hashes": True,
            "final_writer_hash": "hg",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        NO_ORACLE - {"missing", "pristine"},
        FULL - {"missing", "pristine"},
    ),
    (
        "CON7 off: final writer uncertain, hash equal",
        {
            **UNCERTAIN,
            "content_hashes": True,
            "final_writer_hash": "hh",
            "baseline_exists": True,
            "baseline_sha256": "hh",
        },
        NO_ORACLE - {"missing", "pristine"},
        FULL - {"missing", "pristine"},
    ),
    (
        "CON15 off: final writer uncertain, delete exists",
        {**UNCERTAIN, "final_writer_op": "delete", "grading_exists": True},
        NO_ORACLE - {"pristine", "deleted", "missing"},
        FULL - {"pristine", "deleted", "missing"},
    ),
    # D73: a final diff that may predate a late agent write binds M_s only.
    (
        "CON3 late: final diff deleted, relaxed model unbound",
        {
            "grading_exists": False,
            "content_cmp": "differs",
            "content_late": True,
        },
        S({"deleted", "missing"}),
        FULL,
    ),
    (
        "CON4 late: final diff equal, relaxed model unbound",
        {"grading_exists": True, "content_cmp": "equal", "content_late": True},
        S({"pristine", "rewritten_identical"}),
        FULL,
    ),
    (
        "CON15 off: final writer uncertain, create absent",
        {**UNCERTAIN, "final_writer_op": "create", "grading_exists": False},
        S({"deleted", "missing"}),
        S({"deleted", "missing", "oracle_written"}),
    ),
)
INTERSECT_PARAMS = [
    pytest.param(*row, id=row[0]) for row in (*CONSTRAINTS, *CONSTRAINTS_CYCLE2)
]


@pytest.mark.parametrize(("name", "fields", "under", "over"), INTERSECT_PARAMS)
def test_intersect(
    name: str, fields: dict, under: frozenset, over: frozenset
) -> None:
    """Each constraint of §6.3 alone, under each model.

    Rules: CON1, CON2, CON3, CON4, CON5, CON6, CON7, CON8, CON9, CON12,
    CON15, S1, S2, S3, S4, S5.
    """
    module = _module()
    evidence = _evidence(**fields)
    assert set(module.intersect(evidence, True)) == set(under), name
    assert set(module.intersect(evidence, False)) == set(over), name


# Constraints whose evidence fields do not overlap: their meet is the
# intersection of their sets, whatever the order of application.
INDEPENDENT = (
    {"baseline_exists": True},
    {"baseline_exists": False},
    {"grading_exists": True},
    {"grading_exists": False},
    {"content_cmp": "equal"},
    {"content_cmp": "differs"},
    {"events": ["w1"]},
    {"oracle_writes": False, "oracle_writes_basis": "exact"},
    {"oracle_writes": False, "oracle_writes_basis": "fallback"},
    {"content_sources_disagree": True},
)


def _compatible(parts: tuple[dict, ...]) -> bool:
    keys = [k for part in parts for k in part]
    return len(keys) == len(set(keys))


@pytest.mark.parametrize("strict", [True, False])
def test_intersect_is_a_meet(strict: bool) -> None:
    """The order of application does not change the result.

    Combined evidence gives the intersection of each part's labels.
    Rules: CON1, CON2, CON3, CON4, CON5, CON12.
    """
    module = _module()
    for size in (2, 3):
        for parts in itertools.combinations(INDEPENDENT, size):
            if not _compatible(parts):
                continue
            combined = _evidence(**{k: v for p in parts for k, v in p.items()})
            want = FULL
            for part in parts:
                want = want & set(module.intersect(_evidence(**part), strict))
            got = set(module.intersect(combined, strict))
            assert got == want, parts


def test_intersect_reads_only_evidence() -> None:
    """The classification is a pure function of Evidence.

    Rules: CON13.
    """
    module = _module()
    first = module.intersect(_evidence(baseline_exists=True), True)
    second = module.intersect(_evidence(baseline_exists=True), True)
    assert isinstance(first, frozenset)
    assert first == second


# --------------------------------------------------------------- predicates


def test_predicates_empty() -> None:
    """An empty states_under makes every P_under false.

    Rules: CNT1.
    """
    under, over = _module().predicates([], ["command_written"])
    assert set(under) == set(helpers.PREDICATE_NAMES)
    assert not any(under.values())
    assert over == {
        "baseline_content": False,
        "fresh": True,
        "agent_written": True,
        "edit_written": False,
        "command_written": True,
        "absent": False,
    }


def test_predicates_singleton() -> None:
    """A singleton states_under: P_under exactly for the sets holding it.

    Rules: CNT1.
    """
    under, over = _module().predicates(
        ["pristine"], ["pristine", "rewritten_identical"]
    )
    assert under == {
        "baseline_content": True,
        "fresh": False,
        "agent_written": False,
        "edit_written": False,
        "command_written": False,
        "absent": False,
    }
    assert over["fresh"] is True
    assert over["baseline_content"] is True


def test_predicates_straddling() -> None:
    """A set straddling two predicates satisfies neither under, both over.

    Rules: CNT1.
    """
    states = ["rewritten_identical", "edit_written"]
    under, over = _module().predicates(states, states)
    assert under["baseline_content"] is False
    assert under["agent_written"] is False
    assert under["fresh"] is True
    assert over["baseline_content"] is True
    assert over["agent_written"] is True
    assert over["absent"] is False


# ------------------------------------------------------------------ classify


def _scenario(stem: str) -> dict[str, Any]:
    (path,) = [p for p in helpers.scenario_paths() if p.stem.startswith(stem)]
    return helpers.load_scenario(path)


def test_classify_rejects_unknown_config() -> None:
    """A config key not in DEFAULTS raises ValueError.

    Rules: PHS3.
    """
    with pytest.raises(ValueError):
        helpers.run_classify(_scenario("P01_"), config={"nope": 1})


def test_passing_statuses_override() -> None:
    """DEFAULTS["passing_statuses"] decides which phases pass.

    Rules: PHS3.
    """
    scenario = _scenario("P35_")
    plain = helpers.run_classify(scenario)
    wide = helpers.run_classify(
        scenario, config={"passing_statuses": ["success", "failure"]}
    )
    m32 = plain["state_counts"]["metrics"]["M3.2"]
    assert m32["passing_phases"] == 2
    assert wide["state_counts"]["metrics"]["M3.2"]["passing_phases"] == 3


def test_classify_reads_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module reads no file (D32): open is never called.

    Rules: EVD2, PRE4.
    """
    scenario = _scenario("P30_")
    module = _module()

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"file opened: {args}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    result = helpers.run_classify(scenario)
    assert result["input_states"]
    assert module.classify is not None


def test_final_files_absolute_path() -> None:
    """A final_files entry written as an absolute path is normalized.

    end_of_agent's index is by workspace-relative path (D55).
    Rules: EVD2.
    """
    raw = helpers.read_raw(
        [p for p in helpers.scenario_paths() if p.stem.startswith("P12b_")][0]
    )
    raw["final_files"][0]["path"] = "/repo/out/new.csv"
    result = helpers.run_classify(helpers.build_scenario(raw))
    (row,) = result["input_states"]
    assert row["content_source"] == "final_workspace"
    assert row["state_under"] == "command_written"


def test_shared_end_of_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVD2 source 2 calls oracle_inputs.end_of_agent, the one function.

    Rules: EVD2.
    """
    from trajectory_checks import oracle_inputs

    seen = []
    original = oracle_inputs.end_of_agent

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(args[1] if len(args) > 1 else kwargs.get("path"))
        return original(*args, **kwargs)

    monkeypatch.setattr(oracle_inputs, "end_of_agent", spy)
    result = helpers.run_classify(_scenario("P12b_"))
    assert result["input_states"][0]["content_source"] == "final_workspace"
    assert "out/new.csv" in seen


def test_pre1_reads_the_null_commit() -> None:
    """PRE1 fails when §0's null_commit fails for the null-agent source.

    For a graded source a failed null_commit voids only M3.5 (OI9), so
    M3.2 is computed (D51); either failure of PRE1 warns as §7 says.
    Rules: PRE1.
    """
    scenario = _scenario("P01_")
    block = scenario["oracle_inputs"]["oracle_inputs_run"]
    block["preconditions"]["null_commit"] = "fails"
    void = helpers.run_classify(scenario)
    counts = void["state_counts"]
    assert counts["preconditions"]["oracle_inputs"] == "fails"
    assert void["input_states"] == []
    assert all(v is None for v in counts["metrics"]["M3.2"].values())
    assert [w for w in counts["warnings"] if "oracle_inputs" in w] == [
        "input oracle_inputs missing: M3.2 null"
    ]
    block["bases"]["source"] = "exact"
    kept = helpers.run_classify(scenario)
    assert kept["state_counts"]["preconditions"]["oracle_inputs"] == "holds"
    assert kept["state_counts"]["metrics"]["M3.2"]["oracle_inputs"] == 1


# ----------------------------------------- cycle-2 critic: A5, S1, D73


def _raw(stem: str) -> dict[str, Any]:
    (path,) = [p for p in helpers.scenario_paths() if p.stem.startswith(stem)]
    return helpers.read_raw(path)


def test_input_rows_drops_reference_listings() -> None:
    """A member of a reference listing is a member of no kept listing.

    The reference row is dropped (INP2), so it is in no member_of.
    Rules: INP2, INP4.
    """
    rows = [
        _source(
            path="/refs/out",
            pattern="*.json",
            input_scope="reference",
            oracle_writes=None,
            oracle_writes_basis=None,
        ),
        _source(path="out/a.json", listed_by=["i0"]),
    ]
    for index, row in enumerate(rows):
        row["input_id"] = f"i{index}"
    (row,), dropped = _module().input_rows(rows)
    assert (row["path"], row["member_of"], dropped) == ("out/a.json", [], 1)


def test_sources_disagree_on_existence_only() -> None:
    """Two sources that agree on the comparison but not on existence.

    The grading read says gone and the final diff says modified: both
    compare as differs, yet they disagree (EVD4).
    Rules: EVD2, EVD4.
    """
    raw = _raw("P32_")
    raw["oracle_inputs"][0].update(grading_exists=False, grading_sha256=None)
    (row,) = helpers.run_classify(helpers.build_scenario(raw))["input_states"]
    assert (row["content_source"], row["grading_exists"]) == (
        "grading_read",
        False,
    )
    assert row["content_cmp"] == "differs"
    assert row["content_sources_disagree"] is True


def test_absolute_snapshot_hash_keys() -> None:
    """A snapshot hash keyed by an absolute path is the same evidence.

    MC-07's keys are normalized to workspace paths, so P37's final
    writer hash still fixes the input (CON7).
    Rules: CON7.
    """
    raw = _raw("P37_")
    raw["execs"][0]["snapshot"]["hashes"] = {"/repo/out/x.bin": "ff"}
    (row,) = helpers.run_classify(helpers.build_scenario(raw))["input_states"]
    assert row["states_under"] == ["command_written"]
    assert row["state_under"] == "command_written"


def test_listing_changed_over_on_a_late_write() -> None:
    """A late-write cause leaves a listing's over value true (D73).

    P23's folder with only its recorded, unchanged member: observable,
    so listing_changed_under is false; a detached exec may add a member
    after the final diff, so listing_changed_over is true. Without the
    detached exec both are false.
    Rules: INP4, EVD8, CNT2.
    """
    raw = _raw("P23_")
    raw.update(links=[], paths=[], oracle_inputs=raw["oracle_inputs"][:2])
    raw["calls"] = [{"call_id": "c1", "command": "./serve.sh &", "t_start": 10}]
    raw["execs"][0]["snapshot"] = {"scope": "workspace"}
    for command, over in (("./serve.sh &", True), ("./serve.sh", False)):
        raw["calls"][0]["command"] = command
        result = helpers.run_classify(helpers.build_scenario(raw))
        rows = {r["input_type"]: r for r in result["input_states"]}
        listing = rows["listing"]
        assert listing["observable"] is True
        assert listing["listing_changed_under"] is False
        assert listing["listing_changed_over"] is over
        m32 = result["state_counts"]["metrics"]["M3.2"]
        assert m32["listing_changed_over"] == int(over)
