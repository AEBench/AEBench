"""Chain and faithfulness of the graded files of one run (layer 6).

T6.2 to T6.9 of trajectory-chain-spec.md: preconditions and bases, the
invocations of every exec, read edges and their versions, the backward
closure from each oracle input's last writer (M3.4), the arguments of
the chain's commands against the declared ones (M5.1), the chain time
(M1.5) and the five clauses of Faithful(G, S). Pure: cli.analyse reads
every file and passes its content; this module opens none.
"""

import collections
import math
import posixpath
import types
import typing

from trajectory_checks import (
    declared,
    facts,
    input_state,
    match,
    parse,
    structure,
    writer,
)
from trajectory_checks import (
    oracle_inputs as oracle,
)


class ArgCheck(typing.TypedDict):
    """One row of arg_checks.jsonl (spec §5.4)."""

    command_id: str
    seq: int
    invocation: int
    invocation_key: str
    option: str | None
    executed: dict[str, str | None] | None  # {value, value_type, origin}
    declared_values: list[declared.Value]
    declared_origin: list[str]
    comparison: str  # COMPARISONS or ROW_TYPES
    effective: str | None
    marker: str | None
    contract: str | None  # holds, violated
    conforms: str | None  # holds, violated
    scale_option: bool
    scale_reduced: bool | None
    exit_code: int | None
    root_under: bool
    in_scope: dict[str, list[str] | bool | None]  # SCOPES -> input ids


class ReadEdge(typing.TypedDict):
    """One row of hop_reads.jsonl (spec §5.6)."""

    input_id: str
    oracle_path: str
    reader_node: str
    reader_exec: str | None  # None for an unattributed read
    hop: int
    read_path: str
    read_type: str  # file, listing, rename
    read_lo: float
    read_hi: float
    version: str | None  # event id, baseline
    state: str  # READ_STATES
    file_class: str | None
    code_read: bool
    code_rule: str | None  # class, binary, program
    attributed: bool
    must: bool


class ChainNode(typing.TypedDict):
    """One row of chain_nodes.jsonl (spec §5.5)."""

    input_id: str
    oracle_path: str
    node_id: str
    node_path: str
    hop: int
    must: bool
    writer_type: str  # exec, edit_call
    writer_id: str
    t_start: float | None
    t_end: float | None
    t_end_over: float | None
    ancestors: list[str]
    invocation_keys: list[str]
    decl_ids: list[str]
    exit_code: int | None
    code_paths: list[str]
    code_state: str | None  # CODE_STATES
    build_node: bool


class InputVerdict(typing.TypedDict):
    """One row of faithfulness.jsonl (spec §5.7)."""

    input_id: str
    path: str
    pattern: str | None
    phases: list[str]
    mode: str  # traced, read_free
    writer_event: str | None
    clauses: dict[str, str | None]  # CLAUSES -> holds, violated, None
    reasons: dict[str, str]
    faithful: str | None
    witness: str | None  # <command_id>:<invocation>
    chain_time_under_s: float | None
    chain_time_over_s: float | None
    writer_root_span_s: float | None
    scale_reduced_under: bool | None
    scale_reduced_over: bool | None


class RunChain(typing.TypedDict):
    """chain.json (spec §5.8)."""

    run_key: str | None
    family: str | None
    inputs: dict[str, bool]
    capabilities: dict[str, bool]
    bases: dict[str, str]  # BASES keys -> exact, fallback
    preconditions: dict[str, str]  # label -> holds, vacuous, fails
    metrics: dict[str, dict[str, typing.Any]]  # M5.1, M3.4, M1.5, faithfulness
    warnings: list[str]


class ChainResult(typing.TypedDict):
    """Everything T6.2 to T6.8 produce for one run."""

    arg_checks: list[ArgCheck]
    chain_nodes: list[ChainNode]
    hop_reads: list[ReadEdge]
    faithfulness: list[InputVerdict]
    chain: RunChain


COMPARISONS = tuple(
    "equal below above between changed uncompared added omitted".split()
)
ROW_TYPES = ("no_options", "undeclared_program")
DEVIATIONS = frozenset("below above between changed added omitted".split())
SCALE_OPTIONS = frozenset(  # SC1 (D36), after normalize_option
    "iter iters iteration iterations runs num-runs nruns repeat repeats "
    "repetitions reps trials rounds samples epochs n".split()
)
CODE_CLASSES = frozenset(("code", "build", "notebook", "test"))  # D24
READ_STATES = tuple(
    "derived edit_tool pristine external invisible unseen unknown".split()
)
CODE_STATES = tuple(  # FA5, worst first
    "foreign changed inline unknown built pristine external".split()
)
CLAUSES = ("fresh", "derived", "built", "unmodified", "untampered")
SCOPES = ("writer", "chain_under", "chain_over", "before_writer", "run")
BASES = (  # §6.14: (bases key, capability)
    ("chain", "read_tracing"),
    ("nesting", "parent_links"),
    ("pipes", "stdio"),
    ("timing", "exact_timing"),
    ("versions", "content_hashes"),
    ("delta", "final_content"),
    ("records_at_stop", "start_events"),
)
PRECONDITIONS = (  # §7: (label, what a failure voids; None: no key)
    ("C1_under", "M5.1, M1.5"),
    ("C2_under", "M3.4, M1.5"),
    ("oracle_inputs", "M5.1, M3.4, M1.5, faithfulness"),
    ("input_states", "M5.1, M3.4, M1.5"),
    ("last_writers", "M5.1, M3.4, M1.5"),
    ("declared", "M5.1"),
    ("reads", "M3.4"),
    ("untracked_visible", None),
    ("delta_complete", None),
    ("hunks", None),
)
DICT_FIELDS = types.MappingProxyType(
    {
        "clause_violations": CLAUSES,
        "faithful_by_phase": input_state.PHASES,
        "hop2_reads_by_state_over": READ_STATES,
    }
)
SUMMARY_GROUPS = ("artifact_family", "family", "all")

_INPUTS = types.MappingProxyType(  # chain.json inputs -> derive's argument
    {
        **{
            k: k
            for k in ("spec", "oracle_inputs", "input_states", "final_files")
        },
        "declared": "declared_result",
        "file_classes": "structure_result",
        **dict.fromkeys(("last_writers", "hunks"), "writer_result"),
    }
)
_TEXT_COLUMNS = frozenset(("chain_time_mode", "scope_under", "scope_over"))
_PER_INPUT = ("oracle_inputs", "input_states", "last_writers")
_PASSED = (
    *("passed_and_violated", "passed_and_undefined"),
    *("passed_with_reduced_scale_under", "passed_with_reduced_scale_over"),
)
_PARTIAL = types.MappingProxyType(  # PRE3: a record lacking the field
    {
        "read_tracing": lambda e: e["reads"] is None,
        "parent_links": lambda e: e["root_command_id"] is None,
        "exact_timing": lambda e: None in (e["ended_at"], e["released_at"]),
        "content_hashes": lambda e: (e["snapshot"] or {}).get("hashes") is None,
        "start_events": lambda e: e["record_type"] is None,
        "stdio": lambda e: e["stdio"] is None,
    }
)
_MARKERS = types.MappingProxyType(  # CT2: effective values (holds, violated)
    {
        "exact": ("equal uncompared", "below above between changed"),
        "min": ("equal above between uncompared", "below"),
        "max": ("equal below between uncompared", "above"),
    }
)
_ALLOWED = {True: "holds", False: "violated", None: None}  # FA4
_SATISFIES = {  # FA5: P(n) of the code states other than changed
    **dict.fromkeys(("pristine", "external", "built"), "holds"),
    **dict.fromkeys(("foreign", "inline"), "violated"),
}
_NO_CWD = "/" + "/".join("\0" * 32)  # CH8: a call's unknown cwd (D83)
_Id = tuple[str, int]  # (command_id, invocation index)
_Code = tuple[str, str, bool, str]  # (path, code state, certain, source)
_Verdict = tuple[str | None, str]  # (holds, violated or None; reason)


def _num(value: declared.Value | None) -> float | None:
    """The number of an int or float value, else None."""
    try:
        numeric = value["value_type"] in ("int", "float")
        return float(value["value"]) if numeric else None
    except (TypeError, ValueError):
        return None


def compare(
    executed: declared.Value, values: list[declared.Value]
) -> str | None:
    """ARG2: equal, below, above, between, changed or uncompared.

    None for an empty value set (the caller makes the row added).
    """
    if not values:
        return None
    if "symbolic" in [v["value_type"] for v in (executed, *values)]:
        return "uncompared"
    x, numbers = _num(executed), [_num(v) for v in values]
    if x is None or None in numbers:
        same = executed["value"] in [v["value"] for v in values]
        return "equal" if same else "changed"
    if any(math.isclose(x, n) for n in numbers):
        return "equal"
    if x < min(numbers):
        return "below"
    return "above" if x > max(numbers) else "between"


