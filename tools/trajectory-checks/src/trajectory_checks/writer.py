"""Layer 5, steps W1 to W8: who wrote each oracle input, and how.

Counts M3.3 (the last writer of each oracle input), M2.4 (the agent calls
that rewrote it) and M4.3 (the hunk types of the final diff) as
`trajectory-writer-spec.md` specifies. The last writer is layer 1's
final writer as layer 4 copies it (D23): this layer restricts it to the
oracle inputs and qualifies it with candidate sets, later-writer risk,
root candidates and the version read; it never re-orders or re-chooses
it. Every function is pure: `cli.analyse` reads the files and passes
their content (D26, D37).
"""

import collections
import math
import posixpath
import re
import types
import typing

from trajectory_checks import facts, input_state, match, parse, structure
from trajectory_checks import oracle_inputs as oracle  # the name is a param


class Risk(typing.TypedDict):
    """One later-writer risk item (LW9)."""

    id: str  # command_id or call_id
    item_type: str  # exec, call
    reason: str  # one of RISK_REASONS


class LastWriter(typing.TypedDict):
    """M3.3 for one oracle input (spec §5.1)."""

    input_id: str
    path: str
    pattern: str | None
    input_type: str  # file, listing
    in_workspace: bool
    in_under: bool
    state_under: str | None
    visible: bool
    exists_at_read: bool | None
    exists_source: str | None  # layer 4's content_source, or last_event
    last_writer: str | None
    last_writer_type: str | None  # exec, edit_call, none
    last_writer_role: str | None  # edit_call, root_exec, nested_exec
    last_writer_op: str | None  # create, modify, delete, mode
    last_writer_candidates: list[str]
    window_lo: float | None
    window_hi: float | None
    writer_command_id: str | None
    writer_call_id: str | None
    writer_call_seq: int | None
    writer_root_certain: bool | None
    writer_duration_s: float | None
    writer_depth: int | None
    writer_outcome: str | None
    root_duration_s: float | None
    root_span_under_s: float | None
    root_span_over_s: float | None
    later_writer_risk: list[Risk]
    root_candidates: list[str]
    root_candidates_complete: bool
    mtime_candidates: list[str] | None
    state_conflict: bool | None
    read_version: str  # agent_final, differs, unverified
    differs_causes: list[str] | None
    hash_confirms_writer: bool | None
    oracle_writes: bool | None
    oracle_writes_basis: str | None  # exact, fallback
    writer_named_written: list[str]
    writer_named_changed: list[str]
    member_input_ids: list[str]
    unattributed_candidates: list[dict[str, str]]
    last_writer_under: bool
    not_under: list[str]


class Member(typing.TypedDict):
    """One agent call that wrote an input's content (RW2, RW3)."""

    call_id: str
    seq: int
    member_type: str  # shell, edit
    events: list[str]
    first_lo: float | None  # ⊥ when unbounded (D46)
    last_hi: float | None
    under: bool
    outcome: str | None
    command_shape: str | None
    numeric_args: list[str] | None
    arg_count: int | None
    rerun_key: str | None


class Pair(typing.TypedDict):
    """Two adjacent members (RW4)."""

    prev_call_id: str
    call_id: str
    after: str  # ok, failed, killed, unknown, edit
    same_key: bool | None
    writer_switched: bool | None
    args_changed: bool | None
    shape_changed: bool | None
    under: bool


class InputRewrites(typing.TypedDict):
    """M2.4 for one oracle input (spec §5.2)."""

    input_id: str
    path: str
    visible: bool
    members: list[Member] | None
    pairs: list[Pair]
    delete_roots: list[str]
    rewrites_under: int | None
    rewrites_over: int | None
    unowned_execs: list[str]  # content writers with no owning call (D48)


class Block(typing.TypedDict):
    """One change block of a diff section (HK3)."""

    old_start: int
    new_start: int
    removed: list[str]
    added: list[str]


class DiffFile(typing.TypedDict):
    """One `diff --git` section (HK1, HK2)."""

    path: str
    renamed_from: str | None
    file_status: str  # added, modified, deleted, renamed
    binary: bool
    blocks: list[Block]


class Token(typing.NamedTuple):
    """One lexeme of a hunk side (HK6)."""

    text: str
    token_type: str  # comment, string, number, word, operator
    line: int  # index of its line within the side


class Hunk(typing.TypedDict):
    """M4.3 for one change block (spec §5.3)."""

    path: str
    file_status: str
    renamed_from: str | None
    file_class: str | None
    language: str | None
    hunk_index: int
    old_start: int
    new_start: int
    lines_removed: int
    lines_added: int
    hunk_type: str
    touches_check: bool | None
    lexed: bool
    in_named_under: bool | None
    in_named_over: bool | None
    in_read: bool | None


class WriterRun(typing.TypedDict):
    """writer.json (spec §5.4)."""

    run_key: str | None
    family: str | None
    capabilities: dict[str, bool]
    bases: dict[str, str]  # every BASES key -> exact, fallback, mixed
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    metrics: dict[str, dict[str, typing.Any]]  # M3.3, M2.4, M4.3
    diff_files_unclassified: list[dict[str, str]]  # {path, reason}
    warnings: list[str]


class WriterResult(typing.TypedDict):
    """Everything W1 to W7 produce for one run."""

    last_writers: list[LastWriter]
    input_rewrites: list[InputRewrites]
    hunks: list[Hunk]
    writer: WriterRun


class Language(typing.NamedTuple):
    """One LANGUAGES row (HK5): language syntax only."""

    name: str
    extensions: tuple[str, ...]
    basenames: tuple[str, ...]
    line_markers: tuple[str, ...]
    block_pairs: tuple[tuple[str, str], ...]
    quotes: tuple[str, ...]  # longest first: triple quotes span lines
    assertions: frozenset[str]
    conditions: frozenset[str]
    comparisons: tuple[str, ...]
    relaxations: frozenset[tuple[str, str]]


# Layer 1's op values that change content (its rule 7: mode is neutral).
CONTENT_OPS = frozenset({"create", "modify"})
# Layer 1's _EXISTING: final states whose final writer is never a delete.
EXISTING_STATES = frozenset({"changed", "mode_only", "reverted"})
# HK8, in precedence order (a partition).
HUNK_TYPES = tuple(
    "whitespace_only comment_only comment_toggle numeric_only "
    "comparison_relaxed check_removed pure_deletion pure_addition "
    "general".split()
)
RISK_REASONS = ("blind_snapshot", "incomplete", "orphaned", "unrecorded")
NOT_UNDER_REASONS = tuple(
    "no_writer invisible input_not_under several_candidates root_uncertain "
    "later_writer_risk state_conflict model_conflict read_version_differs "
    "unverified_oracle_written".split()
)
DIFFERS_CAUSES = ("oracle", "late_process")
# LW16: layer 4's labels that say the agent wrote.
WRITTEN_STATES = frozenset(
    {"rewritten_identical", "edit_written", "command_written", "deleted"}
)
# §7, PRE6: (basis key, the capabilities its exact form needs);
# read_version also needs final_files, hunks final_diff_text, and
# oracle_writes is copied from oracle_inputs.json.
BASES = (
    ("window", ("exact_timing",)),
    ("root_certainty", ("parent_links",)),
    ("ancestry", ("parent_links",)),
    ("unrecorded_risk", ("start_events",)),
    ("read_version", ("oracle_reads", "final_content")),
    ("oracle_writes", ("oracle_reads", "final_content")),
    ("mtime", ("oracle_reads",)),
    ("hash_confirms_writer", ("content_hashes",)),
    ("hunks", ("final_content",)),
    ("in_read", ("read_tracing",)),
)
# §7, in label order: (label, the metrics a failure voids: a block, or
# a block's key).
PRECONDITIONS = (
    ("trace", "M3.3 M2.4"),
    (
        "oracle_inputs",
        "M3.3 M2.4 M4.3.hunks_by_type_named_under "
        "M4.3.hunks_by_type_named_over",
    ),
    ("input_states", "M3.3 M2.4"),
    ("C1_under", "M2.4"),
    (
        "repeats",
        "M2.4.writer_switches_under M2.4.writer_switches_over "
        "M2.4.args_changed_under M2.4.args_changed_over",
    ),
    ("final_diff_content", "M4.3"),
    ("file_class", "M4.3.hunks_by_class_and_type"),
    ("read_events", "M4.3.hunks_by_type_read"),
)


def _what(guarded: str) -> str:
    """The <what> of a label's warning (§5.4, D44 (6))."""
    names = guarded.split()
    blocks = [n for n in names if len(n) == 4]
    if blocks:
        return ", ".join(blocks)
    return f"{names[0][:4]} " + ", ".join(n[5:] for n in names)


