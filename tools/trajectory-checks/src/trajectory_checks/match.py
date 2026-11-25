"""T2 to T6: integrity, the three joins J1 to J3, and coverage.

`link` is the main entry point. It reads normalized Call and Exec records
(parse.py) and returns links, dispositions, per-path summaries and the
coverage report of one run (spec §5.3 to §7).
"""

import bisect
import collections
import posixpath
import types
import typing

from trajectory_checks import parse


class RunInfo(typing.TypedDict):
    """The run facts `link` needs (spec §5.6, decision D26).

    Every field is present; a field the run's monitor did not record is
    None. `capabilities` maps each name of CAPABILITIES to a bool.
    """

    t_start: float | None
    t_end: float | None
    workspace_root: str
    trace_ok: bool
    run_key: str | None
    family: str | None
    log_ok: bool | None
    monitor_version: str | None  # MC-09
    trace_meta: dict | None  # MC-09
    final_diff_content: str | None  # MC-03
    final_workspace: str | None
    oracle_reads: str | None  # MC-02
    case_image_digest: str | None
    commit: str | None
    shim_intact: bool | None  # MC-17
    unattributed_reads: list[dict] | None  # MC-14, no command_id
    capabilities: dict[str, bool]


class CallExecLink(typing.TypedDict):
    """J1: one shell call matched to one root exec."""

    link_type: str
    call_id: str
    command_id: str
    rule: str  # exact, quoting_equivalent, prefix
    dt_s: float


class DuplicateLink(typing.TypedDict):
    """Codex: an inner record repeating its outer, matched record."""

    link_type: str
    command_id: str
    of: str


class ParentLink(typing.TypedDict):
    """J2: the parent exec and root call of a non-matched exec."""

    link_type: str
    command_id: str
    parent: str | None
    root_call_id: str
    rule: str  # time_containment, reparenting
    root_ambiguous: bool
    root_candidates: list[str]


class WriteEvent(typing.TypedDict):
    """J3: one (path, writer) pair with the interval of the write."""

    link_type: str
    event_id: str
    path: str
    in_workspace: bool
    op: str  # modify, create, delete, mode
    scope: str | None
    writer_type: str  # exec, edit_call
    writer_id: str
    t_start: float
    t_end: float
    order_t: float  # the ordering key: the measured end (D32)
    root_call_id: str | None
    root_ambiguous: bool
    root_candidates: list[str]
    depth_rule: str  # none, parent_may_also_write
    possibly_edit_call: str | None
    overlaps_sibling: bool
    source: str  # snapshot, edit_call
    writer_under: bool


Link = CallExecLink | DuplicateLink | ParentLink | WriteEvent


class Disposition(typing.TypedDict):
    """The class of one shell call or exec, with per-item coverage flags."""

    item_type: str  # call, exec
    id: str
    disposition: str
    reason: str | None
    matched: bool | None  # calls only
    match_under: bool | None  # calls only
    snapshot_ok: bool | None  # execs only
    root_under: bool | None  # execs only


class PathSummary(typing.TypedDict):
    """All writers of one path, its final writer and its final state."""

    path: str
    in_workspace: bool
    tracked: bool | None
    events: list[str]
    final_writer: str | None
    final_writer_under: bool
    final_state: str | None
    unattributed_candidates: list[dict[str, str]]


class Coverage(typing.TypedDict):
    """coverage.json (spec §7.1)."""

    run_key: str | None
    family: str | None
    inputs: dict[str, bool]
    integrity: dict[str, typing.Any]
    capabilities: dict[str, bool]
    counts: dict[str, typing.Any]
    rates: dict[str, typing.Any]
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    warnings: list[str]


class LinkResult(typing.TypedDict):
    """Everything T2 to T6 produce for one run."""

    links: list[Link]
    dispositions: list[Disposition]
    paths: list[PathSummary]
    coverage: Coverage