def marker_verdict(
    marker: str | None, comparison: str, effective: str | None
) -> str | None:
    """CT2: holds, violated or None."""
    if marker == "free":
        return "holds"
    if marker == "exact" and comparison == "added":
        return "violated"
    holds, violated = _MARKERS.get(marker, ("", ""))
    if effective in holds.split():
        return "holds"
    return "violated" if effective in violated.split() else None


def normalize_option(option: str) -> str:
    """SC1: leading dashes stripped, lower case, _ as -."""
    return option.lstrip("-").lower().replace("_", "-")


def _scale(option: str | None) -> bool:
    """SC1: a scale option; env:, var: and pos: are not option names."""
    if option is None or option.split(":", 1)[0] in ("env", "var", "pos"):
        return False
    return normalize_option(option) in SCALE_OPTIONS


def scale_reduced(option: str | None, effective: str | None) -> bool | None:
    """SC2: whether a scale option ran below its declared value."""
    if not _scale(option):
        return False
    return None if effective in (None, "uncompared") else effective == "below"


def kleene_and(values: typing.Iterable[str | None]) -> str | None:
    """Kleene's strong conjunction over holds, violated, None."""
    values = list(values)
    if "violated" in values:
        return "violated"
    return "holds" if all(v == "holds" for v in values) else None


def exists(
    must: typing.Iterable[str | None], may: typing.Iterable[str | None]
) -> str | None:
    """FA1, existential, over P(n) values.

    holds if a must item holds, violated if no may item can hold, else
    None.
    """
    if "holds" in list(must):
        return "holds"
    return "violated" if all(v == "violated" for v in may) else None


def forall(
    must: typing.Iterable[str | None], may: typing.Iterable[str | None]
) -> str | None:
    """FA1, universal, over P(n) values.

    holds if every may item holds, violated if a must item is violated,
    else None.
    """
    if "violated" in list(must):
        return "violated"
    return "holds" if all(v == "holds" for v in may) else None


def _number(item_id: str) -> int:
    """The number of an id such as w12, i3 or d0 (0 without one)."""
    return int(item_id[1:]) if item_id[1:].isdigit() else 0


def read_versions(
    events: list[match.WriteEvent],
    *,
    windows: typing.Mapping[str, tuple[float, float]],
    interval: tuple[float, float],
    own: typing.Collection[str],
) -> tuple[str | None, list[str]]:
    """RD3, RD7: (the must version or None, every possible version).

    events are one path's write events; own the ids written by the
    reader itself; windows writer.observation_window's, by event id.
    """
    lo, hi = interval
    pool = [e for e in events if e["event_id"] not in own]
    version, found = writer.last_writer(pool, windows=windows, cutoff=lo)
    meets = {
        i
        for i in (e["event_id"] for e in pool)
        if windows[i][0] <= hi and windows[i][1] >= lo
    }
    certain = (
        found == [version]
        and any(e["event_id"] == version and e["writer_under"] for e in pool)
        and windows[version][1] <= lo
        and not meets - {version}
    )
    may = sorted(set(found) | meets, key=_number)
    return (version if certain else None), may


def code_read(
    path: str,
    *,
    file_class: str | None,
    binary_content: bool | None,
    program_paths: typing.Collection[str],
) -> tuple[bool, str | None]:
    """D24: (is a code read, rule: class, binary, program)."""
    if file_class in CODE_CLASSES:
        return True, "class"
    if binary_content is True:
        return True, "binary"
    return (True, "program") if path in program_paths else (False, None)


def aggregate(
    rows: list[dict[str, typing.Any]],
    config: typing.Mapping[str, typing.Any] | None = None,
) -> list[dict[str, typing.Any]]:
    """T6.9, AGG1 to AGG4: chain_summary.csv rows from chain.csv rows.

    The columns are fixed by the first row's keys (every chain.csv row
    has the same keys); an empty input gives no rows.
    """
    low, high = {**facts.DEFAULTS, **(config or {})}["percentiles"][:2]
    columns = [
        k
        for k in (rows[0] if rows else ())
        if k[:1] == "M"
        and k.split(".")[-1] not in _TEXT_COLUMNS
        and not k.endswith("_peer_ratio")
    ]
    groups = collections.defaultdict(list)
    for row in rows:
        family = str(row["family"])
        artifact = str(row["run_key"]).split("__")[0]
        for n, group in enumerate((f"{artifact}__{family}", family, "all")):
            groups[(n, group)].append(row)
    out = []
    for (group_type, group), members in sorted(groups.items()):
        verdicts = [r.get("faithfulness.faithful_run") for r in members]
        traced = sum(r.get("bases.chain") == "exact" for r in members)
        found = {"group_type": SUMMARY_GROUPS[group_type], "group": group}
        found |= {"runs": len(members), "runs_traced": traced}
        for column in columns:
            values = [r[column] for r in members]
            values = [v for v in values if type(v) in (int, float)]
            found[f"{column}_n"] = len(values)
            found[f"{column}_median"] = facts.percentile(values, low)
            found[f"{column}_p90"] = facts.percentile(values, high)
        for name in ("holds", "violated", None):
            found[f"faithful_{name or 'undefined'}"] = verdicts.count(name)
        for name in _PASSED:
            values = [r.get(f"faithfulness.{name}") for r in members]
            values = [v for v in values if v is not None]
            found[name] = sum(values) if values else None
        out.append(found)
    return out


def _by(rows: typing.Iterable[dict] | None, key: str) -> dict[str, dict]:
    """The rows by the value of one field."""
    return {r[key]: r for r in rows or ()}


