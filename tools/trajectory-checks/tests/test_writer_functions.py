"""Layer 5: table-driven tests of writer.py's public functions (§2.2.1).

Each row holds a hand-written expected value, worked out from the rules
of spec §6. ``writer`` is imported inside each test (D41), so until
``writer.py`` exists each test fails on its own.
"""

import json
import types
from typing import Any

import pytest
import writer_helpers as helpers

from trajectory_checks import structure


def _exec(
    command_id: str, t_start: float, duration_s: float, **extra: Any
) -> dict:
    record = {
        "command_id": command_id,
        "t_start": t_start,
        "duration_s": duration_s,
        "t_end": t_start + duration_s,
        "complete": True,
        "noise": None,
        "released_at": None,
        "ended_at": None,
        "snapshot_after_s": None,
        "orphans": None,
    }
    record.update(extra)
    return record


def _call(
    call_id: str, t_start: float, t_end: float | None, p: float = 1.0
) -> dict:
    return {
        "call_id": call_id,
        "seq": 0,
        "tool_type": "edit",
        "t_start": t_start,
        "t_end": t_end,
        "t_precision_s": p,
    }


def _event(
    number: int,
    order_t: float,
    *,
    op: str = "modify",
    writer: str = "e1",
    writer_type: str = "exec",
    edit: str | None = None,
    root: str | None = "c1",
    candidates: tuple[str, ...] = (),
    path: str = "out/r.json",
) -> dict:
    return {
        "link_type": "write_event",
        "event_id": f"w{number}",
        "path": path,
        "in_workspace": True,
        "op": op,
        "scope": "tracked",
        "writer_type": writer_type,
        "writer_id": writer,
        "t_start": order_t - 1.0,
        "t_end": order_t,
        "order_t": order_t,
        "root_call_id": root,
        "root_ambiguous": bool(candidates),
        "root_candidates": list(candidates),
        "depth_rule": "none",
        "possibly_edit_call": edit,
        "overlaps_sibling": False,
        "source": "snapshot" if writer_type == "exec" else "edit_call",
        "writer_under": True,
    }


# ------------------------------------------------------- observation_window

EXECS = {
    "e1": _exec("e1", 10.0, 0.5),
    "e2": _exec("e2", 10.0, 30.0, complete=False),
    "e3": _exec(
        "e3", 10.0, 0.5, released_at=10.1, ended_at=10.5, snapshot_after_s=0.2
    ),
    "e4": _exec("e4", 10.0, 0.5, ended_at=10.5, snapshot_after_s=0.2),
    "e5": _exec("e5", 10.0, 0.5, released_at=10.1, ended_at=10.5),
    "e6": _exec("e6", 10.0, 30.0, complete=False, released_at=10.1),
}
CALLS = {
    "c1": _call("c1", 20.0, 22.0),
    "c2": _call("c2", 20.0, None),
    "c3": _call("c3", 20.0, 20.0, p=0.001),
}
WINDOWS = (
    ("complete exec", "e1", "exec", False, 2.0, (10.0, 12.5)),
    ("incomplete exec, measured end", "e2", "exec", False, 2.0, (10.0, 42.0)),
    ("exact", "e3", "exec", True, 2.0, (10.1, 10.7)),
    ("exact, no released_at", "e4", "exec", True, 2.0, (10.0, 10.7)),
    ("exact, no snapshot_after_s", "e5", "exec", True, 2.0, (10.1, 12.5)),
    ("exact, no ended_at", "e6", "exec", True, 2.0, (10.0, 42.0)),
    ("exact asked, no capability", "e3", "exec", False, 2.0, (10.0, 12.5)),
    ("slop 0.5", "e1", "exec", False, 0.5, (10.0, 11.0)),
    ("edit with t_end", "c1", "edit_call", False, 2.0, (19.0, 23.0)),
    ("edit without t_end", "c2", "edit_call", False, 2.0, (19.0, 21.0)),
    ("edit, 1 ms precision", "c3", "edit_call", True, 2.0, (19.999, 20.001)),
)


@pytest.mark.parametrize(
    ("writer_id", "writer_type", "exact", "slop", "want"),
    [row[1:] for row in WINDOWS],
    ids=[row[0] for row in WINDOWS],
)
def test_observation_window(
    writer_id: str,
    writer_type: str,
    exact: bool,
    slop: float,
    want: tuple[float, float],
) -> None:
    """LW4: exact windows with per-record fallback, else measured + slop.

    An incomplete exec ends at its measured end plus the slop, never at
    the run's end; an edit call is its logged time plus its precision.
    Rules: LW4, PRE6.
    """
    from trajectory_checks import writer

    event = _event(0, 1.0, writer=writer_id, writer_type=writer_type)
    got = writer.observation_window(
        event, execs=EXECS, calls=CALLS, exact=exact, slop=slop
    )
    assert got == pytest.approx(want)


# -------------------------------------------------------------- last_writer

W = types.MappingProxyType


def _windows(**spans: tuple[float, float]) -> dict[str, tuple[float, float]]:
    return dict(spans)


LAST_WRITER = (
    (
        "one event",
        [_event(0, 11.0)],
        _windows(w0=(10, 13)),
        None,
        None,
        ("w0", ["w0"]),
    ),
    (
        "two ordered",
        [_event(0, 11.0), _event(1, 31.0)],
        _windows(w0=(10, 13), w1=(30, 33)),
        None,
        None,
        ("w1", ["w1"]),
    ),
    (
        "two overlapping",
        [_event(0, 11.0), _event(1, 12.0, writer="e2")],
        _windows(w0=(10, 13), w1=(11, 14)),
        None,
        None,
        ("w1", ["w0", "w1"]),
    ),
    (
        "incomplete exec ends before a short write",
        [_event(0, 40.0), _event(1, 90.5, writer="e2")],
        _windows(w0=(10, 42), w1=(90, 92.5)),
        None,
        None,
        ("w1", ["w1"]),
    ),
    (
        "delete after create, file exists",
        [_event(0, 11.0, op="create"), _event(1, 51.0, op="delete")],
        _windows(w0=(10, 13), w1=(50, 53)),
        True,
        None,
        ("w0", ["w0"]),
    ),
    (
        "delete after create, existence unknown",
        [_event(0, 11.0, op="create"), _event(1, 51.0, op="delete")],
        _windows(w0=(10, 13), w1=(50, 53)),
        None,
        None,
        ("w1", ["w1"]),
    ),
    (
        "mode event last",
        [
            _event(0, 11.0, op="mode"),
            _event(1, 11.5),
            _event(2, 12.0, op="mode"),
        ],
        _windows(w0=(10, 13), w1=(10.5, 13.5), w2=(11, 14)),
        True,
        None,
        ("w2", ["w1", "w2"]),
    ),
    (
        "an exec event that saw an edit",
        [
            _event(0, 15.0, writer="c2", writer_type="edit_call", root="c2"),
            _event(1, 30.0, edit="c2"),
        ],
        _windows(w0=(14, 16), w1=(10, 32)),
        True,
        None,
        ("w0", ["w0"]),
    ),
    (
        "equal order_t, higher event number wins",
        [_event(1, 10.0, writer="e2"), _event(0, 10.0)],
        _windows(w0=(9, 12), w1=(9, 12)),
        None,
        None,
        ("w1", ["w0", "w1"]),
    ),
    (
        "cutoff excludes a later event",
        [_event(0, 11.0), _event(1, 21.0), _event(2, 31.0)],
        _windows(w0=(10, 13), w1=(20, 23), w2=(30, 33)),
        True,
        25.0,
        ("w1", ["w1"]),
    ),
    (
        "cutoff at a window start excludes it",
        [_event(0, 11.0), _event(1, 21.0)],
        _windows(w0=(10, 13), w1=(20, 23)),
        True,
        20.0,
        ("w0", ["w0"]),
    ),
    (
        "an event straddling the cutoff stays",
        [_event(0, 21.0), _event(1, 38.0, writer="e2")],
        _windows(w0=(20, 23), w1=(10, 40)),
        True,
        30.0,
        ("w1", ["w0", "w1"]),
    ),
    (
        "every event after the cutoff",
        [_event(0, 11.0), _event(1, 21.0)],
        _windows(w0=(10, 13), w1=(20, 23)),
        True,
        5.0,
        (None, []),
    ),
    ("no event", [], {}, None, None, (None, [])),
)