# Each default with its basis (spec §6.7).
DEFAULTS = types.MappingProxyType(
    {
        # Design §3.4: start offsets p5-p95 within -0.1..+1.0 s at 1 s logs.
        "window_before_s": 5.0,
        "window_after_s": 5.0,
        # Upper bound for a call without t_end: harness Bash timeout (§3.2).
        "window_open_s": 3600.0,
        # J1 rule prefix: design §3.4 fallback length; not measured.
        "prefix_chars": 200,
        # running_at_stop: t_end within 5 s of the run's end (§5.4).
        "stop_margin_s": 5.0,
        # C0.2 run window margin and threshold (design §3.5).
        "run_window_margin_s": 60.0,
        "run_window_share_warn": 0.95,
        # Coverage preconditions on under-approximated rates (§7.2, D10).
        "join_rate_warn": 0.9,
        # New; to be set from the measured distribution.
        "snapshot_coverage_warn": 0.8,
        # J3 observation window: the snapshot walk's 2 s cap (D33).
        "snapshot_slop_s": 2.0,
    }
)
# Spec §5.6: what a run's records and folder actually hold.
CAPABILITIES = (
    *("parent_links", "start_events", "exact_timing", "content_hashes"),
    *("untracked_visible", "read_tracing", "final_content", "oracle_reads"),
    *("egress_log", "stdio"),
)
# (label, rate key, threshold, metrics voided) of spec §7.2.
PRECONDITIONS = (
    ("C1_under", "call_match_rate_under", ">=", "M1, M2, M5.2, M6.1"),
    ("C2_under", "exec_explained_rate_under", ">=", "M1.4"),
    ("C7_under", "final_diff_attributed_rate_under", ">=", "M4.1, M4.2"),
    ("C6", "listed_path_rate", "=", "M4.1, M4.2"),
)
LINK_ORDER = ("call_exec", "wrapper_duplicate", "exec_parent", "write_event")
UNDER_RULES = ("exact", "quoting_equivalent")
EXEC_DISPOSITIONS = (
    *("matched_root", "wrapper_duplicate", "nested", "noise", "unrooted"),
    "out_of_window",
)
# The exec disposition each J1 or J2 link type gives its command_id.
_LINK_DISPOSITION = types.MappingProxyType(
    {
        "call_exec": "matched_root",
        "wrapper_duplicate": "wrapper_duplicate",
        "exec_parent": "nested",
    }
)
# The link fields coverage counts by value.
_LINK_FIELDS = (
    *("link_type", "rule", "root_ambiguous", "writer_type", "in_workspace"),
    *("depth_rule", "possibly_edit_call"),
)
_FLAGS = ("matched", "match_under", "snapshot_ok", "root_under")
_DIFF_KEYS = ("modified", "deleted", "mode_changed")
_OPS = (("create", "created"), ("modify", "modified"), ("delete", "deleted"))
_STATES = (
    ("changed", "modified"),
    ("deleted", "deleted"),
    ("mode_only", "mode_changed"),
)
_EVENT_FIELDS = (
    *("path", "in_workspace", "op", "scope", "writer_type", "writer_id"),
    *("t_start", "t_end"),
)
# Final states in which the file exists at the end (decision D34).
_EXISTING = ("changed", "mode_only", "reverted")
_STARTERS = ("nohup", "setsid", "disown")
_CONTROL = (";", "&&", "||", "|", "&", "(", "{", "!", "then", "do", "else")


def _containing(
    execs: list[parse.Exec], t: float, ends: dict[str, float]
) -> list[parse.Exec]:
    """Returns the execs whose [t_start, end] contains time t.

    Args:
        execs: Execs sorted by t_start.
        t: The time.
        ends: End time per command_id (incomplete execs extended).

    Returns:
        The containing execs, in start order.
    """
    stop = bisect.bisect_right([e["t_start"] for e in execs], t)
    return [e for e in execs[:stop] if ends[e["command_id"]] >= t]


def backgrounds(command: str) -> bool:
    """Whether a command backgrounds something, by shell syntax only.

    Public (decision D31): layer 4 must decide it exactly as J2 does.

    Args:
        command: Shell text.

    Returns:
        True for a `&` control operator or a leading nohup, setsid or disown.
    """
    for line in command.split("\n"):
        words = parse.tokens(line)
        for i, word in enumerate(words):
            if word == "&" or (
                word in _STARTERS and (i == 0 or words[i - 1] in _CONTROL)
            ):
                return True
    return False


def _integrity(
    execs: list[parse.Exec], run: RunInfo, baseline: dict | None, cfg: dict
) -> tuple[dict, set[str], list[str]]:
    """C0.1, C0.2, C0.5 (decision F2), the out-of-window execs, warnings."""
    counted = [e for e in execs if e["noise"] is None]
    margin = cfg["run_window_margin_s"]
    outside: set[str] = set()
    if run["t_start"] is not None and run["t_end"] is not None:
        low, high = run["t_start"] - margin, run["t_end"] + margin
        outside = {
            e["command_id"] for e in counted if not low <= e["t_start"] <= high
        }
    share = None
    if counted and run["t_start"] is not None:
        share = 1 - len(outside) / len(counted)
    present = bool(run["trace_ok"] or counted)
    warnings = []
    if not present:
        warnings.append("trace missing: trace-derived outputs null")
    elif not run["trace_ok"]:
        warnings.append(
            "integrity C0.1 failed: trace belongs to another "
            "run; trace-derived outputs null"
        )
    if share is not None and share < cfg["run_window_share_warn"]:
        warnings.append(
            f"integrity C0.2 {share:.2f} < {cfg['run_window_share_warn']}"
        )
    if baseline is None:
        warnings.append("integrity C0.5 failed: tracked_files.json missing")
    integrity = {
        "C0.1": bool(run["trace_ok"]) if present else None,
        "C0.2": share,
        "C0.5": baseline is not None,
    }
    return integrity, outside, warnings