# §5.5, for cli.facts_row (D40).
DICT_FIELDS = types.MappingProxyType(
    {f"last_writer_by_role_{m}": facts.ROLES for m in ("under", "over")}
    | dict.fromkeys(
        "hunks_by_type files_by_type hunks_by_type_named_under "
        "hunks_by_type_named_over hunks_by_type_read".split(),
        HUNK_TYPES,
    )
    | {
        "hunks_by_class_and_type": tuple(
            f"{c}.{t}" for c in structure.FILE_CLASSES for t in HUNK_TYPES
        )
    }
)
# §5.6: the writer_inputs.csv columns before the ratios (D40).
INPUT_COLUMNS = tuple(
    "run_key input_id path pattern last_writer_type last_writer_role "
    "last_writer_under M3.3.writer_duration_s M3.3.root_span_over_s "
    "M2.4.rewrites_over".split()
)
# HK6: a number lexeme, matched only at a token start.
NUMBER = re.compile(
    r"0[xX][0-9a-fA-F_]+[uUlL]*|(\d[\d_]*(\.[\d_]*)?|\.\d[\d_]*)"
    r"([eE][-+]?\d+)?[a-zA-Z]{0,3}"
)
# HK5: the relaxing operator pairs; a row keeps those whose two operators
# it has (so verilog, without `<=`, keeps two).
_RELAXATIONS = (
    *(("<", "<="), (">", ">="), ("==", "<="), ("==", ">=")),
    *(("-lt", "-le"), ("-gt", "-ge"), ("-eq", "-le"), ("-eq", "-ge")),
)
_CMP = "== != <= >= < >"
# HK5, one row per language, fields separated by `;`: name, extensions,
# line markers, block pairs, assertion keywords, condition keywords,
# comparison operators, quotes (longest first), basenames.
_TABLE = (
    "c_family; .c .h .cc .cpp .cxx .hpp .hh .cu .java .cs; //; /* */;"
    f" assert static_assert; if while; {_CMP}; \" '",
    "script_family; .js .mjs .cjs .ts .tsx .go .scala .kt .swift; //;"
    f" /* */; ; if while; === !== {_CMP}; \" ' `",
    "rust; .rs; //; /* */; assert! assert_eq! assert_ne! debug_assert!"
    f" debug_assert_eq! debug_assert_ne!; if while; {_CMP}; \" '",
    f"python; .py .pyi; #; ; assert; if elif while; {_CMP}; \"\"\" ''' \" '",
    "shell; .sh .bash .zsh; #; ; ; if elif while until [ [[ test;"
    " -eq -ne -lt -le -gt -ge == !=; \" ' `",
    "make; .mk; #; ; ; ifeq ifneq; ; ; Makefile makefile GNUmakefile",
    "dafny; .dfy; //; /* */; assert assume expect requires ensures"
    f" invariant; if while; {_CMP} ==> <==>; \" '",
    "verilog; .sv .svh .vh; //; /* */; assert assume; if while;"
    " == != === !== >= < >; \" '",
    f"lean; .lean; --; /- -/; ; if; {_CMP}; \" '",
    f"julia; .jl; #; #= =#; @assert; if elseif while; {_CMP}; \" '",
    "config; .toml .yaml .yml .ini .cfg .conf; #; ; ; ; ; \" '",
    "json; .json; ; ; ; ; ; \" '",
    "tex; .tex .sty; %; ; ; ; ; ",
)


def _language(row: str) -> Language:
    """One LANGUAGES row from its _TABLE string (§3.3)."""
    name, ext, line, blocks, asserts, conditions, cmp, quotes, bases = (
        tuple(field.split()) for field in (row + ";").split(";")[:9]
    )
    pairs = tuple(zip(blocks[::2], blocks[1::2], strict=True))
    sets = frozenset(asserts), frozenset(conditions)
    relax = frozenset(p for p in _RELAXATIONS if set(p) <= set(cmp))
    return Language(name[0], ext, bases, line, pairs, quotes, *sets, cmp, relax)


LANGUAGES = tuple(map(_language, _TABLE))
_ROWS = types.MappingProxyType({row.name: row for row in LANGUAGES})
# HK6: the operators of every row beyond its comparisons.
_COMMON_OPERATORS = frozenset(
    "<==> ==> === !== == != <= >= << >> && || -> => :: ++ -- += -= *= /= "
    "%= ** // [[ ]]".split()
)
_SHELL_TESTS = frozenset({"-eq", "-ne", "-lt", "-le", "-gt", "-ge"})
# HK8 (D51): the rows whose indentation is syntax (yaml in config), and
# those where whitespace between two tokens is (`a=1` and `a = 1`).
_INDENTED = frozenset({"python", "make", "config", "lean"})
_SPACED = frozenset({"shell", "make"})
_GENERIC = re.compile(
    rf"(?P<number>{NUMBER.pattern})|(?P<word>\w+)|(?P<operator>\S)"
)
_AT = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

Window = tuple[float, float]
# LW13: the writer facts, ⊥ until known.
_FACT_FIELDS = tuple(
    "writer_command_id writer_call_id writer_call_seq writer_duration_s "
    "writer_depth writer_outcome root_duration_s root_span_under_s "
    "root_span_over_s last_writer_role".split()
)
_SPANS = ("duration_s", "span_under_s", "span_over_s")
# RW3: the fields copied from layer 2's call_facts.jsonl.
_CALL_FIELDS = ("outcome", "command_shape", "numeric_args", "arg_count")
# §5.1: the fields copied from layer 4's row.
_COPIED = tuple("input_id path pattern input_type in_under state_under".split())


def _number(item_id: str) -> int:
    """The number of an `i<n>` or `w<n>` id."""
    return int(item_id[1:])


def _by_seq(seq: typing.Mapping[str, int], ids: typing.Iterable[str]) -> list:
    """Call ids sorted by seq; unknown ids last, by id."""
    return sorted(set(ids), key=lambda c: (seq.get(c, math.inf), c))


def _count(
    rows: typing.Iterable[typing.Mapping], key: str, value: typing.Any = True
) -> int:
    """The rows whose key holds value (§6.7: 0 over no row)."""
    return sum(r[key] == value for r in rows)


def _nonempty(rows: typing.Iterable[typing.Mapping], key: str) -> int:
    """The rows whose list under key is not empty."""
    return sum(bool(r[key]) for r in rows)


def _extreme(
    pick: typing.Callable, values: typing.Iterable[typing.Any]
) -> typing.Any:
    """The min or max of the non-⊥ values; ⊥ over none (§6.7)."""
    return pick((v for v in values if v is not None), default=None)


def _bounded(t: float | None) -> float | None:
    """A window end as written: ⊥ when unbounded, as JSON has no ±inf."""
    return t if t is None or math.isfinite(t) else None


# W2: observation windows (LW4).


def observation_window(
    event: match.WriteEvent,
    *,
    execs: typing.Mapping[str, parse.Exec],
    calls: typing.Mapping[str, parse.Call],
    exact: bool,
    slop: float,
) -> tuple[float, float]:
    """LW4: the observation window of one write event.

    Args:
        event: A write event (only `writer_type` and `writer_id` read).
        execs: Execs by command_id.
        calls: Calls by call_id.
        exact: The capability exact_timing; a record without `ended_at`
            takes the fallback for itself only (D43).
        slop: Layer 1's snapshot_slop_s.

    Returns:
        (window_lo, window_hi).
    """
    if event["writer_type"] != "exec":
        return _call_window(calls[event["writer_id"]], None)
    return _exec_window(execs[event["writer_id"]], exact, slop)


def _exec_window(e: parse.Exec, exact: bool, slop: float) -> Window:
    """LW4 for an exec; one without `ended_at` takes the fallback (D43)."""
    if exact and e.get("ended_at") is not None:
        lo, after = e.get("released_at"), e.get("snapshot_after_s")
        lo = e["t_start"] if lo is None else lo
        return lo, e["ended_at"] + (slop if after is None else after)
    return e["t_start"], e["t_start"] + e["duration_s"] + slop


def _call_window(call: parse.Call, run_end: float | None) -> Window:
    """LW4 for an edit call, or a shell call used as a risk item.

    An untimed start is -inf and an untimed end the run's end, as layer
    4's EVD7 reads them: the call cannot be excluded.
    """
    precision = call["t_precision_s"] or 0.0
    start = -math.inf if call["t_start"] is None else call["t_start"]
    ends = (call["t_end"], run_end, call["t_start"], math.inf)
    end = next(t for t in ends if t is not None)
    return start - precision, end + precision


# W3: the last writer (M3.3).


def _pool(
    events: typing.Iterable[match.WriteEvent], exists: bool | None
) -> list[match.WriteEvent]:
    """LW3: layer 1's own final-writer filter, in layer 1's order."""
    ordered = sorted(
        events, key=lambda e: (e["order_t"], _number(e["event_id"]))
    )
    live = [e for e in ordered if not (exists is True and e["op"] == "delete")]
    return [e for e in live if e["possibly_edit_call"] is None] or live