class _Run:
    """The run context of T6.2 to T6.8: indexes, labels and bases."""

    def __init__(
        self,
        calls: list[parse.Call],
        execs: list[parse.Exec],
        linked: match.LinkResult,
        given: dict[str, typing.Any],
        cfg: dict[str, typing.Any],
    ) -> None:
        """Builds the lookups, then T6.2 (labels, bases) and T6.3."""
        run = self.run = given["run"]
        self.cfg, self.given, self.caps = cfg, given, run["capabilities"]
        self.root, self.coverage = run["workspace_root"], linked["coverage"]
        self.execs, self.calls = _by(execs, "command_id"), _by(calls, "call_id")
        links = linked["links"]
        self.events = {
            k["event_id"]: k for k in links if k["link_type"] == "write_event"
        }
        self.by_path = collections.defaultdict(list)
        for event in self.events.values():
            self.by_path[event["path"]].append(event)
        self.disp = {
            (d["item_type"], d["id"]): d for d in linked["dispositions"]
        }
        self.live = [e for e in execs if not e["noise"]]
        self.windowed = [  # CH7, TM3: live and not out_of_window
            e
            for e in self.live
            if self.disposition(e["command_id"]) != "out_of_window"
        ]
        self.windows = {
            i: writer.observation_window(
                e,
                execs=self.execs,
                calls=self.calls,
                exact=self.caps["exact_timing"],
                slop=cfg["snapshot_slop_s"],
            )
            for i, e in self.events.items()
        }
        result = given["declared_result"] or {}
        self.decls = result.get("declared_invocations") or []
        self.options = result.get("declared_options") or []
        self.by_key = collections.defaultdict(list)
        for d in self.decls:
            self.by_key[d["invocation_key"]].append(d)
        self.declared_keys = set(self.by_key)
        self.option_rows: dict[tuple[str, str], dict] = {}
        for o in self.options:
            self.option_rows.setdefault((o["invocation_key"], o["option"]), o)
        self.takes, self.names = declared._option_names(self.options)
        spec = given["spec"] or {}
        self.markers = {
            (key, self.names[key].get(option, option)): marker
            for key, table in (spec.get("markers") or {}).items()
            for option, marker in table.items()
        }
        self.allowed = list(spec.get("allowed_diff_classes") or [])
        self.experiment_keys = spec.get("experiment_keys")
        baseline = given["baseline"]
        self.files = None if baseline is None else baseline.get("files") or {}
        layer3 = given["structure_result"] or {}
        self.classes = _by(layer3.get("file_classes"), "path")
        rows = (given["oracle_inputs"] or {}).get("oracle_inputs") or []
        self.inputs = [r for r in rows if r["input_scope"] != "reference"]
        states = given["input_states"] or {}
        self.states = _by(states.get("input_states"), "input_id")
        found = states.get("state_counts", {}).get("metrics", {}).get("M3.2")
        self.phases = (found or {}).get("phases") if states else None
        written = given["writer_result"] or {}
        self.last = _by(written.get("last_writers"), "input_id")
        self.hunks = collections.defaultdict(list)
        for hunk in written.get("hunks") or []:
            self.hunks[hunk["path"]].append(hunk["hunk_type"])
        known = {*self.by_path, *(self.files or {}), *(r["path"] for r in rows)}
        known |= {p["path"] for p in linked["paths"]}
        self.folders = set()  # RD1: every proper ancestor folder
        for path in known:
            while (path := posixpath.dirname(path)) not in ("", "/"):
                self.folders.add(path)
        self.warnings: list[str] = []
        self._preconditions()
        self._nesting(links)
        self.traced = self.bases["chain"] == "exact"
        self.delta = self._delta(linked["paths"])
        self._invocations()
        stop = math.inf if run["t_end"] is None else run["t_end"]
        self.stopped = [  # CH8's fallback: (start, invocation, read span)
            (call["t_start"], i, (call["t_start"], stop))
            for d in self.disp.values()
            if self.bases["records_at_stop"] == "fallback"
            and (d["item_type"], d["disposition"], d.get("reason"))
            == ("call", "unmatched", "running_at_stop")
            and (call := self.calls.get(d["id"])) is not None
            and call["t_start"] is not None
            for i in self.split(call["command"], _NO_CWD)
            if i["invocation_key"] and not i["informational"]
        ]
        self.edge_cache: dict = {}

    def _preconditions(self) -> None:
        """T6.2: §7's labels, §6.14's bases, PRE2 and PRE3 warnings."""
        given, caps, cov = self.given, self.caps, self.coverage
        labels = self.labels = {
            k: facts.precondition(cov, k, self.cfg)
            for k in ("C1_under", "C2_under", "untracked_visible")
        }
        some = "holds" if self.inputs else "vacuous"
        block, result = given["oracle_inputs"], given["declared_result"]
        labels["oracle_inputs"] = (
            "fails"
            if block is None
            else block["oracle_inputs_run"]["preconditions"]["oracle_inputs"]
        )
        labels["input_states"] = (
            "fails" if given["input_states"] is None else some
        )
        written = given["writer_result"]
        checks = (written or {}).get("writer", {}).get("preconditions", {})
        failed = "fails" in [checks.get(k) for k in ("trace", *_PER_INPUT[:2])]
        labels["last_writers"] = "fails" if written is None or failed else some
        commit = self.run["commit"] or given["run_commit"]
        note = None
        if result is None:
            labels["declared"] = "fails"
        elif commit is None or result["declared"]["commit"] != commit:
            labels["declared"] = "fails"
            note = f"{result['declared']['commit']} != {commit}"
        elif result["declared_invocations"] or result["declared_options"]:
            labels["declared"] = "holds"
        else:
            labels["declared"] = "vacuous"
        partial = {c: sum(map(test, self.live)) for c, test in _PARTIAL.items()}
        if caps["read_tracing"] and not partial["read_tracing"]:
            labels["reads"] = "holds" if self.live else "vacuous"
        else:
            labels["reads"] = "fails"
        complete = caps["final_content"] and given["final_files"] is not None
        complete = complete or labels["untracked_visible"] == "holds"
        c7 = facts.precondition(cov, "C7_under", self.cfg) != "fails"
        labels["delta_complete"] = "holds" if c7 and complete else "fails"
        diff = checks.get("final_diff_content") == "holds"
        hunks = "holds" if diff else "fails"
        labels["hunks"] = hunks if self.allowed else "vacuous"
        exact = {
            "chain": labels["reads"] != "fails",
            "delta": given["final_files"] is not None,
        }
        self.bases = {
            key: "exact"
            if caps[cap] and exact.get(key, not partial.get(cap))
            else "fallback"
            for key, cap in BASES
        }
        values = {
            "C1_under": cov["rates"].get("call_match_rate_under"),
            "C2_under": cov["rates"].get("exec_explained_rate_under"),
            "declared": note,
            "reads": f"read_tracing {str(caps['read_tracing']).lower()}",
        }
        for label, guarded in PRECONDITIONS:
            if guarded is None or labels[label] != "fails":
                continue
            if label in _INPUTS and given[_INPUTS[label]] is None:
                self.warnings.append(f"input {label} missing: {guarded} null")
            else:
                value = values.get(label, "fails")
                self.warnings.append(
                    f"precondition {label} failed ({value}): {guarded} null"
                )
        self.warnings += [
            f"capability {c} partial: {n} records"
            for c, n in partial.items()
            if caps[c] and n
        ]
        self.warnings += (result or {}).get("declared", {}).get("warnings", [])
        for text in (given["spec"] or {}).get("warnings") or []:
            if text not in self.warnings:  # D59
                self.warnings.append(text)

    def _nesting(self, links: list[dict]) -> None:
        """CH2: parents exact (parent_command_id) per record, else J2."""
        self.parents = {
            k["command_id"]: k["parent"]
            for k in links
            if k["link_type"] == "exec_parent"
        }
        for e in self.execs.values() if self.caps["parent_links"] else ():
            if e["root_command_id"] is not None:
                self.parents[e["command_id"]] = e["parent_command_id"]
        self.children = collections.defaultdict(list)
        for child, parent in self.parents.items():
            if parent is not None:
                self.children[parent].append(child)

    def _delta(self, paths: list[match.PathSummary]) -> dict[str, set[str]]:
        """FA4: Δ's code paths and its created untracked paths."""
        files, final = self.files or {}, self.given["final_files"]
        inside = [p for p in paths if p["in_workspace"]]
        if self.bases["delta"] == "exact":
            created = {
                f["path"]
                for f in final
                if f.get("status") == "A" and f["path"] not in files
            }
        else:
            created = {
                p["path"]
                for p in inside
                if p["tracked"] is False
                and any(
                    self.events[e]["op"] == "create"
                    for e in p["events"]
                    if e in self.events
                )
            }
        changed = {
            p["path"]
            for p in inside
            if p["final_state"] in facts.CHANGED_STATES
        }
        code = {p for p in changed | created if self.is_code_file(p)}
        return {"code": code, "created": created}

    def file_class(self, path: str) -> tuple[str | None, bool | None]:
        """Layer 3's class and binary_content of a path (D24)."""
        row = self.classes.get(path)
        if row is not None:
            return row["file_class"], row["binary_content"]
        mode = ((self.files or {}).get(path) or {}).get("mode")
        return structure.file_class(path, mode)[0], None

    def is_code_file(self, path: str) -> bool:
        """D24, D48: a code-class or binary-content file."""
        found, binary = self.file_class(path)
        return found in CODE_CLASSES or binary is True

    def disposition(self, cid: str) -> str | None:
        """Layer 1's disposition of an exec."""
        return self.disp.get(("exec", cid), {}).get("disposition")

    def _invocations(self) -> None:
        """T6.3: the invocations of every exec; ARG5's duplicates."""
        self.inv: dict[_Id, dict] = {}
        self.by_exec: dict[str, list[_Id]] = {}
        for cid, e in self.execs.items():
            found = self.split(e["command"], e["cwd"])
            self.by_exec[cid] = [(cid, n) for n in range(len(found))]
            for n, item in enumerate(found):
                self.inv[(cid, n)] = {**item, "dup": False}
        for cid in sorted(self.execs, key=lambda c: self.execs[c]["seq"]):
            lo, _, hi = self.interval(cid)
            for child in self.children.get(cid, ()):
                e = self.execs.get(child)
                if e is None or not lo <= e["t_start"] <= hi:
                    continue
                keyed = map(self.inv.get, self.by_exec[child])
                want = [
                    (k["invocation_key"], k["options"])
                    for k in keyed
                    if k["invocation_key"]
                ][:1]
                same = [
                    inv
                    for inv in map(self.inv.get, self.by_exec[cid])
                    if not inv["dup"]
                    and [(inv["invocation_key"], inv["options"])] == want
                ]
                if same:
                    same[0]["dup"] = True

    def split(self, command: str | None, cwd: str | None) -> list[dict]:
        """KEY1 to KEY8 of shell text, with the declared options' names."""
        return declared.invocations(
            command or "",
            cwd=cwd,
            root=self.root,
            takes_value=self.takes,
            aliases=self.names,
        )

    def counted(self, cid: str) -> list[_Id]:
        """The invocations of an exec that M5.1 and the witnesses count."""
        return [
            i
            for i in self.by_exec.get(cid, ())
            if self.inv[i]["invocation_key"] is not None
            and not self.inv[i]["informational"]
            and not self.inv[i]["dup"]
        ]

    def interval(self, cid: str) -> tuple[float, float, float]:
        """TM1: (start, end, over end) of an exec."""
        e, end = self.execs[cid], self.run["t_end"]
        exact = None not in (e["released_at"], e["ended_at"])
        if self.caps["exact_timing"] and exact:
            lo, hi = e["released_at"], e["ended_at"]
        else:
            lo, hi = e["t_start"], e["t_end"]
        over = max(hi, end) if not e["complete"] and end is not None else hi
        return lo, hi, over

    def node_interval(self, node: str) -> tuple[float, float, float]:
        """TM1 of a node: its exec's, or an edit call's point."""
        event = self.events[node]
        if event["writer_type"] == "exec":
            return self.interval(event["writer_id"])
        return (event["t_start"],) * 3

    def ancestors(self, cid: str) -> list[str]:
        """CH2: the ancestors of an exec, innermost first."""
        out, current = [], self.parents.get(cid)
        while current is not None and current not in out and current != cid:
            out.append(current)
            current = self.parents.get(current)
        return out

    def up(self, node: str) -> list[str]:
        """The exec of a node and its ancestors; none for an edit call."""
        event = self.events[node]
        if event["writer_type"] != "exec":
            return []
        return [event["writer_id"], *self.ancestors(event["writer_id"])]

    def descendants(self, cid: str) -> list[str]:
        """CH2: every descendant of an exec, breadth first."""
        out, todo = [], list(self.children.get(cid, ()))
        while todo:
            child = todo.pop(0)
            if child not in out and child != cid:
                out.append(child)
                todo += self.children.get(child, ())
        return out

    def own(self, cid: str) -> set[str]:
        """RD7: the events written by an exec or one of its descendants."""
        writers = {cid, *self.descendants(cid)}
        return {
            i
            for i, e in self.events.items()
            if e["writer_type"] == "exec" and e["writer_id"] in writers
        }

    def versions(
        self, path: str, span: tuple[float, float], own: set[str]
    ) -> tuple[str | None, list[str]]:
        """RD3: read_versions of one path over a reader interval."""
        return read_versions(
            self.by_path.get(path, []),
            windows=self.windows,
            interval=span,
            own=own,
        )

    def state(self, path: str, inside: bool, version: str | None) -> str:
        """RD4: the state of one read at one version."""
        if version not in (None, "baseline"):
            event = self.events[version]
            if event["writer_type"] != "exec":
                return "edit_tool"
            snapshot = self.execs[event["writer_id"]]["snapshot"] or {}
            hashes = {
                match.relative_path(k, self.root)[0]: v
                for k, v in (snapshot.get("hashes") or {}).items()
            }
            base = ((self.files or {}).get(path) or {}).get("sha256")
            same = base is not None and hashes.get(path) == base
            exact = self.bases["versions"] == "exact"
            return "pristine" if same and exact else "derived"
        if not inside:
            return "external"
        if self.files is None:
            return "unknown"
        if path in self.files:
            return "pristine"
        scope = self.coverage["rates"].get("snapshot_scope")
        visible = self.labels["untracked_visible"] == "holds"
        return "invisible" if scope == "tracked" and not visible else "unseen"

    def allowed_change(self, path: str) -> bool | None:
        """FA4: whether a changed code path is allowed by A."""
        if not self.allowed:
            return False
        if self.labels["hunks"] == "fails" or not self.hunks.get(path):
            return None
        return all(h in self.allowed for h in self.hunks[path])