def _duplicate(
    outer: parse.Exec,
    execs: list[parse.Exec],
    starts: list[float],
    used: set[str],
    ends: dict[str, float],
) -> list[Link]:
    """The Codex inner record repeating a snapshot-wrapped outer record.

    Decision D25: the earliest unused record with the same command_norm
    that is not itself snapshot-wrapped and starts within the outer's
    interval [t_start, end].

    Args:
        outer: The matched codex_snapshot_exec record.
        execs: Candidate execs (non-noise, in the run window), sorted.
        starts: Their start times.
        used: Execs already linked; the duplicate is added.
        ends: End time per command_id (incomplete execs extended).

    Returns:
        The wrapper_duplicate link, or no link.
    """
    first = bisect.bisect_left(starts, outer["t_start"])
    last = bisect.bisect_right(starts, ends[outer["command_id"]])
    for inner in execs[first:last]:
        if (
            inner["command_id"] not in used
            and inner["wrapper"] != "codex_snapshot_exec"
            and inner["command_norm"] == outer["command_norm"]
        ):
            used.add(inner["command_id"])
            return [
                DuplicateLink(
                    link_type="wrapper_duplicate",
                    command_id=inner["command_id"],
                    of=outer["command_id"],
                )
            ]
    return []


def _match_calls(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    ends: dict[str, float],
    cfg: dict,
) -> list[Link]:
    """J1: the three rules in order, each a queue match in the window.

    Args:
        calls: Shell calls with a command, in log order.
        execs: Candidate execs (non-noise, in the run window), sorted.
        ends: End time per command_id (incomplete execs extended).
        cfg: Configuration.

    Returns:
        The call_exec and wrapper_duplicate links.
    """
    starts = [e["t_start"] for e in execs]
    used: set[str] = set()
    matched: set[str] = set()
    links: list[Link] = []
    width = cfg["prefix_chars"]
    rules = (
        ("exact", lambda a, b: a == b),
        ("quoting_equivalent", parse.same_tokens),
        ("prefix", lambda a, b: a[:width] == b[:width]),
    )
    for rule, same in rules:
        for call in calls:
            if call["call_id"] in matched or call["t_start"] is None:
                continue
            low = call["t_start"] - cfg["window_before_s"]
            high = (
                call["t_end"] + cfg["window_after_s"]
                if call["t_end"] is not None
                else call["t_start"] + cfg["window_open_s"]
            )
            first, last = (
                bisect.bisect_left(starts, low),
                bisect.bisect_right(starts, high),
            )
            for record in execs[first:last]:
                if record["command_id"] in used or not same(
                    call["command_norm"], record["command_norm"]
                ):
                    continue
                used.add(record["command_id"])
                matched.add(call["call_id"])
                links.append(
                    CallExecLink(
                        link_type="call_exec",
                        call_id=call["call_id"],
                        command_id=record["command_id"],
                        rule=rule,
                        dt_s=record["t_start"] - call["t_start"],
                    )
                )
                if record["wrapper"] == "codex_snapshot_exec":
                    links += _duplicate(record, execs, starts, used, ends)
                break
    return links


def _affinity(command: str) -> set[str]:
    """The first word and script basenames of an exec, for reparenting."""
    words = [w for w in parse.tokens(command)[:3] if not w.startswith("-")]
    return {posixpath.basename(w) for w in words[:2] if w} - {""}


def _nest(
    execs: list[parse.Exec],
    root_of: dict[str, str],
    under: dict[str, bool],
    ends: dict[str, float],
    duplicates: dict[str, str],
    parent_links: bool,
) -> list[Link]:
    """J2: rule parent_id, time containment, reparenting (spec §6.5).

    Args:
        execs: Non-noise, in-window execs sorted by start.
        root_of: Root call per matched or duplicate exec; extended in place
            with the root of every nested exec.
        under: Rule R3 per matched or duplicate exec; extended in place:
            a nested exec is under when its parent contains it, is under
            and its root is unambiguous.
        ends: End time per command_id.
        duplicates: Wrapper duplicates, which are never parents.
        parent_links: The run's capability: try each exec's recorded
            parent first (decision D28).

    Returns:
        The exec_parent links.
    """
    links: list[Link] = []
    active: list[parse.Exec] = []  # started, not yet ended, in start order
    finished: list[parse.Exec] = []  # matched roots that background
    matched = set(root_of)
    for record in execs:
        cid, t = record["command_id"], record["t_start"]
        active = [e for e in active if ends[e["command_id"]] >= t]
        if cid in duplicates:
            continue
        if cid in matched:
            active.append(record)
            if backgrounds(record["command"]):
                finished.append(record)
            continue
        candidates = [e for e in active if e["command_id"] != cid]
        roots = sorted({root_of[e["command_id"]] for e in candidates})
        rule, parent = "time_containment", None
        recorded = record["parent_command_id"] if parent_links else None
        recorded = duplicates.get(recorded, recorded)
        if recorded in root_of and recorded != cid:
            rule, parent, root = "parent_id", recorded, root_of[recorded]
            roots = [root]
        elif candidates:
            inner = max(candidates, key=lambda e: (e["t_start"], e["seq"]))
            parent, root = inner["command_id"], root_of[inner["command_id"]]
        else:
            ended = [e for e in finished if ends[e["command_id"]] < t]
            words = _affinity(record["command"])
            near = [e for e in ended if any(w in e["command"] for w in words)]
            pool = near or ended
            if not pool:
                continue
            rule, root = "reparenting", root_of[pool[-1]["command_id"]]
            roots = sorted({root_of[e["command_id"]] for e in pool})
        ambiguous = len(roots) > 1
        root_of[cid] = root
        under[cid] = (
            parent is not None and not ambiguous and under.get(parent, False)
        )
        active.append(record)
        links.append(
            ParentLink(
                link_type="exec_parent",
                command_id=cid,
                parent=parent,
                root_call_id=root,
                rule=rule,
                root_ambiguous=ambiguous,
                root_candidates=roots if ambiguous else [],
            )
        )
    return links


