"""Layer 4, steps P1 to P6: the state of each oracle input at grading.

Counts M3.2 as `trajectory-provenance-spec.md` §1-§13 specify. Every
oracle input of §0 (`oracle_inputs.py`) gets the labels of `STATES` that
its evidence allows under two models: the strict model M_s (assumptions
S1-S5) gives `states_under`, the relaxed model M_r (S1, S2, S4) gives
`states_over`. The evidence is layer 1's records, §0's rows, the baseline,
the final diff and MC-03's final files; the final writer of a path is
layer 1's, copied (D29). Every function is pure: `cli.analyse` reads the
files and passes their content (D32).
"""

import itertools
import math
import types
import typing

from trajectory_checks import match, parse
from trajectory_checks import oracle_inputs as oracle  # the name is a param


class InputState(typing.TypedDict):
    """One oracle input and how its graded content got there (spec §5.1)."""

    path: str
    input_id: str
    input_type: str  # file, listing
    input_scope: str  # workspace, outside_workspace
    pattern: str | None
    in_under: bool
    source_type: str  # graded, null_agent
    access_types: list[str]
    phases: list[str]
    check_ids: list[str]
    member_of: list[str]
    members: list[str]
    tracked: bool | None
    baseline_exists: bool | None
    baseline_sha256: str | None
    baseline_source: str | None  # tracked_files, pre_oracle_pass
    grading_exists: bool | None
    grading_sha256: str | None
    content_cmp: str | None  # equal, differs
    content_source: str | None  # grading_read, final_workspace, final_diff
    content_sources_disagree: bool
    oracle_writes: bool | None
    events: list[str]
    edit_events: list[str]
    writer_roots: list[dict[str, typing.Any]]
    final_writer: str | None
    final_writer_type: str | None  # exec, edit_call
    final_writer_id: str | None
    final_writer_root: str | None
    final_writer_under: bool
    observable: bool
    blind_execs: list[str]
    unmonitored_calls: list[str]
    detached_execs: list[str]
    unseen_writer_possible: bool
    late_write: bool  # the run's D73 cause, for layer 5 (L4-a)
    rewrite_type: str | None  # same_content, restored
    states_under: list[str]
    states_over: list[str]
    state_under: str | None
    under_reason: str | None
    model_conflict: bool
    origin_under: str | None  # baseline_file, new_file
    origins_over: list[str]
    predicates_under: dict[str, bool]
    predicates_over: dict[str, bool]
    listing_changed_under: bool | None
    listing_changed_over: bool | None


class Evidence(typing.TypedDict):
    """The P3 and P4 facts of one input that intersect reads (§6.3)."""

    log: bool  # coverage.inputs.log (CON6)
    baseline_exists: bool | None
    baseline_sha256: str | None
    grading_exists: bool | None
    content_cmp: str | None  # equal, differs
    content_sources_disagree: bool
    oracle_writes: bool | None
    oracle_writes_basis: str | None  # exact, fallback (§0's row)
    events: list[str]
    edit_events: list[str]
    exec_events: list[str]  # events with writer_type exec
    final_writer_type: str | None  # exec, edit_call
    final_writer_under: bool
    final_writer_op: str | None  # create, modify, delete, mode (CON15)
    final_writer_hash: str | None  # snapshot.hashes[path] (CON7)
    content_hashes: bool  # the capability (CON7)
    content_late: bool  # the final diff may predate an agent write (D73)
    observable: bool
    blind_execs: list[str]
    unmonitored_calls: list[str]


class StateCounts(typing.TypedDict):
    """state_counts.json (spec §5.2)."""

    schema_version: str
    run_key: str | None
    family: str | None
    capabilities: dict[str, bool]
    bases: dict[str, str]  # basis key -> exact, fallback
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    metrics: dict[str, dict[str, typing.Any]]  # "M3.2" -> fields
    warnings: list[str]


class StateResult(typing.TypedDict):
    """Everything P1 to P6 produce for one run."""

    input_states: list[InputState]
    state_counts: StateCounts