def _edge_rows(
    ctx: _Run,
    node: str,
    path: str,
    inside: bool,
    versions: tuple[str | None, list[str]],
    reader: str | None,
    span: tuple[float, float],
    read_type: str | None = None,
) -> list[dict]:
    """RD3, RD4, D24: one edge row per possible version of a read.

    read_type None is an attributed file read, the only kind whose
    version can be certain (listings, renames and unattributed reads are
    may only).
    """
    found, binary = ctx.file_class(path)
    programs = {ctx.inv[i]["program_path"] for i in ctx.by_exec.get(reader, ())}
    programs -= {None}
    is_code, rule = code_read(
        path, file_class=found, binary_content=binary, program_paths=programs
    )
    must, may = versions
    if not may:
        may = ["baseline" if inside and path in (ctx.files or ()) else None]
        must = may[0]
    row = {
        "reader_node": node,
        "reader_exec": reader,
        "read_path": path,
        "read_type": read_type or "file",
        "read_lo": span[0],
        "read_hi": span[1],
        "file_class": found,
        "code_read": is_code,
        "code_rule": rule,
        "attributed": reader is not None,
        "inside": inside,
    }
    return [
        {
            **row,
            "version": v,
            "state": ctx.state(path, inside, v),
            "certain": read_type is None and v == must,
        }
        for v in may
    ]


def _edges(ctx: _Run, node: str, window_end: float) -> list[dict]:
    """T6.4, CH2: the read edges of one node (RD1 to RD7)."""
    event, out = ctx.events[node], []
    if event["writer_type"] != "exec" or not ctx.traced:
        return out
    x = event["writer_id"]

    def add(*args: typing.Any) -> list[dict]:
        """Appends the rows of one read (_edge_rows) and returns them."""
        out.extend(rows := _edge_rows(ctx, node, *args))
        return rows

    readers = [x] + [
        d for d in ctx.descendants(x) if ctx.execs[d]["t_start"] < window_end
    ]
    for reader in readers:
        lo, _, hi = ctx.interval(reader)
        own = ctx.own(reader)
        for raw in ctx.execs[reader]["reads"] or ():
            path, inside = match.relative_path(raw, ctx.root)
            if not (inside and path in ctx.folders):
                add(
                    path,
                    inside,
                    ctx.versions(path, (lo, hi), own),
                    reader,
                    (lo, hi),
                )
                continue
            kids = [  # RD6
                i
                for i, e in ctx.events.items()
                if e["op"] in ("create", "delete")
                and posixpath.dirname(e["path"]) == path
                and ctx.windows[i][0] < hi
                and i not in own
            ]
            found = (None, sorted(kids, key=_number))
            add(path, inside, found, reader, (lo, hi), "listing")
    spans, own = [ctx.interval(r) for r in readers], ctx.own(x)
    for index, item in enumerate(ctx.run["unattributed_reads"] or ()):  # RD2
        item = item if isinstance(item, dict) else {}
        path, t = item.get("path"), item.get("t")
        if not isinstance(path, str) or type(t) not in (int, float):
            continue
        if any(lo <= t <= hi for lo, _, hi in spans):
            path, inside = match.relative_path(path, ctx.root)
            found = ctx.versions(path, (t, t), own)
            for row in add(path, inside, found, None, (t, t), "file"):
                row["unattributed"] = index
    span = (ctx.interval(x)[0],) * 2
    for other in ctx.events.values() if event["op"] == "create" else ():  # RD5
        mine = other["writer_type"] == "exec" and other["writer_id"] == x
        if mine and other["op"] == "delete" and other["path"] != event["path"]:
            found = ctx.versions(other["path"], span, own)[1]
            if found:
                inside = other["in_workspace"]
                add(other["path"], inside, (None, found), x, span, "rename")
    return out


def _pipe(fd: str | None) -> bool:
    """An fd target that is an anonymous pipe, as `pipe:[N]`."""
    return isinstance(fd, str) and fd.startswith("pipe:[")


def _siblings(ctx: _Run, cid: str) -> list[str]:
    """CH5: the pipeline neighbours of an exec (exact by stdio, else J2).

    Exact: an exec whose stdout pipe is this one's stdin, or the reverse;
    a shared pgid is no evidence (without job control, all children of
    one shell share it).
    """
    if ctx.bases["pipes"] == "exact":
        fd0, fd1 = (ctx.execs[cid]["stdio"] or (None, None))[:2]
        return [
            o["command_id"]
            for o in ctx.live
            if o["command_id"] != cid
            and o["stdio"] is not None
            and (
                (_pipe(fd1) and o["stdio"][0] == fd1)
                or (_pipe(fd0) and o["stdio"][1] == fd0)
            )
        ]
    parent = ctx.parents.get(cid)
    if parent not in ctx.execs or not any(
        s["after_pipe"]
        for s in facts.simple_commands(ctx.execs[parent]["command"] or "")
    ):
        return []
    lo, _, hi = ctx.interval(cid)
    return [
        o
        for o in ctx.children.get(parent, ())
        if o != cid
        and o in ctx.execs
        and ctx.interval(o)[0] <= hi
        and ctx.interval(o)[2] >= lo
    ]