def relative_path(path: str, root: str) -> tuple[str, bool]:
    """Workspace-relative path and whether it is inside the workspace.

    J3 rule 1. Public (decision D31): layer 4 must normalize oracle paths
    exactly as J3 normalizes snapshot paths.

    Args:
        path: A path, relative to the workspace or absolute.
        root: The workspace root.

    Returns:
        (workspace-relative path, True), or (absolute path, False) outside.
    """
    root = posixpath.normpath(root or "/")
    full = posixpath.normpath(posixpath.join(root, path))
    if full == root or full.startswith(root.rstrip("/") + "/"):
        return posixpath.relpath(full, root), True
    return full, False


def _event(*values: typing.Any) -> dict:
    """A write event from the values of _EVENT_FIELDS, before J3 rule 5."""
    event = dict(zip(_EVENT_FIELDS, values, strict=True))
    source = "snapshot" if event["writer_type"] == "exec" else "edit_call"
    return dict(
        event, depth_rule="none", possibly_edit_call=None, source=source
    )


def _window_end(event: dict, slop: float) -> float:
    """The end of an event's observation window (decision D33)."""
    return event["t_end"] + (slop if event["writer_type"] == "exec" else 0.0)


def _write_events(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    nest_links: list[Link],
    root_of: dict[str, str],
    under: dict[str, bool],
    slop: float,
    diff: dict[str, set[str]],
    tracked: dict,
    root: str,
) -> list[Link]:
    """J3: write events by rules 1 to 8, in event order (spec §6.6).

    An exec event has the exec's measured interval (decision D32); the
    overlap tests of rules 5 and 6 use observation windows (D33).

    Args:
        calls: All calls.
        execs: Non-noise, in-window execs that are not wrapper duplicates,
            sorted by start.
        nest_links: The exec_parent links of J2.
        root_of: Root call per rooted exec.
        under: Rule R3 per exec.
        slop: snapshot_slop_s, added to an exec event's end.
        diff: Workspace-relative final-diff paths per _DIFF_KEYS key.
        tracked: The baseline's files ({} without a baseline).
        root: The workspace root.

    Returns:
        The write_event links in event order.
    """
    parent_of = {k["command_id"]: k["parent"] for k in nest_links}
    candidates = {k["command_id"]: k["root_candidates"] for k in nest_links}
    mode_only = diff["mode_changed"] - diff["modified"]
    reports: dict[str, list[dict]] = {}
    for record in execs:
        cid, snap = record["command_id"], record["snapshot"]
        span = record["t_start"], record["t_end"]  # measured (D32)
        writer = (snap["scope"], "exec", cid, *span)
        for op, key in _OPS:
            for raw in snap[key]:
                path, inside = relative_path(raw, root)
                op_type = "mode" if op == "modify" and path in mode_only else op
                reports.setdefault(path, []).append(
                    _event(path, inside, op_type, *writer)
                )
    events: list[dict] = []
    for found in reports.values():  # rule 3: deepest wins
        ids = {e["writer_id"] for e in found}
        folded: set[str] = set()
        for event in found:
            up = parent_of.get(event["writer_id"])
            while up is not None:
                if up in ids:
                    folded.add(up)
                    event["depth_rule"] = "parent_may_also_write"
                up = parent_of.get(up)
        events += [e for e in found if e["writer_id"] not in folded]
    for call in calls:  # rule 4
        t, edit_type, cid = call["t_start"], call["tool_type"], call["call_id"]
        if (
            edit_type not in ("edit", "write")
            or not call["log_ok"]
            or t is None
        ):
            continue
        for raw in call["paths"]:
            path, inside = relative_path(raw, root)
            op = (
                "create"
                if edit_type == "write" and path not in tracked
                else "modify"
            )
            events.append(
                _event(path, inside, op, None, "edit_call", cid, t, t)
            )
    by_path: dict[str, list[dict]] = {}
    for event in events:
        wid = event["writer_id"]
        event["root_call_id"] = (
            root_of.get(wid) if event["writer_type"] == "exec" else wid
        )
        event["root_candidates"] = list(candidates.get(wid, []))
        event["root_ambiguous"] = bool(event["root_candidates"])
        event["overlaps_sibling"] = False
        event["order_t"] = event["t_end"]
        by_path.setdefault(event["path"], []).append(event)
    for found in by_path.values():  # rules 5 and 6
        for edit in [e for e in found if e["writer_type"] == "edit_call"]:
            for other in found:
                if (
                    other["writer_type"] == "exec"
                    and other["t_start"]
                    <= edit["t_start"]
                    <= _window_end(other, slop)
                    and other["possibly_edit_call"] is None
                ):
                    other["possibly_edit_call"] = edit["writer_id"]
        for i, a in enumerate(found):
            for b in found[i + 1 :]:
                if (
                    a["t_start"] > _window_end(b, slop)
                    or b["t_start"] > _window_end(a, slop)
                    or _explains(a, b)
                ):
                    continue
                if a["root_call_id"] == b["root_call_id"]:
                    a["overlaps_sibling"] = b["overlaps_sibling"] = True
                    continue
                pair = {a["root_call_id"], b["root_call_id"]} - {None}
                for e in (a, b):
                    e["root_ambiguous"] = True
                    e["root_candidates"] = sorted(
                        set(e["root_candidates"]) | pair
                    )
    for event in events:  # rule 8
        event["writer_under"] = (
            (
                under.get(event["writer_id"], False)
                if event["writer_type"] == "exec"
                else True
            )
            and not event["root_ambiguous"]
            and event["possibly_edit_call"] is None
        )
    events.sort(
        key=lambda e: (e["order_t"], e["t_start"], e["path"], e["writer_id"])
    )
    for n, event in enumerate(events):
        event.update(link_type="write_event", event_id=f"w{n}")
    return typing.cast(list[Link], events)