# §6.3: the ground-truth labels; the order is the output order.
STATES = tuple(
    "pristine rewritten_identical edit_written command_written "
    "oracle_written deleted missing".split()
)
# §6.5, CNT1: predicate -> the labels it holds on.
PREDICATES = types.MappingProxyType(
    {
        name: frozenset(labels.split())
        for name, labels in (
            ("baseline_content", "pristine rewritten_identical"),
            (
                "fresh",
                "rewritten_identical edit_written command_written "
                "oracle_written",
            ),
            ("agent_written", "edit_written command_written"),
            ("edit_written", "edit_written"),
            ("command_written", "command_written"),
            ("absent", "deleted missing"),
        )
    }
)
# §0.3: the access types that read content.
CONTENT_ACCESS = frozenset({"read", "command"})
# CON13, in precedence order.
UNDER_REASONS = tuple(
    "model_conflict no_content_evidence writers_unobserved "
    "no_baseline_evidence final_writer_uncertain underdetermined".split()
)
# §6.9: (basis key, layer 1's capability, rule); §0's keys first.
CAPABILITY_RULES = (
    ("source", "oracle_reads", "OI1"),
    ("pattern_matches", "untracked_visible", "OI5"),
    ("oracle_writes", "oracle_reads", "OI10"),
    ("content", "oracle_reads", "EVD2"),
    ("observable", "untracked_visible", "EVD6"),
    ("shim", None, "EVD6"),  # RunInfo.shim_intact known (MC-17)
    ("unmonitored", "start_events", "EVD7"),
    ("detached", "parent_links", "EVD8"),
    ("content_hash", "content_hashes", "CON7"),
    ("timing", "exact_timing", "EVD7"),
)
# §7: (label, what a failure voids).
PRECONDITIONS = (
    ("oracle_inputs", "M3.2"),
    ("log", "M3.2"),
    ("trace", None),
    ("content", None),
)
# §6.7: the status oracle_result.json writes for a passed phase.
DEFAULTS = types.MappingProxyType({"passing_statuses": ("success",)})
# PHS1, PHS2: the per-phase flags, also the CSV sub-keys of `phases`.
PHASE_FLAGS = tuple(
    "status inputs inputs_complete baseline_content_all_under "
    "baseline_content_all_over agent_written_any_under "
    "agent_written_any_over".split()
)
# §0.2: the oracle's phases, the fixed CSV sub-keys of `phases` (D69).
PHASES = ("env_setup", "artifact_build", "benchmark_prep", "experiment_runs")
# §5.3, for cli.facts_row; `phases` is added by cli.layer4_rows (D38).
DICT_FIELDS = types.MappingProxyType(
    {"by_state_under": STATES, "by_state_over": STATES}
)
# CON11: the origins a label implies; any other label implies both.
_ORIGINS = types.MappingProxyType(
    dict.fromkeys(
        ("pristine", "rewritten_identical", "deleted"), {"baseline_file"}
    )
    | {"missing": {"new_file"}}
)
_NO_CONTENT = types.MappingProxyType(
    dict.fromkeys(
        ("content_source", "grading_exists", "grading_sha256", "content_cmp")
    )
    | {"content_sources_disagree": False}
)
_lower = oracle._lower  # hashes are compared as lowercase hex (§6.8)


def input_rows(
    rows: list[oracle.OracleInput],
) -> tuple[list[dict[str, typing.Any]], int]:
    """P2 (INP2-INP4): the input rows to classify, and the rows dropped.

    Each returned dict holds the §5.1 fields copied or derived from §0
    (path, input_id, input_type, input_scope, pattern, in_under,
    source_type, access_types, phases, check_ids, member_of, members)
    and is completed by P3 to P5; the int is reference_inputs_excluded.
    """
    kept = [r for r in rows if r["input_scope"] != "reference"]
    by_id = {r["input_id"]: r for r in kept}
    members: dict[str, set[str]] = {}  # one pass, not one per row (S1)
    for row in kept:
        for i in row["listed_by"]:
            members.setdefault(i, set()).add(row["path"])
    out = []
    for row in kept:
        listing = row["match_type"] == "pattern"
        listed = {by_id[i]["path"] for i in row["listed_by"] if i in by_id}
        out.append(
            {
                "path": row["path"],
                "input_id": row["input_id"],
                "input_type": "listing" if listing else "file",
                "input_scope": row["input_scope"],
                "pattern": row["pattern"],
                "in_under": row["in_under"],
                "source_type": row["source_type"],
                "access_types": list(row["access_types"]),
                "phases": list(row["phases"]),
                "check_ids": list(row["checks"]),
                "member_of": [] if listing else sorted(listed),
                "members": sorted(members.get(row["input_id"], ()))
                if listing
                else [],
            }
        )
    out.sort(
        key=lambda r: (
            r["input_type"] != "file",
            r["input_scope"] != "workspace",
            r["path"],
            r["pattern"] or "",
        )
    )
    return out, len(rows) - len(kept)


def content_compare(
    baseline_exists: bool | None,
    baseline_sha256: str | None,
    grading_exists: bool | None,
    grading_sha256: str | None,
    *,
    diff_cmp: str | None = None,
) -> str | None:
    """EVD3: equal, differs or None; diff_cmp is the final diff's answer."""
    if diff_cmp is not None:
        return diff_cmp
    if baseline_exists is None or grading_exists is None:
        return None
    if baseline_exists != grading_exists:
        return "differs"
    one, two = _lower(baseline_sha256), _lower(grading_sha256)
    if not baseline_exists or one is None or two is None:
        return None
    return "equal" if one == two else "differs"


