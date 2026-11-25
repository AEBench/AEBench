"""Independent checker of the matching-layer invariants (spec §2.3).

This is test oracle code, written separately from the implementation. It
takes the inputs given to ``match.link`` and its result, and returns one
message per violated invariant:

1. Partition: every exec has exactly one disposition, noise execs exactly
   ``noise``; every shell call exactly one disposition row.
2. J1 is one-to-one (plus at most one ``wrapper_duplicate`` per outer exec)
   and agrees with the dispositions.
3. Matched pairs satisfy the window, and ``dt_s`` is the start difference.
4. ``exec_parent`` links: containment for ``time_containment``, a finished,
   backgrounding root for ``reparenting``, the recorded parent for
   ``parent_id`` (D28), acyclic chains, one root per nested exec,
   consistent ambiguity fields.
5. Disposition counts sum to the number of execs.
6. Rates are in [0, 1] or None.
7. Write events refer to existing writers; every ``final_writer`` is one of
   its path's events; every path with events has one, unless all its events
   are deletions of a file that exists at the end (D34); every final-diff
   path has a paths row.
8. An exec's write event has the exec's measured interval (D32); a
   ``delete`` is never the final writer of a file that exists (D34).
9. Precondition states are holds, vacuous or fails; holds has a rate,
   vacuous none; all fail without a usable trace (D30).

Plus format checks from decision R6 (link order, event ids).
"""

import math
import shlex
from collections.abc import Mapping, Sequence
from typing import Any

WINDOW_BEFORE_S = 5.0
WINDOW_AFTER_S = 5.0
WINDOW_OPEN_S = 3600.0
TOLERANCE = 1e-6

LINK_TYPE_ORDER = (
    "call_exec",
    "wrapper_duplicate",
    "exec_parent",
    "write_event",
)
EXEC_DISPOSITIONS = frozenset(
    {
        "matched_root",
        "wrapper_duplicate",
        "nested",
        "noise",
        "unrooted",
        "out_of_window",
    }
)
CALL_DISPOSITIONS = frozenset({"matched", "unmatched"})
UNMATCHED_REASONS = frozenset(
    {
        "no_candidate",
        "outside_window",
        "running_at_stop",
        "trace_missing",
        "trace_mismatched",
    }
)
J1_RULES = frozenset({"exact", "quoting_equivalent", "prefix"})
J2_RULES = frozenset({"time_containment", "reparenting", "parent_id"})
# Keys of coverage["rates"] that are not rates in [0, 1].
NON_RATE_KEYS = frozenset(
    {"reverted_changes", "snapshot_scope", "untracked_visible"}
)
BACKGROUND_PREFIXES = frozenset({"nohup", "setsid", "disown"})
# Final states in which the file exists at the end of the run (D34).
EXISTING_STATES = frozenset({"changed", "mode_only", "reverted"})
# Coverage precondition label -> the rate it reads (D30).
PRECONDITION_RATES = {
    "C1_under": "call_match_rate_under",
    "C2_under": "exec_explained_rate_under",
    "C6": "listed_path_rate",
    "C7_under": "final_diff_attributed_rate_under",
}
PRECONDITION_STATES = frozenset({"holds", "vacuous", "fails"})


def check_invariants(
    calls: Sequence[Mapping[str, Any]],
    execs: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    run: Mapping[str, Any] | None = None,
    final_diff: Mapping[str, Any] | None = None,
) -> list[str]:
    """Checks the §2.3 invariants on one ``match.link`` result.

    Args:
        calls: The Call records given to ``link``.
        execs: The Exec records given to ``link``.
        result: What ``link`` returned.
        run: The ``run`` argument given to ``link``; when its ``trace_ok`` is
            false, trace-derived links must be absent and exec dispositions
            are not checked.
        final_diff: The ``final_diff`` argument given to ``link``.

    Returns:
        One message per violation; empty when every invariant holds.
    """
    problems: list[str] = []
    trace_ok = True if run is None else bool(run.get("trace_ok", True))
    run_end = None if run is None else run.get("t_end")
    calls_by_id = {c["call_id"]: c for c in calls}
    execs_by_id = {e["command_id"]: e for e in execs}
    links = list(result.get("links", []))
    rows = list(result.get("dispositions", []))
    by_type: dict[str, list[Mapping[str, Any]]] = {
        t: [] for t in LINK_TYPE_ORDER
    }
    for link in links:
        link_type = link.get("link_type")
        if link_type not in by_type:
            problems.append(f"unknown link_type {link_type!r}")
            continue
        by_type[link_type].append(link)

    problems += _check_order(links)
    if not trace_ok:
        for link_type in ("call_exec", "wrapper_duplicate", "exec_parent"):
            if by_type[link_type]:
                problems.append(f"trace not ok but {link_type} links exist")
    rows_by_id = _rows_by_id(rows, problems)
    problems += _check_call_rows(calls, rows_by_id, by_type, trace_ok)
    if trace_ok:
        problems += _check_exec_rows(execs, rows_by_id, by_type)
    problems += _check_j1(calls_by_id, execs_by_id, by_type)
    problems += _check_j2(
        calls_by_id, execs_by_id, by_type, rows_by_id, run_end, trace_ok
    )
    problems += _check_j3(calls_by_id, execs_by_id, by_type, result, final_diff)
    problems += _check_rates(result.get("coverage", {}))
    problems += _check_preconditions(result.get("coverage", {}), trace_ok)
    return problems