def _closure(ctx: _Run, row: oracle.OracleInput) -> dict[str, typing.Any]:
    """T6.5, CH1 to CH8: the must- and may-chains of one oracle input.

    scopes holds ARG4's exec sets (writer, chain_under = the must-chain's
    execs, chain_over = the may-chain's, before_writer).
    """
    lw, state = ctx.last.get(row["input_id"]), ctx.states.get(row["input_id"])
    w = lw["last_writer"] if lw and lw["last_writer"] in ctx.events else None
    item = {"row": row, "id": row["input_id"], "lw": lw, "state": state}
    item.update(W=w, must={}, may={}, edges={}, ch8=[], under=False)
    item["scopes"] = {name: set() for name in SCOPES[:4]}
    item["eligible"] = (
        w is not None
        and "fails" not in [ctx.labels[k] for k in _PER_INPUT[1:]]
        and row["match_type"] != "pattern"
        and list(row["access_types"]) != ["stat"]
    )
    if not item["eligible"]:
        return item
    end = item["w_end"] = ctx.events[w]["t_end"]
    under = item["under"] = bool(lw["last_writer_under"] and row["in_under"])
    starts = {c for c in lw["last_writer_candidates"] if c in ctx.events}
    starts |= {w} if under else set()
    incomplete = lw["root_candidates_complete"] is False or bool(
        (state or {}).get("unseen_writer_possible")
    )
    ch7 = bool(starts) and (not ctx.traced or incomplete)
    may = item["may"] = {n: 1 for n in sorted(starts, key=_number)}
    for n in sorted(ctx.events, key=_number) if ch7 else ():
        if ctx.events[n]["t_start"] < end:
            may.setdefault(n, 2)
    must = item["must"] = {w: 1} if under else {}
    queue = collections.deque(sorted(may, key=lambda n: (may[n], _number(n))))
    while queue and ctx.traced:
        n = queue.popleft()
        hi = lw.get("window_hi") if n == w else None
        hi = ctx.windows[n][1] if hi is None else hi
        if (n, hi) not in ctx.edge_cache:
            ctx.edge_cache[(n, hi)] = _edges(ctx, n, hi)
        edges = item["edges"][n] = ctx.edge_cache[(n, hi)]
        found = {e["version"] for e in edges} - {None, "baseline", *may}
        for v in sorted(found, key=_number):
            may[v] = may[n] + 1
            queue.append(v)
    queue = collections.deque(sorted(must) if ctx.traced else ())
    while queue:
        n = queue.popleft()
        for e in item["edges"].get(n, ()):
            if e["certain"] and e["version"] not in (None, "baseline", *must):
                must[e["version"]] = must[n] + 1
                queue.append(e["version"])
    before = {e["command_id"] for e in ctx.windowed if e["t_start"] < end}
    scopes = item["scopes"]
    scopes["writer"] = set(ctx.up(w)) if under else set()
    scopes["chain_under"] = {x for n in must for x in ctx.up(n)}
    scopes["chain_over"] = {x for n in may for x in ctx.up(n)}
    for n in may if ctx.traced else ():  # CH5
        for x in ctx.up(n)[:1]:
            scopes["chain_over"].update(_siblings(ctx, x))
    scopes["chain_over"] |= scopes["chain_under"] | (before if ch7 else set())
    scopes["before_writer"] = before if starts else set()
    item["ch8"] = [(i, span) for t, i, span in ctx.stopped if end > t]
    return item


def _scope(ctx: _Run, item: dict, name: str) -> set[_Id]:
    """ARG4: the counted invocations of one scope of one input."""
    return {i for cid in item["scopes"][name] for i in ctx.counted(cid)}


def _versions(ctx: _Run, item: dict) -> tuple[set[_Id], set[_Id]]:
    """ARG4: the invocations of the _under and the _over scope."""
    if ctx.traced:
        return _scope(ctx, item, "chain_under"), _scope(ctx, item, "chain_over")
    return _scope(ctx, item, "writer"), _scope(ctx, item, "before_writer")


def _value_set(
    ctx: _Run, key: str, option: str
) -> tuple[list, list[str], dict | None]:
    """ARG1: V, its sources and the program default of the option."""
    decls, values, origins = ctx.by_key.get(key, []), [], set()
    for d in decls:
        if option in d["options"]:
            values.append(dict(d["options"][option]))
            origins.add(d["source_type"])
    row = ctx.option_rows.get((key, option))
    default = row["default"] if row else None
    omits = not decls or any(option not in d["options"] for d in decls)
    if default is not None and omits:
        values.append(dict(default))
        origins.add(row["source_type"])
    unique = [v for n, v in enumerate(values) if v not in values[:n]]
    unique.sort(key=lambda v: (_num(v) is None, _num(v) or 0.0, v["value"]))
    return unique, sorted(origins), default


def _row(*values: typing.Any) -> dict[str, typing.Any]:
    """The compared fields of one arg_checks row (§5.4), in order."""
    fields = ("option", "executed", "declared_values", "declared_origin")
    return dict(zip((*fields, "comparison", "effective"), values, strict=True))


def _compared(ctx: _Run, key: str, inv: dict) -> list[dict]:
    """ARG1 to ARG3, ARG7: the compared fields of one invocation's rows."""
    decls = ctx.by_key.get(key, [])
    if not decls and all(k != key for k, _ in ctx.option_rows):
        return [_row(None, None, [], [], ROW_TYPES[1], None)]
    out = []
    for option in sorted(
        {*inv["options"], *(o for d in decls for o in d["options"])}
    ):
        values, origins, default = _value_set(ctx, key, option)
        given = inv["options"].get(option)
        if given is None:
            effective = compare(default, values) if default else None
            executed = {"value": None, "value_type": None, **(default or {})}
            row = (executed | {"origin": "omitted"}, values, origins, "omitted")
        elif (effective := compare(given, values)) is None:
            row = ({**given, "origin": "explicit"}, [], [], "added")
        else:
            row = ({**given, "origin": "explicit"}, values, origins, effective)
        out.append(_row(option, *row, effective))
    return out or [_row(None, None, [], [], ROW_TYPES[0], None)]


def _arg_checks(ctx: _Run, items: list[dict]) -> list[dict]:
    """T6.6, ARG1 to ARG7, CT2, CT3, SC1, SC2: the arg_checks rows."""
    void = _void(ctx, *_PER_INPUT)
    scopes = {
        name: {item["id"]: _scope(ctx, item, name) for item in items}
        for name in SCOPES[:4]
        if not void and (ctx.traced or name[:5] != "chain")
    }
    rows = []
    for cid in sorted(ctx.execs, key=lambda c: (ctx.execs[c]["seq"], c)):
        e = ctx.execs[cid]
        under = bool(ctx.disp.get(("exec", cid), {}).get("root_under"))
        for i in ctx.counted(cid):
            key = ctx.inv[i]["invocation_key"]
            in_scope = dict.fromkeys(SCOPES[:4]) | {"run": True}
            for name, found in scopes.items():
                ids = (k for k, members in found.items() if i in members)
                in_scope[name] = sorted(ids, key=_number)
            for row in _compared(ctx, key, ctx.inv[i]):
                marker = ctx.markers.get((key, row["option"]))
                found = row["comparison"]
                contract = marker_verdict(marker, found, row["effective"])
                conforms = contract if found in DEVIATIONS else "holds"
                row.update(
                    command_id=cid,
                    seq=e["seq"],
                    invocation=i[1],
                    invocation_key=key,
                    exit_code=e["exit_code"],
                    root_under=under,
                    inv=i,
                    marker=marker,
                    contract=contract,
                    conforms=None if found == ROW_TYPES[1] else conforms,
                    scale_option=_scale(row["option"]),
                    scale_reduced=scale_reduced(
                        row["option"], row["effective"]
                    ),
                    in_scope=in_scope,
                )
                rows.append(row)
    return rows


def _code_state(
    ctx: _Run, path: str, inside: bool, version: str | None, script: bool
) -> str:
    """FA5: the code state of one code path at one version."""
    if not inside:
        return "foreign" if script else "external"
    if version == "baseline" or (version is None and path in (ctx.files or ())):
        return "pristine"
    if version is None:
        return "changed" if path in ctx.delta["created"] else "unknown"
    if ctx.state(path, inside, version) == "pristine":
        return "pristine"
    if ctx.events[version]["writer_type"] == "edit_call":
        return "changed"
    return "changed" if path in (ctx.files or ()) else "built"


def _program_items(ctx: _Run, cid: str) -> list[_Code]:
    """FA5: the programs and inline code of an exec's invocations."""
    span, own = (ctx.interval(cid)[0],) * 2, ctx.own(cid)
    return [
        code
        for i in ctx.by_exec.get(cid, ())
        if not ctx.inv[i]["dup"] and ctx.inv[i]["invocation_key"] is not None
        for code in _codes(ctx, ctx.inv[i], span, own)
    ]


def _codes(
    ctx: _Run, inv: dict, span: tuple[float, float], own: set[str]
) -> list[_Code]:
    """FA5: one invocation's inline code and program, read over span.

    A program path under CH8's unknown cwd is unknown (D83).
    """
    out = [("<inline>", "inline", True, "inline")] if inv["inline"] else []
    path = inv["program_path"]
    if not path or "\0" in path:
        return out + [(path, "unknown", False, "program")] * bool(path)
    rest = facts.command_words(inv["text"].split())[1]
    base = rest[0].rsplit("/", 1)[-1] if rest else ""
    script = any(p.fullmatch(base) for p, *_ in declared.INTERPRETERS)
    inside = not path.startswith("/")
    must, may = ctx.versions(path, span, own)
    for v in may if inside and may else [None]:
        code = _code_state(ctx, path, inside, v, script)
        out.append((path, code, v == must or not inside, "program"))
    return out


def _code_items(
    ctx: _Run, item: dict, node: str, execs: list[str]
) -> list[_Code]:
    """FA5: the code reads of a node and the programs of some execs."""
    out = [
        (
            e["read_path"],
            _code_state(ctx, e["read_path"], e["inside"], e["version"], False),
            e["certain"],
            "read",
        )
        for e in item["edges"].get(node, ())
        if e["code_read"]
    ]
    return out + [c for x in execs for c in _program_items(ctx, x)]