def detached_execs(
    execs: list[parse.Exec],
    links: list[match.Link],
    *,
    capabilities: typing.Mapping[str, bool],
) -> tuple[list[str], str]:
    """EVD8: detached exec ids in exec seq order, and the basis."""
    live = sorted((e for e in execs if e["noise"] is None), key=_seq)
    parents = bool(capabilities.get("parent_links"))
    if parents and all(isinstance(e["orphans"], list) for e in live):
        ids = [
            e["command_id"] for e in live if e["orphans"] or not e["complete"]
        ]
        return ids, "exact"
    suspects = {
        link["command_id"]
        for link in links
        if link["link_type"] == "exec_parent" and link["rule"] == "reparenting"
    }
    if parents:
        suspects |= {
            e["parent_command_id"]
            for e in execs
            if e["parent_link"] == "adopted" and e["parent_command_id"]
        }
    ids = [
        e["command_id"]
        for e in live
        if not e["complete"]
        or e["command_id"] in suspects
        or match.backgrounds(e["command"] or "")
    ]
    return ids, "fallback"


def rewrite_type(
    event_hashes: list[str | None],
    baseline_sha256: str | None,
    *,
    content_hashes: bool,
    has_edit: bool,
) -> tuple[str | None, str]:
    """CON7's split for a rewritten_identical candidate, and the basis.

    event_hashes holds snapshot.hashes[path] of each exec event, in event
    order; the caller applies it only when rewritten_identical is in
    states_over and the row has events.
    """
    if not content_hashes:
        return None, "fallback"
    base = _lower(baseline_sha256)
    if has_edit or base is None or not event_hashes or None in event_hashes:
        return None, "exact"  # D71: an unknown baseline is ⊥, as D57
    if any(_lower(h) != base for h in event_hashes):
        return "restored", "exact"
    return "same_content", "exact"


def intersect(evidence: Evidence, strict: bool) -> frozenset[str]:
    """Returns the labels the evidence allows under one model (§6.3)."""
    ev = evidence
    labels = set(STATES)
    written, basis = ev["oracle_writes"], ev["oracle_writes_basis"]
    if written is not None and (strict or basis == "exact"):  # CON1
        if written:
            return frozenset({"oracle_written"})
        labels.discard("oracle_written")
    elif written is None and strict:  # S5, D5
        labels.discard("oracle_written")
    if ev["baseline_exists"] is True:  # CON2
        labels.discard("missing")
    elif ev["baseline_exists"] is False:
        labels -= {"pristine", "rewritten_identical", "deleted"}
    if strict or not ev["content_late"]:  # D73: S3 orders the final diff
        _exists(labels, ev["grading_exists"])  # CON3
        _compared(labels, ev["content_cmp"])  # CON4
    if ev["events"]:  # CON5
        labels.discard("pristine")
    if ev["log"] and not _possible(ev, "edit_call", "edit_events"):  # CON6
        labels.discard("edit_written")
    if not strict:
        return frozenset(labels)
    clear = (  # D61: CON7, CON8, CON9 and CON15 need a clear window
        ev["observable"]
        and not ev["blind_execs"]
        and not ev["unmonitored_calls"]
    )
    certain = clear and ev["final_writer_under"]  # D66: CON7, CON15
    seen = ev["final_writer_hash"]
    if (
        certain
        and ev["content_hashes"]
        and ev["content_cmp"] is None
        and seen is not None
    ):
        _exists(labels, True)  # CON7
        base = ev["baseline_sha256"]
        if base is not None:
            _compared(
                labels, "equal" if _lower(base) == _lower(seen) else "differs"
            )
        elif ev["baseline_exists"] is False:  # D57
            _compared(labels, "differs")
    if clear and not ev["events"]:  # CON8
        labels -= {"rewritten_identical", "deleted"}
    if clear and not _possible(ev, "exec", "exec_events"):  # CON9
        labels.discard("command_written")
    op, exists = ev["final_writer_op"], ev["grading_exists"]
    unseen = (op == "delete" and exists is True) or (
        op in ("create", "modify") and exists is False
    )
    if ev["content_sources_disagree"] or (certain and unseen):  # CON12, 15
        return frozenset()
    return frozenset(labels)


def _possible(ev: Evidence, writer_type: str, events: str) -> bool:
    """CON6, CON9: whether the final write can be of this writer type."""
    if ev["final_writer_under"]:
        return ev["final_writer_type"] == writer_type
    return bool(ev[events])