def _explains(a: dict, b: dict) -> bool:
    """Decision R5: an edit event and the exec event it explains."""
    return any(
        x["writer_type"] == "edit_call"
        and y["possibly_edit_call"] == x["writer_id"]
        for x, y in ((a, b), (b, a))
    )


def _paths(
    events: list[Link],
    diff: dict[str, set[str]],
    has_diff: bool,
    tracked: dict | None,
    execs: list[parse.Exec],
    root: str,
    slop: float,
) -> list[PathSummary]:
    """The per-path summaries of spec §6.6 (rules 1 to 4, decision D34).

    Args:
        events: The write events in event order.
        diff: Workspace-relative final-diff paths per _DIFF_KEYS key.
        has_diff: Whether tracked_files_diff.json was read.
        tracked: The baseline's files, or None without a baseline.
        execs: Non-noise, in-window execs (unattributed candidates).
        root: The workspace root.
        slop: snapshot_slop_s, for the overlap clause of rule 2.

    Returns:
        One summary per written or changed path, workspace paths first.
    """
    by_path: dict[str, list[typing.Any]] = {}
    for event in events:
        by_path.setdefault(typing.cast(WriteEvent, event)["path"], []).append(
            event
        )
    in_diff = set().union(*diff.values())
    inside = {p: relative_path(p, root)[1] for p in set(by_path) | in_diff}
    blind = [
        {"command_id": e["command_id"], "reason": e["snapshot"]["status"]}
        for e in execs
        if e["snapshot"]["status"] in ("truncated", "skipped", "absent")
    ]
    rows: list[PathSummary] = []
    for path in sorted(inside, key=lambda p: (not inside[p], p)):
        found = by_path.get(path, [])
        is_tracked = path in tracked if tracked is not None else None
        state = next((s for s, key in _STATES if path in diff[key]), None)
        if state is None and has_diff:
            state = {True: "reverted", False: "untracked"}.get(is_tracked)
        live = [
            e for e in found if state not in _EXISTING or e["op"] != "delete"
        ]
        pick = [e for e in live if e["possibly_edit_call"] is None] or live
        final = pick[-1] if pick else None
        under = bool(
            final
            and final["writer_under"]
            and not any(
                e is not final
                and e["root_call_id"] != final["root_call_id"]
                and e["t_start"] <= final["t_end"] <= _window_end(e, slop)
                and not _explains(e, final)
                for e in found
            )
        )
        rows.append(
            PathSummary(
                path=path,
                in_workspace=inside[path],
                tracked=is_tracked,
                events=[e["event_id"] for e in found],
                final_writer=final["event_id"] if final else None,
                final_writer_under=under,
                final_state=state,
                unattributed_candidates=(
                    [] if final or path not in in_diff else blind
                ),
            )
        )
    return rows