def _candidates(
    pool: list[match.WriteEvent],
    writer: match.WriteEvent,
    windows: typing.Mapping[str, Window],
) -> list[str]:
    """LW6: the writer and every pool event it cannot be ordered after."""
    lo = windows[writer["event_id"]][0]
    found = {
        e["event_id"]
        for e in pool
        if e["op"] != "mode" and windows[e["event_id"]][1] >= lo
    }
    return sorted(found | {writer["event_id"]}, key=_number)


def last_writer(
    events: list[match.WriteEvent],
    *,
    windows: typing.Mapping[str, tuple[float, float]],
    exists: bool | None = None,
    cutoff: float | None = None,
) -> tuple[str | None, list[str]]:
    """LW3, LW5, LW6: the last writer before cutoff and its candidates.

    Orders by layer 1's key (order_t, then event number), so with no
    cutoff and exists from layer 1's final state the writer is layer 1's
    final_writer. Exported for layer 6 (spec §5.7).

    Args:
        events: The write events of one path (or of a listing's members).
        windows: LW4's window of each event, by event_id.
        exists: True drops delete events (EXISTING_STATES).
        cutoff: Events whose window starts at or after it are left out.

    Returns:
        (event_id of the last writer or None, last_writer_candidates).
    """
    if cutoff is not None:
        events = [e for e in events if windows[e["event_id"]][0] < cutoff]
    pool = _pool(events, exists)
    if not pool:
        return None, []
    return pool[-1]["event_id"], _candidates(pool, pool[-1], windows)


def later_writer_risk(
    state: input_state.InputState,
    *,
    writer_id: str | None,
    window_lo: float,
    windows: typing.Mapping[str, tuple[float, float]],
    execs: typing.Mapping[str, parse.Exec],
    orphans_exact: bool,
) -> list[Risk]:
    """LW9; windows holds LW4's window of every exec and shell call.

    Args:
        state: Layer 4's row (its evidence lists are read, D23).
        writer_id: The writer exec's command_id; None for an edit writer
            or none (every orphaned exec then counts, D44).
        window_lo: The last writer's window start (the run's start
            without one).
        windows: LW4's window by command_id and call_id.
        execs: Execs by command_id.
        orphans_exact: Layer 4's detached basis is exact (D33).

    Returns:
        One item per id, its first reason in RISK_REASONS order (D44).
    """
    found: dict[str, tuple[str, str]] = {}
    bound = windows[writer_id][1] if writer_id in windows else math.inf
    late = {i for i, w in windows.items() if w[1] >= window_lo}
    outlived = input_state.outlived(execs.values(), execs)
    for i in state["blind_execs"]:
        if i in late:
            found.setdefault(i, ("exec", "blind_snapshot"))
    for i in state["detached_execs"]:
        e = execs.get(i)
        if i == writer_id or e is None:
            continue
        if not e["complete"] and i in late:
            found.setdefault(i, ("exec", "incomplete"))
        elif (
            e["complete"]
            and orphans_exact
            and (input_state.own_orphans(e, execs) or i in outlived)
        ):
            if i in windows and windows[i][0] < bound:
                found.setdefault(i, ("exec", "orphaned"))
    for i in state["unmonitored_calls"]:
        if i in late:
            found.setdefault(i, ("call", "unrecorded"))
    return sorted(
        (Risk(id=i, item_type=t, reason=r) for i, (t, r) in found.items()),
        key=lambda r: (RISK_REASONS.index(r["reason"]), r["id"]),
    )


def read_version(
    end: tuple[bool | None, str | None], read: tuple[bool | None, str | None]
) -> str:
    """LW11: agent_final, differs or unverified from (exists, sha256)."""
    (end_exists, end_sha), (read_exists, read_sha) = end, read
    if end_exists is None or read_exists is None:
        return "unverified"
    if end_exists != read_exists:
        return "differs"
    if not end_exists:
        return "agent_final"
    if end_sha is None or read_sha is None:
        return "unverified"
    same = oracle._lower(end_sha) == oracle._lower(read_sha)
    return "agent_final" if same else "differs"


def root_candidates(
    candidates: list[str],
    *,
    events: typing.Mapping[str, match.WriteEvent],
    state: input_state.InputState,
    mtime: list[str] | None,
    roots: typing.Mapping[str, str | None],
    certain: typing.Mapping[str, bool],
    seq: typing.Mapping[str, int],
) -> tuple[list[str], bool]:
    """LW16: (root_candidates, root_candidates_complete).

    roots maps exec and edit-call ids to their root call; certain maps
    event ids to LW8's answer; seq maps call ids to their seq.
    """
    unseen = bool(state["unseen_writer_possible"])
    if not WRITTEN_STATES.intersection(state["states_over"] or ()):
        return [], not unseen
    found = set()
    for c in candidates:
        found.update(filter(None, [events[c]["root_call_id"]]))
        found.update(events[c]["root_candidates"] or ())
    if unseen:  # the roots of mtime_candidates too (D44)
        items = state["detached_execs"] + state["blind_execs"] + (mtime or [])
        found.update(filter(None, map(roots.get, items)))
        found.update(roots.get(i) or i for i in state["unmonitored_calls"])
    sure = all(certain.get(c) for c in candidates)
    return _by_seq(seq, found), not unseen and sure


def mtime_candidates(
    mtime_ns: int | None, windows: typing.Mapping[str, tuple[float, float]]
) -> list[str] | None:
    """LW17: ids whose window contains mtime_ns; None without it.

    Keeps the order of windows, which derive builds in seq order, execs
    first, then edit calls (D44).
    """
    if mtime_ns is None:
        return None
    t = mtime_ns / 1e9
    return [i for i, (lo, hi) in windows.items() if lo <= t <= hi]


def named_paths(
    commands: list[tuple[str, str | None]],
    *,
    root: str,
    paths: typing.Mapping[str, match.PathSummary],
    events: typing.Mapping[str, match.WriteEvent],
    excluded: typing.Collection[str],
) -> tuple[list[str], list[str]]:
    """LW14: (writer_named_written, writer_named_changed).

    commands holds (command, cwd) of the writer exec and its ancestors.
    """
    found = set()
    for command, cwd in commands:
        for simple in facts.simple_commands(command or ""):
            for word in simple["words"] + [t for _, t in simple["redirects"]]:
                if word:
                    joined = posixpath.join(cwd or root, word)
                    found.add(match.relative_path(joined, root)[0])
    written = sorted(p for p in found if p in paths and p not in excluded)

    def changed(row: match.PathSummary) -> bool:
        ops = {events[e]["op"] for e in row["events"] if e in events}
        if row["final_state"] in ("changed", "deleted"):
            return True
        return row["tracked"] is False and "create" in ops

    return written, [p for p in written if changed(paths[p])]