def _exists(labels: set[str], exists: bool | None) -> None:
    """CON3: narrows labels by the existence at grading."""
    if exists is False:
        labels &= {"deleted", "missing", "oracle_written"}
    elif exists is True:
        labels -= {"deleted", "missing"}


def _compared(labels: set[str], cmp: str | None) -> None:
    """CON4: narrows labels by the content comparison."""
    if cmp == "equal":
        labels &= {"pristine", "rewritten_identical", "oracle_written"}
    elif cmp == "differs":
        labels -= {"pristine", "rewritten_identical"}


def predicates(
    states_under: typing.Collection[str],
    states_over: typing.Collection[str],
) -> tuple[dict[str, bool], dict[str, bool]]:
    """CNT1: (predicates_under, predicates_over), one key per PREDICATES."""
    under, over = set(states_under), set(states_over)
    return (
        {n: bool(under) and under <= p for n, p in PREDICATES.items()},
        {n: bool(over & p) for n, p in PREDICATES.items()},
    )


def _seq(record: typing.Mapping[str, typing.Any]) -> int:
    return record["seq"]


def _ordered(labels: typing.Collection[str]) -> list[str]:
    """Labels in STATES order (§5)."""
    return [s for s in STATES if s in labels]


def _origins(labels: list[str], baseline_exists: bool | None) -> set[str]:
    """CON11: the origins left by the baseline or by the labels."""
    if baseline_exists is not None:
        return {"baseline_file" if baseline_exists else "new_file"}
    found: set[str] = set()
    for label in labels:
        found |= _ORIGINS.get(label, {"baseline_file", "new_file"})
    return found


def _preconditions(
    coverage: match.Coverage,
    run: match.RunInfo,
    given: oracle.OracleInputsResult | None,
    has_content: bool,
) -> tuple[dict[str, str], list[str]]:
    """P1, PRE1-PRE4: the labels and their warnings."""
    labels, warnings = {}, []
    block = (given or {}).get("oracle_inputs_run") or {}
    pre = block.get("preconditions") or {}
    fallback = (block.get("bases") or {}).get("source") != "exact"
    if (
        given is None
        or pre.get("oracle_inputs") == "fails"
        or (pre.get("null_commit") == "fails" and fallback)  # D51
    ):
        labels["oracle_inputs"] = "fails"
        warnings.append("input oracle_inputs missing: M3.2 null")
    elif any(r["input_scope"] != "reference" for r in given["oracle_inputs"]):
        labels["oracle_inputs"] = "holds"
    else:
        labels["oracle_inputs"] = "vacuous"
    labels["log"] = "holds" if coverage["inputs"]["log"] else "fails"
    if labels["log"] == "fails":
        warnings.append("precondition log failed: M3.2 null")
    traced = coverage["inputs"]["trace"] and coverage["integrity"]["C0.1"]
    labels["trace"] = "holds" if traced else "fails"
    labels["content"] = "holds" if has_content else "fails"
    if run.get("shim_intact") is False:
        warnings.append("shim not intact: snapshots not trusted")
    return labels, warnings


def _bases(run: match.RunInfo, detached: str) -> dict[str, str]:
    """§6.9: the run-level basis of each capability-dependent rule."""
    known = {**run["capabilities"], None: run.get("shim_intact") is not None}
    bases = {
        key: "exact" if known.get(capability) else "fallback"
        for key, capability, _ in CAPABILITY_RULES[4:]  # after §0's, content
    }
    return dict(sorted({**bases, "detached": detached}.items()))


class _Run(typing.NamedTuple):
    """The run-level facts P3 to P5 read for every input."""

    run: match.RunInfo
    calls: list[parse.Call]
    execs: list[parse.Exec]  # in seq order
    hashes: dict[str, dict[str, str]]  # MC-07: command_id -> path -> hash
    events: dict[str, match.WriteEvent]
    paths: dict[str, match.PathSummary]
    unmatched: list[parse.Call]
    files: typing.Mapping[str, typing.Any]
    baseline: typing.Mapping[str, typing.Any] | None
    final_diff: typing.Mapping[str, typing.Any] | None
    final_index: dict[str, oracle.FinalFile] | None
    traced: bool
    log: bool
    scope: str | None
    detached: list[str]
    late: bool  # a process may write after the final diff (D73)
    blind: list[tuple[str, float, float]]  # EVD7: from _intervals


