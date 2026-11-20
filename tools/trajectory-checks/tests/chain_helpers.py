"""Scenario loader and defaults for the chain-layer tests (layer 6).

Chain scenarios (``tests/chain_scenarios/H<nn>_<slug>.yaml``) are written
directly as lower-layer output records (spec ``trajectory-chain-spec.md``
§2.2.3): layer 1's records (built by ``writer_helpers.build_scenario``,
so layer 5's defaults apply), layer 2's ``facts``, layer 3's
``file_classes``, layer 4's ``oracle_inputs`` and ``input_states``, layer
5's ``last_writers``, ``hunks`` and ``writer`` block, MC-03's
``final_files``, this layer's ``declared`` records and the case's
``spec`` (its ``[declared]`` table). No lower layer is run.

This module imports nothing from ``trajectory_checks.declared`` or
``trajectory_checks.chain``: ``test_chain_rules.py`` loads it before
either module exists (D46). It imports lower-layer modules for record
types and constants only.

Scenario file layout (keys beyond writer_helpers' own)::

    id: H01
    title: one line
    xfail: "reason"                # optional: strict xfail with it
    rules: [ARG1, ARG2]
    run: {...}                     # RunInfo overrides (commit: abc0)
    execs, calls, links, paths, dispositions, coverage: as layer 5
    baseline: absent | {commit: c, files: {...}}  # merged into default
    baseline_files: [path | {path, sha256, mode}]
    classes: {path: file_class}    # layer 3's file_class per paths row
    file_classes: [...]            # layer 3 rows merged by path
    oracle_inputs: [...] | absent
    state: label | {input_id: label}   # layer 4's state (command_written)
    input_states: [...] | absent   # merged by input_id
    state_counts: {dotted.key: value}
    last_writers: [...] | absent   # merged by input_id
    writer_block: {dotted.key: value}
    hunks: [{path, hunk_type}]
    final_files: [...]
    declared: absent | {invocations: [...], options: [...], meta: {...}}
    spec: absent | {lines, options, experiment_keys, allowed_diff_classes}
    run_commit: str                # chain.derive's run_commit
    config: {...}
    runs: [{...}, ...]             # several runs (H43, H44, H62)
    expect:
      arg_checks: [{command_id, invocation, option, ...}]
      chain_nodes: [{input_id, node_id, ...}]
      hop_reads: [{input_id, reader_node, read_path, version, ...}]
      faithfulness: [{input_id, ...}]
      absent: {arg_checks: [{command_id, invocation, option}], ...}
      reasons_include: {input_id: {clause: substring}}
      row_counts: {arg_checks: n, ...}
      bases, preconditions, capabilities, inputs: {...}
      metrics: {M5.1: {...}, ...}
      metrics_null: [M3.4, ...]
      warnings: [...]              # the whole list
      warnings_include: [...]      # each is one of the warnings
      warnings_with: [[a, b]]      # one warning holds both texts
      summary: [{group_type, group, ...}]  # chain.aggregate over runs
"""

import copy
import pathlib
import re
import types
from collections.abc import Mapping
from typing import Any

import conftest
import pytest
import writer_helpers
import yaml

from trajectory_checks import input_state, match, writer

SCENARIOS_DIR = conftest.TESTS_DIR / "chain_scenarios"
DECLARED_FIXTURES_DIR = conftest.FIXTURES_DIR / "declared"