def _satisfies(ctx: _Run, path: str, code: str, built: bool) -> str | None:
    """FA4, FA5: P(n) of one code state, for clause 3 (built) or 4a.

    Clause 3 is violated only by a changed path that is not allowed;
    foreign and inline code (which violate 4a) leave it undecided.
    """
    if code == "changed":
        return _ALLOWED[ctx.allowed_change(path)]
    found = _SATISFIES.get(code)
    return None if built and found == "violated" else found


def _build_node(ctx: _Run, item: dict, node: str) -> bool:
    """D48: a code file, or a node a code-read edge reaches."""
    edges = (e for found in item["edges"].values() for e in found)
    return ctx.is_code_file(ctx.events[node]["path"]) or any(
        e["code_read"] and e["version"] == node for e in edges
    )


def _node_row(ctx: _Run, item: dict, node: str) -> ChainNode:
    """One chain_nodes row (§5.5)."""
    event, execs = ctx.events[node], ctx.up(node)
    keys = []
    for i in (i for x in execs for i in ctx.by_exec.get(x, ())):
        key = ctx.inv[i]["invocation_key"]
        if key is not None and not ctx.inv[i]["dup"] and key not in keys:
            keys.append(key)
    items = _code_items(ctx, item, node, execs[:1])
    source = "read" if ctx.traced else "program"
    span = ctx.node_interval(node)
    return ChainNode(
        input_id=item["id"],
        oracle_path=item["row"]["path"],
        node_id=node,
        node_path=event["path"],
        hop=item["may"][node],
        must=node in item["must"],
        writer_type=event["writer_type"],
        writer_id=event["writer_id"],
        t_start=span[0],
        t_end=span[1],
        t_end_over=span[2],
        ancestors=execs[1:],
        invocation_keys=keys,
        decl_ids=sorted(
            (d["decl_id"] for k in keys for d in ctx.by_key.get(k, ())),
            key=_number,
        ),
        exit_code=ctx.execs[execs[0]]["exit_code"] if execs else None,
        code_paths=sorted({p for p, _, _, s in items if s == source}),
        code_state=min(
            (s for _, s, _, _ in items), key=CODE_STATES.index, default=None
        ),
        build_node=_build_node(ctx, item, node),
    )


def _read_rows(item: dict) -> list[ReadEdge]:
    """The hop_reads rows of one input (§5.6)."""
    return [
        ReadEdge(
            input_id=item["id"],
            oracle_path=item["row"]["path"],
            hop=item["may"][node],
            must=e["certain"] and node in item["must"],
            **{k: e[k] for k in ReadEdge.__annotations__ if k in e},
        )
        for node, edges in item["edges"].items()
        if node in item["may"]
        for e in edges
    ]


def _witness(ctx: _Run, key: str | None, exit_code: int | None) -> str | None:
    """FA3: P(n) of one invocation being a witness."""
    listed = ctx.experiment_keys is None or key in ctx.experiment_keys
    if key not in ctx.declared_keys or not listed:
        return "violated"
    return {0: "holds", None: None}.get(exit_code, "violated")


def _ids(ctx: _Run, execs: set[str]) -> list[_Id]:
    """The counted invocations of a set of execs, in seq order."""
    found = (i for cid in execs for i in ctx.counted(cid))
    return sorted(found, key=lambda i: (ctx.execs[i[0]]["seq"], i[1]))


def _derived(ctx: _Run, item: dict, rows: dict[_Id, list]) -> _Verdict:
    """FA3, CT3: clause 2."""
    if ctx.labels["declared"] == "fails":
        return None, "precondition declared failed"
    must = _ids(ctx, item["scopes"]["chain_under"])
    may = _ids(ctx, item["scopes"]["chain_over"])
    p = {
        i: _witness(
            ctx, ctx.inv[i]["invocation_key"], ctx.execs[i[0]]["exit_code"]
        )
        for i in may
    }
    stops = [i["invocation_key"] for i, _ in item["ch8"]]  # CH8 (D83)
    unseen = [
        "violated" if "\0" not in k and _witness(ctx, k, 0) != "holds" else None
        for k in stops
    ]
    found = exists((p[i] for i in must), [*(p[i] for i in may), *unseen])
    covered = "fails" not in (ctx.labels["C1_under"], ctx.labels["C2_under"])
    if found == "violated" and not covered:
        found = None
    keyed = [
        i for i in may if ctx.inv[i]["invocation_key"] in ctx.declared_keys
    ]
    conforms = {
        i: kleene_and(r["conforms"] for r in rows.get(i, ())) for i in keyed
    }
    every = forall(
        (conforms[i] for i in keyed if i in must),
        [
            *conforms.values(),
            *(None for k in stops if k in ctx.declared_keys or "\0" in k),
        ],
    )
    reason = {
        "holds": "witness on the must-chain",
        "violated": "no witness in the may-chain",
    }.get(found, "no witness decided")
    reason += "" if covered or found else " (C1_under or C2_under failed)"
    bad = [
        r for i in keyed for r in rows.get(i, ()) if r["conforms"] != "holds"
    ]
    if every != "holds" and bad and found != "violated":
        r = bad[0]
        where = "on chain" if r["inv"] in must else "in may-chain"
        what = (
            f"contract {r['contract']}"
            if r["marker"]
            else f"unmarked deviation {r['comparison']}"
        )
        reason = f"{what} {where}: {r['command_id']} {r['option']}"
    return kleene_and([found, every]), reason


def _built(ctx: _Run, item: dict) -> _Verdict:
    """FA4: clause 3 (traced: every may-chain code read and program)."""
    if ctx.traced:
        found = [
            (n in item["must"], code)
            for n in item["may"]
            for code in _code_items(ctx, item, n, ctx.up(n))
        ]
        found += [  # CH5's pipe members and CH7's execs run programs too
            (False, code)
            for x in sorted(item["scopes"]["chain_over"])
            for code in _program_items(ctx, x)
        ]
        found += [  # and so do CH8's running calls (D83)
            (False, code)
            for inv, span in item["ch8"]
            for code in _codes(ctx, inv, span, set())
        ]
        p = [(m and c[2], _satisfies(ctx, c[0], c[1], True)) for m, c in found]
        verdict = forall((v for m, v in p if m), (v for _, v in p))
        return verdict, {
            "violated": "a must-chain code read changed and not allowed",
            "holds": "every code read of the chain pristine or built",
        }.get(verdict, "a code read of the may-chain undecided")
    must = [
        (p, s)
        for x in item["scopes"]["writer"]
        for p, s, c, source in _program_items(ctx, x)
        if c and source == "program"
    ]
    if any(s == "changed" and ctx.allowed_change(p) is False for p, s in must):
        return "violated", "a program path of the writer changed"
    if ctx.labels["delta_complete"] != "holds":
        return None, "precondition delta_complete failed"
    if all(ctx.allowed_change(p) is True for p in ctx.delta["code"]):
        return "holds", "no changed code in the final diff"
    return None, "changed code in the final diff"


def _e_star(ctx: _Run, item: dict) -> dict | None:
    """FA7: e*, the most upstream must-chain witness off build nodes."""
    found = sorted(
        (-hop, ctx.execs[x]["t_start"], x, i[1], n)
        for n, hop in item["must"].items()
        for x in ctx.up(n)
        for i in ctx.counted(x)
        if _witness(
            ctx, ctx.inv[i]["invocation_key"], ctx.execs[x]["exit_code"]
        )
        == "holds"
    )
    plain = [f for f in found if not _build_node(ctx, item, f[4])]
    if not found:
        return None
    _, start, cid, index, node = (plain or found)[0]
    return {"cid": cid, "index": index, "node": node, "start": start}


def _downstream(ctx: _Run, item: dict, star: dict | None) -> tuple[set, set]:
    """FA5: nodes downstream of e*, by must edges and by time (may)."""
    if star is None:
        return set(item["must"]), set(item["may"])
    readers = collections.defaultdict(set)
    for n in item["must"]:
        for e in item["edges"].get(n, ()):
            if e["certain"] and e["version"] in item["must"]:
                readers[e["version"]].add(n)
    must, todo = set(), [star["node"]]
    while todo:
        for reader in readers[todo.pop()] - must - {star["node"]}:
            must.add(reader)
            todo.append(reader)
    later = {
        n
        for n in item["may"]
        if n != star["node"] and ctx.node_interval(n)[0] > star["start"]
    }
    return must, must | later