def _baseline(
    ctx: _Run, path: str, source: oracle.OracleInput
) -> dict[str, typing.Any]:
    """EVD1: tracked and the baseline existence, hash and source."""
    listed = path in ctx.files
    exists = True if listed else source["at_baseline"]
    return {
        "tracked": listed
        if ctx.baseline is not None
        else source["tracked_at_baseline"],
        "baseline_exists": exists,
        "baseline_sha256": _lower(ctx.files[path].get("sha256"))
        if listed
        else source["baseline_sha256"],
        "baseline_source": "tracked_files"
        if listed
        else ("pre_oracle_pass" if exists is not None else None),
    }


def _observable(ctx: _Run, inside: bool, tracked: bool | None) -> bool:
    """EVD6: whether every write to the path would be seen."""
    caps = ctx.run["capabilities"]
    if not (ctx.traced and ctx.run.get("shim_intact") is not False and inside):
        return False
    if ctx.scope in ("workspace", "tracked+untracked"):
        return True
    return ctx.scope == "tracked" and (
        tracked is True or bool(caps.get("untracked_visible"))
    )


def _content(
    ctx: _Run, out: dict[str, typing.Any], source: oracle.OracleInput
) -> dict[str, typing.Any]:
    """EVD2-EVD4: grading existence and hash, comparison, disagreement."""
    path, bex, bsha = map(
        out.get, ("path", "baseline_exists", "baseline_sha256")
    )
    found = []  # (content_source, exists, sha256, content_cmp), by priority
    if (
        source["source_type"] == "graded"
        and source["grading_exists"] is not None
    ):
        sha = _lower(source["grading_sha256"])
        gex = source["grading_exists"]
        found.append(
            ("grading_read", gex, sha, content_compare(bex, bsha, gex, sha))
        )
    if ctx.final_index is not None and source["in_workspace"]:
        end = oracle.end_of_agent(
            ctx.final_index,
            path,
            tracked_at_baseline=out["tracked"],
            baseline_sha256=bsha,
        )
        if end[0] is not None:
            cmp = content_compare(bex, bsha, *end)
            found.append(("final_workspace", *end, cmp))
    if ctx.final_diff is not None and out["tracked"] is True:
        if path in (ctx.final_diff.get("deleted") or []):
            found.append(("final_diff", False, None, "differs"))
        elif path in (ctx.final_diff.get("modified") or []):
            found.append(("final_diff", True, None, "differs"))
        else:
            found.append(("final_diff", True, bsha, "equal"))
    if not found:
        return dict(_NO_CONTENT)
    used, gex, sha, cmp = found[0]
    disagree = source["oracle_writes"] is not True and any(  # EVD4, D70
        None not in pair and pair[0] != pair[1]
        for one, two in itertools.combinations(found, 2)
        for pair in ((one[1], two[1]), (one[3], two[3]))
    )
    return {
        "content_source": used,
        "grading_exists": gex,
        "grading_sha256": None if used == "final_diff" else sha,
        "content_cmp": cmp,
        "content_sources_disagree": disagree,
    }


def _intervals(
    execs: list[parse.Exec], run: match.RunInfo
) -> list[tuple[str, float, float]]:
    """EVD7: (command_id, start, end) of every blind exec, once per run."""
    exact = run["capabilities"].get("exact_timing")
    stop = run["t_end"] if run["t_end"] is not None else math.inf
    out = []
    for e in execs:
        if e["noise"] is not None or e["snapshot"]["status"] == "ok":
            continue
        low, high = e["t_start"], e["t_end"]
        if exact and e["ended_at"] is not None:  # D43
            low = e["released_at"] if e["released_at"] is not None else low
            high = e["ended_at"]
        out.append(
            (e["command_id"], low, high if e["complete"] else max(high, stop))
        )
    return out


def _windows(ctx: _Run, start: float | None) -> dict[str, list[str]]:
    """EVD7: blind execs and unmonitored calls meeting [start, t_end]."""
    begin = start if start is not None else -math.inf
    stop = ctx.run["t_end"] if ctx.run["t_end"] is not None else math.inf
    blind = [i for i, low, high in ctx.blind if low <= stop and high >= begin]
    unmonitored = [
        c["call_id"]
        for c in ctx.unmatched
        if (c["t_start"] if c["t_start"] is not None else -math.inf) <= stop
        and (c["t_end"] if c["t_end"] is not None else stop) >= begin
    ]
    return {"blind_execs": blind, "unmonitored_calls": unmonitored}