class _Run:
    """The run-level facts W3 to W6 read for every input (W2).

    The constructor reads layers 1 and 2; derive adds the lookups of
    layers 3 and 4 (keys, sources, excluded, orphans_exact, end_index).
    """

    def __init__(
        self,
        calls: list[parse.Call],
        execs: list[parse.Exec],
        linked: match.LinkResult,
        facts_result: facts.FactsResult,
        *,
        run: match.RunInfo,
        caps: dict[str, bool],
        slop: float,
    ) -> None:
        """Builds the lookups, LW4's windows and LW8's root certainty."""
        root = self.root = run["workspace_root"]
        self.run, self.caps = run, caps
        self.final_diff = bool(linked["coverage"]["inputs"]["final_diff"])
        self.execs = {e["command_id"]: e for e in execs}
        self.calls = {c["call_id"]: c for c in calls}
        self.paths = {p["path"]: p for p in linked["paths"]}
        links = collections.defaultdict(list)
        for link in linked["links"]:
            links[link["link_type"]].append(link)
        self.events = {e["event_id"]: e for e in links["write_event"]}
        exact = caps["exact_timing"]
        self.windows = {  # LW4 of every write event
            i: observation_window(
                e, execs=self.execs, calls=self.calls, exact=exact, slop=slop
            )
            for i, e in self.events.items()
        }
        live = (e for e in execs if e["noise"] is None)
        by_seq = {
            e["command_id"]: _exec_window(e, exact, slop)
            for e in sorted(live, key=lambda e: e["seq"])
        }
        edits = {
            e["writer_id"]
            for e in self.events.values()
            if e["writer_type"] == "edit_call"
        }
        ordered = sorted(calls, key=lambda c: c["seq"])
        self.items = by_seq | {  # LW9: every exec and shell call
            c["call_id"]: _call_window(c, run["t_end"])
            for c in ordered
            if c["tool_type"] == "shell"
        }
        self.mtime_windows = by_seq | {  # LW17: execs, then edit calls
            c["call_id"]: _call_window(c, None)
            for c in ordered
            if c["tool_type"] in ("edit", "write")
            or (c["tool_type"] != "shell" and c["call_id"] in edits)
        }
        self.dispositions = {
            (d["item_type"], d["id"]): d for d in linked["dispositions"]
        }
        self.j1 = {k["command_id"]: k["call_id"] for k in links["call_exec"]}
        self.certain = {i: self._certain(e) for i, e in self.events.items()}
        self.parents = {  # LW14's ancestry: J2's, the fallback
            k["command_id"]: k["parent"] for k in links["exec_parent"]
        }
        if caps["parent_links"]:  # recorded, for each record with MC-01's
            self.parents |= {  # fields (D43, D47)
                e["command_id"]: e.get("parent_command_id")
                for e in execs
                if e.get("root_command_id") is not None
            }
        self.hashes = {  # MC-07, keys normalized once per run
            e["command_id"]: {
                match.relative_path(k, root)[0]: v
                for k, v in ((e["snapshot"] or {}).get("hashes") or {}).items()
            }
            for e in execs
        }
        self.exec_facts = {
            f["command_id"]: f for f in facts_result["exec_facts"]
        }
        self.call_facts = {f["call_id"]: f for f in facts_result["call_facts"]}
        self.roots = {k: f["root_call_id"] for k, f in self.exec_facts.items()}
        self.roots |= {c["call_id"]: c["call_id"] for c in calls}
        self.seq = {c["call_id"]: c["seq"] for c in calls}
        self.keys: dict[str, str | None] = {}  # layer 3's program_key
        self.sources: dict[str, oracle.OracleInput] = {}
        self.excluded: set[str] = set()  # the oracle-input paths (LW14)
        self.orphans_exact = False  # layer 4's detached basis is exact
        self.end_index: dict[str, oracle.FinalFile] | None = None  # MC-03

    def _certain(self, event: match.WriteEvent) -> bool:
        """LW8: whether the event's owning call is not a guess."""
        if event["writer_type"] != "exec":
            return True
        found = self.dispositions.get(("exec", event["writer_id"])) or {}
        record = self.execs.get(event["writer_id"]) or {}
        root = record.get("root_command_id")
        if self.caps["parent_links"] and root is not None:  # recorded
            found = self.dispositions.get(("exec", root)) or {}
            call = self.dispositions.get(("call", self.j1.get(root, "")))
            under = bool((call or {}).get("match_under"))
            return found.get("disposition") == "matched_root" and under
        if found.get("disposition") == "nested":
            return bool(found.get("root_under"))
        return found.get("disposition") == "matched_root"


def _input_events(
    ctx: _Run, row: input_state.InputState | LastWriter
) -> list[match.WriteEvent]:
    """The write events of a file input, or of a listing's members (LW15)."""
    paths = [row["path"]]
    if row["input_type"] == "listing":
        pattern = posixpath.join(row["path"], row["pattern"] or "")
        paths = [p for p in ctx.paths if oracle.glob_match(pattern, p)]
    found = [ctx.paths[p]["events"] for p in paths if p in ctx.paths]
    return [ctx.events[e] for ids in found for e in ids if e in ctx.events]


def _writer(
    ctx: _Run, state: input_state.InputState
) -> tuple[match.WriteEvent | None, list[str], str | None, list]:
    """LW2, LW5, LW6, LW15: (writer, candidates, writer type, unattributed)."""
    visible = bool(state["observable"])
    if state["input_type"] == "listing":
        events = [
            e
            for e in _input_events(ctx, state)
            if e["op"] in ("create", "delete")
        ]
        found, candidates = last_writer(events, windows=ctx.windows)
        writer = ctx.events.get(found or "")
        if writer is None:
            return None, [], "none" if visible else None, []
        return writer, candidates, writer["writer_type"], []
    summary = ctx.paths.get(state["path"])
    if summary is None:
        return None, [], "none" if visible else None, []
    if not summary["events"]:
        return None, [], None, list(summary["unattributed_candidates"])
    if state["final_writer"] is None:
        return None, [], None, []
    writer = ctx.events[state["final_writer"]]
    exists = True if summary["final_state"] in EXISTING_STATES else None
    pool = _pool((ctx.events[e] for e in summary["events"]), exists)
    candidates = _candidates(pool, writer, ctx.windows)
    return writer, candidates, writer["writer_type"], []


def _facts(
    ctx: _Run, state: input_state.InputState, writer: match.WriteEvent | None
) -> dict[str, typing.Any]:
    """LW13, LW14: the writer's facts, role and named paths."""
    out: dict[str, typing.Any] = dict.fromkeys(_FACT_FIELDS)
    out |= {"writer_named_written": [], "writer_named_changed": []}
    if writer is None:
        return out
    if writer["writer_type"] != "exec":
        call = writer["writer_id"]
        out |= {"writer_call_id": call, "last_writer_role": "edit_call"}
        return out | {"writer_call_seq": ctx.seq.get(call)}
    # Layer 4's final_writer_id and final_writer_root are these (D23).
    command, call = writer["writer_id"], writer["root_call_id"]
    efacts = ctx.exec_facts.get(command or "") or {}
    cfacts = ctx.call_facts.get(call or "") or {}
    out |= {f"writer_{k}": efacts.get(k) for k in ("depth", "outcome")}
    out |= {f"root_{k}": cfacts.get(k) for k in _SPANS}
    matched = efacts.get("disposition") == "matched_root"
    out.update(
        writer_command_id=command,
        writer_call_id=call,
        writer_call_seq=ctx.seq.get(call or ""),
        writer_duration_s=(ctx.execs.get(command) or {}).get("duration_s"),
        last_writer_role="root_exec" if matched else "nested_exec",
    )
    chain: list[str] = []  # the exec and its ancestors (LW14)
    while command in ctx.execs and command not in chain:
        chain.append(command)
        command = ctx.parents.get(command)
    out["writer_named_written"], out["writer_named_changed"] = named_paths(
        [(ctx.execs[x]["command"], ctx.execs[x]["cwd"]) for x in chain],
        root=ctx.root,
        paths=ctx.paths,
        events=ctx.events,
        excluded=ctx.excluded,
    )
    return out


def _conflict(
    ctx: _Run,
    state: input_state.InputState,
    writer: match.WriteEvent | None,
    exists: tuple[bool | None, str | None],
) -> bool | None:
    """LW10: the ops observed contradict the existence at read."""
    found, source = exists
    if state["input_type"] == "listing" or source in (None, "last_event"):
        return None
    if writer is None:
        return False
    hi = ctx.windows[writer["event_id"]][1]
    ops = {writer["op"]} | {
        ctx.events[e]["op"]
        for e in ctx.paths[state["path"]]["events"]
        if ctx.windows[e][0] > hi
    }
    return "delete" in ops if found else ops != {"delete"}  # mode too (D50)


def _version(
    ctx: _Run,
    state: input_state.InputState,
    source: typing.Mapping[str, typing.Any],
    writer: match.WriteEvent | None,
) -> dict[str, typing.Any]:
    """LW11: read_version, differs_causes, hash_confirms_writer."""
    out: dict[str, typing.Any] = {"read_version": "unverified"}
    out |= dict.fromkeys(("differs_causes", "hash_confirms_writer"))
    if state["input_type"] == "listing":
        return out
    path, end = state["path"], (None, None)
    final_state = (ctx.paths.get(path) or {}).get("final_state")  # ⊥: no row
    if ctx.end_index is not None:
        end = oracle.end_of_agent(
            ctx.end_index,
            path,
            tracked_at_baseline=state["tracked"],
            baseline_sha256=state["baseline_sha256"],
        )
    elif state["tracked"] is True and ctx.final_diff:
        if final_state in (None, "mode_only", "reverted"):
            end = (True, state["baseline_sha256"])
    read = (None, None)
    if source.get("source_type") == "graded":
        read = (source.get("grading_exists"), source.get("grading_sha256"))
    out["read_version"] = read_version(end, read)
    if out["read_version"] == "differs":  # D32: missing late_write is true
        late = ctx.end_index is None and state.get("late_write", True)
        out["differs_causes"] = list(DIFFERS_CAUSES[: 1 + bool(late)])
    reference = read[1] if read[1] is not None else end[1]
    if ctx.caps["content_hashes"] and writer and reference is not None:
        found = ctx.hashes.get(writer["writer_id"], {}).get(path)
        if writer["writer_type"] == "exec" and found is not None:
            same = oracle._lower(reference) == oracle._lower(found)
            out["hash_confirms_writer"] = same
    return out