@pytest.mark.parametrize(
    ("events", "windows", "exists", "cutoff", "want"),
    [row[1:] for row in LAST_WRITER],
    ids=[row[0] for row in LAST_WRITER],
)
def test_last_writer(
    events: list[dict],
    windows: dict,
    exists: bool | None,
    cutoff: float | None,
    want: tuple[str | None, list[str]],
) -> None:
    """LW3, LW5, LW6: layer 1's writer, candidates by window, a cutoff.

    Orders by order_t then event number; drops deletes of an existing
    file, events that saw an edit when another event did not, and mode
    events other than the writer from the candidates.
    Rules: LW3, LW5, LW6.
    """
    from trajectory_checks import writer

    got = writer.last_writer(
        events, windows=windows, exists=exists, cutoff=cutoff
    )
    assert (got[0], list(got[1])) == want


# -------------------------------------------------------- later_writer_risk


def _state(**lists: list[str]) -> dict:
    return {
        "blind_execs": lists.get("blind", []),
        "detached_execs": lists.get("detached", []),
        "unmonitored_calls": lists.get("unmonitored", []),
        "unseen_writer_possible": bool(lists),
    }


RISK_EXECS = {
    "e1": _exec("e1", 1.0, 1.0),
    "e2": _exec("e2", 1.0, 1.0),
    "e3": _exec("e3", 1.0, 1.0),
    "e4": _exec("e4", 1.0, 8.0, complete=False),
    "e5": _exec("e5", 1.0, 1.0, orphans=[]),
    "e6": _exec("e6", 1.0, 1.0, orphans=[4242]),
    "e7": _exec("e7", 1.0, 1.0, orphans=[{"pid": 7, "inherited_from": "e6"}]),
    "e9": _exec("e9", 5.0, 1.0),
}
RISK_WINDOWS = {
    "e1": (1.0, 4.0),
    "e2": (1.0, 5.0),
    "e3": (6.0, 9.0),
    "e4": (1.0, 11.0),
    "e5": (1.0, 4.0),
    "e6": (1.0, 4.0),
    "e7": (1.0, 4.0),
    "e9": (5.0, 8.0),
    "c1": (0.0, 1001.0),
    "c2": (1.0, 3.0),
}


def _risk(item: str, reason: str) -> dict:
    kind = "call" if item.startswith("c") else "exec"
    return {"id": item, "item_type": kind, "reason": reason}


RISKS = (
    ("blind exec ends before", _state(blind=["e1"]), "e9", 5.0, False, []),
    (
        "blind exec ends at window_lo",
        _state(blind=["e2"]),
        "e9",
        5.0,
        False,
        [_risk("e2", "blind_snapshot")],
    ),
    (
        "blind exec after",
        _state(blind=["e3"]),
        "e9",
        5.0,
        False,
        [_risk("e3", "blind_snapshot")],
    ),
    (
        "incomplete detached exec",
        _state(detached=["e4"]),
        "e9",
        5.0,
        False,
        [_risk("e4", "incomplete")],
    ),
    (
        "complete detached exec only backgrounds",
        _state(detached=["e5"]),
        "e9",
        5.0,
        False,
        [],
    ),
    (
        "the writer itself incomplete",
        _state(detached=["e4"]),
        "e4",
        5.0,
        False,
        [],
    ),
    (
        "orphans, exact basis",
        _state(detached=["e6"]),
        "e9",
        5.0,
        True,
        [_risk("e6", "orphaned")],
    ),
    ("orphans, fallback basis", _state(detached=["e6"]), "e9", 5.0, False, []),
    (
        "empty orphans, exact basis",
        _state(detached=["e5"]),
        "e9",
        5.0,
        True,
        [],
    ),
    (
        "only inherited orphans, exact basis",
        _state(detached=["e7"]),
        "e9",
        5.0,
        True,
        [],
    ),
    (
        "unmonitored call open to the run's end",
        _state(unmonitored=["c1"]),
        "e9",
        5.0,
        False,
        [_risk("c1", "unrecorded")],
    ),
    (
        "unmonitored call before",
        _state(unmonitored=["c2"]),
        "e9",
        5.0,
        False,
        [],
    ),
    (
        "no last writer: window_lo is the run's start",
        _state(blind=["e1"]),
        None,
        0.0,
        False,
        [_risk("e1", "blind_snapshot")],
    ),
    (
        "order: reasons, then ids",
        _state(blind=["e3", "e2"], detached=["e4"], unmonitored=["c1"]),
        "e9",
        5.0,
        False,
        [
            _risk("e2", "blind_snapshot"),
            _risk("e3", "blind_snapshot"),
            _risk("e4", "incomplete"),
            _risk("c1", "unrecorded"),
        ],
    ),
    (
        "an item once",
        _state(blind=["e4", "e4"], detached=["e4"]),
        "e9",
        5.0,
        False,
        [_risk("e4", "blind_snapshot")],
    ),
)


@pytest.mark.parametrize(
    ("state", "writer_id", "window_lo", "orphans_exact", "want"),
    [row[1:] for row in RISKS],
    ids=[row[0] for row in RISKS],
)
def test_later_writer_risk(
    state: dict,
    writer_id: str | None,
    window_lo: float,
    orphans_exact: bool,
    want: list[dict],
) -> None:
    """LW9: layer 4's evidence items whose window reaches window_lo.

    Each item once, with its first reason in RISK_REASONS order, then by
    id; an orphaned exec counts only on layer 4's exact detached basis.
    Rules: LW9.
    """
    from trajectory_checks import writer

    got = writer.later_writer_risk(
        state,
        writer_id=writer_id,
        window_lo=window_lo,
        windows=RISK_WINDOWS,
        execs=RISK_EXECS,
        orphans_exact=orphans_exact,
    )
    assert got == want