def _writer(ctx: _Run, path: str | None) -> tuple[dict, list, dict | None]:
    """EVD5 and LWR4: the path's events, layer 1's final writer, roots."""
    summary = ctx.paths.get(path) if path is not None else None
    ids = list(summary["events"]) if summary else []
    events = [ctx.events[i] for i in ids if i in ctx.events]
    last = ctx.events.get(summary["final_writer"]) if summary else None
    fields = {
        "events": ids,
        "edit_events": [
            e["event_id"] for e in events if e["writer_type"] == "edit_call"
        ],
        "writer_roots": [
            {
                "event_id": e["event_id"],
                "writer_type": e["writer_type"],
                "root_call_id": e["root_call_id"],
                "root_under": bool(e["writer_under"])
                if e["writer_type"] == "exec"
                else True,
            }
            for e in events
        ],
        "final_writer": summary["final_writer"] if summary else None,
        "final_writer_type": last["writer_type"] if last else None,
        "final_writer_id": last["writer_id"] if last else None,
        "final_writer_root": last["root_call_id"] if last else None,
        "final_writer_under": bool(summary and summary["final_writer_under"]),
    }
    return fields, events, last


def _state(
    ctx: _Run, row: dict[str, typing.Any], source: oracle.OracleInput
) -> InputState:
    """P3 to P5 for one input row: evidence, final writer, labels."""
    listing = row["input_type"] == "listing"
    out = {**row, **_baseline(ctx, row["path"], source)}
    out["observable"] = _observable(ctx, source["in_workspace"], out["tracked"])
    out |= _NO_CONTENT if listing else _content(ctx, out, source)  # CON14
    writer, events, last = _writer(ctx, None if listing else row["path"])
    out |= writer
    out |= _windows(ctx, last["t_start"] if last else ctx.run["t_start"])
    out["oracle_writes"] = source["oracle_writes"]
    hashes = {  # MC-07: the hash each exec event's snapshot recorded
        e["event_id"]: ctx.hashes.get(e["writer_id"], {}).get(row["path"])
        for e in events
        if e["writer_type"] == "exec"
    }
    evidence = typing.cast(
        Evidence,
        {key: out.get(key) for key in Evidence.__annotations__}
        | {
            "log": ctx.log,
            "oracle_writes_basis": source["oracle_writes_basis"],
            "exec_events": list(hashes),
            "final_writer_op": last["op"] if last else None,
            "final_writer_hash": hashes.get(out["final_writer"]),
            "content_hashes": bool(
                ctx.run["capabilities"].get("content_hashes")
            ),
            "content_late": ctx.late and out["content_source"] == "final_diff",
        },
    )
    under = [] if listing else _ordered(intersect(evidence, True))
    over = [] if listing else _ordered(intersect(evidence, False))
    conflict = not listing and not under
    origins = _origins(under, out["baseline_exists"])
    out.update(
        detached_execs=list(ctx.detached),
        late_write=ctx.late,
        unseen_writer_possible=bool(
            ctx.detached
            or not out["observable"]
            or out["blind_execs"]
            or out["unmonitored_calls"]
            or conflict
        ),
        rewrite_type=rewrite_type(
            list(hashes.values()),
            out["baseline_sha256"],
            content_hashes=evidence["content_hashes"],
            has_edit=bool(out["edit_events"]),
        )[0]
        if "rewritten_identical" in over and out["events"]
        else None,
        states_under=under,
        states_over=over,
        state_under=under[0] if len(under) == 1 else None,
        under_reason=None if len(under) == 1 else _reason(evidence, conflict),
        model_conflict=conflict,
        origin_under=next(iter(origins)) if len(origins) == 1 else None,
        origins_over=sorted(_origins(over, out["baseline_exists"])),
        listing_changed_under=None,
        listing_changed_over=None,
    )
    if listing:  # CON14
        out |= {"under_reason": None, "origin_under": None, "origins_over": []}
    out["predicates_under"], out["predicates_over"] = predicates(under, over)
    return typing.cast(InputState, out)


def _reason(ev: Evidence, conflict: bool) -> str:
    """CON13: the first UNDER_REASONS entry that applies."""
    if conflict:
        return "model_conflict"
    if ev["grading_exists"] is None and ev["content_cmp"] is None:
        return "no_content_evidence"
    if not ev["observable"] or ev["blind_execs"] or ev["unmonitored_calls"]:
        return "writers_unobserved"
    if ev["baseline_exists"] is None:
        return "no_baseline_evidence"
    if not ev["final_writer_under"] and ev["edit_events"] and ev["exec_events"]:
        return "final_writer_uncertain"
    return "underdetermined"