# Spec §6 and §7: every rule id.
SECTION6_RULES = (
    *(f"DEC{n}" for n in range(1, 8)),
    *(f"KEY{n}" for n in range(1, 9)),
    *("ARG1", "ARG2", "ARG3", "ARG4", "ARG5", "ARG7"),
    *("CT1", "CT2", "CT3", "CT4", "CT5"),
    *("SC1", "SC2", "SC3"),
    *(f"RD{n}" for n in range(1, 8)),
    *(f"CH{n}" for n in range(1, 9)),
    *("TM1", "TM2", "TM3"),
    *(f"FA{n}" for n in range(1, 10)),
    *("AGG1", "AGG2", "AGG3", "AGG4"),
    *("PRE1", "PRE2", "PRE3"),
)
# ARG6 and TM4 are descriptive: ids, not rules a scenario must cite; D24
# (the code-read rule) is cited as §2.2.2's table cites it.
RULE_IDS = (*SECTION6_RULES, "ARG6", "TM4", "D24")
SPEC_IDS = (
    *(f"H{n:02d}" for n in range(1, 45)),
    *(f"H{n}{h}" for n in range(45, 53) for h in "ab"),
    *(f"H{n:02d}" for n in range(53, 77)),
)
# §2.2.3's capability pairs: (capability, bases key, true id, false id,
# the rule both halves cite).
CAPABILITY_PAIRS = (
    ("read_tracing", "chain", "H45a", "H45b", "CH7"),
    ("parent_links", "nesting", "H46a", "H46b", "CH2"),
    ("parent_links", "pipes", "H47a", "H47b", "CH5"),
    ("exact_timing", "timing", "H48a", "H48b", "TM1"),
    ("content_hashes", "versions", "H49a", "H49b", "RD4"),
    ("final_content", "delta", "H50a", "H50b", "FA4"),
    ("start_events", "records_at_stop", "H51a", "H51b", "CH8"),
    ("read_tracing", "chain", "H52a", "H52b", "PRE3"),
)
BASES_KEYS = (
    *("chain", "nesting", "pipes", "timing", "versions", "delta"),
    "records_at_stop",
)
BASIS_CAPABILITY = types.MappingProxyType(
    {
        "chain": "read_tracing",
        "nesting": "parent_links",
        "pipes": "parent_links",
        "timing": "exact_timing",
        "versions": "content_hashes",
        "delta": "final_content",
        "records_at_stop": "start_events",
    }
)
PRECONDITION_LABELS = (
    *("C1_under", "C2_under", "oracle_inputs", "input_states"),
    *("last_writers", "declared", "reads", "untracked_visible"),
    *("delta_complete", "hunks"),
)
INPUT_KEYS = (
    *("declared", "spec", "file_classes", "oracle_inputs", "input_states"),
    *("last_writers", "hunks", "final_files"),
)
COMPARISONS = (
    *("equal", "below", "above", "between", "changed", "uncompared"),
    *("added", "omitted"),
)
ROW_TYPES = ("no_options", "undeclared_program")
DEVIATIONS = frozenset(
    {"below", "above", "between", "changed", "added", "omitted"}
)
CLAUSES = ("fresh", "derived", "built", "unmodified", "untampered")
READ_STATES = (
    *("derived", "edit_tool", "pristine", "external", "invisible"),
    *("unseen", "unknown"),
)
CODE_STATES = (
    *("foreign", "changed", "inline", "unknown", "built", "pristine"),
    "external",
)
VERDICTS = ("holds", "violated", None)
SCALE_NAMES = frozenset(
    {
        *("iter", "iters", "iteration", "iterations", "runs", "num-runs"),
        *("nruns", "repeat", "repeats", "repetitions", "reps", "trials"),
        *("rounds", "samples", "epochs", "n"),
    }
)