# ------------------------------------------------------------- read_version

READ_VERSIONS = (
    ((True, "aa"), (True, "aa"), "agent_final"),
    ((True, "aa"), (True, "bb"), "differs"),
    ((True, "aa"), (False, None), "differs"),
    ((False, None), (True, None), "differs"),
    ((False, None), (False, None), "agent_final"),
    ((None, None), (True, "aa"), "unverified"),
    ((True, "aa"), (None, None), "unverified"),
    ((True, None), (True, "aa"), "unverified"),
    ((True, "aa"), (True, None), "unverified"),
    ((True, "AB"), (True, "ab"), "agent_final"),
)


@pytest.mark.parametrize(("end", "read", "want"), READ_VERSIONS)
def test_read_version(end: tuple, read: tuple, want: str) -> None:
    """LW11: existence first, then lower-case hashes; else unverified.

    Rules: LW11.
    """
    from trajectory_checks import writer

    assert writer.read_version(end, read) == want


# ---------------------------------------------------------- root_candidates

ROOT_EVENTS = {
    "w0": _event(0, 11.0, root="c1"),
    "w1": _event(1, 12.0, root="c1", candidates=("c1", "c2")),
    "w2": _event(2, 13.0, writer="c2", writer_type="edit_call", root="c2"),
}
ROOTS = {"e9": "c0", "e5": "c3", "e8": "c2"}
SEQ = {"c0": 0, "c1": 5, "c2": 2, "c3": 9}


def _rstate(
    states: list[str], unseen: bool = False, **lists: list[str]
) -> dict:
    out = _state(**lists)
    out.update(states_over=states, unseen_writer_possible=unseen)
    return out


ROOT_CASES = (
    (
        "unique certain root",
        ["w0"],
        _rstate(["command_written"]),
        None,
        {"w0": True},
        (["c1"], True),
    ),
    (
        "a detached exec's root added",
        ["w0"],
        _rstate(["command_written"], True, detached=["e9"]),
        None,
        {"w0": True},
        (["c0", "c1"], False),
    ),
    (
        "blind exec and unmonitored call roots added",
        ["w0"],
        _rstate(["command_written"], True, blind=["e8"], unmonitored=["c4"]),
        None,
        {"w0": True},
        (["c2", "c1", "c4"], False),
    ),
    (
        "ambiguous J2 root candidates added",
        ["w1"],
        _rstate(["command_written"]),
        None,
        {"w1": False},
        (["c2", "c1"], False),
    ),
    (
        "an uncertain root",
        ["w0"],
        _rstate(["command_written"]),
        None,
        {"w0": False},
        (["c1"], False),
    ),
    (
        "no written state",
        ["w0"],
        _rstate(["pristine"]),
        None,
        {"w0": True},
        ([], True),
    ),
    (
        "no written state, unseen writer",
        ["w0"],
        _rstate(["pristine"], True),
        None,
        {"w0": True},
        ([], False),
    ),
    (
        "mtime candidates' roots added with an unseen writer",
        ["w0"],
        _rstate(["command_written"], True),
        ["e5"],
        {"w0": True},
        (["c1", "c3"], False),
    ),
    (
        "mtime candidates' roots not added without one",
        ["w0"],
        _rstate(["command_written"]),
        ["e5"],
        {"w0": True},
        (["c1"], True),
    ),
    (
        "sorted by seq",
        ["w0", "w2"],
        _rstate(["edit_written"]),
        None,
        {"w0": True, "w2": True},
        (["c2", "c1"], True),
    ),
)


@pytest.mark.parametrize(
    ("candidates", "state", "mtime", "certain", "want"),
    [row[1:] for row in ROOT_CASES],
    ids=[row[0] for row in ROOT_CASES],
)
def test_root_candidates(
    candidates: list[str],
    state: dict,
    mtime: list[str] | None,
    certain: dict,
    want: tuple[list[str], bool],
) -> None:
    """LW16: candidates' roots, unseen writers' roots, mtime roots.

    Complete only with no unseen writer and every candidate's root
    certain; empty when layer 4 says nothing was written. An unmonitored
    call is its own root; mtime candidates' roots are added only when
    unseen_writer_possible holds, as LW16's sentence reads.
    Rules: LW16.
    """
    from trajectory_checks import writer

    got = writer.root_candidates(
        candidates,
        events=ROOT_EVENTS,
        state=state,
        mtime=mtime,
        roots=ROOTS,
        certain=certain,
        seq=SEQ,
    )
    assert (list(got[0]), got[1]) == want


# --------------------------------------------------------- mtime_candidates

MTIME_WINDOWS = {
    "e1": (100.0, 103.0),
    "c2": (102.0, 104.0),
    "e3": (500.0, 503.0),
}
MTIMES = (
    ("inside one exec window", 101_500_000_000, ["e1"]),
    ("inside an exec and an edit window", 102_500_000_000, ["c2", "e1"]),
    ("on a window bound", 103_000_000_000, ["c2", "e1"]),
    ("on the other bound", 500_000_000_000, ["e3"]),
    ("outside every window", 300_000_000_000, []),
    ("no modification time", None, None),
)


@pytest.mark.parametrize(
    ("mtime_ns", "want"), [r[1:] for r in MTIMES], ids=[r[0] for r in MTIMES]
)
def test_mtime_candidates(mtime_ns: int | None, want: list[str] | None) -> None:
    """LW17: the ids whose window contains the time, bounds included.

    The order is the caller's (seq); the set is compared.
    Rules: LW17.
    """
    from trajectory_checks import writer

    got = writer.mtime_candidates(mtime_ns, MTIME_WINDOWS)
    if want is None:
        assert got is None
    else:
        assert sorted(got) == want
        assert len(set(got)) == len(got)


# -------------------------------------------------------------- named_paths


def _summary(
    path: str, state: str | None, tracked: bool | None = True, events=()
) -> dict:
    return {
        "path": path,
        "in_workspace": not path.startswith("/"),
        "tracked": tracked,
        "events": list(events),
        "final_writer": None,
        "final_writer_under": False,
        "final_state": state,
        "unattributed_candidates": [],
    }


