"""Independent invariants of the writer layer (spec §2.2.3).

``check_writer(scenario, result)`` reads only a loaded scenario (the
input records) and ``writer.derive``'s result (the output records), and
returns one message per violated invariant. It does not import
``writer.py``: windows (LW4), the candidate pool (LW3) and listing
members (LW15) are recomputed here from the spec's rules, so a bug the
implementation shares with a scenario's expectations can still show.

Invariant 6's second half calls ``writer.last_writer`` and lives in
``test_writer_invariants.py``.
"""

import fnmatch
import math
import posixpath
import re
from collections.abc import Mapping
from typing import Any

import writer_helpers as helpers

from trajectory_checks import match

Problems = list[str]


def _number(event_id: str) -> int:
    return int(event_id[1:])


def windows(scenario: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    """LW4's observation window of every write event, from the spec.

    Args:
        scenario: A loaded scenario.

    Returns:
        Event id -> (window_lo, window_hi).
    """
    run = scenario["run"]
    exact = run["capabilities"]["exact_timing"]
    slop = {**match.DEFAULTS, **(scenario["config"] or {})}["snapshot_slop_s"]
    execs = {e["command_id"]: e for e in scenario["execs"]}
    calls = {c["call_id"]: c for c in scenario["calls"]}
    out = {}
    for event_id, event in helpers.events_of(scenario).items():
        if event["writer_type"] == "exec":
            e = execs[event["writer_id"]]
            if exact and e["ended_at"] is not None:
                low = e["released_at"]
                after = e["snapshot_after_s"]
                out[event_id] = (
                    e["t_start"] if low is None else low,
                    e["ended_at"] + (slop if after is None else after),
                )
            else:
                out[event_id] = (
                    e["t_start"],
                    e["t_start"] + e["duration_s"] + slop,
                )
        else:
            c = calls[event["writer_id"]]  # an untimed start is -inf
            start = -math.inf if c["t_start"] is None else c["t_start"]
            ends = (c["t_end"], c["t_start"], math.inf)
            end = next(t for t in ends if t is not None)
            p = c["t_precision_s"]
            out[event_id] = (start - p, end + p)
    return out


def _glob(pattern: str, path: str) -> bool:
    # oracle_inputs.glob_match's semantics (layer 4's OI5), for the
    # synthetic patterns of the scenarios: per component, ** any number
    # of components, a hidden name matched only by an explicit dot.
    def parts(pat: list[str], got: list[str]) -> bool:
        if not pat:
            return not got
        head = pat[0]
        hidden = bool(got) and got[0].startswith(".")
        if head == "**":
            return parts(pat[1:], got) or (
                bool(got) and not hidden and parts(pat, got[1:])
            )
        return (
            bool(got)
            and (head.startswith(".") or not hidden)
            and fnmatch.fnmatchcase(got[0], head)
            and parts(pat[1:], got[1:])
        )

    return parts(pattern.split("/"), path.split("/"))


def _member_paths(
    scenario: Mapping[str, Any], row: Mapping[str, Any]
) -> list[str]:
    pattern = posixpath.join(row["path"], row["pattern"] or "")
    return [
        p["path"]
        for p in scenario["linked"]["paths"]
        if _glob(pattern, p["path"])
    ]


def _pool(
    events: list[Mapping[str, Any]], exists: bool | None, writer: str
) -> list[Mapping[str, Any]]:
    live = [e for e in events if not (exists is True and e["op"] == "delete")]
    pick = [e for e in live if e["possibly_edit_call"] is None] or live
    return [e for e in pick if e["op"] != "mode" or e["event_id"] == writer]


def _rows_by_id(rows: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {r["input_id"]: r for r in rows}


def _labels(result: Mapping[str, Any]) -> dict[str, str]:
    return result["writer"]["preconditions"]


def _one_row_each(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    states = (scenario["input_states"] or {}).get("input_states") or []
    want = sorted(s["input_id"] for s in states)
    problems = []
    for name in ("last_writers", "input_rewrites"):
        got = [r["input_id"] for r in result[name]]
        if sorted(got) != want or len(set(got)) != len(got):
            problems.append(f"inv1 {name}: rows {got}, expected {want}")
        if got != sorted(got, key=helpers.input_number):
            problems.append(f"inv1 {name}: not sorted by input number")
    return problems


def _same_bound(written: float | None, bound: float) -> bool:
    """A written window end against LW4's: an unbounded one is null."""
    if not math.isfinite(bound):
        return written is None
    return written is not None and math.isclose(written, bound, abs_tol=1e-6)


def _writer_checks(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    problems = []
    events = helpers.events_of(scenario)
    found = windows(scenario)
    paths = {p["path"]: p for p in scenario["linked"]["paths"]}
    states = _rows_by_id(
        (scenario["input_states"] or {}).get("input_states") or []
    )
    for row in result["last_writers"]:
        where = f"input {row['input_id']}"
        state = states.get(row["input_id"]) or {}
        listing = row["input_type"] == "listing"
        writer = row["last_writer"]
        if listing:
            allowed = {
                e
                for p in _member_paths(scenario, row)
                for e in paths[p]["events"]
            }
        else:
            allowed = set((paths.get(row["path"]) or {}).get("events") or [])
        candidates = row["last_writer_candidates"]
        if not listing and writer != state.get("final_writer"):
            problems.append(f"inv6 {where}: writer is not layer 4's")
        if writer is None:
            if candidates:
                problems.append(f"inv2 {where}: candidates without a writer")
            continue
        # 2
        if writer not in allowed:
            problems.append(f"inv2 {where}: {writer} is not an event of it")
        if writer not in candidates:
            problems.append(f"inv2 {where}: writer not among candidates")
        if candidates != sorted(candidates, key=_number):
            problems.append(f"inv2 {where}: candidates not in event order")
        # 3
        op = row["last_writer_op"]
        if op != events[writer]["op"]:
            problems.append(f"inv3 {where}: last_writer_op is not the event's")
        if op not in ("create", "modify", "delete", "mode"):
            problems.append(f"inv3 {where}: op {op!r}")
        if listing and op not in ("create", "delete"):
            problems.append(f"inv3 {where}: listing writer op {op!r}")
        if op == "mode" and (
            listing
            or (paths.get(row["path"]) or {}).get("final_state") != "mode_only"
        ):
            problems.append(
                f"inv3 {where}: mode writer of a non-mode_only path"
            )
        exists = row["exists_at_read"]
        disagrees = (op == "delete" and exists is True) or (
            op in helpers.CONTENT_OPS and exists is False
        )
        if (
            disagrees
            and row["exists_source"] not in (None, "last_event")
            and row["state_conflict"] is not True
        ):
            problems.append(
                f"inv3 {where}: op contradicts existence, no conflict"
            )
        # 4
        low = found[writer][0]
        if not _same_bound(row["window_lo"], low):
            problems.append(
                f"inv4 {where}: window_lo {row['window_lo']} != {low}"
            )
        if not _same_bound(row["window_hi"], found[writer][1]):
            problems.append(f"inv4 {where}: window_hi")
        for c in candidates:
            if found[c][1] < low - 1e-6:
                problems.append(f"inv4 {where}: candidate {c} ends before lo")
        if listing:
            pool_events = [
                events[e]
                for e in allowed
                if events[e]["op"] in ("create", "delete")
            ]
            pool = _pool(pool_events, None, writer)
        else:
            state_ = (paths.get(row["path"]) or {}).get("final_state")
            exists_ = True if state_ in helpers.EXISTING_STATES else None
            pool = _pool([events[e] for e in allowed], exists_, writer)
        for event in pool:
            if found[event["event_id"]][1] >= low + 1e-9 and (
                event["event_id"] not in candidates
            ):
                problems.append(
                    f"inv4 {where}: pool event {event['event_id']} missing"
                )
        # 6 (first half)
        if not listing:
            summary = paths.get(row["path"]) or {}
            if writer != summary.get("final_writer"):
                problems.append(f"inv6 {where}: writer is not layer 1's")
    return problems


def _under_checks(
    result: Mapping[str, Any], scenario: Mapping[str, Any]
) -> Problems:
    problems = []
    sources = _rows_by_id(
        (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []
    )
    states = _rows_by_id(
        (scenario["input_states"] or {}).get("input_states") or []
    )
    for row in result["last_writers"]:
        where = f"input {row['input_id']}"
        reasons = row["not_under"]
        if bool(reasons) == bool(row["last_writer_under"]):
            problems.append(f"inv5 {where}: not_under {reasons} vs under")
        if set(reasons) - set(helpers.NOT_UNDER_REASONS):
            problems.append(f"inv5 {where}: unknown reasons {reasons}")
        if reasons != sorted(reasons, key=helpers.NOT_UNDER_REASONS.index):
            problems.append(f"inv5 {where}: reasons not in order")
        if row["last_writer_under"]:
            state = states.get(row["input_id"]) or {}
            written = row["last_writer_type"] in ("exec", "edit_call")
            bad = [
                not row["visible"],
                not row["in_under"],
                row["last_writer_type"] is None,
                written and len(row["last_writer_candidates"]) != 1,
                written and row["writer_root_certain"] is not True,
                bool(row["later_writer_risk"]),
                row["state_conflict"] is True,
                row["read_version"] == "differs",
                row["read_version"] == "unverified"
                and row["oracle_writes"] is True,
                bool(state.get("model_conflict")),
            ]
            if any(bad):
                problems.append(f"inv5 {where}: under although {bad}")
        source = sources.get(row["input_id"]) or {}
        for key in ("oracle_writes", "oracle_writes_basis"):
            if row[key] != source.get(key):
                problems.append(f"inv5 {where}: {key} not copied")
    return problems


def _rewrite_checks(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    problems = []
    events = helpers.events_of(scenario)
    afters = {"ok", "failed", "killed", "unknown", "edit"}
    for row in result["input_rewrites"]:
        where = f"input {row['input_id']}"
        members = row["members"]
        if members is None:
            if row["pairs"] or row["rewrites_over"] is not None:
                problems.append(
                    f"inv7 {where}: pairs or counts without members"
                )
            continue
        keys = [
            (-math.inf if m["first_lo"] is None else m["first_lo"], m["seq"])
            for m in members
        ]
        if keys != sorted(keys):
            problems.append(f"inv7 {where}: members not sorted by first_lo")
        for member in members:
            owned = [
                e
                for e in member["events"]
                if events[e]["op"] in helpers.CONTENT_OPS
            ]
            if not owned:
                problems.append(f"inv7 {where}: member without content event")
            for e in member["events"]:
                event = events[e]
                owner = (
                    event["root_call_id"]
                    if event["writer_type"] == "exec"
                    else event["writer_id"]
                )
                if owner != member["call_id"]:
                    problems.append(f"inv7 {where}: {e} owned by another call")
        unowned = row["unowned_execs"]
        if row["rewrites_over"] != max(0, len(members) + len(unowned) - 1):
            problems.append(f"inv7 {where}: rewrites_over")
        for command in unowned:  # a content writer with no owning call
            if not any(
                e["writer_id"] == command
                and e["writer_type"] == "exec"
                and e["root_call_id"] is None
                and e["op"] in helpers.CONTENT_OPS
                for e in events.values()
            ):
                problems.append(f"inv7 {where}: {command} is not unowned")
        if not 0 <= row["rewrites_under"] <= row["rewrites_over"]:
            problems.append(f"inv7 {where}: rewrites_under")
        if len(row["pairs"]) != max(0, len(members) - 1):
            problems.append(f"inv7 {where}: pairs")
        for pair in row["pairs"]:
            if pair["after"] not in afters:
                problems.append(f"inv7 {where}: after {pair['after']!r}")
        if row["rewrites_under"] != sum(p["under"] for p in row["pairs"]):
            problems.append(f"inv7 {where}: rewrites_under is not the pairs'")
        if set(row["delete_roots"]) & {m["call_id"] for m in members}:
            problems.append(f"inv7 {where}: a member is a delete root")
    return problems


def diff_line_counts(text: str) -> dict[str, tuple[int, int]]:
    """Per diff section path: its removed and added body line counts.

    Args:
        text: A git diff.

    Returns:
        Path (the ``b/`` path, the ``a/`` path of a deletion) -> (removed,
        added), counted inside ``@@`` sections only.
    """
    out = {}
    for section in re.split(r"^diff --git ", text or "", flags=re.M)[1:]:
        lines = section.split("\n")
        header = lines[0]
        old, _, new = header.partition(" b/")
        path = new
        removed = added = 0
        body = False
        for line in lines[1:]:
            if line.startswith("deleted file mode"):
                path = old[2:]
            if line.startswith("rename to "):
                path = line[len("rename to ") :]
            if line.startswith("@@"):
                body = True
                continue
            if body and line.startswith("-"):
                removed += 1
            elif body and line.startswith("+"):
                added += 1
        out[path] = (removed, added)
    return out


def _hunk_checks(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    problems = []
    hunks = result["hunks"]
    keys = [(h["path"], h["hunk_index"]) for h in hunks]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        problems.append("inv8: hunks not sorted by (path, hunk_index)")
    for hunk in hunks:
        where = f"hunk {hunk['path']}#{hunk['hunk_index']}"
        if hunk["hunk_type"] not in helpers.HUNK_TYPES:
            problems.append(f"inv8 {where}: type {hunk['hunk_type']!r}")
        if hunk["in_named_under"] is True and hunk["in_named_over"] is not True:
            problems.append(f"inv9 {where}: named under but not over")
        if not hunk["lexed"] and hunk["touches_check"] is not None:
            problems.append(f"inv8 {where}: touches_check without lexing")
    text = scenario["final_diff_text"]
    labels = _labels(result)
    if labels["final_diff_content"] == "fails":
        if hunks:
            problems.append("inv10: hunks without final_diff_content")
        return problems
    unclassified = {
        u["path"] for u in result["writer"]["diff_files_unclassified"]
    }
    sums: dict[str, list[int]] = {}
    indexes: dict[str, list[int]] = {}
    for hunk in hunks:
        got = sums.setdefault(hunk["path"], [0, 0])
        got[0] += hunk["lines_removed"]
        got[1] += hunk["lines_added"]
        indexes.setdefault(hunk["path"], []).append(hunk["hunk_index"])
    for path, found in indexes.items():
        if found != list(range(len(found))):
            problems.append(f"inv8 {path}: hunk_index not 0, 1, ...")
    for path, (removed, added) in diff_line_counts(text).items():
        if path in unclassified or (removed, added) == (0, 0):
            continue
        if tuple(sums.get(path, (0, 0))) != (removed, added):
            problems.append(
                f"inv8 {path}: hunk lines {sums.get(path)} != diff "
                f"{(removed, added)}"
            )
    return problems


_GUARDED = (
    ("trace", "M3.3", None),
    ("trace", "M2.4", None),
    ("oracle_inputs", "M3.3", None),
    ("oracle_inputs", "M2.4", None),
    ("oracle_inputs", "M4.3", helpers.NAMED_KEYS),
    ("input_states", "M3.3", None),
    ("input_states", "M2.4", None),
    ("C1_under", "M2.4", None),
    ("repeats", "M2.4", helpers.REPEATS_KEYS),
    ("final_diff_content", "M4.3", None),
    ("file_class", "M4.3", frozenset({"hunks_by_class_and_type"})),
    ("read_events", "M4.3", frozenset({"hunks_by_type_read"})),
)


def _metric_checks(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    problems = []
    run = result["writer"]
    labels = run["preconditions"]
    if set(labels) != set(helpers.PRECONDITION_LABELS):
        problems.append(f"inv10: precondition labels {sorted(labels)}")
    for label, value in labels.items():
        if value not in ("holds", "vacuous", "fails"):
            problems.append(f"inv10: {label} = {value!r}")
    voided: set[tuple[str, str]] = set()
    for label, block, keys in _GUARDED:
        if labels.get(label) == "fails":
            names = helpers.METRIC_KEYS[block] if keys is None else keys
            voided |= {(block, k) for k in names}
    for block, names in helpers.METRIC_KEYS.items():
        values = run["metrics"][block]
        if set(values) != set(names):
            problems.append(
                f"inv10 {block}: keys {sorted(set(values) ^ set(names))}"
            )
            continue
        for key in names:
            value = values[key]
            if (block, key) in voided:
                if value is not None:
                    problems.append(f"inv10 {block}.{key}: not voided")
                continue
            if value is None:
                if key not in helpers.EXTREMA_KEYS:
                    problems.append(f"inv10 {block}.{key}: null, not voided")
                continue
            if isinstance(value, dict):
                cells = [
                    v
                    for sub in value.values()
                    for v in (sub.values() if isinstance(sub, dict) else [sub])
                ]
                if not all(isinstance(v, int) and v >= 0 for v in cells):
                    problems.append(f"inv10 {block}.{key}: {value}")
            elif key not in helpers.EXTREMA_KEYS and not (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            ):
                problems.append(f"inv10 {block}.{key}: {value!r}")
    if run["metrics"]["M4.3"]["hunks_by_type"] is not None:
        if set(run["metrics"]["M4.3"]["hunks_by_type"]) != set(
            helpers.HUNK_TYPES
        ):
            problems.append("inv10: hunks_by_type lacks a type")
    # 12
    coverage = scenario["linked"]["coverage"]
    if run["capabilities"] != coverage["capabilities"]:
        problems.append("inv12: capabilities not copied from coverage.json")
    if set(run["bases"]) != set(helpers.BASES_KEYS):
        problems.append(f"inv12: bases keys {sorted(run['bases'])}")
    caps = scenario["run"]["capabilities"]
    for key, needs in helpers.BASIS_NEEDS.items():
        if run["bases"].get(key) == "exact" and not all(caps[n] for n in needs):
            problems.append(f"inv12: bases.{key} exact without {needs}")
        if run["bases"].get(key) not in ("exact", "fallback"):
            problems.append(f"inv12: bases.{key} = {run['bases'].get(key)!r}")
    block = (scenario["oracle_inputs"] or {}).get("oracle_inputs_run")
    if (
        block is not None
        and run["bases"]["oracle_writes"] != block["bases"]["oracle_writes"]
    ):
        problems.append("inv12: bases.oracle_writes not copied")
    for warning in run["warnings"]:
        if not warning.startswith(("precondition ", "input ")):
            problems.append(f"warning form: {warning!r}")
    if (
        run["schema_version"] != "1"
        or run["run_key"] != scenario["run"]["run_key"]
    ):
        problems.append("writer.json identity")
    return problems


def _evidence_checks(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    problems = []
    run = result["writer"]
    events = helpers.events_of(scenario)
    states = _rows_by_id(
        (scenario["input_states"] or {}).get("input_states") or []
    )
    sources = _rows_by_id(
        (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []
    )
    lists = {
        "blind_snapshot": ("blind_execs", "exec"),
        "incomplete": ("detached_execs", "exec"),
        "orphaned": ("detached_execs", "exec"),
        "unrecorded": ("unmonitored_calls", "call"),
    }
    for row in result["last_writers"]:
        where = f"input {row['input_id']}"
        state = states.get(row["input_id"]) or {}
        # 11
        written = set(state.get("states_over") or []) & helpers.WRITTEN_STATES
        if written:
            for c in row["last_writer_candidates"]:
                event = events[c]
                root = (
                    event["root_call_id"]
                    if event["writer_type"] == "exec"
                    else event["writer_id"]
                )
                if root is not None and root not in row["root_candidates"]:
                    problems.append(f"inv11 {where}: root {root} missing")
        elif row["root_candidates"]:
            problems.append(f"inv11 {where}: root candidates without a write")
        if row["root_candidates_complete"] and state.get(
            "unseen_writer_possible"
        ):
            problems.append(f"inv11 {where}: complete with an unseen writer")
        # 13
        causes = row["differs_causes"]
        if (causes is not None) != (row["read_version"] == "differs"):
            problems.append(f"inv13 {where}: differs_causes {causes}")
        if (
            causes
            and "late_process" in causes
            and not (
                run["bases"]["read_version"] == "fallback"
                and state.get("late_write", True)
            )
        ):
            problems.append(f"inv13 {where}: late_process without its cause")
        if causes is not None and causes != [
            c for c in ("oracle", "late_process") if c in causes
        ]:
            problems.append(f"inv13 {where}: causes order")
        # 14
        source = sources.get(row["input_id"]) or {}
        if (
            source.get("oracle_writes_basis") == "exact"
            and run["bases"]["read_version"] == "exact"
            and (row["read_version"] == "differs")
            != (source.get("oracle_writes") is True)
        ):
            problems.append(f"inv14 {where}: differs vs exact oracle_writes")
        # 15
        risks = row["later_writer_risk"]
        if [(r["reason"], r["id"]) for r in risks] != sorted(
            ((r["reason"], r["id"]) for r in risks),
            key=lambda x: (helpers.RISK_REASONS.index(x[0]), x[1]),
        ):
            problems.append(f"inv15 {where}: risk items not in order")
        for risk in risks:
            name, item_type = lists[risk["reason"]]
            if risk["id"] not in (state.get(name) or []):
                problems.append(
                    f"inv15 {where}: {risk} not in layer 4's {name}"
                )
            if (
                risk["item_type"] != item_type
                or set(risk) != helpers.RISK_FIELDS
            ):
                problems.append(f"inv15 {where}: {risk}")
    return problems


def check_writer(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> Problems:
    """Checks invariants 1 to 15 of spec §2.2.3 (6: its first half).

    Args:
        scenario: A loaded scenario (``writer_helpers.build_scenario``).
        result: ``writer.derive``'s result on it.

    Returns:
        One message per violation; empty when every invariant holds.
    """
    return [
        *_one_row_each(scenario, result),
        *_writer_checks(scenario, result),
        *_under_checks(result, scenario),
        *_rewrite_checks(scenario, result),
        *_hunk_checks(scenario, result),
        *_metric_checks(scenario, result),
        *_evidence_checks(scenario, result),
    ]