def backgrounds(command: str) -> bool:
    """Tells whether a shell command backgrounds something (§6.5 step 1).

    A ``&`` control operator (not ``&&``, not in quotes, not part of a
    redirection such as ``2>&1``), or a leading ``nohup``, ``setsid`` or
    ``disown``.

    Args:
        command: Shell text.

    Returns:
        True when the command backgrounds a process.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    if tokens and tokens[0] in BACKGROUND_PREFIXES:
        return True
    # The lexer keeps "&&", ">&" (as in 2>&1) and "&>" as single tokens, so
    # a lone "&" token is the background control operator.
    return "&" in tokens


def _check_order(links: Sequence[Mapping[str, Any]]) -> list[str]:
    ranks = [
        LINK_TYPE_ORDER.index(lk["link_type"])
        for lk in links
        if lk.get("link_type") in LINK_TYPE_ORDER
    ]
    if ranks != sorted(ranks):
        return ["links not grouped in the order of decision R6"]
    return []


def _rows_by_id(
    rows: Sequence[Mapping[str, Any]], problems: list[str]
) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    out: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        item_type = row.get("item_type")
        if item_type not in ("call", "exec"):
            problems.append(f"dispositions row with item_type {item_type!r}")
            continue
        out.setdefault((item_type, row.get("id")), []).append(row)
    return out


def _check_call_rows(calls, rows_by_id, by_type, trace_ok) -> list[str]:
    problems = []
    linked = {lk["call_id"] for lk in by_type["call_exec"]}
    for call in calls:
        if call.get("tool_type") != "shell":
            continue
        call_id = call["call_id"]
        got = rows_by_id.get(("call", call_id), [])
        if len(got) != 1:
            problems.append(f"call {call_id}: {len(got)} dispositions rows")
            continue
        row = got[0]
        disposition = row.get("disposition")
        if disposition not in CALL_DISPOSITIONS:
            problems.append(f"call {call_id}: disposition {disposition!r}")
            continue
        matched = disposition == "matched"
        if row.get("matched") is not matched:
            problems.append(f"call {call_id}: matched flag disagrees")
        if matched != (call_id in linked):
            problems.append(f"call {call_id}: matched without link or reverse")
        if matched and row.get("reason") is not None:
            problems.append(f"call {call_id}: matched with a reason")
        if not matched:
            if row.get("reason") not in UNMATCHED_REASONS:
                problems.append(f"call {call_id}: reason {row.get('reason')!r}")
            if row.get("match_under") is not False:
                problems.append(f"call {call_id}: unmatched but match_under")
        if not trace_ok and row.get("reason") not in (
            "trace_missing",
            "trace_mismatched",
        ):
            problems.append(f"call {call_id}: trace not ok, reason wrong")
    return problems


def _check_exec_rows(execs, rows_by_id, by_type) -> list[str]:
    problems = []
    matched = {lk["command_id"] for lk in by_type["call_exec"]}
    duplicates = {lk["command_id"] for lk in by_type["wrapper_duplicate"]}
    parented = {lk["command_id"] for lk in by_type["exec_parent"]}
    counted = 0
    for record in execs:
        command_id = record["command_id"]
        got = rows_by_id.get(("exec", command_id), [])
        if len(got) != 1:
            problems.append(f"exec {command_id}: {len(got)} dispositions rows")
            continue
        counted += 1
        disposition = got[0].get("disposition")
        if disposition not in EXEC_DISPOSITIONS:
            problems.append(f"exec {command_id}: disposition {disposition!r}")
        if (record.get("noise") is not None) != (disposition == "noise"):
            problems.append(f"exec {command_id}: noise and disposition differ")
        if (disposition == "matched_root") != (command_id in matched):
            problems.append(f"exec {command_id}: matched_root without J1 link")
        if (disposition == "wrapper_duplicate") != (command_id in duplicates):
            problems.append(f"exec {command_id}: duplicate without link")
        if (disposition == "nested") != (command_id in parented):
            problems.append(f"exec {command_id}: nested without exec_parent")
        if got[0].get("snapshot_ok") is not (
            record["snapshot"]["status"] == "ok"
        ):
            problems.append(f"exec {command_id}: snapshot_ok wrong")
        if disposition == "unrooted" and got[0].get("root_under") is not False:
            problems.append(f"exec {command_id}: unrooted but root_under")
    exec_rows = sum(len(v) for k, v in rows_by_id.items() if k[0] == "exec")
    if exec_rows != len(execs) or counted != len(execs):
        problems.append(
            f"{exec_rows} exec dispositions rows for {len(execs)} execs"
        )
    return problems


def _window_ok(call: Mapping[str, Any], start: float) -> bool:
    low = call["t_start"] - WINDOW_BEFORE_S
    if call.get("t_end") is None:
        high = call["t_start"] + WINDOW_OPEN_S
    else:
        high = call["t_end"] + WINDOW_AFTER_S
    return low - TOLERANCE <= start <= high + TOLERANCE


def _check_j1(calls_by_id, execs_by_id, by_type) -> list[str]:
    problems = []
    seen_calls: set[str] = set()
    seen_execs: set[str] = set()
    for link in by_type["call_exec"]:
        call_id, command_id = link.get("call_id"), link.get("command_id")
        if call_id not in calls_by_id or command_id not in execs_by_id:
            problems.append(f"call_exec {call_id}->{command_id}: unknown id")
            continue
        if call_id in seen_calls:
            problems.append(f"call {call_id} matched twice")
        if command_id in seen_execs:
            problems.append(f"exec {command_id} matched twice")
        seen_calls.add(call_id)
        seen_execs.add(command_id)
        call, record = calls_by_id[call_id], execs_by_id[command_id]
        if link.get("rule") not in J1_RULES:
            problems.append(f"call_exec {call_id}: rule {link.get('rule')!r}")
        if record.get("noise") is not None:
            problems.append(f"call {call_id} matched to noise {command_id}")
        if not _window_ok(call, record["t_start"]):
            problems.append(f"call {call_id}: {command_id} outside window")
        dt_s = link.get("dt_s")
        if not isinstance(dt_s, int | float) or not math.isclose(
            dt_s, record["t_start"] - call["t_start"], abs_tol=TOLERANCE
        ):
            problems.append(f"call_exec {call_id}: dt_s {dt_s!r}")
        if link.get("rule") == "exact" and (
            call.get("command_norm") != record.get("command_norm")
        ):
            problems.append(f"call_exec {call_id}: exact but texts differ")
    outers: set[str] = set()
    for link in by_type["wrapper_duplicate"]:
        command_id, outer = link.get("command_id"), link.get("of")
        if outer not in seen_execs:
            problems.append(f"wrapper_duplicate {command_id}: outer unmatched")
        if outer in outers:
            problems.append(f"exec {outer} has two wrapper duplicates")
        outers.add(outer)
        if command_id in seen_execs:
            problems.append(f"exec {command_id} both matched and duplicate")
    return problems


def _interval_end(record: Mapping[str, Any], run_end: Any) -> float:
    if not record.get("complete", True) and run_end is not None:
        return max(record["t_end"], run_end)
    return record["t_end"]


def _check_j2(
    calls_by_id, execs_by_id, by_type, rows_by_id, run_end, trace_ok
) -> list[str]:
    problems = []
    roots = {lk["command_id"]: lk["call_id"] for lk in by_type["call_exec"]}
    parents: dict[str, Any] = {}
    for link in by_type["exec_parent"]:
        command_id = link.get("command_id")
        if command_id in parents:
            problems.append(f"exec {command_id}: two exec_parent links")
        parents[command_id] = link
        if command_id in roots:
            problems.append(f"exec {command_id}: matched and nested")
    for command_id, link in parents.items():
        record = execs_by_id.get(command_id)
        if record is None:
            problems.append(f"exec_parent {command_id}: unknown exec")
            continue
        rule, parent = link.get("rule"), link.get("parent")
        root_call = link.get("root_call_id")
        if rule not in J2_RULES:
            problems.append(f"exec_parent {command_id}: rule {rule!r}")
        if root_call not in calls_by_id:
            problems.append(f"exec_parent {command_id}: root {root_call!r}")
        candidates = link.get("root_candidates")
        if link.get("root_ambiguous") is True:
            if not isinstance(candidates, list) or len(set(candidates)) < 2:
                problems.append(f"exec {command_id}: ambiguous, < 2 candidates")
            elif root_call not in candidates:
                problems.append(f"exec {command_id}: root not a candidate")
        elif candidates != []:
            problems.append(f"exec {command_id}: candidates while unambiguous")
        if rule == "time_containment":
            parent_record = execs_by_id.get(parent)
            if parent_record is None:
                problems.append(f"exec {command_id}: parent {parent!r} unknown")
                continue
            if parent not in roots and parent not in parents:
                problems.append(f"exec {command_id}: parent is not a root")
            start = record["t_start"]
            end = _interval_end(parent_record, run_end)
            if not (
                parent_record["t_start"] - TOLERANCE <= start <= end + TOLERANCE
            ):
                problems.append(f"exec {command_id}: start outside parent")
            expected_root = roots.get(parent) or (
                parents.get(parent, {}).get("root_call_id")
            )
            if expected_root != root_call:
                problems.append(f"exec {command_id}: root differs from parent")
        elif rule == "parent_id":
            recorded = record.get("parent_command_id")
            outer = {
                lk["command_id"]: lk["of"]
                for lk in by_type["wrapper_duplicate"]
            }
            if parent != outer.get(recorded, recorded):
                problems.append(f"exec {command_id}: parent_id not recorded")
            expected_root = roots.get(parent) or (
                parents.get(parent, {}).get("root_call_id")
            )
            if expected_root != root_call or link.get("root_ambiguous"):
                problems.append(f"exec {command_id}: parent_id root differs")
        elif rule == "reparenting":
            if parent is not None:
                problems.append(f"exec {command_id}: reparented with a parent")
            launcher = [e for e, c in roots.items() if c == root_call]
            if not launcher:
                problems.append(f"exec {command_id}: root has no record")
                continue
            launched = execs_by_id[launcher[0]]
            if launched["t_end"] > record["t_start"] + TOLERANCE:
                problems.append(f"exec {command_id}: root not finished")
            if not backgrounds(launched.get("command") or ""):
                problems.append(f"exec {command_id}: root backgrounds nothing")
    for command_id in parents:
        seen = set()
        node = command_id
        while node in parents and parents[node].get("parent") is not None:
            if node in seen:
                problems.append(f"exec {command_id}: parent chain has a cycle")
                break
            seen.add(node)
            node = parents[node]["parent"]
    for (item_type, item_id), got in rows_by_id.items():
        if item_type != "exec" or len(got) != 1 or not trace_ok:
            continue
        row = got[0]
        if (
            row.get("disposition") == "matched_root"
            and row.get("root_under") is not True
        ):
            link = next(
                lk for lk in by_type["call_exec"] if lk["command_id"] == item_id
            )
            if link.get("rule") != "prefix":
                problems.append(f"exec {item_id}: matched root not root_under")
        if row.get("disposition") == "nested" and item_id in parents:
            link = parents[item_id]
            if row.get("root_under") is True and (
                link.get("rule") == "reparenting" or link.get("root_ambiguous")
            ):
                problems.append(f"exec {item_id}: root_under on a guess")
    return problems


def _event_key(event: Mapping[str, Any]) -> tuple:
    return (event["t_end"], event["t_start"], event["path"], event["writer_id"])


def _check_j3(calls_by_id, execs_by_id, by_type, result, final_diff):
    problems = []
    events = by_type["write_event"]
    by_id = {}
    for event in events:
        event_id = event.get("event_id")
        if event_id in by_id:
            problems.append(f"write_event id {event_id} repeated")
        by_id[event_id] = event
        writer_type, writer = event.get("writer_type"), event.get("writer_id")
        if writer_type == "exec":
            if writer not in execs_by_id:
                problems.append(f"{event_id}: writer {writer!r} not an exec")
        elif writer_type == "edit_call":
            if writer not in calls_by_id:
                problems.append(f"{event_id}: writer {writer!r} not a call")
            if not math.isclose(
                event.get("t_start", 0), event.get("t_end", 1), abs_tol=1e-9
            ):
                problems.append(f"{event_id}: edit event with an interval")
        else:
            problems.append(f"{event_id}: writer_type {writer_type!r}")
        if event.get("possibly_edit_call") is not None and event.get(
            "writer_under"
        ):
            problems.append(f"{event_id}: possibly_edit_call but writer_under")
        if event.get("root_ambiguous") and event.get("writer_under"):
            problems.append(f"{event_id}: root_ambiguous but writer_under")
        record = execs_by_id.get(writer) if writer_type == "exec" else None
        if record is not None and not (
            math.isclose(event["t_start"], record["t_start"], abs_tol=1e-5)
            and math.isclose(event["t_end"], record["t_end"], abs_tol=1e-5)
        ):
            problems.append(f"{event_id}: not the measured interval (D32)")
        if event.get("order_t") != event.get("t_end"):
            problems.append(f"{event_id}: order_t is not t_end (D32)")
    try:
        ordered = sorted(events, key=_event_key)
        expected_ids = [f"w{n}" for n in range(len(ordered))]
        if [e.get("event_id") for e in ordered] != expected_ids:
            problems.append("event ids are not w0.. in event order (R6)")
    except (KeyError, TypeError):
        problems.append("write events lack sort fields")
    paths = {}
    for row in result.get("paths", []):
        if row.get("path") in paths:
            problems.append(f"paths row for {row.get('path')} repeated")
        paths[row.get("path")] = row
        listed = row.get("events") or []
        for event_id in listed:
            if event_id not in by_id or by_id[event_id]["path"] != row["path"]:
                problems.append(f"path {row['path']}: bad event {event_id}")
        final = row.get("final_writer")
        if final is not None and final not in listed:
            problems.append(f"path {row['path']}: final writer not an event")
        exists = row.get("final_state") in EXISTING_STATES
        if (
            final is None
            and listed
            and not (
                exists
                and all(by_id.get(e, {}).get("op") == "delete" for e in listed)
            )
        ):
            problems.append(f"path {row['path']}: events but no final writer")
        if exists and final in by_id and by_id[final].get("op") == "delete":
            problems.append(f"path {row['path']}: delete is final (D34)")
        if final is None and row.get("final_writer_under"):
            problems.append(f"path {row['path']}: final_writer_under on null")
    for event in events:
        if event.get("path") not in paths:
            problems.append(f"event {event.get('event_id')}: no paths row")
    if final_diff:
        for key in ("modified", "deleted", "mode_changed"):
            for path in final_diff.get(key) or []:
                if path not in paths:
                    problems.append(f"final-diff path {path}: no paths row")
    return problems


def _check_rates(coverage: Mapping[str, Any]) -> list[str]:
    problems = []
    rates = coverage.get("rates", {}) if isinstance(coverage, Mapping) else {}
    for key, value in rates.items():
        if key in NON_RATE_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            problems.append(f"rate {key}: {value!r} not a number")
        elif not 0.0 <= value <= 1.0 or math.isnan(value):
            problems.append(f"rate {key}: {value!r} outside [0, 1]")
    reverted = rates.get("reverted_changes")
    if reverted is not None and (not isinstance(reverted, int) or reverted < 0):
        problems.append(f"reverted_changes {reverted!r}")
    return problems


def _check_preconditions(
    coverage: Mapping[str, Any], trace_ok: bool
) -> list[str]:
    problems = []
    states = coverage.get("preconditions")
    if not isinstance(states, Mapping) or set(states) != set(
        PRECONDITION_RATES
    ):
        return [f"preconditions {states!r}: not the four labels (D30)"]
    rates = coverage.get("rates", {})
    for label, state in states.items():
        rate = rates.get(PRECONDITION_RATES[label])
        if state not in PRECONDITION_STATES:
            problems.append(f"precondition {label}: state {state!r}")
        elif state == "holds" and rate is None:
            problems.append(f"precondition {label}: holds on a null rate")
        elif state == "vacuous" and rate is not None:
            problems.append(f"precondition {label}: vacuous on a rate")
        if not trace_ok and state != "fails":
            problems.append(f"precondition {label}: {state} without trace")
        warned = any(
            w.startswith(f"precondition {label} ")
            for w in coverage.get("warnings", [])
        )
        if warned != (state == "fails"):
            problems.append(f"precondition {label}: {state}, warned {warned}")
    return problems