def _listings(ctx: _Run, rows: list[InputState]) -> None:
    """INP4: listing_changed_under and _over of every listing row."""
    files = {r["path"]: r for r in rows if r["input_type"] == "file"}
    for row in (r for r in rows if r["input_type"] == "listing"):
        changed = False
        for member in filter(None, map(files.get, row["members"])):
            bex, gex = member["baseline_exists"], member["grading_exists"]
            ops = {
                ctx.events[i]["op"] for i in member["events"] if i in ctx.events
            }
            changed |= (bex is False and gex is not False) or (
                bex is True and gex is False
            )
            changed |= bool(ops & {"create", "delete"})
        under = True if changed else (False if row["observable"] else None)
        over = True if not row["observable"] or ctx.late else under  # D73
        row["listing_changed_under"], row["listing_changed_over"] = under, over


def _counts(
    ctx: _Run,
    rows: list[InputState],
    dropped: int,
    given: oracle.OracleInputsResult | None,
    phase_status: typing.Mapping[str, str] | None,
    passing: typing.Collection[str],
) -> dict[str, typing.Any]:
    """P6: the M3.2 block (CNT2-CNT6, PHS1-PHS3)."""
    files = [r for r in rows if r["input_type"] == "file"]
    lists = [r for r in rows if r["input_type"] == "listing"]
    under, over = (
        [(r, r["predicates_under"]) for r in files],
        [(r, r["predicates_over"]) for r in files],
    )
    hashed = ctx.run["capabilities"].get("content_hashes")
    calls = {c["call_id"]: c["seq"] for c in ctx.calls}
    roots = {
        r["final_writer_root"]
        for r, p in under
        if p["agent_written"] and r["final_writer_under"]
    } - {None, ""}
    return {
        "oracle_inputs": len(files),
        "oracle_inputs_under": sum(r["in_under"] for r in files),
        "listing_inputs": len(lists),
        "reference_inputs_excluded": dropped,
        "by_state_under": {
            s: sum(r["state_under"] == s for r in files) for s in STATES
        },
        "state_unknown_under": sum(r["state_under"] is None for r in files),
        "by_state_over": {
            s: sum(s in r["states_over"] for r in files) for s in STATES
        },
        **{
            f"{name}_{model}": sum(p[name] for _, p in pairs)
            for name in PREDICATES
            for model, pairs in (("under", under), ("over", over))
        },
        "created_by_agent_under": sum(
            p["agent_written"] and r["origin_under"] == "new_file"
            for r, p in under
        ),
        "created_by_agent_over": sum(
            p["agent_written"] and "new_file" in r["origins_over"]
            for r, p in over
        ),
        "written_unseen_under": sum(
            r["state_under"] == "command_written"
            and r["final_writer_type"] != "exec"
            for r in files
        ),
        "written_unseen_over": sum(
            p["command_written"]
            and (
                r["final_writer_type"] != "exec" or r["unseen_writer_possible"]
            )
            for r, p in over
        ),
        **{
            f"rewritten_{kind}": sum(r["rewrite_type"] == kind for r in files)
            if hashed
            else None
            for kind in ("same_content", "restored")
        },
        "ever_edit_written": sum(bool(r["edit_events"]) for r in files),
        "unseen_writer_possible": sum(
            r["unseen_writer_possible"] for r in files
        ),
        "model_conflicts": sum(r["model_conflict"] for r in files),
        **{
            f"listing_changed_{m}": sum(
                r[f"listing_changed_{m}"] is True for r in lists
            )
            for m in ("under", "over")
        },
        "final_writer_roots_under": sorted(
            roots, key=lambda c: (calls.get(c, math.inf), c)
        ),
        "detached_possible": bool(ctx.detached),
        **_phases(files, given or {}, phase_status, passing),
    }


def _phases(
    files: list[InputState],
    given: typing.Mapping[str, typing.Any],
    phase_status: typing.Mapping[str, str] | None,
    passing: typing.Collection[str],
) -> dict[str, typing.Any]:
    """PHS1-PHS3: the per-phase flags and the passing-phase counts."""
    m31 = (given.get("oracle_inputs_run") or {}).get("metrics", {}).get("M3.1")
    complete = (m31 or {}).get("phases_complete") or {}
    status = phase_status or {}
    names = {p for r in given.get("oracle_inputs") or [] for p in r["phases"]}
    phases = {}
    for name in sorted(names | set(status)):  # D56
        inputs = [r for r in files if name in r["phases"]]
        flags = dict.fromkeys(PHASE_FLAGS)
        flags |= {
            "status": status.get(name),
            "inputs": len(inputs),
            "inputs_complete": complete.get(name),
        }
        for model in ("under", "over") if inputs else ():
            found = [r[f"predicates_{model}"] for r in inputs]
            flags[f"baseline_content_all_{model}"] = all(
                p["baseline_content"] for p in found
            )
            flags[f"agent_written_any_{model}"] = any(
                p["agent_written"] for p in found
            )
        if inputs and complete.get(name) is not True:
            flags["baseline_content_all_under"] = False
        phases[name] = flags
    passed = [f for f in phases.values() if f["status"] in passing]
    return {
        "phases": phases,
        "passing_phases": len(passed),
        **{
            f"passing_phases_baseline_content_{m}": sum(
                f[f"baseline_content_all_{m}"] is True for f in passed
            )
            for m in ("under", "over")
        },
    }