# Spec §3.4 and §5: the fields of each record.
ARG_CHECK_FIELDS = frozenset(
    {
        *("command_id", "seq", "invocation", "invocation_key", "option"),
        *("executed", "declared_values", "declared_origin", "comparison"),
        *("effective", "marker", "contract", "conforms", "scale_option"),
        *("scale_reduced", "exit_code", "root_under", "in_scope"),
    }
)
READ_EDGE_FIELDS = frozenset(
    {
        *("input_id", "oracle_path", "reader_node", "reader_exec", "hop"),
        *("read_path", "read_type", "read_lo", "read_hi", "version"),
        *("state", "file_class", "code_read", "code_rule", "attributed"),
        "must",
    }
)
CHAIN_NODE_FIELDS = frozenset(
    {
        *("input_id", "oracle_path", "node_id", "node_path", "hop", "must"),
        *("writer_type", "writer_id", "t_start", "t_end", "t_end_over"),
        *("ancestors", "invocation_keys", "decl_ids", "exit_code"),
        *("code_paths", "code_state", "build_node"),
    }
)
VERDICT_FIELDS = frozenset(
    {
        *("input_id", "path", "pattern", "phases", "mode", "writer_event"),
        *("clauses", "reasons", "faithful", "witness"),
        *("chain_time_under_s", "chain_time_over_s", "writer_root_span_s"),
        *("scale_reduced_under", "scale_reduced_over"),
    }
)
RUN_FIELDS = frozenset(
    {
        *("schema_version", "run_key", "family", "inputs", "capabilities"),
        *("bases", "preconditions", "metrics", "warnings"),
    }
)
M51_KEYS = frozenset(
    {
        "options_compared_under",
        "options_compared_over",
        *(f"args_{c}_{v}" for c in COMPARISONS for v in ("under", "over")),
        *("args_contract_violated_under", "args_contract_violated_over"),
        *("args_unmarked_deviations_under", "args_unmarked_deviations_over"),
        *("writers_undeclared_program", "scope_under", "scope_over"),
        *(
            f"args_{c}_run"
            for c in ("below", "above", "between", "changed", "added")
        ),
        *("args_omitted_run", "declared_keys_run"),
        *("scale_reduced_under", "scale_reduced_over", "scale_reduced_run"),
    }
)
M51_RUN_KEYS = frozenset(
    k for k in M51_KEYS if k.endswith("_run") or k == "declared_keys_run"
)
M34_KEYS = frozenset(
    {
        *("hop2_reads_under", "hop2_reads_over", "hop2_reads_by_state_over"),
        *("hop2_writers_under", "hop2_writers_over"),
        *("hop2_writer_max_s_under", "hop2_writer_max_s_over"),
        *("chain_nodes_under", "chain_nodes_over"),
        *("chain_depth_under", "chain_depth_over", "reads_unattributed"),
    }
)
M15_KEYS = frozenset(
    {
        *("chain_time_under_s", "chain_time_over_s", "chain_time_mode"),
        *("chain_share_under", "chain_share_over", "writer_root_span_max_s"),
    }
)
FAITHFULNESS_KEYS = frozenset(
    {
        *("inputs", "inputs_holds", "inputs_violated", "inputs_undefined"),
        *("clause_violations", "faithful_run", "faithful_by_phase"),
        *("passed_and_violated", "passed_and_undefined"),
        "passed_with_reduced_scale_under",
        "passed_with_reduced_scale_over",
    }
)
METRIC_KEYS = types.MappingProxyType(
    {
        "M5.1": M51_KEYS,
        "M3.4": M34_KEYS,
        "M1.5": M15_KEYS,
        "faithfulness": FAITHFULNESS_KEYS,
    }
)
# Keys that are a maximum, a ratio or a verdict: ⊥ also without voiding
# (over no value, a zero denominator, an undecided verdict).
UNVOIDED_NULL_KEYS = frozenset(
    {
        *("hop2_writer_max_s_under", "hop2_writer_max_s_over"),
        *("chain_depth_under", "chain_depth_over"),
        *("chain_time_under_s", "chain_time_over_s"),
        *("chain_share_under", "chain_share_over", "writer_root_span_max_s"),
        *("declared_keys_run", "faithful_run", "faithful_by_phase"),
        *("passed_and_violated", "passed_and_undefined"),
        "passed_with_reduced_scale_under",
        "passed_with_reduced_scale_over",
    }
)
DECLARED_INVOCATION_FIELDS = frozenset(
    {
        *("decl_id", "invocation_key", "options", "source_type"),
        *("source_path", "line", "block_id", "position", "cwd", "text"),
    }
)
DECLARED_OPTION_FIELDS = frozenset(
    {
        *("invocation_key", "option", "aliases", "default", "choices"),
        *("takes_value", "source_type", "source_path", "line"),
    }
)
DECLARED_META_FIELDS = frozenset(
    {
        *("schema_version", "artifact", "commit", "commit_source"),
        *("instructions", "source_url", "files_read", "spec", "warnings"),
    }
)
CASE_SPEC_FIELDS = frozenset(
    {
        *("lines", "markers", "experiment_keys", "allowed_diff_classes"),
        "warnings",
    }
)
INVOCATION_FIELDS = frozenset(
    {
        *("invocation_key", "options", "program_path", "informational"),
        *("inline", "literal_write", "text"),
    }
)
MARKERS = ("exact", "min", "max", "free")
SPEC_KEYS = ("lines", "options", "experiment_keys", "allowed_diff_classes")