def union_length(intervals: list[tuple[float, float]]) -> float:
    """Total length of the union of intervals (public for layer 2)."""
    total, reach = 0.0, float("-inf")
    for low, high in sorted(intervals):
        if high > reach:
            total += high - max(low, reach)
            reach = high
    return total


def _rate(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _measure(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    links: list[Link],
    rows: list[Disposition],
    paths: list[PathSummary],
    ends: dict[str, float],
    diff_paths: set[str] | None,
    usable: bool,
) -> tuple[dict[str, typing.Any], dict[str, typing.Any], dict]:
    """The counts and rates C1 to C8 of coverage.json (spec §7).

    Args:
        calls: All calls.
        execs: All execs.
        links: All links.
        rows: All dispositions.
        paths: All path summaries.
        ends: End time per command_id.
        diff_paths: The final-diff paths, or None without a final diff.
        usable: Whether the trace passed C0.1.

    Returns:
        (counts, rates, denominators): the denominator of the rate each
        precondition label reads, None when its input is missing (D30).
    """
    live = [e for e in execs if e["noise"] is None]
    snapped = [
        e for e in live if e["snapshot"]["status"] in ("ok", "truncated")
    ]
    statuses = collections.Counter(e["snapshot"]["status"] for e in live)
    by_exec = collections.Counter(
        r["disposition"] for r in rows if r["item_type"] == "exec"
    )
    tally = collections.defaultdict(collections.Counter)
    for k in links:
        tally[k["link_type"]].update(
            (field, typing.cast(dict, k).get(field)) for field in _LINK_FIELDS
        )
    nests, writes = tally["exec_parent"], tally["write_event"]
    shells = sum(1 for c in calls if c["tool_type"] == "shell")
    running = sum(1 for r in rows if r["reason"] == "running_at_stop")
    rules = {
        r: tally["call_exec"]["rule", r]
        for r in ("exact", "quoting_equivalent", "prefix")
    }
    edits = [c for c in calls if c["tool_type"] in ("edit", "write")]
    at_risk = sum(
        1
        for c in edits
        if c["t_start"] is not None and _containing(live, c["t_start"], ends)
    )
    listed = sum(len(e["snapshot"][key]) for e in snapped for _, key in _OPS)
    totals = sum(sum((e["snapshot"]["totals"] or {}).values()) for e in snapped)
    scope = next((e["snapshot"]["scope"] for e in snapped), None)
    diff_rows = [p for p in paths if p["path"] in (diff_paths or ())]
    explained = [
        r for r in rows if r["disposition"] in _LINK_DISPOSITION.values()
    ]
    counts = {
        "shell_calls": shells,
        "shell_calls_matched": rules,
        "calls_running_at_stop": running,
        "execs_total": len(execs),
        "execs_by_disposition": {d: by_exec[d] for d in EXEC_DISPOSITIONS},
        "unrooted_execs": by_exec["unrooted"],
        "execs_reparented": nests["rule", "reparenting"],
        "roots_ambiguous": nests["root_ambiguous", True],
        "edit_calls": len(edits),
        "edit_calls_during_exec": at_risk,
        "snapshot_status_counts": {
            s: statuses[s] for s in ("ok", "skipped", "truncated", "absent")
        },
        "paths_unlisted": max(totals - listed, 0),
        "final_diff_files": None if diff_paths is None else len(diff_paths),
        "write_events": {
            w: writes["writer_type", w] for w in ("exec", "edit_call")
        },
        "write_events_outside_workspace": writes["in_workspace", False],
        "events_parent_may_also_write": writes[
            "depth_rule", "parent_may_also_write"
        ],
        "events_possibly_edit_call": writes["link_type", "write_event"]
        - writes["possibly_edit_call", None],
        "paths_unattributed": sum(
            1 for p in diff_rows if p["final_writer"] is None
        ),
    }
    exec_den = len(live) - by_exec["out_of_window"]
    span = union_length([(e["t_start"], ends[e["command_id"]]) for e in live])
    snap_span = union_length(
        [
            (e["t_start"], ends[e["command_id"]])
            for e in live
            if e["snapshot"]["status"] == "ok"
        ]
    )
    rates = {
        "call_match_rate_under": _rate(
            rules["exact"] + rules["quoting_equivalent"], shells - running
        ),
        "call_match_rate_over": _rate(sum(rules.values()), shells - running),
        "exec_explained_rate_under": _rate(
            sum(1 for r in explained if r["root_under"]), exec_den
        ),
        "exec_explained_rate_over": _rate(len(explained), exec_den),
        "root_ambiguous_rate": _rate(
            counts["roots_ambiguous"], nests["link_type", "exec_parent"]
        ),
        "edit_at_risk_rate": _rate(at_risk, len(edits)),
        "snapshot_time_coverage": _rate(snap_span, span),
        "snapshot_count_coverage": _rate(statuses["ok"], len(live)),
        "snapshot_scope": scope,
        "untracked_visible": scope == "workspace" if scope else None,
        "listed_path_rate": _rate(listed, totals),
        "final_diff_attributed_rate_under": _rate(
            sum(1 for p in diff_rows if p["final_writer_under"]), len(diff_rows)
        ),
        "final_diff_attributed_rate_over": _rate(
            sum(1 for p in diff_rows if p["final_writer"] is not None),
            len(diff_rows),
        ),
        "reverted_changes": None
        if diff_paths is None
        else sum(1 for p in paths if p["final_state"] == "reverted"),
    }
    if not usable:
        rates.update(dict.fromkeys(set(rates) - {"reverted_changes"}))
    dens = {
        "C1_under": shells - running,
        "C2_under": exec_den,
        "C6": totals if snapped or not live else None,
        "C7_under": None if diff_paths is None else len(diff_rows),
    }
    return counts, rates, dens if usable else dict.fromkeys(dens)


def _preconditions(
    rates: dict, dens: dict, cfg: dict
) -> tuple[dict[str, str], list[str]]:
    """The precondition states of spec §7.2 (D30) and the warnings.

    Args:
        rates: The coverage rates.
        dens: The denominator per label, None when the input is missing.
        cfg: Configuration.

    Returns:
        (label to holds, vacuous or fails; warnings for the failed ones
        and for low snapshot coverage).
    """
    states, warnings = {}, []
    for label, key, op, metrics in PRECONDITIONS:
        value = rates[key]
        held = value is not None and (
            value >= cfg["join_rate_warn"] if op == ">=" else value == 1
        )
        states[label] = "holds" if held else "fails"
        if dens[label] == 0:
            states[label] = "vacuous"
        if states[label] == "fails":
            shown = "null" if value is None else f"{value:.2f}"
            bound = f">= {cfg['join_rate_warn']}" if op == ">=" else "= 1"
            warnings.append(
                f"precondition {label} {bound} failed ({shown}): {metrics} null"
            )
    value = rates["snapshot_time_coverage"]
    if value is not None and value < cfg["snapshot_coverage_warn"]:
        warnings.append(
            f"snapshot_time_coverage {value:.2f} < "
            f"{cfg['snapshot_coverage_warn']}"
        )
    return dict(sorted(states.items())), warnings


def round_floats(value: typing.Any) -> typing.Any:
    """Rounds every float to 6 decimals, recursively (decision R6).

    Args:
        value: A JSON-like value of dicts, lists and scalars.

    Returns:
        A copy with every float rounded.
    """
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: round_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [round_floats(v) for v in value]
    return value


def _row(
    item_type: str,
    item_id: str,
    disposition: str,
    reason: str | None,
    **flags: bool,
) -> Disposition:
    """A dispositions row; the flags not given are None."""
    return Disposition(
        item_type=item_type,
        id=item_id,
        disposition=disposition,
        reason=reason,
        **{**dict.fromkeys(_FLAGS), **flags},
    )


def _miss_reason(
    call: parse.Call, prefixes: set[str], stop: float | None, width: int
) -> str:
    """Why a shell call has no exec in a usable trace (spec §5.4)."""
    if call["t_end"] is None or (stop is not None and call["t_end"] >= stop):
        return "running_at_stop"
    if (call["command_norm"] or "")[:width] in prefixes:
        return "outside_window"
    return "no_candidate"


def link(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    *,
    run: RunInfo,
    baseline: dict | None = None,
    final_diff: dict | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> LinkResult:
    """Runs integrity, J1, J2, J3 and coverage on one run's records.

    Args:
        calls: Call records of the run (any order; sorted by seq here).
        execs: Exec records of the run (any order; sorted by seq here).
        run: The run's RunInfo (spec §5.6); `capabilities` selects the
            rules that need new monitor data.
        baseline: tracked_files.json content, or None when missing.
        final_diff: tracked_files_diff.json content, or None when missing.
        config: Overrides of DEFAULTS.

    Returns:
        Links, dispositions, path summaries and coverage.

    Raises:
        ValueError: A config key is not in DEFAULTS.
    """
    unknown = set(config or {}) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    cfg = {**DEFAULTS, **(config or {})}
    calls = sorted(calls, key=lambda c: c["seq"])
    execs = sorted(execs, key=lambda e: (e["t_start"], e["command_id"]))
    root, run_end = run["workspace_root"], run["t_end"]
    ends = {
        e["command_id"]: e["t_end"]
        if e["complete"] or run_end is None
        else max(e["t_end"], run_end)
        for e in execs
    }
    integrity, outside, trace_warnings = _integrity(execs, run, baseline, cfg)
    usable = bool(run["trace_ok"])
    live = [
        e
        for e in execs
        if usable and e["noise"] is None and e["command_id"] not in outside
    ]
    shell = [c for c in calls if c["tool_type"] == "shell"]
    matches = _match_calls(
        [c for c in shell if c["command_norm"] is not None], live, ends, cfg
    )
    rule_of = {k["call_id"]: k["rule"] for k in matches if "rule" in k}
    root_of = {k["command_id"]: k["call_id"] for k in matches if "rule" in k}
    under = {cid: rule_of[call] in UNDER_RULES for cid, call in root_of.items()}
    duplicates = {k["command_id"]: k["of"] for k in matches if "of" in k}
    for inner, outer in duplicates.items():
        root_of[inner], under[inner] = root_of[outer], under[outer]
    caps = run["capabilities"]
    nest_links = _nest(
        live, root_of, under, ends, duplicates, caps["parent_links"]
    )
    codes = {e["command_id"]: e["exit_code"] for e in execs}
    logged = {c["call_id"]: c["log_exit_code"] for c in calls}
    warnings = []
    for k in matches:
        if "rule" not in k:
            continue
        log_code, code = logged[k["call_id"]], codes[k["command_id"]]
        if log_code is not None and code is not None and log_code != code:
            warnings.append(
                f"exit_code_mismatch: call {k['call_id']} "
                f"logged {log_code}, exec {k['command_id']} {code}"
            )
    warnings += trace_warnings
    trace_reason = {None: "trace_missing", False: "trace_mismatched"}
    prefixes = {
        e["command_norm"][: cfg["prefix_chars"]]
        for e in execs
        if e["noise"] is None
    }
    stop = None if run_end is None else run_end - cfg["stop_margin_s"]
    rows = []
    for call in shell:
        rule = rule_of.get(call["call_id"])
        reason = None
        if rule is None:
            reason = trace_reason.get(integrity["C0.1"]) or _miss_reason(
                call, prefixes, stop, cfg["prefix_chars"]
            )
        rows.append(
            _row(
                "call",
                call["call_id"],
                "matched" if rule else "unmatched",
                reason,
                matched=rule is not None,
                match_under=rule in UNDER_RULES,
            )
        )
    role = {
        k["command_id"]: _LINK_DISPOSITION[k["link_type"]]
        for k in matches + nest_links
    }
    for record in execs:
        cid = record["command_id"]
        disposition = role.get(cid, "unrooted")
        if record["noise"] is not None:
            disposition = "noise"
        elif cid in outside:
            disposition = "out_of_window"
        rows.append(
            _row(
                "exec",
                cid,
                disposition,
                "trace_mismatched"
                if disposition == "unrooted" and not usable
                else None,
                snapshot_ok=record["snapshot"]["status"] == "ok",
                root_under=under.get(cid, False),
            )
        )
    diff = {
        key: {
            relative_path(p, root)[0] for p in (final_diff or {}).get(key) or []
        }
        for key in _DIFF_KEYS
    }
    files = (baseline or {}).get("files") or {}
    writers = [e for e in live if e["command_id"] not in duplicates]
    events = _write_events(
        *(calls, writers, nest_links, root_of, under),
        *(cfg["snapshot_slop_s"], diff, files, root),
    )
    has_diff = final_diff is not None
    paths = _paths(
        events,
        diff,
        has_diff,
        files if baseline is not None else None,
        live,
        root,
        cfg["snapshot_slop_s"],
    )
    seq = {e["command_id"]: e["seq"] for e in execs}
    seq.update({c["call_id"]: c["seq"] for c in calls})
    links = sorted(
        matches + nest_links,
        key=lambda k: (
            LINK_ORDER.index(k["link_type"]),
            seq[typing.cast(dict, k).get("call_id", k["command_id"])],
            k["command_id"],
        ),
    )
    links += events
    counts, rates, dens = _measure(
        *(calls, execs, links, rows, paths, ends),
        set().union(*diff.values()) if has_diff else None,
        usable,
    )
    states, pre_warnings = _preconditions(rates, dens, cfg)
    coverage = Coverage(
        run_key=run["run_key"],
        family=run["family"] or (calls[0]["family"] if calls else None),
        inputs={
            "log": bool(calls if run["log_ok"] is None else run["log_ok"]),
            "trace": integrity["C0.1"] is not None,
            "tracked_files": integrity["C0.5"],
            "final_diff": has_diff,
        },
        integrity=integrity,
        capabilities={name: bool(caps[name]) for name in CAPABILITIES},
        counts=counts,
        rates=rates,
        preconditions=states,
        warnings=warnings + pre_warnings,
    )
    return round_floats(
        LinkResult(
            links=links, dispositions=rows, paths=paths, coverage=coverage
        )
    )