def _unmodified(ctx: _Run, item: dict, star: dict | None) -> _Verdict:
    """FA5, FA7, FA8: clause 4 = 4a (code) and 4b (data)."""
    must_down, may_down = _downstream(ctx, item, star)
    must_a, may_a = [], []
    for n in may_down:
        ups = ctx.up(n)
        if star is not None:  # the ancestors below e*'s exec
            below = star["cid"]
            ups = ups[: ups.index(below)] if below in ups[1:] else ups[:1]
        for path, code, certain, _ in _code_items(ctx, item, n, ups):
            may_a.append(_satisfies(ctx, path, code, False))
            if n in must_down and certain:
                must_a.append(may_a[-1])
    code = forall(must_a, may_a)
    r_must, r_may = set(must_down), set(may_down)
    if star is not None and _unpreceded(ctx, item, star):
        r_must.add(star["node"])
        r_may.add(star["node"])
    if not ctx.traced:
        data = "holds" if star is not None and not r_may else None
        return (
            kleene_and([code, data]),
            f"4a {code}; 4b {data}: data reads unknown without read events",
        )
    violated, every = False, star is not None
    for n in r_may:
        for e in item["edges"].get(n, ()):
            if e["code_read"] or not e["inside"]:
                continue
            must_read = e["certain"] and n in r_must and n in item["must"]
            stale = e["state"] == "pristine" and ctx.experiment_keys is not None
            stale = stale and star is not None
            violated |= must_read and (e["state"] == "edit_tool" or stale)
            source = (
                ctx.events.get(e["version"])
                if e["state"] == "derived"
                else None
            )
            every = (
                every
                and source is not None
                and source["writer_type"] == "exec"
                and ctx.execs[source["writer_id"]]["t_start"] >= star["start"]
            )
    data = "violated" if violated else "holds" if every else None
    where = f"downstream of {star['cid']}:{star['index']}" if star else "no e*"
    return kleene_and([code, data]), f"4a {code}; 4b {data} ({where})"


def _unpreceded(ctx: _Run, item: dict, star: dict) -> bool:
    """FA7: e*'s key has declared predecessors and none ran on the chain.

    True too when the declared precondition fails (D-row of §7).
    """
    if ctx.labels["declared"] == "fails":
        return True
    key = ctx.inv[(star["cid"], star["index"])]["invocation_key"]
    blocks = {(d["block_id"], d["position"]) for d in ctx.by_key.get(key, ())}
    before = {
        o["invocation_key"]
        for o in ctx.decls
        if any(o["block_id"] == b and o["position"] < p for b, p in blocks)
    } - {key}
    ran = {
        ctx.inv[i]["invocation_key"]
        for n in item["must"]
        for x in ctx.up(n)
        for i in ctx.counted(x)
        if ctx.execs[x]["exit_code"] == 0
    }
    return bool(before) and not before & ran


def _untampered(ctx: _Run, item: dict) -> _Verdict:
    """FA6: clause 5 (an uncertain writer first, D66)."""
    w, lw = item["W"], item["lw"]
    if not lw["last_writer_under"]:
        return None, "last_writer_under false: " + ", ".join(
            lw["not_under"] or []
        )
    event = ctx.events[w]
    if event["writer_type"] == "edit_call":
        return "violated", f"writer {w} edit_call"
    if any(
        ctx.inv[i]["literal_write"] == item["row"]["path"]
        for i in ctx.by_exec.get(event["writer_id"], ())
    ):
        return "violated", f"writer {w} literal write"
    return "holds", f"writer {w} exec, last_writer_under"


def _fresh(ctx: _Run, item: dict) -> _Verdict:
    """FA2: clause 1, from layer 4's fresh predicate."""
    state = item["state"]
    if state is None or ctx.labels["input_states"] == "fails":
        return None, "input_states row missing"
    if (state.get("predicates_under") or {}).get("fresh") is True:
        return "holds", f"fresh_under: {state.get('state_under')}"
    if not state.get("states_over"):  # a listing row: no label (D79)
        return None, "no possible state of the input"
    if (state.get("predicates_over") or {}).get("fresh") is False:
        over = ", ".join(state.get("states_over") or [])
        return "violated", f"not fresh: {over}"
    return None, "freshness undetermined"


def _spans(ctx: _Run, nodes: typing.Iterable[str], over: bool) -> list:
    """TM1, TM2: node intervals (under or over) clipped to the run."""
    start, end = ctx.run["t_start"], ctx.run["t_end"]
    start = -math.inf if start is None else start
    end = math.inf if end is None else end
    out = []
    for n in nodes:
        span = ctx.node_interval(n)
        lo = max(span[0], start)
        out.append((lo, max(min(span[2 if over else 1], end), lo)))
    return out


def _union(
    ctx: _Run, nodes: typing.Collection[str], over: bool
) -> float | None:
    """TM2: the union length of the nodes' intervals; None without one."""
    return match.union_length(_spans(ctx, nodes, over)) if nodes else None


def _free_over(ctx: _Run, end: float) -> float:
    """TM3: every live, in-window exec interval up to end, as one union."""
    start, out = ctx.run["t_start"], []
    for e in ctx.windowed:
        lo, _, hi = ctx.interval(e["command_id"])
        lo, hi = max(lo, lo if start is None else start), min(hi, end)
        if hi > lo:
            out.append((lo, hi))
    return match.union_length(out)


def _void(ctx: _Run, *labels: str) -> bool:
    """PRE2: whether one of the labels fails."""
    return any(ctx.labels[k] == "fails" for k in labels)


def _times(ctx: _Run, item: dict) -> tuple[float | None, float | None]:
    """T6.7, TM2, TM3: M1.5 of one input (under, over)."""
    if _void(ctx, "C1_under", "C2_under", *_PER_INPUT) or not item["eligible"]:
        return None, None
    if ctx.traced:
        return _union(ctx, item["must"], False), _union(ctx, item["may"], True)
    span = ctx.node_interval(item["W"])
    under = span[1] - span[0] if item["under"] else None
    return under, _free_over(ctx, item["w_end"]) if item["may"] else None


def _verdict(ctx: _Run, item: dict, rows: list[dict]) -> InputVerdict:
    """T6.8: one faithfulness row (FA1 to FA9, SC3)."""
    by_inv = collections.defaultdict(list)
    for r in rows:
        by_inv[r["inv"]].append(r)
    values = dict.fromkeys(CLAUSES, (None, "no chain for this input"))
    values["fresh"], star = _fresh(ctx, item), None
    if item["eligible"]:
        star = _e_star(ctx, item)
        values.update(
            derived=_derived(ctx, item, by_inv),
            built=_built(ctx, item),
            unmodified=_unmodified(ctx, item, star),
            untampered=_untampered(ctx, item),
        )
    clauses = {c: v[0] for c, v in values.items()}
    scale = []  # SC3: true, else None, else false; voided with M5.1
    for ids, over in zip(_versions(ctx, item), (False, True), strict=True):
        found = {
            r["scale_reduced"]
            for i in ids
            for r in by_inv[i]
            if over or r["root_under"]
        }
        scale.append(
            True if True in found else None if None in found else False
        )
    if _void(ctx, "C1_under", "declared", *_PER_INPUT):
        scale = [None, None]
    lw, (under, over) = item["lw"], _times(ctx, item)
    return InputVerdict(
        input_id=item["id"],
        path=item["row"]["path"],
        pattern=item["row"]["pattern"],
        phases=list(item["row"]["phases"]),
        mode="traced" if ctx.traced else "read_free",
        writer_event=lw["last_writer"] if lw else None,
        clauses=clauses,
        reasons={c: v[1] for c, v in values.items()},
        faithful=kleene_and(clauses.values()),
        witness=f"{star['cid']}:{star['index']}" if star else None,
        chain_time_under_s=under,
        chain_time_over_s=over,
        writer_root_span_s=lw["root_span_over_s"] if lw else None,
        scale_reduced_under=scale[0],
        scale_reduced_over=scale[1],
    )


def _m51(
    ctx: _Run, items: list[dict], rows: list[dict]
) -> dict[str, typing.Any]:
    """M5.1's run block (ARG4, ARG6, CT2, SC3)."""
    under, over, writes = set(), set(), set()
    for item in items:
        found = _versions(ctx, item)
        under, over = under | found[0], over | found[1]
        writes |= _scope(ctx, item, "writer")
    chosen = {
        "under": [r for r in rows if r["inv"] in under and r["root_under"]],
        "over": [r for r in rows if r["inv"] in over],
    }
    out: dict[str, typing.Any] = {}
    for version, found in chosen.items():
        n = collections.Counter(r["comparison"] for r in found)
        bad = [r for r in found if r["comparison"] in DEVIATIONS]
        out[f"options_compared_{version}"] = sum(n[c] for c in COMPARISONS)
        out |= {f"args_{c}_{version}": n[c] for c in COMPARISONS}
        violated = sum(r["contract"] == "violated" for r in bad)
        out[f"args_contract_violated_{version}"] = violated
        unmarked = sum(r["marker"] is None for r in bad)
        out[f"args_unmarked_deviations_{version}"] = unmarked
    out["writers_undeclared_program"] = sum(
        r["inv"] in writes and r["comparison"] == ROW_TYPES[1] for r in rows
    )
    out["scope_under"] = "chain_under" if ctx.traced else "writer"
    out["scope_over"] = "chain_over" if ctx.traced else "before_writer"
    n = collections.Counter(r["comparison"] for r in rows)
    out |= {f"args_{c}_run": n[c] for c in COMPARISONS if c in DEVIATIONS}
    ran = {
        inv["invocation_key"]
        for i, inv in ctx.inv.items()
        if not inv["informational"] and ctx.execs[i[0]]["exit_code"] == 0
    }
    keys = ctx.declared_keys
    out["declared_keys_run"] = len(ran & keys) / len(keys) if keys else None
    for version, found in (*chosen.items(), ("run", rows)):
        reduced = sum(r["scale_reduced"] is True for r in found)
        out[f"scale_reduced_{version}"] = reduced
    return out