TOP_KEYS = frozenset(
    {
        *("id", "title", "description", "xfail", "rules", "run", "calls"),
        *("execs", "links", "dispositions", "paths", "coverage", "facts"),
        *("structure", "classes", "file_classes", "baseline"),
        *("baseline_files", "oracle_inputs", "oracle_inputs_block"),
        *("state", "input_states", "state_counts", "last_writers"),
        *("writer_block", "hunks", "final_files", "declared", "spec"),
        *("run_commit", "config", "runs", "expect"),
    }
)
EXPECT_KEYS = frozenset(
    {
        *("arg_checks", "chain_nodes", "hop_reads", "faithfulness"),
        *("absent", "reasons_include", "row_counts", "bases"),
        *("preconditions", "capabilities", "inputs", "metrics"),
        *("metrics_null", "warnings", "warnings_include", "warnings_with"),
        "summary",
    }
)
DERIVE_KEYS = (
    *("run", "baseline", "oracle_inputs", "input_states", "writer_result"),
    *("final_files", "declared_result", "spec", "run_commit", "config"),
)
ROW_KEYS = types.MappingProxyType(
    {
        "arg_checks": ("command_id", "invocation", "option"),
        "chain_nodes": ("input_id", "node_id"),
        "hop_reads": ("input_id", "reader_node", "read_path", "version"),
        "faithfulness": ("input_id",),
    }
)
_LAYER5_KEYS = frozenset(
    {
        *("id", "title", "description", "xfail", "rules", "run", "calls"),
        *("execs", "links", "dispositions", "paths", "coverage"),
        *("oracle_inputs", "oracle_inputs_block", "input_states"),
        *("state_counts", "final_files", "config", "file_classes"),
        "structure",
    }
)


def scenario_paths() -> list[pathlib.Path]:
    """Lists the chain scenario files in name order.

    Returns:
        Sorted paths of ``chain_scenarios/*.yaml``.
    """
    return sorted(SCENARIOS_DIR.glob("*.yaml"))


def read_raw(path: pathlib.Path) -> dict[str, Any]:
    """Reads one scenario file as written.

    Args:
        path: A scenario file.

    Returns:
        The decoded YAML mapping.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def scenario_id(path: pathlib.Path) -> str:
    """The scenario id of a file: its stem up to the first underscore.

    Args:
        path: A scenario file.

    Returns:
        ``H01``, ``H45a`` and so on.
    """
    return path.stem.split("_", 1)[0]


def scenario_params() -> list[Any]:
    """The scenario files as pytest params, xfail-marked when they say so.

    Returns:
        One ``pytest.param`` per scenario file, id the stem.
    """
    params = []
    for path in scenario_paths():
        reason = read_raw(path).get("xfail")
        marks = (
            [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        )
        params.append(pytest.param(path, id=path.stem, marks=marks))
    return params


def run_raws(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The raw scenario of each run (``runs`` items override the keys).

    Args:
        raw: A scenario as written.

    Returns:
        One raw mapping per run.
    """
    return writer_helpers.run_raws(raw)