def _last_writer_row(
    ctx: _Run, state: input_state.InputState, oracle_rows: list
) -> LastWriter:
    """W3 for one layer-4 row: LW1 to LW17, EX1, VIS1, VIS2."""
    source = ctx.sources.get(state["input_id"]) or {}
    writer, candidates, writer_type, unattributed = _writer(ctx, state)
    lo, hi = ctx.windows[writer["event_id"]] if writer else (None, None)
    certain = ctx.certain[writer["event_id"]] if writer else None
    out = _facts(ctx, state, writer) | _version(ctx, state, source, writer)
    exists = (None, None)  # EX1: (exists_at_read, exists_source)
    if None not in (state["content_source"], state["grading_exists"]):
        exists = (state["grading_exists"], state["content_source"])
    elif writer is not None:
        exists = (writer["op"] != "delete", "last_event")
    if state["input_type"] == "listing":
        exists = (None, None)
    start = ctx.run["t_start"]
    risk = later_writer_risk(
        state,
        writer_id=out["writer_command_id"],
        window_lo=next(t for t in (lo, start, -math.inf) if t is not None),
        windows=ctx.items,
        execs=ctx.execs,
        orphans_exact=ctx.orphans_exact,
    )
    found = source.get("grading_mtime_ns") if ctx.caps["oracle_reads"] else None
    mtime = mtime_candidates(found, ctx.mtime_windows)  # LW17
    roots, complete = root_candidates(
        candidates,
        events=ctx.events,
        state=state,
        mtime=mtime,
        roots=ctx.roots,
        certain=ctx.certain,
        seq=ctx.seq,
    )
    conflict = _conflict(ctx, state, writer, exists)
    failing = (
        writer_type is None,
        not state["observable"],
        not state["in_under"],
        len(candidates) > 1,
        writer is not None and not certain,
        bool(risk),
        conflict is True,
        bool(state["model_conflict"]),
        out["read_version"] == "differs",
        out["read_version"] == "unverified"
        and source.get("oracle_writes") is True,
    )
    not_under = [
        r for r, f in zip(NOT_UNDER_REASONS, failing, strict=True) if f
    ]
    me = state["input_id"]  # a file's id is in no listed_by (LW15)
    members = [
        r["input_id"] for r in oracle_rows if me in (r["listed_by"] or ())
    ]
    out |= {k: state[k] for k in _COPIED}
    out |= {k: source.get(k) for k in ("oracle_writes", "oracle_writes_basis")}
    out.update(
        in_workspace=source.get("in_workspace", True),
        visible=bool(state["observable"]),
        exists_at_read=exists[0],
        exists_source=exists[1],
        last_writer=writer["event_id"] if writer else None,
        last_writer_type=writer_type,
        last_writer_op=writer["op"] if writer else None,
        last_writer_candidates=candidates,
        window_lo=_bounded(lo),
        window_hi=_bounded(hi),
        writer_root_certain=certain,
        later_writer_risk=risk,
        root_candidates=roots,
        root_candidates_complete=complete,
        mtime_candidates=mtime,
        state_conflict=conflict,
        member_input_ids=sorted(members, key=_number),
        unattributed_candidates=unattributed,
        last_writer_under=not not_under,
        not_under=not_under,
    )
    return typing.cast(LastWriter, out)


# W4: rewrites (M2.4).


def _rewrites(ctx: _Run, row: LastWriter) -> InputRewrites:
    """RW1 to RW6 for one input."""
    events = _input_events(ctx, row)

    def owner(e: match.WriteEvent) -> str | None:  # RW2: the owning call
        exec_event = e["writer_type"] == "exec"
        return e["root_call_id"] if exec_event else e["writer_id"]

    content = [e for e in events if e["op"] in CONTENT_OPS]
    content = [e for e in content if e["possibly_edit_call"] is None] or content
    writers = {owner(e) for e in content}
    deletes = {owner(e) for e in events if e["op"] == "delete"} - writers
    unowned = {e["writer_id"] for e in content if owner(e) is None}
    out = InputRewrites(
        input_id=row["input_id"],
        path=row["path"],
        visible=row["visible"],
        members=None,
        pairs=[],
        delete_roots=_by_seq(ctx.seq, filter(None, deletes)),
        rewrites_under=None,
        rewrites_over=None,
        unowned_execs=sorted(unowned),
    )
    edited = any(e["writer_type"] == "edit_call" for e in events)
    if not row["visible"] and not edited:  # RW1
        return out
    groups = collections.defaultdict(list)
    for e in content:
        if owner(e) is not None:
            groups[owner(e)].append(e)
    members = sorted(
        (_member(ctx, call, group) for call, group in groups.items()),
        key=lambda m: (
            min(ctx.windows[e][0] for e in m["events"]),  # first_lo, unbounded
            *(m["seq"] is None, m["seq"] or 0),
        ),
    )
    pairs = [_pair(a, b) for a, b in zip(members, members[1:], strict=False)]
    out.update(
        members=members,
        pairs=pairs,
        rewrites_over=max(0, len(members) + len(unowned) - 1),  # D48
        rewrites_under=sum(p["under"] for p in pairs),
    )
    return out


def _member(ctx: _Run, call: str, events: list[match.WriteEvent]) -> Member:
    """RW2, RW3: one member and its layer 2 and 3 facts."""
    record = ctx.calls.get(call) or {}
    shell = record.get("tool_type") == "shell"
    cfacts = (ctx.call_facts.get(call) or {}) if shell else {}
    ids = [e["event_id"] for e in events]
    return Member(
        call_id=call,
        seq=record.get("seq"),
        member_type="shell" if shell else "edit",
        events=sorted(ids, key=_number),
        first_lo=_bounded(min(ctx.windows[i][0] for i in ids)),
        last_hi=_bounded(max(ctx.windows[i][1] for i in ids)),
        under=any(ctx.certain[i] for i in ids),
        **{k: cfacts.get(k) for k in _CALL_FIELDS},  # layer 2's
        rerun_key=ctx.keys.get(call) if shell else None,
    )


def _pair(prev: Member, member: Member) -> Pair:
    """RW4: how one member followed the previous one (D44 (8))."""
    shell = prev["member_type"] == "shell"
    keys = (prev["rerun_key"], member["rerun_key"])
    same = switched = None
    if prev["member_type"] != member["member_type"]:
        same, switched = False, True
    elif not shell:
        switched = False
    elif None not in keys:
        same = keys[0] == keys[1]
        switched = not same
    args = None if same is None else False
    if same:
        args = prev["arg_count"] != member["arg_count"]
        args |= prev["numeric_args"] != member["numeric_args"]
    shapes = (prev["command_shape"], member["command_shape"])
    return Pair(
        prev_call_id=prev["call_id"],
        call_id=member["call_id"],
        after=(prev["outcome"] or "unknown") if shell else "edit",
        same_key=same,
        writer_switched=switched,
        args_changed=args,
        shape_changed=None if None in shapes else shapes[0] != shapes[1],
        under=prev["under"] and member["under"],
    )


# W5: hunks of the final diff (M4.3).


def _side(path: str) -> str:
    """A header path without quotes and its `a/` or `b/` prefix."""
    path = structure.unquote(path)
    return path[2:] if path[:2] in ("a/", "b/") else path


def change_blocks(
    old_start: int, new_start: int, lines: list[str]
) -> list[Block]:
    """HK3: the change blocks of one @@ section's body lines.

    git writes the start of an empty side as the line before the change,
    so an empty side counts from the next line (D44 (4)).
    """
    tags = [line[:1] or " " for line in lines]
    old = old_start + (not any(t in " -" for t in tags))
    new = new_start + (not any(t in " +" for t in tags))
    blocks: list[Block] = []
    current: Block | None = None
    for tag, line in zip(tags, lines, strict=True):
        if tag == " ":
            current = None
            old, new = old + 1, new + 1
        elif tag in "-+":
            if current is None:
                current = Block(
                    old_start=old, new_start=new, removed=[], added=[]
                )
                blocks.append(current)
            side = current["removed"] if tag == "-" else current["added"]
            side.append(line[1:])
            old, new = (old + 1, new) if tag == "-" else (old, new + 1)
    return blocks


def parse_diff(text: str) -> list[DiffFile]:
    """HK1 to HK3: the sections of a git diff with their change blocks."""
    binary = structure.binary_sections(text)
    files: list[DiffFile] = []
    for section in re.split(r"^diff --git ", text or "", flags=re.M)[1:]:
        header, *lines = section.rstrip("\n").split("\n")
        found = structure.header_paths(header)  # as BIN1 reads it
        if not found:
            continue
        old, new = map(_side, found)
        status, renamed, blocks = "modified", None, []
        at, body = None, []  # the current @@ line and its body
        for line in [*lines, "@@ -0 +0 @@"]:  # the sentinel ends the last
            start = _AT.match(line)
            if start or (at and (line[:1] or " ") not in " -+\\"):
                if at:
                    blocks += change_blocks(int(at[1]), int(at[3]), body)
                at, body = start, []
            elif at:
                body.append(line)
            elif line.startswith("new file mode"):
                status = "added"
            elif line.startswith("deleted file mode"):
                status = "deleted"
            elif line.startswith("rename from "):
                status, renamed = "renamed", structure.unquote(line[12:])
            elif line.startswith("rename to "):
                new = structure.unquote(line[10:])
        files.append(
            DiffFile(
                path=old if status == "deleted" else new,
                renamed_from=renamed,
                file_status=status,
                binary=binary.get(_side(found[1]), False),
                blocks=blocks,
            )
        )
    return files