def classify(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    *,
    run: match.RunInfo,
    oracle_inputs: oracle.OracleInputsResult | None,
    baseline: typing.Mapping[str, typing.Any] | None = None,
    final_diff: typing.Mapping[str, typing.Any] | None = None,
    final_files: list[oracle.FinalFile] | None = None,
    phase_status: typing.Mapping[str, str] | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> StateResult:
    """Classifies the state at grading of every oracle input of one run.

    Grading reads come only through oracle_inputs (graded rows); the
    end-of-agent state only through final_files (§0's end_of_agent).
    Every capability-dependent rule branches on run["capabilities"] or
    linked["coverage"] (spec §6.9).

    Args:
        calls: Layer 1's Call records.
        execs: Layer 1's Exec records (universal fields included).
        linked: Layer 1's links, dispositions, paths and coverage.
        run: The run's RunInfo.
        oracle_inputs: §0's result, or None when it was not built.
        baseline: monitor/tracked_files.json.
        final_diff: monitor/tracked_files_diff.json.
        final_files: monitor/final_files.json (MC-03).
        phase_status: phase -> status from run/oracle_result.json.
        config: Overrides of DEFAULTS.

    Returns:
        The rows of input_states.jsonl and state_counts.json.

    Raises:
        ValueError: A config key is not in DEFAULTS.
    """
    cfg = oracle.merge_config(DEFAULTS, config)
    coverage = linked["coverage"]
    caps = run["capabilities"]
    source_rows = (oracle_inputs or {}).get("oracle_inputs") or []
    final_index = oracle.final_index(
        final_files if caps.get("final_content") else None,
        run["workspace_root"],
    )
    graded = any(r["source_type"] == "graded" for r in source_rows)
    labels, warnings = _preconditions(
        coverage,
        run,
        oracle_inputs,
        graded or final_index is not None or final_diff is not None,
    )
    detached, detached_basis = detached_execs(
        execs, linked["links"], capabilities=caps
    )
    unmatched = {
        d["id"]
        for d in linked["dispositions"]
        if d["item_type"] == "call" and d["disposition"] == "unmatched"
    }
    shells = [
        c
        for c in sorted(calls, key=_seq)
        if c["tool_type"] == "shell" and c["call_id"] in unmatched
    ]
    ordered = sorted(execs, key=_seq)
    ctx = _Run(
        run=run,
        calls=calls,
        execs=ordered,
        hashes={  # normalized once per run, not once per input (S1)
            e["command_id"]: {
                match.relative_path(k, run["workspace_root"])[0]: v
                for k, v in ((e["snapshot"] or {}).get("hashes") or {}).items()
            }
            for e in execs
        },
        events={
            link["event_id"]: link
            for link in linked["links"]
            if link["link_type"] == "write_event"
        },
        paths={p["path"]: p for p in linked["paths"]},
        unmatched=shells,
        files=(baseline or {}).get("files") or {},
        baseline=baseline,
        final_diff=final_diff,
        final_index=final_index,
        traced=labels["trace"] == "holds",
        log=bool(coverage["inputs"]["log"]),
        scope=coverage["rates"].get("snapshot_scope"),
        detached=detached,
        late=bool(
            detached
            or shells
            or labels["trace"] != "holds"
            or run.get("shim_intact") is False
        ),
        blind=_intervals(ordered, run),
    )
    rows: list[InputState] = []
    dropped = 0
    if labels["oracle_inputs"] != "fails":
        sources = {r["input_id"]: r for r in source_rows}
        selected, dropped = input_rows(source_rows)
        rows = [_state(ctx, r, sources[r["input_id"]]) for r in selected]
        _listings(ctx, rows)
    m32 = _counts(
        ctx, rows, dropped, oracle_inputs, phase_status, cfg["passing_statuses"]
    )
    if any(labels[k] == "fails" for k, voids in PRECONDITIONS if voids):
        m32 = dict.fromkeys(m32)
    counts = {
        "schema_version": "1",
        "run_key": run.get("run_key"),
        "family": run.get("family"),
        "capabilities": dict(sorted(coverage["capabilities"].items())),
        "bases": _bases(run, detached_basis),
        "preconditions": labels,
        "metrics": {"M3.2": m32},
        "warnings": warnings,
    }
    return {"input_states": rows, "state_counts": counts}