def number(item_id: str) -> int:
    """The number of an ``i<n>``, ``w<n>`` or ``d<n>`` id.

    Args:
        item_id: An id with a one-letter prefix.

    Returns:
        Its number.
    """
    return int(item_id[1:])


def _merge(target: dict[str, Any], overrides: Mapping[str, Any]) -> None:
    writer_helpers._merge(target, overrides)


def parse_spec(table: Mapping[str, Any] | None) -> dict[str, Any]:
    """The case's ``[declared]`` table as a CaseSpec (§4.7, CT1, CT5).

    Written from the spec, not by calling ``declared.case_spec``: a key
    is split at its last space, a malformed entry is skipped with one
    warning ``declared <entry>: <problem>``.

    Args:
        table: The table as written, or None.

    Returns:
        A CaseSpec dict.
    """
    spec: dict[str, Any] = {
        "lines": [],
        "markers": {},
        "experiment_keys": None,
        "allowed_diff_classes": [],
        "warnings": [],
    }
    for key, value in (table or {}).items():
        if key not in SPEC_KEYS:
            spec["warnings"].append(f"declared {key}: unknown key")
        elif key == "options":
            for name, marker in value.items():
                if " " not in name:
                    spec["warnings"].append(f"declared {name}: no space")
                elif marker not in MARKERS:
                    spec["warnings"].append(
                        f"declared {name}: unknown marker {marker}"
                    )
                else:
                    head, option = name.rsplit(" ", 1)
                    spec["markers"].setdefault(head, {})[option] = marker
        elif key == "allowed_diff_classes":
            for item in value:
                if item in writer.HUNK_TYPES:
                    spec["allowed_diff_classes"].append(item)
                else:
                    spec["warnings"].append(
                        f"declared allowed_diff_classes: unknown {item}"
                    )
        else:
            spec[key] = list(value)
    return spec


def _declared(raw: Any, run_commit: str) -> dict[str, Any] | None:
    if raw == "absent":
        return None
    raw = copy.deepcopy(dict(raw or {}))
    invocations = []
    for index, item in enumerate(raw.get("invocations") or []):
        row = {
            "decl_id": f"d{index}",
            "invocation_key": None,
            "options": {},
            "source_type": "readme",
            "source_path": "README.md",
            "line": index + 2,
            "block_id": "README.md:1",
            "position": index,
            "cwd": ".",
            "text": "",
        }
        row.update(item)
        if row["source_type"] == "case_block":
            row.setdefault("source_path", None)
        invocations.append(row)
    options = []
    for item in raw.get("options") or []:
        row = {
            "invocation_key": None,
            "option": None,
            "aliases": [item["option"]],
            "default": None,
            "choices": None,
            "takes_value": True,
            "source_type": "argparse",
            "source_path": item.get("invocation_key"),
            "line": 1,
        }
        row.update(item)
        options.append(row)
    meta = {
        "schema_version": "1",
        "artifact": "art",
        "commit": run_commit,
        "commit_source": "checkout",
        "instructions": "README.md",
        "source_url": None,
        "files_read": ["README.md"],
        "spec": parse_spec(None),
        "warnings": [],
    }
    meta.update(raw.get("meta") or {})
    return {
        "declared_invocations": invocations,
        "declared_options": options,
        "declared": meta,
    }