def language_of(path: str) -> str | None:
    """HK5: the LANGUAGES row of a path, or None."""
    base = posixpath.basename(path)
    for row in LANGUAGES:
        if base in row.basenames:
            return row.name
    extension = posixpath.splitext(base)[1].lower()
    rows = [
        r.name for r in LANGUAGES if extension and extension in r.extensions
    ]
    return rows[0] if len(rows) == 1 else None


def _generic(lines: list[str]) -> list[Token]:
    """HK6's generic rule: word runs, numbers, single punctuation."""
    return [
        Token(m[0], typing.cast(str, m.lastgroup), i)
        for i, line in enumerate(lines)
        for m in _GENERIC.finditer(line)
    ]


def _scanner(row: Language) -> re.Pattern[str]:
    """HK6: one language's lexemes, in the lexer's precedence order.

    `open` starts a block comment or triple quote (which may span
    lines); `bad` is a quote with no closing quote on its line.
    """

    def either(texts: typing.Iterable[str]) -> str:
        return "|".join(map(re.escape, texts))

    opens = [a for a, _ in row.block_pairs]
    opens += [q for q in row.quotes if len(q) == 3]
    marker = either(row.line_markers)
    if row.name == "shell":  # `#` starts a comment only at a word start
        marker = rf"(?<![^\s;|&(])(?:{marker})"
    quotes = [re.escape(q) for q in row.quotes if len(q) == 1]
    strings = "|".join(rf"{q}(?:[^{q}\\]|\\.)*{q}" for q in quotes)
    operators = "|".join(  # longest first; `-eq` not inside `-eqx`
        re.escape(o) + (r"(?!\w)" if o[-1].isalpha() else "")
        for o in sorted(_COMMON_OPERATORS | set(row.comparisons), key=len)[::-1]
    )
    at = "@?" if any(a.startswith("@") for a in row.assertions) else ""
    bang = r"(?:!(?!=))?" if any(a[-1] == "!" for a in row.assertions) else ""
    parts = (
        f"(?P<open>{either(opens)})" if opens else "",
        f"(?P<comment>(?:{marker}).*)" if marker else "",
        f"(?P<string>{strings})|(?P<bad>{'|'.join(quotes)})" if quotes else "",
        f"(?P<number>{NUMBER.pattern})",
        rf"(?P<word>{at}[^\W\d]\w*{bang})",
        rf"(?P<operator>{operators}|\S)",
    )
    return re.compile("|".join(p for p in parts if p))


_SCANNERS = types.MappingProxyType({r.name: _scanner(r) for r in LANGUAGES})


def lex(lines: list[str], language: str | None) -> tuple[list[Token], bool]:
    """HK6: the tokens of one hunk side and whether it lexed cleanly.

    Each side starts outside any comment or string; an unterminated
    block comment or string gives the generic tokens and False, as does
    a language of None.
    """
    row = _ROWS.get(language or "")
    if row is None:
        return _generic(lines), False
    closing = dict(row.block_pairs) | {q: q for q in row.quotes}
    tokens: list[Token] = []
    stop, open_type = None, ""  # a block comment or triple quote still open
    for i, line in enumerate(lines):
        pos = start = 0
        while True:
            if stop is not None:
                found = line.find(stop, pos)
                end = len(line) if found < 0 else found + len(stop)
                tokens.append(Token(line[start:end], open_type, i))
                if found < 0:
                    break
                stop, pos = None, end
            lexeme = _SCANNERS[row.name].search(line, pos)
            if lexeme is None:
                break
            if lexeme.lastgroup == "bad":
                return _generic(lines), False
            if lexeme.lastgroup == "open":
                stop, (start, pos) = closing[lexeme[0]], lexeme.span()
                open_type = "string" if stop in row.quotes else "comment"
                continue
            tokens.append(Token(lexeme[0], str(lexeme.lastgroup), i))
            pos = lexeme.end()
    if stop is not None:
        return _generic(lines), False
    return tokens, True


def _code(tokens: typing.Iterable[Token]) -> list[str]:
    """HK9: the texts of the code tokens."""
    return [t.text for t in tokens if t.token_type != "comment"]


def is_check_line(tokens: list[Token], language: str | None) -> bool:
    """HK7: whether one line's tokens make a check line."""
    row = _ROWS.get(language or "")
    if row is None:
        return False
    code = _code(tokens)
    if row.assertions.intersection(code):
        return True
    if not row.conditions.intersection(code):
        return False
    if row.name != "shell":
        return any(t in row.comparisons for t in code)
    depth = 0  # `>` is a redirection; == and != compare inside [[ ]]
    for t in code:
        depth += (t == "[[") - (t == "]]")
        if t in _SHELL_TESTS or (t in ("==", "!=") and depth > 0):
            return True
    return False


def _unmarked(lines: list[str], row: Language) -> tuple | None:
    """comment_toggle's comparison: comment markers removed (D44 (14)).

    The lines without their markers, as _layout reads them (D51); None
    when they do not lex.
    """
    out = []
    for line in lines:
        text = line.strip()
        marker = next((m for m in row.line_markers if text.startswith(m)), "")
        text = text[len(marker) :]
        text = text[1:] if marker and text.startswith(" ") else text
        for start, stop in row.block_pairs:
            text = text.removeprefix(start).removesuffix(stop)
        out.append(text)
    tokens, lexed = lex(out, row.name)
    return _layout(out, tokens, row) if lexed else None


def _per_line(tokens: list[Token], count: int) -> list[list[Token]]:
    """The tokens of each of a side's count lines."""
    lines: list[list[Token]] = [[] for _ in range(count)]
    for t in tokens:
        lines[t.line].append(t)
    return lines


def _check_lines(tokens: list[Token], count: int, language: str) -> set[str]:
    """The code of the check lines of one side (HK7, HK9)."""
    return {
        " ".join(_code(line))
        for line in _per_line(tokens, count)
        if is_check_line(line, language)
    }


def _layout(
    lines: list[str], tokens: list[Token], row: Language | None
) -> tuple[list, list[str], list[str]]:
    """What whitespace_only compares (HK8, D51): (shape, texts, quoted).

    shape holds, per line with code, its indentation where that is
    syntax (an _INDENTED row, or no row) and, per code token, whether
    whitespace precedes it on its line where that is syntax (a _SPACED
    row), so line breaks, indentation and word splits count; texts the
    token texts, comments without whitespace; quoted, with no row, each
    line holding a quote without its trailing whitespace (the generic
    rule sees no string).
    """
    shape = []
    for line, found in zip(lines, _per_line(tokens, len(lines)), strict=True):
        gaps, cursor = [], 0
        for t in found:  # tokens are in order, only whitespace between
            at = line.find(t.text, cursor)
            if t.token_type != "comment":
                gaps.append(
                    bool(row and row.name in _SPACED and 0 < cursor < at)
                )
            cursor = at + len(t.text)
        if gaps:
            indent = line[: len(line) - len(line.lstrip())]
            syntax = row is None or row.name in _INDENTED
            shape.append((indent if syntax else "", gaps))
    texts = [
        re.sub(r"\s", "", t.text) if t.token_type == "comment" else t.text
        for t in tokens
    ]
    quoted = [x.rstrip() for x in lines if not row and re.search("[\"'`]", x)]
    return shape, texts, quoted


def _numbers(tokens: list[Token]) -> list[str]:
    """Every token's text, numbers as N."""
    return ["N" if t.token_type == "number" else t.text for t in tokens]