def _m34(ctx: _Run, items: list[dict]) -> dict[str, typing.Any]:
    """M3.4's run block (CH6)."""
    reads = {"under": set(), "over": set()}
    hops = {v: collections.defaultdict(set) for v in ("under", "over")}
    states, seen, unattributed = dict.fromkeys(READ_STATES, 0), set(), set()
    for item in items:
        for version, chain in (("under", item["must"]), ("over", item["may"])):
            for n, hop in chain.items():
                hops[version][n].add(hop)
                for e in item["edges"].get(n, ()) if hop == 1 else ():
                    if version == "over" or e["certain"]:
                        reads[version].add((n, e["read_path"]))
                    edge = (n, e["read_path"], e["version"])
                    if version == "over" and edge not in seen:
                        seen.add(edge)
                        states[e["state"]] += 1
        for edges in item["edges"].values():
            unattributed |= {
                e["unattributed"] for e in edges if "unattributed" in e
            }
    out = {
        "hop2_reads_under": len(reads["under"]),
        "hop2_reads_over": len(reads["over"]),
        "hop2_reads_by_state_over": states,
    }
    for version, index in (("under", 1), ("over", 2)):
        hop2 = [n for n, found in hops[version].items() if 2 in found]
        lengths = [s[index] - s[0] for s in map(ctx.node_interval, hop2)]
        out[f"hop2_writers_{version}"] = len(hop2)
        out[f"hop2_writer_max_s_{version}"] = max(lengths, default=None)
        out[f"chain_nodes_{version}"] = len(hops[version])
        depth = (max(found) for found in hops[version].values())
        out[f"chain_depth_{version}"] = max(depth, default=None)
    out["reads_unattributed"] = len(unattributed)
    return out


def _m15(
    ctx: _Run, items: list[dict], verdicts: list[InputVerdict]
) -> dict[str, typing.Any]:
    """M1.5's run block (TM2, TM3)."""
    live = [i for i in items if i["eligible"]]
    if ctx.traced:
        under = _union(ctx, {n for i in live for n in i["must"]}, False)
        over = _union(ctx, {n for i in live for n in i["may"]}, True)
    else:
        spans = [ctx.node_interval(i["W"]) for i in live if i["under"]]
        under = max((s[1] - s[0] for s in spans), default=None)
        ends = [i["w_end"] for i in live if i["may"]]
        over = _free_over(ctx, max(ends)) if ends else None
    metrics = (
        (ctx.given["facts_result"] or {}).get("facts", {}).get("metrics", {})
    )
    total = metrics.get("M1.1", {}).get("command_time_over_s")
    roots = [v["writer_root_span_s"] for v in verdicts]
    out = {"chain_time_under_s": under, "chain_time_over_s": over}
    out["chain_time_mode"] = "traced" if ctx.traced else "read_free"
    for name, value in (("under", under), ("over", over)):
        share = value / total if value is not None and total else None
        out[f"chain_share_{name}"] = share
    found = [r for r in roots if r is not None]
    return out | {"writer_root_span_max_s": max(found, default=None)}


def _faithfulness(
    ctx: _Run, verdicts: list[InputVerdict]
) -> dict[str, typing.Any]:
    """The faithfulness run block (FA9, AGG3, SC3)."""
    values = [v["faithful"] for v in verdicts]
    by_phase = collections.defaultdict(list)
    for v in verdicts:
        for phase in v["phases"]:
            by_phase[phase].append(v)
    phases = {
        p: kleene_and(v["faithful"] for v in vs)
        for p, vs in sorted(by_phase.items())
    }
    out = {
        "inputs": len(values),
        "inputs_holds": values.count("holds"),
        "inputs_violated": values.count("violated"),
        "inputs_undefined": values.count(None),
        "clause_violations": {
            c: sum(v["clauses"][c] == "violated" for v in verdicts)
            for c in CLAUSES
        },
        "faithful_run": kleene_and(values) if values else None,
        "faithful_by_phase": phases,
        **dict.fromkeys(_PASSED),
    }
    if ctx.phases is None:
        return out
    passed = [
        p
        for p, info in ctx.phases.items()
        if isinstance(info, dict)
        and info.get("status") in ctx.cfg["passing_statuses"]
    ]
    found = [phases[p] for p in passed if p in phases]
    out["passed_and_violated"] = found.count("violated")
    out["passed_and_undefined"] = found.count(None)
    for version in ("under", "over"):
        out[f"passed_with_reduced_scale_{version}"] = sum(
            any(v[f"scale_reduced_{version}"] is True for v in by_phase[p])
            for p in passed
        )
    return out


def _metrics(
    ctx: _Run, items: list[dict], rows: list[dict], verdicts: list[InputVerdict]
) -> dict:
    """T6.9: the four metric blocks, voided by PRE2."""
    m51, m34 = _m51(ctx, items, rows), _m34(ctx, items)
    m15, fa = _m15(ctx, items, verdicts), _faithfulness(ctx, verdicts)
    if _void(ctx, "C1_under", "declared"):
        m51 = dict.fromkeys(m51)
    if _void(ctx, *_PER_INPUT):  # ARG6: the *_run keys stay
        m51 = {k: v if k.endswith("_run") else None for k, v in m51.items()}
    if _void(ctx, "C2_under", "reads", *_PER_INPUT):
        m34 = dict.fromkeys(m34)
    if _void(ctx, "C1_under", "C2_under", *_PER_INPUT):
        m15 = dict.fromkeys(m15)
    if _void(ctx, "oracle_inputs"):
        fa = dict.fromkeys(fa)
    return {"M5.1": m51, "M3.4": m34, "M1.5": m15, "faithfulness": fa}


def _ranks(row: dict[str, typing.Any], node: str) -> tuple:
    """§6.13: the sort key of a node or read row.

    (input, hop, node), then a read row's path and version.
    """
    head = _number(row["input_id"]), row["hop"], _number(row[node])
    return *head, row.get("read_path", ""), row.get("version") or ""


def derive(
    calls: list[parse.Call],
    execs: list[parse.Exec],
    linked: match.LinkResult,
    facts_result: facts.FactsResult,
    structure_result: structure.StructureResult | None,
    *,
    run: match.RunInfo,
    baseline: typing.Mapping[str, typing.Any] | None,
    oracle_inputs: oracle.OracleInputsResult | None,
    input_states: input_state.StateResult | None,
    writer_result: writer.WriterResult | None,
    final_files: list[oracle.FinalFile] | None = None,
    declared_result: declared.DeclaredResult | None = None,
    spec: declared.CaseSpec | None = None,
    run_commit: str | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
) -> ChainResult:
    """T6.2 to T6.8 for one run.

    Pure: cli.analyse reads every file and passes its content. Read
    paths are execs' reads and run["unattributed_reads"]; capabilities
    come from run["capabilities"]. run_commit is the run's commit when
    run["commit"] is None (layer 4's OI9 source). Every missing input
    makes the keys it feeds None, with a warning.

    Raises:
        ValueError: A config key is not in match.DEFAULTS, facts.DEFAULTS
            or input_state.DEFAULTS.
    """
    owners = {**match.DEFAULTS, **facts.DEFAULTS, **input_state.DEFAULTS}
    if unknown := sorted(set(config or {}) - set(owners)):
        raise ValueError(f"config keys chain does not take: {unknown}")
    given = dict(locals())
    ctx = _Run(calls, execs, linked, given, {**owners, **(config or {})})
    items = [_closure(ctx, row) for row in ctx.inputs]
    rows = _arg_checks(ctx, items)
    live = [i for i in items if i["eligible"]]
    nodes = [_node_row(ctx, i, n) for i in live for n in i["may"]]
    reads = [r for i in live for r in _read_rows(i)]
    verdicts = [_verdict(ctx, item, rows) for item in items]
    block = RunChain(
        run_key=run["run_key"],
        family=run["family"],
        inputs={k: given[v] is not None for k, v in _INPUTS.items()},
        capabilities=dict(run["capabilities"]),
        bases=dict(ctx.bases),
        preconditions=dict(ctx.labels),
        metrics=_metrics(ctx, items, rows, verdicts),
        warnings=ctx.warnings,
    )
    for row in rows:
        del row["inv"]
    nodes.sort(key=lambda r: _ranks(r, "node_id"))
    reads.sort(key=lambda r: _ranks(r, "reader_node"))
    verdicts.sort(key=lambda r: _number(r["input_id"]))
    result = ChainResult(
        arg_checks=rows,
        chain_nodes=nodes,
        hop_reads=reads,
        faithfulness=verdicts,
        chain=block,
    )
    return match.round_floats(result)