NAMED_PATHS = {
    p["path"]: p
    for p in (
        _summary("tools/run.py", "changed"),
        _summary("sub/a.py", "changed"),
        _summary("sub/b", "changed"),
        _summary("c.py", "reverted"),
        _summary("a.py", "deleted"),
        _summary("logs/run.log", "untracked", False, ["w7"]),
        _summary("cfg/a.json", "changed"),
        _summary("out.txt", "untracked", False, ["w8"]),
        _summary("out/r.json", "changed"),
        _summary("scripts/x.sh", "changed"),
        _summary("/tmp/s.py", "untracked", False, ["w9"]),
    )
}
NAMED_EVENTS = {
    "w7": _event(7, 1.0, op="create", path="logs/run.log"),
    "w8": _event(8, 2.0, op="modify", path="out.txt"),
    "w9": _event(9, 3.0, op="create", path="/tmp/s.py"),
}
NAMED = (
    (
        "relative word",
        [("python3 tools/run.py", "/repo")],
        (["tools/run.py"], ["tools/run.py"]),
    ),
    (
        "absolute word",
        [("python3 /repo/tools/run.py", "/repo")],
        (["tools/run.py"], ["tools/run.py"]),
    ),
    (
        "./x, x/ and ../x against the cwd",
        [("python3 ./a.py b/ ../c.py", "/repo/sub")],
        (["c.py", "sub/a.py", "sub/b"], ["sub/a.py", "sub/b"]),
    ),
    (
        "no cwd: the workspace root",
        [("python3 a.py", None)],
        (["a.py"], ["a.py"]),
    ),
    (
        "heredoc body ignored, redirection target kept",
        [("cat > out.txt <<EOF\nscripts/x.sh\nEOF", "/repo")],
        (["out.txt"], []),
    ),
    (
        "redirection target, created untracked file",
        [("./gen > logs/run.log 2>&1", "/repo")],
        (["logs/run.log"], ["logs/run.log"]),
    ),
    (
        "a word inside $(...)",
        [("echo $(cat cfg/a.json)", "/repo")],
        (["cfg/a.json"], ["cfg/a.json"]),
    ),
    (
        "an oracle input excluded, a word without a row dropped",
        [("python3 tools/run.py --out out/r.json --tag v2", "/repo")],
        (["tools/run.py"], ["tools/run.py"]),
    ),
    (
        "an outside path created",
        [("python3 /tmp/s.py", "/repo")],
        (["/tmp/s.py"], ["/tmp/s.py"]),
    ),
    (
        "the writer and its ancestors",
        [("python3 a.py", "/repo"), ("bash scripts/x.sh", "/repo")],
        (["a.py", "scripts/x.sh"], ["a.py", "scripts/x.sh"]),
    ),
    ("no command", [], ([], [])),
)


@pytest.mark.parametrize(
    ("commands", "want"), [r[1:] for r in NAMED], ids=[r[0] for r in NAMED]
)
def test_named_paths(commands: list, want: tuple[list[str], list[str]]) -> None:
    """LW14: words and redirection targets with a paths row, resolved.

    Changed: final_state changed or deleted, or untracked with a create
    event; never reverted. Heredoc bodies are split off by
    facts.simple_commands; words inside substitutions are kept.
    Rules: LW14.
    """
    from trajectory_checks import writer

    got = writer.named_paths(
        commands,
        root="/repo",
        paths=NAMED_PATHS,
        events=NAMED_EVENTS,
        excluded={"out/r.json"},
    )
    assert (list(got[0]), list(got[1])) == want


# --------------------------------------------------------------- parse_diff

MODIFIED = """\
diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,3 @@
 import os
-x = 1
+x = 2
 y = 3
"""
ADDED = """\
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+one
+two
"""
DELETED = """\
diff --git a/old.txt b/old.txt
deleted file mode 100644
index 1234567..0000000
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""
RENAMED = """\
diff --git a/old/a.py b/new/a.py
similarity index 80%
rename from old/a.py
rename to new/a.py
index 1111111..2222222 100644
--- a/old/a.py
+++ b/new/a.py
@@ -1,2 +1,2 @@
 import os
-x = 1
+x = 5
"""
MOVED = """\
diff --git a/old/b.py b/new/b.py
similarity index 100%
rename from old/b.py
rename to new/b.py
"""
MODE = """\
diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
"""
BINARY = """\
diff --git a/img.png b/img.png
index 1111111..2222222 100644
Binary files a/img.png and b/img.png differ
diff --git a/blob.bin b/blob.bin
new file mode 100644
index 0000000..89abcde
GIT binary patch
literal 3
KcmZQzWMT#Y01f~L

literal 0
HcmV?d00001

"""
QUOTED = """\
diff --git "a/a b.txt" "b/a b.txt"
index 1111111..2222222 100644
--- "a/a b.txt"
+++ "b/a b.txt"
@@ -1 +1 @@
-x
+y
"""
SPACED_B = """\
diff --git a/x b/y.txt b/x b/y.txt
index 1111111..2222222 100644
--- a/x b/y.txt
+++ b/x b/y.txt
@@ -1 +1 @@
-x
+y
diff --git a/p b/q.bin b/p b/q.bin
index 3333333..4444444 100644
Binary files a/p b/q.bin and b/p b/q.bin differ
"""
NO_NEWLINE_OLD = """\
diff --git a/t.txt b/t.txt
index 1111111..2222222 100644
--- a/t.txt
+++ b/t.txt
@@ -1 +1 @@
-last
\\ No newline at end of file
+last
"""
NO_NEWLINE_NEW = """\
diff --git a/t.txt b/t.txt
index 1111111..2222222 100644
--- a/t.txt
+++ b/t.txt
@@ -1,2 +1,2 @@
 first