def hunk_type(
    block: Block, language: str | None
) -> tuple[str, bool | None, bool]:
    """HK8: (hunk_type, touches_check, lexed) of one block.

    A block that does not lex cleanly is classified as for a ⊥ language:
    generic tokens, and never the four types that need the language.
    Every type that leaves the code alone, or changes one token kind,
    also needs the two sides' line breaks and syntactic indentation
    equal (D51).
    """
    old, new = block["removed"], block["added"]
    old_tokens, old_ok = lex(old, language)
    new_tokens, new_ok = lex(new, language)
    row = _ROWS.get(language or "") if old_ok and new_ok else None
    if row is None:
        old_tokens, new_tokens = _generic(old), _generic(new)
    layout, other = _layout(old, old_tokens, row), _layout(new, new_tokens, row)
    shaped = layout[0] == other[0]  # line breaks, indentation, word splits
    texts_differ = [t.text for t in old_tokens] != [t.text for t in new_tokens]
    numeric = texts_differ and _numbers(old_tokens) == _numbers(new_tokens)
    holds = {
        "whitespace_only": layout == other,
        "numeric_only": bool(old and new) and numeric and shaped,
        "pure_deletion": not new,
        "pure_addition": not old,
    }
    touches = None
    if row is not None:  # the types that need the language
        checks_old = _check_lines(old_tokens, len(old), row.name)
        checks_new = _check_lines(new_tokens, len(new), row.name)
        touches = bool(checks_old or checks_new)
        code_old, code_new = _code(old_tokens), _code(new_tokens)
        toggled = _unmarked(old, row)
        pairs = [(a, b) for a, b in zip(code_old, code_new, strict=False)]
        differing = [p for p in pairs if p[0] != p[1]]
        holds |= {
            "comment_only": shaped and code_old == code_new,  # texts differ
            "comment_toggle": code_old != code_new
            and toggled is not None
            and toggled == _unmarked(new, row),
            "comparison_relaxed": shaped  # so as many code tokens
            and bool(differing)
            and row.relaxations.issuperset(differing),
            "check_removed": bool(checks_old - checks_new)
            and checks_new <= checks_old,
        }
    found = next((t for t in HUNK_TYPES if holds.get(t)), "general")
    return found, touches, row is not None


def _hunks(
    text: str,
    root: str,
    classes: dict[str, typing.Any] | None,
    final_files: list[oracle.FinalFile] | None,
) -> tuple[list[Hunk], list[dict[str, str]], int]:
    """HK1 to HK8: (hunks, diff_files_unclassified, files_omitted).

    classes is layer 3's file_classes by path; None without layer 3.
    """
    index = oracle.final_index(final_files, root) or {}
    omitted = sorted(p for p, e in index.items() if e.get("reason_omitted"))
    hunks: list[Hunk] = []
    unclassified = [{"path": p, "reason": "omitted"} for p in omitted]
    for diff in parse_diff(text):
        path = match.relative_path(diff["path"], root)[0]
        found = (classes or {}).get(path) or {}
        binary = found.get("binary_content")
        binary = diff["binary"] if binary is None else binary
        file_class = None
        if classes is not None:  # D34: layer 3's function for the rest
            file_class = found.get("file_class")
            file_class = file_class or structure.file_class(path, None)[0]
        if binary:
            unclassified.append({"path": path, "reason": "binary"})
        if binary or path in omitted:
            continue
        renamed = diff["renamed_from"]
        renamed = renamed and match.relative_path(renamed, root)[0]
        language = language_of(path)
        for k, block in enumerate(diff["blocks"]):
            found_type, touches, lexed = hunk_type(block, language)
            hunks.append(
                Hunk(
                    path=path,
                    file_status=diff["file_status"],
                    renamed_from=renamed,
                    file_class=file_class,
                    language=language,
                    hunk_index=k,
                    old_start=block["old_start"],
                    new_start=block["new_start"],
                    lines_removed=len(block["removed"]),
                    lines_added=len(block["added"]),
                    hunk_type=found_type,
                    touches_check=touches,
                    lexed=lexed,
                    **dict.fromkeys(("in_named_under", "in_named_over")),
                    in_read=None,  # W6 sets the filters
                )
            )
    unclassified.sort(key=lambda u: (u["path"], u["reason"]))
    return hunks, unclassified, len(omitted)


# W6: filters.


def _filters(
    ctx: _Run,
    rows: list[LastWriter],
    hunks: list[Hunk],
    named: bool,
    unseen: set[str],
) -> None:
    """FLT2, FLT3: sets in_named_under, in_named_over and in_read.

    named is false when the oracle_inputs precondition fails (FLT2 ⊥);
    unseen holds the input_ids whose unseen_writer_possible is true.
    """
    named_by = [(r, set(r["writer_named_written"])) for r in rows]
    over = set().union(*(n for r, n in named_by if r["last_writer"]))
    under = set().union(*(n for r, n in named_by if r["last_writer_under"]))
    children = collections.defaultdict(list)
    for command, parent in ctx.parents.items():
        children[parent].append(command)

    def tree_reads(todo: list[str | None]) -> set[str]:
        read: set[str] = set()
        seen: set[str | None] = {None}
        while todo:  # the writers and their descendants, by LW14's ancestry
            command = todo.pop()
            if command in seen:
                continue
            seen.add(command)
            reads = (ctx.execs.get(command or "") or {}).get("reads") or ()
            read.update(match.relative_path(p, ctx.root)[0] for p in reads)
            todo += children.get(command, [])
        return read

    execs = [r for r in rows if r["last_writer_type"] == "exec"]
    read = tree_reads([r["writer_command_id"] for r in execs])
    # FLT3 (D53 (3)): false only when no candidate writer read the path.
    events = [ctx.events[e] for r in rows for e in r["last_writer_candidates"]]
    ids = [e["writer_id"] for e in events if e["writer_type"] == "exec"]
    maybe = tree_reads(ids) | read
    open_ = None in ids or any(
        r["last_writer_type"] is None
        or r["later_writer_risk"]
        or r["state_conflict"]
        or r["input_id"] in unseen
        for r in rows
    )
    for hunk in hunks:
        if named:
            hunk["in_named_over"] = hunk["path"] in over
            hunk["in_named_under"] = hunk["path"] in under
        if ctx.caps["read_tracing"]:
            path = hunk["path"]
            known = path in read or not (open_ or path in maybe)
            hunk["in_read"] = (path in read) if known else None


# W7: run metrics.


def _m33(rows: list[LastWriter]) -> dict[str, typing.Any]:
    """§5.4's M3.3 block, before voiding."""
    versions = [r["read_version"] for r in rows]
    incomplete = _count(rows, "root_candidates_complete", False)
    out: dict[str, typing.Any] = {
        "inputs": len(rows),
        "inputs_visible": _count(rows, "visible"),
        "inputs_unknown": _count(rows, "last_writer_type", None),
        "writer_depth_max_over": _extreme(
            max, (r["writer_depth"] for r in rows)
        ),
        "inputs_later_writer_risk": _nonempty(rows, "later_writer_risk"),
        "inputs_state_conflict": _count(rows, "state_conflict"),
        "inputs_read_version_differs": versions.count("differs"),
        "inputs_read_version_unverified": versions.count("unverified"),
        "inputs_root_candidates_incomplete": incomplete,
    }
    for mode in ("under", "over"):
        keep = [r for r in rows if mode == "over" or r["last_writer_under"]]
        found = [r["last_writer_type"] for r in keep]
        roles = collections.Counter(r["last_writer_role"] for r in keep)
        durations = [r["writer_duration_s"] for r in keep]  # ⊥ unless exec
        spans = [r[f"root_span_{mode}_s"] for r in keep]
        calls = {r["writer_call_id"] for r in keep} - {None}
        out |= {
            f"inputs_written_{mode}": found.count("exec")
            + found.count("edit_call"),
            f"inputs_not_written_{mode}": found.count("none"),
            f"last_writer_by_role_{mode}": {k: roles[k] for k in facts.ROLES},
            f"writer_duration_min_s_{mode}": _extreme(min, durations),
            f"writer_duration_max_s_{mode}": _extreme(max, durations),
            f"root_span_max_s_{mode}": _extreme(max, spans),
            f"writer_calls_{mode}": len(calls),
            f"inputs_writer_code_changed_{mode}": _nonempty(
                keep, "writer_named_changed"
            ),
        }
    return out


def _m24(rows: list[InputRewrites]) -> dict[str, typing.Any]:
    """§5.4's M2.4 block, before voiding; rows with members ⊥ left out."""
    counted = [r for r in rows if r["members"] is not None]
    every = [p for r in counted for p in r["pairs"]]
    out: dict[str, typing.Any] = {
        "inputs_with_delete_roots": _nonempty(rows, "delete_roots")
    }
    for mode in ("under", "over"):
        pairs = [p for p in every if mode == "over" or p["under"]]
        counts = [r[f"rewrites_{mode}"] for r in counted]
        afters = collections.Counter(p["after"] for p in pairs)
        if mode == "over":  # D48: a rewrite by an unowned exec, unpaired
            afters["unknown"] += sum(
                r["rewrites_over"] - len(r["pairs"]) for r in counted
            )
        out |= {
            f"inputs_rewritten_{mode}": sum(n >= 1 for n in counts),
            f"rewrites_total_{mode}": sum(counts),
            f"rewrites_max_{mode}": _extreme(max, counts),
            f"rewrites_after_ok_{mode}": afters["ok"],
            f"rewrites_after_failure_{mode}": afters["failed"]
            + afters["killed"],
            f"rewrites_after_other_{mode}": afters["unknown"] + afters["edit"],
            f"writer_switches_{mode}": _count(pairs, "writer_switched"),
            f"args_changed_{mode}": _count(pairs, "args_changed"),
        }
    return out