def _baseline(
    raw: Mapping[str, Any], paths: list[Mapping[str, Any]], commit: Any
) -> dict[str, Any] | None:
    given = raw.get("baseline")
    if given == "absent":
        return None
    files: dict[str, Any] = {}
    for row in paths:
        if row["tracked"] is True and row["in_workspace"]:
            files[row["path"]] = {"mode": 420, "sha256": None}
    for item in raw.get("baseline_files") or []:
        item = {"path": item} if isinstance(item, str) else dict(item)
        files[item.pop("path")] = {"mode": 420, "sha256": None, **item}
    baseline = {"commit": commit, "files": files}
    for key, value in (given or {}).items():
        if key == "files":
            baseline["files"].update(copy.deepcopy(value))
        else:
            baseline[key] = copy.deepcopy(value)
    return baseline


def _state_of(raw: Mapping[str, Any], input_id: str) -> str:
    given = raw.get("state", "command_written")
    if isinstance(given, Mapping):
        return given.get(input_id, "command_written")
    return given


def _layer4_extra(raw: Mapping[str, Any], scenario: dict[str, Any]) -> None:
    states = scenario["input_states"]
    if states is None:
        return
    given = {r["input_id"]: r for r in raw.get("input_states") or []}
    for row in states["input_states"]:
        label = _state_of(raw, row["input_id"])
        predicates = {
            name: label in labels
            for name, labels in input_state.PREDICATES.items()
        }
        row.update(
            state_under=label,
            states_under=[label],
            states_over=[label],
            predicates_under=dict(predicates),
            predicates_over=dict(predicates),
        )
        row.update(copy.deepcopy(dict(given.get(row["input_id"]) or {})))
    oracle = (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []
    phases = sorted({p for r in oracle for p in r["phases"]})
    counts = states["state_counts"]
    counts["metrics"] = {
        "M3.2": {"phases": {p: {"status": "success"} for p in phases}}
    }
    _merge(counts, raw.get("state_counts") or {})


def _last_writers(
    raw: Mapping[str, Any], scenario: dict[str, Any]
) -> list[dict[str, Any]]:
    events = writer_helpers.events_of(scenario)
    execs = {e["command_id"]: e for e in scenario["execs"]}
    given = {r["input_id"]: r for r in raw.get("last_writers") or []}
    rows = []
    states = (scenario["input_states"] or {}).get("input_states") or []
    for state in states:
        event = events.get(state["final_writer"] or "")
        is_exec = bool(event and event["writer_type"] == "exec")
        duration = execs[event["writer_id"]]["duration_s"] if is_exec else None
        row: dict[str, Any] = dict.fromkeys(writer.LastWriter.__annotations__)
        row.update(
            input_id=state["input_id"],
            path=state["path"],
            pattern=state["pattern"],
            input_type=state["input_type"],
            in_workspace=True,
            in_under=state["in_under"],
            state_under=state["state_under"],
            visible=True,
            last_writer=event["event_id"] if event else None,
            last_writer_type=event["writer_type"] if event else "none",
            last_writer_op=event["op"] if event else None,
            last_writer_candidates=[event["event_id"]] if event else [],
            window_lo=event["t_start"] if event else None,
            window_hi=(event["t_end"] + (2.0 if is_exec else 0.0))
            if event
            else None,
            writer_command_id=event["writer_id"] if is_exec else None,
            writer_call_id=event["root_call_id"] if event else None,
            writer_duration_s=duration,
            root_span_over_s=duration,
            later_writer_risk=[],
            root_candidates=[event["root_call_id"]]
            if event and event["root_call_id"]
            else [],
            root_candidates_complete=True,
            read_version="unverified",
            writer_named_written=[],
            writer_named_changed=[],
            member_input_ids=[],
            unattributed_candidates=[],
            last_writer_under=bool(state["final_writer_under"]),
            not_under=[] if state["final_writer_under"] else ["no_writer"],
        )
        row.update(copy.deepcopy(dict(given.get(state["input_id"]) or {})))
        rows.append(row)
    return rows


def _writer_result(
    raw: Mapping[str, Any], scenario: dict[str, Any]
) -> dict[str, Any] | None:
    if raw.get("last_writers") == "absent":
        return None
    run = scenario["run"]
    hunks = []
    for item in raw.get("hunks") or []:
        hunk: dict[str, Any] = dict.fromkeys(writer.Hunk.__annotations__)
        hunk.update(file_status="M", hunk_index=0, lexed=True)
        hunk.update(copy.deepcopy(dict(item)))
        hunks.append(hunk)
    block = {
        "schema_version": "1",
        "run_key": run["run_key"],
        "family": run["family"],
        "capabilities": dict(run["capabilities"]),
        "bases": {},
        "preconditions": {
            "trace": "holds",
            "oracle_inputs": "holds",
            "input_states": "holds"
            if scenario["input_states"] is not None
            else "fails",
            "final_diff_content": "holds"
            if run["capabilities"]["final_content"]
            else "fails",
        },
        "metrics": {},
        "diff_files_unclassified": [],
        "warnings": [],
    }
    _merge(block, raw.get("writer_block") or {})
    return {
        "last_writers": _last_writers(raw, scenario),
        "input_rewrites": [],
        "hunks": hunks,
        "writer": block,
    }


def _facts(scenario: dict[str, Any]) -> None:
    spans = [(e["t_start"], e["t_end"]) for e in scenario["execs"]]
    run_facts = scenario["facts_result"]["facts"]
    metrics = run_facts.setdefault("metrics", {})
    metrics.setdefault("M1.1", {})["command_time_over_s"] = match.union_length(
        spans
    )


def build_scenario(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Completes a raw scenario mapping with every default (§2.2.3).

    Args:
        raw: A scenario as written, without ``runs``.

    Returns:
        A dict with ``id``, ``title``, ``rules``, ``raw``, the arguments
        of ``chain.derive`` (``calls``, ``execs``, ``linked``,
        ``facts_result``, ``structure_result`` and ``DERIVE_KEYS``) and
        ``expect``.
    """
    raw = copy.deepcopy(dict(raw))
    lower = {k: v for k, v in raw.items() if k in _LAYER5_KEYS}
    lower["run"] = {"commit": "abc0", **dict(raw.get("run") or {})}
    lower.pop("config", None)
    classes = raw.get("classes") or {}
    rows = [{"path": p, "file_class": c} for p, c in classes.items()]
    lower["file_classes"] = rows + list(raw.get("file_classes") or [])
    scenario = writer_helpers.build_scenario(lower)
    scenario["raw"] = raw
    scenario["expect"] = raw.get("expect") or {}
    scenario["config"] = copy.deepcopy(raw.get("config") or {})
    _facts(scenario)
    _layer4_extra(raw, scenario)
    run = scenario["run"]
    scenario["baseline"] = _baseline(
        raw, scenario["linked"]["paths"], run["commit"]
    )
    scenario["writer_result"] = _writer_result(raw, scenario)
    scenario["declared_result"] = _declared(
        raw.get("declared"), raw.get("run_commit") or run["commit"]
    )
    scenario["spec"] = (
        None if raw.get("spec") == "absent" else parse_spec(raw.get("spec"))
    )
    scenario["run_commit"] = raw.get("run_commit")
    return scenario


def load_runs(path: pathlib.Path) -> list[dict[str, Any]]:
    """Loads every run of a scenario file.

    Args:
        path: A scenario file.

    Returns:
        One ``build_scenario`` value per run.
    """
    return [build_scenario(r) for r in run_raws(read_raw(path))]


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads the first run of a scenario file.

    Args:
        path: A scenario file.

    Returns:
        Its ``build_scenario`` value.
    """
    return load_runs(path)[0]


def by_id(name: str) -> pathlib.Path:
    """The scenario file of one id.

    Args:
        name: A scenario id such as ``H75``.

    Returns:
        Its path.
    """
    return next(p for p in scenario_paths() if scenario_id(p) == name)


def run_derive(scenario: Mapping[str, Any], **overrides: Any) -> Any:
    """Calls ``chain.derive`` on a loaded scenario.

    Args:
        scenario: A dict from ``build_scenario``.
        **overrides: Replacements for any argument of ``derive``.

    Returns:
        The result of ``chain.derive``.
    """
    from trajectory_checks import chain  # late: D46

    keys = (
        *("calls", "execs", "linked", "facts_result", "structure_result"),
        *DERIVE_KEYS,
    )
    args = {
        key: copy.deepcopy(scenario[key])
        for key in keys
        if key not in overrides
    }
    args.update(overrides)
    return chain.derive(
        args.pop("calls"),
        args.pop("execs"),
        args.pop("linked"),
        args.pop("facts_result"),
        args.pop("structure_result"),
        **args,
    )


def validate(scenario: Mapping[str, Any], stem: str) -> list[str]:
    """Checks that a loaded scenario is internally consistent (§2.2.6).

    Args:
        scenario: A dict from ``build_scenario``.
        stem: The file stem it was read from.

    Returns:
        One message per problem; empty when consistent.
    """
    problems = []
    raw = scenario["raw"]
    if not re.fullmatch(r"H\d{2}[ab]?_[a-z0-9_]+", stem):
        problems.append(f"file stem {stem!r} is not H<nn>[ab]_<snake_case>")
    if raw.get("id") != stem.split("_", 1)[0]:
        problems.append(f"id {raw.get('id')!r} does not match {stem!r}")
    if not raw.get("title"):
        problems.append("no title")
    if set(raw) - TOP_KEYS:
        problems.append(f"unknown top-level keys {sorted(set(raw) - TOP_KEYS)}")
    if set(scenario["expect"]) - EXPECT_KEYS:
        unknown = sorted(set(scenario["expect"]) - EXPECT_KEYS)
        problems.append(f"unknown expect keys {unknown}")
    rules = raw.get("rules") or []
    if not rules:
        problems.append("no rules cited")
    if set(rules) - set(RULE_IDS):
        problems.append(
            f"unknown rule ids {sorted(set(rules) - set(RULE_IDS))}"
        )
    if len(set(rules)) != len(rules):
        problems.append("a rule id is cited twice")
    layer5 = writer_helpers.validate(scenario, "W99_chain")
    problems += [
        p
        for p in layer5
        if not p.startswith(("file stem", "id ", "unknown top-level", "no "))
        and "unknown rule ids" not in p
        and "unknown expect keys" not in p
    ]
    ids = {
        r["input_id"]
        for r in (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []
    }
    for name in ("input_states", "last_writers"):
        listed = raw.get(name)
        for row in listed if isinstance(listed, list) else []:
            if row["input_id"] not in ids:
                problems.append(
                    f"{name} row of unknown input {row['input_id']}"
                )
    declared = scenario["declared_result"]
    if declared is not None:
        decl_ids = [d["decl_id"] for d in declared["declared_invocations"]]
        if len(set(decl_ids)) != len(decl_ids):
            problems.append("duplicate decl_id")
        for row in declared["declared_invocations"]:
            if set(row) != DECLARED_INVOCATION_FIELDS:
                problems.append(f"declared invocation {row['decl_id']}: fields")
            if not row["invocation_key"]:
                problems.append(f"declared invocation {row['decl_id']}: no key")
        for row in declared["declared_options"]:
            if set(row) != DECLARED_OPTION_FIELDS:
                problems.append(f"declared option {row['option']}: fields")
    calls = {c["call_id"] for c in scenario["calls"]}
    execs = {e["command_id"] for e in scenario["execs"]}
    for row in scenario["linked"]["dispositions"]:
        if row["id"] not in calls | execs:
            problems.append(f"disposition of unknown id {row['id']}")
    for path in raw.get("classes") or {}:
        if path not in {p["path"] for p in scenario["linked"]["paths"]}:
            problems.append(f"classes entry without a paths row: {path}")
    return problems