-last
+last
\\ No newline at end of file
"""
CRLF = (
    "diff --git a/w.txt b/w.txt\nindex 1111111..2222222 100644\n"
    "--- a/w.txt\n+++ b/w.txt\n@@ -1 +1 @@\n-a = 1\r\n+a = 1\n"
)


def _block(old: int, new: int, removed: list[str], added: list[str]) -> dict:
    return {
        "old_start": old,
        "new_start": new,
        "removed": removed,
        "added": added,
    }


def _file(
    path: str, status: str, blocks: list[dict], *, renamed=None, binary=False
) -> dict:
    return {
        "path": path,
        "renamed_from": renamed,
        "file_status": status,
        "binary": binary,
        "blocks": blocks,
    }


PARSE = (
    (
        "modified",
        MODIFIED,
        [_file("src/a.py", "modified", [_block(2, 2, ["x = 1"], ["x = 2"])])],
    ),
    (
        "added",
        ADDED,
        [_file("new.txt", "added", [_block(1, 1, [], ["one", "two"])])],
    ),
    (
        "deleted",
        DELETED,
        [_file("old.txt", "deleted", [_block(1, 1, ["gone"], [])])],
    ),
    (
        "renamed with a change",
        RENAMED,
        [
            _file(
                "new/a.py",
                "renamed",
                [_block(2, 2, ["x = 1"], ["x = 5"])],
                renamed="old/a.py",
            )
        ],
    ),
    (
        "renamed without a change",
        MOVED,
        [_file("new/b.py", "renamed", [], renamed="old/b.py")],
    ),
    ("mode only", MODE, [_file("run.sh", "modified", [])]),
    (
        "binary, both forms",
        BINARY,
        [
            _file("img.png", "modified", [], binary=True),
            _file("blob.bin", "added", [], binary=True),
        ],
    ),
    (
        "C-quoted path",
        QUOTED,
        [_file("a b.txt", "modified", [_block(1, 1, ["x"], ["y"])])],
    ),
    (
        "unquoted path holding ' b/' (D52)",
        SPACED_B,
        [
            _file("x b/y.txt", "modified", [_block(1, 1, ["x"], ["y"])]),
            _file("p b/q.bin", "modified", [], binary=True),
        ],
    ),
    (
        "no newline, old side",
        NO_NEWLINE_OLD,
        [_file("t.txt", "modified", [_block(1, 1, ["last"], ["last"])])],
    ),
    (
        "no newline, new side",
        NO_NEWLINE_NEW,
        [_file("t.txt", "modified", [_block(2, 2, ["last"], ["last"])])],
    ),
    (
        "CRLF line",
        CRLF,
        [_file("w.txt", "modified", [_block(1, 1, ["a = 1\r"], ["a = 1"])])],
    ),
    ("empty diff", "", []),
)


@pytest.mark.parametrize(
    ("text", "want"), [r[1:] for r in PARSE], ids=[r[0] for r in PARSE]
)
def test_parse_diff(text: str, want: list[dict]) -> None:
    """HK1 to HK3: sections, status, rename, binary, blocks.

    Block lines hold the text after the +/- marker without the line
    terminator; a "No newline at end of file" line is dropped (adopted
    reading), so an end-of-line change shows as two equal sides. A
    trailing CR is text. `binary` agrees with structure.binary_sections
    on the same text.
    Rules: HK1, HK2, HK3.
    """
    from trajectory_checks import writer

    got = writer.parse_diff(text)
    assert [dict(f) for f in got] == want
    sections = structure.binary_sections(text)
    for found in got:
        assert found["binary"] == sections[found["path"]]


@pytest.mark.parametrize("context", ["u0", "u3", "u10"])
def test_parse_diff_context_fixtures(context: str) -> None:
    """The same edit at three context sizes gives the same blocks.

    Rules: HK3.
    """
    from trajectory_checks import writer

    text = (helpers.DIFF_FIXTURES_DIR / f"context.{context}.diff").read_text()
    (found,) = writer.parse_diff(text)
    assert found["blocks"] == [
        _block(2, 2, ["b = 2"], ["b = 20"]),
        _block(5, 5, [], ["x = 0"]),
        _block(16, 17, ["p = 16"], []),
    ]


def test_parse_diff_example_b() -> None:
    """Example B's diff (fixture) gives the spec's hunks, file by file.

    Rules: HK1, HK3, HK5, HK8.
    """
    from trajectory_checks import writer

    text = (helpers.DIFF_FIXTURES_DIR / "example_b.diff").read_text()
    want = json.loads(
        (helpers.DIFF_FIXTURES_DIR / "example_b.hunks.json").read_text()
    )
    got = []
    for found in writer.parse_diff(text):
        if found["binary"]:
            assert found["path"] == "data/big.bin"
            continue
        language = writer.language_of(found["path"])
        for index, block in enumerate(found["blocks"]):
            kind, touches, lexed = writer.hunk_type(block, language)
            got.append(
                {
                    "path": found["path"],
                    "hunk_index": index,
                    "file_status": found["file_status"],
                    "renamed_from": found["renamed_from"],
                    "language": language,
                    "old_start": block["old_start"],
                    "new_start": block["new_start"],
                    "lines_removed": len(block["removed"]),
                    "lines_added": len(block["added"]),
                    "hunk_type": kind,
                    "touches_check": touches,
                    "lexed": lexed,
                }
            )
    assert got == want


# ------------------------------------------------------------ change_blocks

BLOCKS = (
    (
        "two blocks in one section",
        1,
        1,
        [" a", "-b", "+B", " c", "-d", " e"],
        [_block(2, 2, ["b"], ["B"]), _block(4, 4, ["d"], [])],
    ),
    ("an empty old side (-4,0 +5)", 4, 5, ["+x"], [_block(5, 5, [], ["x"])]),
    (
        "an empty new side (-10 +9,0)",
        10,
        9,
        ["-y"],
        [_block(10, 10, ["y"], [])],
    ),
    (
        "an empty file (-0,0 +1,2)",
        0,
        1,
        ["+p", "+q"],
        [_block(1, 1, [], ["p", "q"])],
    ),
    (
        "removed then added lines",
        3,
        3,
        ["-a", "-b", "+c"],
        [_block(3, 3, ["a", "b"], ["c"])],
    ),
    ("context only", 1, 1, [" a", " b"], []),
)


@pytest.mark.parametrize(
    ("old", "new", "lines", "want"),
    [r[1:] for r in BLOCKS],
    ids=[r[0] for r in BLOCKS],
)
def test_change_blocks(
    old: int, new: int, lines: list[str], want: list[dict]
) -> None:
    """HK3: maximal runs of -/+ lines; an empty side starts after git's.

    git writes the start of an empty side as the line before the change;
    old_start and new_start are the line that follows it (adopted reading
    of HK3, so -U0 and -U3 agree).
    Rules: HK3.
    """
    from trajectory_checks import writer

    assert [dict(b) for b in writer.change_blocks(old, new, lines)] == want


# -------------------------------------------------------------- language_of

LANGUAGES_BY_PATH = (
    ("src/a.c", "c_family"),
    ("x/y.hpp", "c_family"),
    ("k.java", "c_family"),
    ("a.ts", "script_family"),
    ("main.go", "script_family"),
    ("lib.rs", "rust"),
    ("a.py", "python"),
    ("t.pyi", "python"),
    ("run.sh", "shell"),
    ("x.zsh", "shell"),
    ("rules.mk", "make"),
    ("Makefile", "make"),
    ("sub/makefile", "make"),
    ("GNUmakefile", "make"),
    ("p.dfy", "dafny"),
    ("top.sv", "verilog"),
    ("a.lean", "lean"),
    ("a.jl", "julia"),
    ("c.toml", "config"),
    ("c.yml", "config"),
    ("d.json", "json"),
    ("paper.tex", "tex"),
    ("A.PY", "python"),
    ("Main.C", "c_family"),
    ("m.v", None),
    ("notes.md", None),
    ("data.xyz", None),
    ("README", None),
)


@pytest.mark.parametrize(("path", "want"), LANGUAGES_BY_PATH)
def test_language_of(path: str, want: str | None) -> None:
    """HK5: basename first, then the lower-cased extension; else None.

    Rules: HK5.
    """
    from trajectory_checks import writer

    assert writer.language_of(path) == want


# --------------------------------------------------------------------- lex


def _code(tokens: list) -> list[str]:
    return [t.text for t in tokens if t.token_type != "comment"]


LEX = (
    (
        "c line comment",
        ["int a = 1; // note"],
        "c_family",
        ["int", "a", "=", "1", ";"],
        ["// note"],
        True,
    ),
    (
        "python line comment",
        ["x = 1  # c"],
        "python",
        ["x", "=", "1"],
        ["# c"],
        True,
    ),
    (
        "shell line comment",
        ["echo hi # c"],
        "shell",
        ["echo", "hi"],
        ["# c"],
        True,
    ),
    ("lean line comment", ["def x := 1 -- c"], "lean", None, ["-- c"], True),
    ("tex comment", [r"\section{A} % c"], "tex", None, ["% c"], True),
    ("julia block comment", ["x #= c =# y"], "julia", ["x", "y"], None, True),
    (
        "block comment across two lines",
        ["a /* one", "two */ b"],
        "c_family",
        ["a", "b"],
        None,
        True,
    ),
    (
        "string with a comment marker",
        ['s = "a # b"'],
        "python",
        ["s", "=", '"a # b"'],
        [],
        True,
    ),
    (
        "number with exponent and suffix",
        ["x = 1.5e-3f;"],
        "c_family",
        ["x", "=", "1.5e-3f", ";"],
        [],
        True,
    ),
    (
        "hex number",
        ["y = 0x1Fu;"],
        "c_family",
        ["y", "=", "0x1Fu", ";"],
        [],
        True,
    ),
    ("operator <", ["a < b"], "c_family", ["a", "<", "b"], [], True),
    ("operator <=", ["a <= b"], "c_family", ["a", "<=", "b"], [], True),
    ("operator <<", ["a << b"], "c_family", ["a", "<<", "b"], [], True),
    (
        "rust macro",
        ["assert!(a != b);"],
        "rust",
        ["assert!", "(", "a", "!=", "b", ")", ";"],
        [],
        True,
    ),
    (
        "unterminated block comment",
        ["a /* open"],
        "c_family",
        None,
        None,
        False,
    ),
    ("unterminated string", ['x = "abc'], "python", None, None, False),
)


@pytest.mark.parametrize(
    ("lines", "language", "code", "comments", "lexed"),
    [r[1:] for r in LEX],
    ids=[r[0] for r in LEX],
)
def test_lex(
    lines: list[str],
    language: str,
    code: list[str] | None,
    comments: list[str] | None,
    lexed: bool,
) -> None:
    """HK6: comments, strings, numbers, identifiers, longest operators.

    Comment segments are compared when listed; a block comment's
    segmentation across lines is the implementation's. An unterminated
    block comment or string sets lexed to false.
    Rules: HK6, HK9.
    """
    from trajectory_checks import writer

    tokens, ok = writer.lex(lines, language)
    assert ok is lexed
    if code is not None:
        assert _code(tokens) == code
    if comments is not None:
        assert [t.text for t in tokens if t.token_type == "comment"] == comments
    for token in tokens:
        assert token.token_type in (
            "comment",
            "string",
            "number",
            "word",
            "operator",
        )
        assert 0 <= token.line < len(lines)
        assert token.text.strip() == token.text or token.token_type in (
            "comment",
            "string",
        )


def test_lex_token_types_and_lines() -> None:
    """Token types and line indices of a two-line side.

    Rules: HK6.
    """
    from trajectory_checks import writer

    tokens, ok = writer.lex(["x = 10  # n", 'y = "s"'], "python")
    assert ok
    assert [tuple(t) for t in tokens] == [
        ("x", "word", 0),
        ("=", "operator", 0),
        ("10", "number", 0),
        ("# n", "comment", 0),
        ("y", "word", 1),
        ("=", "operator", 1),
        ('"s"', "string", 1),
    ]


def test_lex_generic_rule() -> None:
    """A ⊥ language: words, numbers and single punctuation; not lexed.

    Rules: HK5, HK6.
    """
    from trajectory_checks import writer

    tokens, ok = writer.lex(["rows=100 x.y"], None)
    assert ok is False
    assert [(t.text, t.token_type) for t in tokens] == [
        ("rows", "word"),
        ("=", "operator"),
        ("100", "number"),
        ("x", "word"),
        (".", "operator"),
        ("y", "word"),
    ]


# ------------------------------------------------------------ is_check_line

CHECKS = (
    ("c assert", "assert(x);", "c_family", True),
    ("python assert", "assert x", "python", True),
    ("rust assert_eq!", "assert_eq!(a, b);", "rust", True),
    ("julia @assert", "@assert x > 0", "julia", True),
    ("dafny ensures", "ensures r >= 0", "dafny", True),
    ("c if with comparison", "if (a < b) {", "c_family", True),
    ("python if with ==", "if a == b:", "python", True),
    ("python elif", "elif a != b:", "python", True),
    ("shell test operator", 'if [ "$a" -lt 3 ]; then', "shell", True),
    ("shell == inside [[ ]]", "[[ $a == b ]] && echo y", "shell", True),
    ("shell == inside [ ]", 'if [ "$a" == b ]; then', "shell", False),
    ("comparison without a condition", "x = a < b;", "c_family", False),
    ("python comparison without a condition", "y = a == b", "python", False),
    ("shell redirection >>", "echo x >> log", "shell", False),
    (
        "shell redirection under if",
        "if true; then echo x > f; fi",
        "shell",
        False,
    ),
    ("template brackets", "std::vector<int> v;", "c_family", False),
    ("make conditional without comparison", "ifeq ($(X),1)", "make", False),
    ("comment only", "// assert(x);", "c_family", False),
    ("verilog <= is an assignment", "if (en) q <= d;", "verilog", False),
    ("a library call", "if allclose(a, b):", "python", False),
)


@pytest.mark.parametrize(
    ("line", "language", "want"),
    [r[1:] for r in CHECKS],
    ids=[r[0] for r in CHECKS],
)
def test_is_check_line(line: str, language: str, want: bool) -> None:
    """HK7: an assertion keyword, or a condition keyword and a comparison.

    Rules: HK7, HK9.
    """
    from trajectory_checks import writer

    tokens, ok = writer.lex([line], language)
    assert ok
    assert writer.is_check_line(tokens, language) is want


def test_is_check_line_without_language() -> None:
    """A ⊥ language has no keywords, so no line is a check line.

    Rules: HK7.
    """
    from trajectory_checks import writer

    tokens, _ = writer.lex(["assert x < 1"], None)
    assert writer.is_check_line(tokens, None) is False


# ---------------------------------------------------------------- hunk_type


def _hunk(removed: list[str], added: list[str]) -> dict:
    return _block(1, 1, removed, added)


HUNKS = (
    # whitespace_only
    ("ws c", ["{   "], ["{"], "c_family", ("whitespace_only", False, True)),
    (
        "ws python",
        ["x=1"],
        ["x = 1"],
        "python",
        ("whitespace_only", False, True),
    ),
    ("ws none", ["a  b"], ["a b"], None, ("whitespace_only", None, False)),
    (
        "ws blank line removed",
        ["   "],
        [],
        "python",
        ("whitespace_only", False, True),
    ),
    ("ws blank line added", [], [""], None, ("whitespace_only", None, False)),
    # whitespace that is syntax is no whitespace_only change (D51)
    (
        "ws inside a string",
        ['s = "a b";'],
        ['s = "ab";'],
        "c_family",
        ("general", False, True),
    ),
    (
        "ws joins two words",
        ["int x;"],
        ["intx;"],
        "c_family",
        ("general", False, True),
    ),
    (
        "ws python indentation",
        ["    y = 1"],
        ["y = 1"],
        "python",
        ("general", False, True),
    ),
    (
        "ws yaml indentation",
        ["  key: 1"],
        ["key: 1"],
        "config",
        ("general", False, True),
    ),
    (
        "ws line break in shell",
        ["rm -f a b"],
        ["rm -f a", "b"],
        "shell",
        ("general", False, True),
    ),
    (
        "ws none in a quote",
        ['x = " "'],
        ['x = ""'],
        None,
        ("general", None, False),
    ),
    (
        "ws none indentation",
        ["  a"],
        ["a"],
        None,
        ("general", None, False),
    ),
    (
        "ws splits a shell word",
        ["a=1"],
        ["a = 1"],
        "shell",
        ("general", False, True),
    ),
    (
        "ws splits a shell word, numeric",
        ["a=1"],
        ["a = 2"],
        "shell",
        ("general", False, True),
    ),
    (
        "ws between shell words",
        ["echo  a   b"],
        ["echo a b"],
        "shell",
        ("whitespace_only", False, True),
    ),
    (
        "ws inside a shell string",
        ["echo 'a  b'"],
        ["echo 'a b'"],
        "shell",
        ("general", False, True),
    ),
    (
        "ws python indentation, numeric",
        ["    y = 1"],
        ["y = 2"],
        "python",
        ("general", False, True),
    ),
    (
        "ws c indentation",
        ["  f(x);"],
        ["\tf( x );"],
        "c_family",
        ("whitespace_only", False, True),
    ),
    (
        "ws in a python comment",
        ["x = 1  # a  b"],
        ["x = 1 # a b"],
        "python",
        ("whitespace_only", False, True),
    ),
    # comment_only
    (
        "comment c",
        ["x = 1; // a"],
        ["x = 1; // b"],
        "c_family",
        ("comment_only", False, True),
    ),
    (
        "comment python",
        ["x = 1  # a"],
        ["x = 1  # b"],
        "python",
        ("comment_only", False, True),
    ),
    (
        "comment none is general",
        ["x # a"],
        ["x # b"],
        None,
        ("general", None, False),
    ),
    (
        "comment line added",
        [],
        ["# note"],
        "python",
        ("comment_only", False, True),
    ),
    (
        "comment line removed",
        ["// old"],
        [],
        "c_family",
        ("comment_only", False, True),
    ),
    # comment_toggle
    (
        "toggle python",
        ["    f(x)"],
        ["    # f(x)"],
        "python",
        ("comment_toggle", False, True),
    ),
    (
        "toggle c",
        ["g();"],
        ["// g();"],
        "c_family",
        ("comment_toggle", False, True),
    ),
    (
        "toggle c block",
        ["g();"],
        ["/* g(); */"],
        "c_family",
        ("comment_toggle", False, True),
    ),
    (
        "toggle back in",
        ["# assert x"],
        ["assert x"],
        "python",
        ("comment_toggle", True, True),
    ),
    (
        "toggle none is general",
        ["f(x)"],
        ["# f(x)"],
        None,
        ("general", None, False),
    ),
    # numeric_only
    (
        "numeric python",
        ["n = 10"],
        ["n = 20"],
        "python",
        ("numeric_only", False, True),
    ),
    ("numeric shell", ["N=1"], ["N=2"], "shell", ("numeric_only", False, True)),
    (
        "numeric none",
        ["rows 100"],
        ["rows 200"],
        None,
        ("numeric_only", None, False),
    ),
    (
        "numeric in a check",
        ["assert err < 0.01"],
        ["assert err < 0.1"],
        "python",
        ("numeric_only", True, True),
    ),
    (
        "a sign is not a number",
        ["x = 30"],
        ["x = -30"],
        "python",
        ("general", False, True),
    ),
    # comparison_relaxed
    (
        "relaxed c",
        ["if (a < b) {"],
        ["if (a <= b) {"],
        "c_family",
        ("comparison_relaxed", True, True),
    ),
    (
        "relaxed python ==",
        ["if a == b:"],
        ["if a >= b:"],
        "python",
        ("comparison_relaxed", True, True),
    ),
    (
        "relaxed shell",
        ['if [ "$a" -lt 3 ]; then'],
        ['if [ "$a" -le 3 ]; then'],
        "shell",
        ("comparison_relaxed", True, True),
    ),
    (
        "relaxed verilog >",
        ["if (a > b)"],
        ["if (a >= b)"],
        "verilog",
        ("comparison_relaxed", True, True),
    ),
    (
        "tightened is not relaxed",
        ["if (a <= b) {"],
        ["if (a < b) {"],
        "c_family",
        ("general", True, True),
    ),
    (
        "relaxed none is general",
        ["a < b"],
        ["a <= b"],
        None,
        ("general", None, False),
    ),
    # check_removed
    (
        "check removed rust",
        ["assert!(a == b);"],
        [],
        "rust",
        ("check_removed", True, True),
    ),
    (
        "check removed python",
        ["assert x > 0", "y = 1"],
        ["y = 1"],
        "python",
        ("check_removed", True, True),
    ),
    (
        "check removed c",
        ["if (e > tol) return 1;"],
        ["return 1;"],
        "c_family",
        ("check_removed", True, True),
    ),
    (
        "check kept is general",
        ["assert x > 0", "y = 1"],
        ["assert x > 0", "y = 2 + z"],
        "python",
        ("general", True, True),
    ),
    (
        "check removed none is a deletion",
        ["assert x"],
        [],
        None,
        ("pure_deletion", None, False),
    ),
    # pure_deletion
    (
        "deletion python",
        ["y = 1"],
        [],
        "python",
        ("pure_deletion", False, True),
    ),
    ("deletion c", ["int z;"], [], "c_family", ("pure_deletion", False, True)),
    ("deletion none", ["text"], [], None, ("pure_deletion", None, False)),
    # pure_addition
    (
        "addition python",
        [],
        ["y = 1"],
        "python",
        ("pure_addition", False, True),
    ),
    ("addition c", [], ["int z;"], "c_family", ("pure_addition", False, True)),
    ("addition none", [], ["text"], None, ("pure_addition", None, False)),
    (
        "addition of a check",
        [],
        ["assert x"],
        "python",
        ("pure_addition", True, True),
    ),
    # general
    (
        "general python",
        ["print(z)"],
        ["log(z)"],
        "python",
        ("general", False, True),
    ),
    (
        "general c",
        ["a = b;"],
        ["a = c + 1;"],
        "c_family",
        ("general", False, True),
    ),
    ("general none", ["foo"], ["bar"], None, ("general", None, False)),
    # precedence pairs and lexing failures
    (
        "whitespace before comment",
        ["x = 1 # a b"],
        ["x = 1 # ab"],
        "python",
        ("whitespace_only", False, True),
    ),
    (
        "unterminated comment: as a ⊥ language",
        ["a /* x"],
        ["b /* x"],
        "c_family",
        ("general", None, False),
    ),
    (
        "unterminated comment, numeric",
        ["n = 1 /* x"],
        ["n = 2 /* x"],
        "c_family",
        ("numeric_only", None, False),
    ),
)


@pytest.mark.parametrize(
    ("removed", "added", "language", "want"),
    [r[1:] for r in HUNKS],
    ids=[r[0] for r in HUNKS],
)
def test_hunk_type(
    removed: list[str], added: list[str], language: str | None, want: tuple
) -> None:
    """HK8: the first type of HUNK_TYPES that holds; touches_check (HK7).

    Each type in two languages and a language-less file; earlier types
    win; touches_check is None when the block is not lexed (HK6).
    Rules: HK6, HK7, HK8, HK9.
    """
    from trajectory_checks import writer

    assert writer.hunk_type(_hunk(removed, added), language) == want


# -------------------------------------------------------- input_peer_ratios


def _peer_row(
    run_key: str, input_id: str, value: float | None, pattern=None
) -> dict:
    return {
        "run_key": run_key,
        "input_id": input_id,
        "path": "results/t.json",
        "pattern": pattern,
        "last_writer_type": "exec",
        "last_writer_role": "root_exec",
        "last_writer_under": True,
        "M3.3.writer_duration_s": value,
        "M3.3.root_span_over_s": value,
        "M2.4.rewrites_over": 0,
    }


PEER_ROWS = [
    _peer_row("art__claude__1", "i0", 1.0),
    _peer_row("art__claude__2", "i3", 2.0),
    _peer_row("other__claude__1", "i0", 5.0),
    _peer_row("art__codex__1", "i1", 10.0),
    _peer_row("art__claude__3", "i2", 7.0, pattern="*.json"),
    _peer_row("art__claude__4", "i0", None),
]


def test_input_peer_ratios() -> None:
    """PEER2: per (path, pattern), over the same artifact's other runs.

    Values 1, 2, 10 give 1/6, 2/5.5, 10/1.5; another artifact, another
    pattern and a ⊥ value are not peers; a ⊥ value has no ratio; a peer
    median of 0 gives none; the input order is kept.
    Rules: PEER2.
    """
    from trajectory_checks import writer

    got = writer.input_peer_ratios(PEER_ROWS)
    assert [r["run_key"] for r in got] == [r["run_key"] for r in PEER_ROWS]
    ratios = [r["M3.3.writer_duration_s_peer_ratio"] for r in got]
    assert ratios == [0.166667, 0.363636, None, 6.666667, None, None]
    assert [r["M3.3.root_span_over_s_peer_ratio"] for r in got] == ratios
    assert all(r["M2.4.rewrites_over_peer_ratio"] is None for r in got)
    assert not any("last_writer_under_peer_ratio" in r for r in got)
    for row, given in zip(got, PEER_ROWS, strict=True):
        assert {k: row[k] for k in given} == given


def test_input_peer_ratios_min_peers() -> None:
    """Fewer than min_peers peers give no ratio.

    Rules: PEER2.
    """
    from trajectory_checks import writer

    got = writer.input_peer_ratios(PEER_ROWS, {"min_peers": 3})
    assert all(r["M3.3.writer_duration_s_peer_ratio"] is None for r in got)
    assert writer.input_peer_ratios([]) == []


# ------------------------------------------------------------------ tables


def test_tables() -> None:
    """§3.3's lookup tables: values, order and immutability; no DEFAULTS.

    Rules: HK5, HK8, LW9, LW12, LW16, PRE6.
    """
    from trajectory_checks import facts, writer

    assert writer.HUNK_TYPES == helpers.HUNK_TYPES
    assert writer.RISK_REASONS == helpers.RISK_REASONS
    assert writer.NOT_UNDER_REASONS == helpers.NOT_UNDER_REASONS
    assert writer.DIFFERS_CAUSES == ("oracle", "late_process")
    assert writer.WRITTEN_STATES == helpers.WRITTEN_STATES
    assert writer.CONTENT_OPS == helpers.CONTENT_OPS
    assert writer.EXISTING_STATES == helpers.EXISTING_STATES
    assert tuple(writer.INPUT_COLUMNS) == helpers.INPUT_COLUMNS
    for name in (
        *("HUNK_TYPES", "RISK_REASONS", "NOT_UNDER_REASONS"),
        *("DIFFERS_CAUSES", "INPUT_COLUMNS", "LANGUAGES"),
    ):
        assert isinstance(getattr(writer, name), tuple), name
    for name in ("WRITTEN_STATES", "CONTENT_OPS", "EXISTING_STATES"):
        assert isinstance(getattr(writer, name), frozenset), name
    assert isinstance(writer.DICT_FIELDS, types.MappingProxyType)
    assert not hasattr(writer, "DEFAULTS")
    names = [row.name for row in writer.LANGUAGES]
    assert names == [
        *("c_family", "script_family", "rust", "python", "shell", "make"),
        *("dafny", "verilog", "lean", "julia", "config", "json", "tex"),
    ]
    fields = writer.DICT_FIELDS
    for key in ("last_writer_by_role_under", "last_writer_by_role_over"):
        assert tuple(fields[key]) == facts.ROLES
    for key in (
        *("hunks_by_type", "files_by_type", "hunks_by_type_named_under"),
        *("hunks_by_type_named_over", "hunks_by_type_read"),
    ):
        assert tuple(fields[key]) == helpers.HUNK_TYPES
    pairs = list(fields["hunks_by_class_and_type"])
    assert len(pairs) == 81
    assert pairs == [
        f"{c}.{t}" for c in structure.FILE_CLASSES for t in helpers.HUNK_TYPES
    ]
    assert set(fields) == {
        *("last_writer_by_role_under", "last_writer_by_role_over"),
        *("hunks_by_type", "files_by_type", "hunks_by_type_named_under"),
        *("hunks_by_type_named_over", "hunks_by_type_read"),
        "hunks_by_class_and_type",
    }
    assert writer.NUMBER.fullmatch("1.5e-3f")
    assert writer.NUMBER.fullmatch("0x1Fu")
    assert not writer.NUMBER.fullmatch("-30")


def test_token_is_a_named_tuple() -> None:
    """Token is (text, token_type, line).

    Rules: HK6.
    """
    from trajectory_checks import writer

    token = writer.Token("x", "word", 0)
    assert tuple(token) == ("x", "word", 0)
    assert writer.Token._fields == ("text", "token_type", "line")