def _m43(
    hunks: list[Hunk], unclassified: list[dict[str, str]], omitted: int
) -> dict[str, typing.Any]:
    """§5.4's M4.3 block, before voiding (D44 (7))."""

    def by_type(selected: typing.Iterable[Hunk]) -> dict[str, int]:
        found = collections.Counter(h["hunk_type"] for h in selected)
        return {t: found[t] for t in HUNK_TYPES}

    files = collections.defaultdict(set)
    classes = collections.defaultdict(list)
    for h in hunks:
        files[h["path"]].add(h["hunk_type"])
        if h["file_class"] is not None:
            classes[h["file_class"]].append(h)
    return {
        "files_classified": len(files),
        "files_binary": _count(unclassified, "reason", "binary"),
        "files_omitted": omitted,
        "hunks_total": len(hunks),
        "hunks_unlexed": _count(hunks, "lexed", False),
        "hunks_touching_check": _count(hunks, "touches_check"),
        "hunks_by_type": by_type(hunks),
        "files_by_type": {
            t: sum(t in found for found in files.values()) for t in HUNK_TYPES
        },
        "hunks_by_class_and_type": {
            c: by_type(found) for c, found in sorted(classes.items())
        },
        **{
            f"hunks_by_type_{key}": by_type(h for h in hunks if h[f"in_{key}"])
            for key in ("named_under", "named_over", "read")
        },
    }


def _void(
    metrics: dict[str, dict[str, typing.Any]], labels: dict[str, str]
) -> list[str]:
    """PRE3: voids what each failing label guards; returns the warnings.

    A failing label warns only when it voids a key that no earlier
    failing label voided (§5.4, D44 (6)).
    """
    warnings, voided = [], set()
    for label, guarded in PRECONDITIONS:
        if labels[label] != "fails":
            continue
        keys = {
            (name[:4], k)
            for name in guarded.split()
            for k in (metrics[name] if len(name) == 4 else (name[5:],))
        }
        if keys - voided:
            form = "precondition {} failed"
            if label in ("oracle_inputs", "input_states"):
                form = "input {} missing"
            warnings.append(f"{form.format(label)}: {_what(guarded)} null")
        voided |= keys
    for block, key in voided:
        metrics[block][key] = None
    return warnings


# W8: across runs.


def input_peer_ratios(
    rows: list[dict[str, typing.Any]],
    config: typing.Mapping[str, typing.Any] | None = None,
) -> list[dict[str, typing.Any]]:
    """PEER2: facts.peer_ratios per (path, pattern) group, input order."""
    groups = collections.defaultdict(list)
    for i, row in enumerate(rows):
        groups[(row["path"], row["pattern"])].append(i)
    out: list[dict[str, typing.Any]] = [{} for _ in rows]
    for indexes in groups.values():
        ratios = facts.peer_ratios([rows[i] for i in indexes], config)
        for i, ratio in zip(indexes, ratios, strict=True):
            out[i] = ratio
    return out


def derive(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    facts_result: facts.FactsResult,
    structure_result: structure.StructureResult | None,
    *,
    run: match.RunInfo,
    oracle_inputs: oracle.OracleInputsResult | None,
    input_states: input_state.StateResult | None,
    final_diff_text: str | None = None,
    final_files: list[oracle.FinalFile] | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> WriterResult:
    """Computes M3.3, M2.4 and M4.3 for one run (W1 to W7).

    Pure: cli.analyse reads every file and passes its content.
    run["capabilities"] selects each rule's form; final_diff_text is
    MC-03's final.diff (read when final_content holds), final_files its
    final_files.json, both as layers 3 and 4 receive them.

    Args:
        calls: Layer 1's calls.
        execs: Layer 1's execs.
        linked: match.link's result.
        facts_result: facts.derive's result.
        structure_result: structure.derive's result; None without it.
        run: The run's RunInfo.
        oracle_inputs: oracle_inputs.derive's result; None without it.
        input_states: input_state.classify's result; None without it.
        final_diff_text: The text of final.diff.
        final_files: final_files.json's entries with a path.
        config: Overrides of match.DEFAULTS (layer 5 owns no key, D36).

    Returns:
        last_writers, input_rewrites, hunks and writer (writer.json).

    Raises:
        ValueError: A config key is not in match.DEFAULTS.
    """
    slop = oracle.merge_config(match.DEFAULTS, config)["snapshot_slop_s"]
    given = run.get("capabilities") or {}
    caps = {k: bool(given.get(k)) for k in match.CAPABILITIES}
    coverage = linked["coverage"]
    states = (input_states or {}).get("input_states") or []
    counts = (input_states or {}).get("state_counts") or {}
    oracle_run = (oracle_inputs or {}).get("oracle_inputs_run") or {}
    # W1: the labels of §7 in label order (PRE1 to PRE5); layer 4's own
    # oracle_inputs label, else oracle_inputs.json's (PRE4).
    held = (counts or oracle_run).get("preconditions") or {}
    diff = "fails"
    if caps["final_content"] and final_diff_text is not None:
        diff = "holds" if final_diff_text.strip() else "vacuous"  # D44 (13)
    traced = coverage["inputs"]["trace"] and coverage["integrity"]["C0.1"]
    layer3 = "fails" if structure_result is None else "holds"
    labels = {
        "trace": "holds" if traced else "fails",
        "oracle_inputs": held.get("oracle_inputs", "fails"),
        "input_states": "holds" if states else "vacuous",
        "C1_under": facts_result["facts"]["preconditions"].get("C1_under")
        or "fails",
        "repeats": layer3,
        "final_diff_content": diff,
        "file_class": layer3,
        "read_events": "holds" if caps["read_tracing"] else "fails",
    }
    if input_states is None:
        labels["input_states"] = "fails"
    # PRE6: the run's form of each basis (D44 (12)); D31's oracle_writes.
    bases = {
        key: "exact" if all(caps[c] for c in wanted) else "fallback"
        for key, wanted in BASES
    }
    if final_files is None:
        bases["read_version"] = "fallback"
    if final_diff_text is None:
        bases["hunks"] = "fallback"
    found = (oracle_run.get("bases") or {}).get("oracle_writes")
    bases["oracle_writes"] = found or "fallback"
    ctx = _Run(
        calls, execs, linked, facts_result, run=run, caps=caps, slop=slop
    )
    oracle_rows = (oracle_inputs or {}).get("oracle_inputs") or []
    shaped = structure_result or {}
    ctx.keys = {
        r["call_id"]: r["program_key"]
        for r in shaped.get("call_structure") or []
    }
    ctx.sources = {r["input_id"]: r for r in oracle_rows}
    ctx.excluded = {
        match.relative_path(r["path"], ctx.root)[0] for r in oracle_rows
    }
    ctx.orphans_exact = (counts.get("bases") or {}).get("detached") == "exact"
    if caps["final_content"]:
        ctx.end_index = oracle.final_index(final_files, ctx.root)
    rows = sorted(
        (_last_writer_row(ctx, s, oracle_rows) for s in states),
        key=lambda r: _number(r["input_id"]),
    )
    rewrites = [_rewrites(ctx, r) for r in rows]
    hunks, unclassified, omitted = [], [], 0
    if labels["final_diff_content"] != "fails":
        classes = shaped.get("file_classes")
        hunks, unclassified, omitted = _hunks(
            final_diff_text or "",
            ctx.root,
            None if classes is None else {r["path"]: r for r in classes},
            final_files,
        )
    unseen = {s["input_id"] for s in states if s["unseen_writer_possible"]}
    _filters(ctx, rows, hunks, labels["oracle_inputs"] != "fails", unseen)
    hunks.sort(key=lambda h: (h["path"], h["hunk_index"]))
    metrics = {
        "M3.3": _m33(rows),
        "M2.4": _m24(rewrites),
        "M4.3": _m43(hunks, unclassified, omitted),
    }
    warnings = _void(metrics, labels)
    writer_run = WriterRun(
        run_key=run.get("run_key"),
        family=run.get("family"),
        capabilities=dict(sorted(coverage["capabilities"].items())),
        bases=dict(sorted(bases.items())),
        preconditions=labels,
        metrics=metrics,
        diff_files_unclassified=unclassified,
        warnings=warnings,
    )
    result = WriterResult(
        last_writers=rows,
        input_rewrites=rewrites,
        hunks=hunks,
        writer=writer_run,
    )
    return match.round_floats(result)
