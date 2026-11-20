"""Scenario loader and defaults for the writer-layer tests (layer 5).

Writer scenarios (``tests/writer_scenarios/W<nn>_<slug>.yaml``) are written
directly as lower-layer output records in their committed formats (spec
``trajectory-writer-spec.md`` §2.2.2): layer 1's ``run`` (``RunInfo``),
``calls``, ``execs``, ``links``, ``dispositions``, ``paths`` and
``coverage``; layer 2's ``call_facts``, ``exec_facts`` and ``facts``;
layer 3's ``call_structure`` and ``file_classes``; layer 4's
``oracle_inputs`` rows and block and ``input_states`` rows and
``state_counts``; MC-03's ``final_diff`` (text) and ``final_files``. No
lower layer is run, so a layer-5 test cannot fail because of a change
below it.

This module imports nothing from ``trajectory_checks.writer``: it is
loaded by ``test_writer_rules.py``, which must pass before ``writer.py``
exists (D41). It imports lower-layer modules for record types and
constants only.

Scenario file layout::

    id: W01                         # equal to the file stem's prefix
    title: one line
    description: optional, a folded block
    xfail: "reason"                 # optional: test_scenario is marked
                                    # xfail(strict=True) with this reason
    rules: [LW1, LW3]               # rule ids exercised (spec §6, §7)
    run: {...}                      # RunInfo overrides; run.capabilities
                                    # is merged into the defaults
    calls, execs, links: [...]      # partial layer-1 records
    dispositions: {id: {...}}       # merged into the derived rows
    paths: [...]                    # partial paths.jsonl rows
    coverage: {dotted.key: value}   # coverage.json overrides
    call_facts, exec_facts: [...]   # partial rows, merged by id
    facts: {preconditions: {...}}   # facts.json overrides
    structure: absent               # optional: structure_result = None
    call_structure, file_classes: [...]  # partial rows, merged by id
    oracle_inputs: [...] | absent   # §0 rows; absent passes None
    oracle_inputs_block: {dotted.key: value}
    input_states: [...] | absent    # layer-4 rows merged by input_id;
                                    # absent passes None
    state_counts: {dotted.key: value}
    final_diff: text                # MC-03's final.diff
    final_files: [...]              # MC-03's final_files.json entries
    config: {...}                   # passed to writer.derive
    runs: [{...}, ...]              # optional (W37): one run per item,
                                    # each a top-level override of the
                                    # scenario's keys
    expect:
      last_writers: [{input_id, ...}]    # partial rows
      input_rewrites: [{input_id, ...}]  # partial rows
      hunks: [{path, hunk_index, ...}]   # partial rows
      row_counts: {last_writers: n, input_rewrites: n, hunks: n}
      bases: {...}; preconditions: {...}; capabilities: {...}
      metrics: {M3.3: {...}, ...}        # key by key
      metrics_null: [M3.3, ...]          # every key of the block null
      diff_files_unclassified: [...]     # the whole list
      warnings: [...]                    # the whole list
      peer_ratios: [{run_key, input_id, <ratio column>: value}]  # runs

Defaults (spec §2.2.2's table):

- ``run``: ``t_start`` 0, ``t_end`` 1000, workspace ``/repo``,
  ``trace_ok`` true, ``run_key`` ``art__claude__1``, ``family``
  ``claude``, every other RunInfo field null, every capability false.
- ``calls``: ``conftest``'s defaults, except ``t_end`` = ``t_start``.
- ``execs``: ``conftest``'s defaults (``complete`` true, snapshot ``ok``
  in ``tracked`` scope, ``exit_code`` 0, ``cwd`` the workspace root,
  ``t_end`` = ``t_start + duration_s``, every universal field null).
  ``seq`` is kept when every exec gives it, else set by start time.
- ``links``: ``facts_helpers``' defaults, plus ``order_t`` = ``t_end``
  on write events.
- ``dispositions``: an exec named by a ``call_exec`` link is
  ``matched_root``, by an ``exec_parent`` link ``nested``, both with
  ``root_under`` true; a noise exec ``noise``, any other ``unrooted``;
  a shell call with a ``call_exec`` link ``matched`` with
  ``match_under`` true, one without ``unmatched`` with reason null.
- ``paths``: ``in_workspace`` true, ``tracked`` true, ``final_state``
  ``changed``, ``unattributed_candidates`` []; ``events`` the path's
  write events by (``order_t``, event number); ``final_writer`` by
  layer 1's rule over them, ``final_writer_under`` its ``writer_under``.
  A row is added for every written path the scenario does not list.
- ``coverage``: ``facts_helpers``' defaults (``inputs`` all true),
  ``integrity.C0.1`` = ``run.trace_ok``, ``capabilities`` the run's.
- ``call_facts``, ``exec_facts``: one row per shell call and per
  non-noise exec, ``outcome`` by OUT1 of the root exec, ``disposition``
  copied, ``depth`` 0 for a root exec and the J2 depth for a nested one,
  ``root_call_id`` from the links, ``duration_s`` and both spans the root
  exec's duration, ``command_shape`` the command, ``numeric_args`` [],
  ``arg_count`` 1; ``facts.preconditions.C1_under`` ``holds``.
- ``call_structure``: one row per shell call, ``program_key`` its
  command. ``file_classes``: one row per ``paths`` row, ``file_class``
  ``other``, ``binary_content`` null without ``final_content``, else
  false.
- ``oracle_inputs``: ``input_id`` ``i<n>`` by order; ``in_workspace``
  and ``input_scope`` from the path (``workspace`` for a workspace
  path); ``pattern`` null; ``match_type`` ``literal`` (``pattern`` when
  a pattern is given); ``listed_by`` []; ``in_under`` true on literal
  rows only (as layer 4's P0 writes it); ``access_types`` [read]; ``checks`` [];
  ``phases`` [experiment_runs]; ``source_type`` ``null_agent``; every
  other field null. Block: ``preconditions.oracle_inputs`` ``holds``
  (``vacuous`` with no row); ``bases.oracle_writes`` by layer 4's D34.
- ``input_states``: one row per non-reference oracle row, completed as
  §2.2.2 says (``final_writer*`` from the path's ``paths`` row, as
  layer 4's LWR4; ``observable`` true; ``late_write`` false; ...).
  ``state_counts.preconditions.oracle_inputs`` is the oracle block's
  label (``fails`` without the block); ``bases.detached`` ``exact``
  when ``parent_links`` holds and every non-noise exec has an
  ``orphans`` list, else ``fallback``.
- ``final_diff``, ``final_files``: null; ``config``: {}.
"""

import copy
import pathlib
import re
import types
from collections.abc import Mapping
from typing import Any

import conftest
import facts_helpers
import pytest
import yaml

from trajectory_checks import (
    facts,
    input_state,
    match,
    oracle_inputs,
    structure,
)

SCENARIOS_DIR = conftest.TESTS_DIR / "writer_scenarios"
DIFF_FIXTURES_DIR = conftest.FIXTURES_DIR / "diff"

# Spec §6: every rule id. LW7 is removed and not reused (D23).
SECTION6_RULES = (
    *("PRE1", "PRE2", "PRE3", "PRE4", "PRE5", "PRE6"),
    *("VIS1", "VIS2", "EX1"),
    *("LW1", "LW2", "LW3", "LW4", "LW5", "LW6", "LW8", "LW9", "LW10"),
    *("LW11", "LW12", "LW13", "LW14", "LW15", "LW16", "LW17"),
    *("RW1", "RW2", "RW3", "RW4", "RW5", "RW6"),
    *("HK1", "HK2", "HK3", "HK4", "HK5", "HK6", "HK7", "HK8", "HK9"),
    *("FLT1", "FLT2", "FLT3"),
    *("PEER1", "PEER2"),
)
# RW7 is descriptive and OUT1 (§6.10) a step: ids, not scenario rules.
RULE_IDS = (*SECTION6_RULES, "RW7", "OUT1")
RETIRED_RULE_IDS = ("LW7",)
SPEC_IDS = (
    *(f"W{n:02d}" for n in range(1, 24)),
    "W23b",
    *(f"W{n:02d}" for n in range(24, 71)),
)
# Spec §2.2.2's capability pairs: (capability, rule, basis key, the
# scenario with the capability true, the one with it false). A basis key
# None is layer 4's (read, not copied into writer.json).
CAPABILITY_PAIRS = (
    ("exact_timing", "LW4", "window", "W43", "W44"),
    ("parent_links", "LW8", "root_certainty", "W45", "W46"),
    ("parent_links", "LW14", "ancestry", "W47", "W48"),
    ("parent_links", "LW9", None, "W57", "W57"),
    ("start_events", "LW9", "unrecorded_risk", "W49", "W12"),
    ("oracle_reads", "LW17", "mtime", "W50", "W51"),
    ("final_content", "LW11", "read_version", "W17", "W52"),
    ("final_content", "HK1", "hunks", "W32", "W33"),
    ("oracle_reads", "LW12", "oracle_writes", "W17", "W18"),
    ("content_hashes", "LW11", "hash_confirms_writer", "W19", "W53"),
    ("read_tracing", "FLT3", "in_read", "W35", "W54"),
    ("untracked_visible", "VIS1", None, "W14", "W13"),
)
# Spec §7's BASES keys and precondition labels, as writer.json has them.
BASES_KEYS = (
    *("window", "root_certainty", "ancestry", "unrecorded_risk"),
    *("read_version", "oracle_writes", "mtime", "hash_confirms_writer"),
    *("hunks", "in_read"),
)
PRECONDITION_LABELS = (
    *("trace", "oracle_inputs", "input_states", "C1_under", "repeats"),
    *("final_diff_content", "file_class", "read_events"),
)
# The capabilities each basis key's exact form needs (§7), for
# invariant 12; final_files is the MC-03 list, not a capability.
BASIS_NEEDS = types.MappingProxyType(
    {
        "window": ("exact_timing",),
        "root_certainty": ("parent_links",),
        "ancestry": ("parent_links",),
        "unrecorded_risk": ("start_events",),
        "read_version": ("oracle_reads", "final_content"),
        "mtime": ("oracle_reads",),
        "hash_confirms_writer": ("content_hashes",),
        "hunks": ("final_content",),
        "in_read": ("read_tracing",),
    }
)
HUNK_TYPES = (
    *("whitespace_only", "comment_only", "comment_toggle", "numeric_only"),
    *("comparison_relaxed", "check_removed", "pure_deletion"),
    *("pure_addition", "general"),
)
RISK_REASONS = ("blind_snapshot", "incomplete", "orphaned", "unrecorded")
NOT_UNDER_REASONS = (
    *("no_writer", "invisible", "input_not_under", "several_candidates"),
    *("root_uncertain", "later_writer_risk", "state_conflict"),
    *("model_conflict", "read_version_differs", "unverified_oracle_written"),
)
WRITTEN_STATES = frozenset(
    {"rewritten_identical", "edit_written", "command_written", "deleted"}
)
CONTENT_OPS = frozenset({"create", "modify"})
EXISTING_STATES = frozenset({"changed", "mode_only", "reverted"})
INPUT_COLUMNS = (
    *("run_key", "input_id", "path", "pattern", "last_writer_type"),
    *("last_writer_role", "last_writer_under", "M3.3.writer_duration_s"),
    *("M3.3.root_span_over_s", "M2.4.rewrites_over"),
)
RATIO_COLUMNS = tuple(f"{c}_peer_ratio" for c in INPUT_COLUMNS[7:])

# Spec §3.4: the fields of each record.
LAST_WRITER_FIELDS = frozenset(
    {
        *("input_id", "path", "pattern", "input_type", "in_workspace"),
        *("in_under", "state_under", "visible", "exists_at_read"),
        *("exists_source", "last_writer", "last_writer_type"),
        *("last_writer_role", "last_writer_op", "last_writer_candidates"),
        *("window_lo", "window_hi", "writer_command_id", "writer_call_id"),
        *("writer_call_seq", "writer_root_certain", "writer_duration_s"),
        *("writer_depth", "writer_outcome", "root_duration_s"),
        *("root_span_under_s", "root_span_over_s", "later_writer_risk"),
        *("root_candidates", "root_candidates_complete", "mtime_candidates"),
        *("state_conflict", "read_version", "differs_causes"),
        *("hash_confirms_writer", "oracle_writes", "oracle_writes_basis"),
        *("writer_named_written", "writer_named_changed"),
        *("member_input_ids", "unattributed_candidates"),
        *("last_writer_under", "not_under"),
    }
)
RISK_FIELDS = frozenset({"id", "item_type", "reason"})
MEMBER_FIELDS = frozenset(
    {
        *("call_id", "seq", "member_type", "events", "first_lo", "last_hi"),
        *("under", "outcome", "command_shape", "numeric_args", "arg_count"),
        "rerun_key",
    }
)
PAIR_FIELDS = frozenset(
    {
        *("prev_call_id", "call_id", "after", "same_key", "writer_switched"),
        *("args_changed", "shape_changed", "under"),
    }
)
REWRITES_FIELDS = frozenset(
    {
        *("input_id", "path", "visible", "members", "pairs", "delete_roots"),
        *("rewrites_under", "rewrites_over", "unowned_execs"),
    }
)
HUNK_FIELDS = frozenset(
    {
        *("path", "file_status", "renamed_from", "file_class", "language"),
        *("hunk_index", "old_start", "new_start", "lines_removed"),
        *("lines_added", "hunk_type", "touches_check", "lexed"),
        *("in_named_under", "in_named_over", "in_read"),
    }
)
WRITER_FIELDS = frozenset(
    {
        *("schema_version", "run_key", "family", "capabilities", "bases"),
        *("preconditions", "metrics", "diff_files_unclassified", "warnings"),
    }
)
# Spec §5.4: the keys of each metric block.
M33_KEYS = frozenset(
    {
        *("inputs", "inputs_visible", "inputs_written_under"),
        *("inputs_written_over", "inputs_not_written_under"),
        *("inputs_not_written_over", "inputs_unknown"),
        *("last_writer_by_role_under", "last_writer_by_role_over"),
        *("writer_duration_min_s_under", "writer_duration_min_s_over"),
        *("writer_duration_max_s_under", "writer_duration_max_s_over"),
        *("root_span_max_s_under", "root_span_max_s_over"),
        *("writer_depth_max_over", "writer_calls_under", "writer_calls_over"),
        *("inputs_later_writer_risk", "inputs_state_conflict"),
        *("inputs_read_version_differs", "inputs_read_version_unverified"),
        "inputs_root_candidates_incomplete",
        "inputs_writer_code_changed_under",
        "inputs_writer_code_changed_over",
    }
)
M24_KEYS = frozenset(
    {
        *("inputs_rewritten_under", "inputs_rewritten_over"),
        *("rewrites_total_under", "rewrites_total_over"),
        *("rewrites_max_under", "rewrites_max_over"),
        *("rewrites_after_ok_under", "rewrites_after_ok_over"),
        *("rewrites_after_failure_under", "rewrites_after_failure_over"),
        *("rewrites_after_other_under", "rewrites_after_other_over"),
        *("writer_switches_under", "writer_switches_over"),
        *("args_changed_under", "args_changed_over"),
        "inputs_with_delete_roots",
    }
)
M43_KEYS = frozenset(
    {
        *("files_classified", "files_binary", "files_omitted"),
        *("hunks_total", "hunks_unlexed", "hunks_touching_check"),
        *("hunks_by_type", "files_by_type", "hunks_by_class_and_type"),
        *("hunks_by_type_named_under", "hunks_by_type_named_over"),
        "hunks_by_type_read",
    }
)
METRIC_KEYS = types.MappingProxyType(
    {"M3.3": M33_KEYS, "M2.4": M24_KEYS, "M4.3": M43_KEYS}
)
# The M2.4 keys `repeats` guards (PRE5) and the M4.3 keys of the
# field-level labels and of FLT2.
REPEATS_KEYS = frozenset(
    {
        *("writer_switches_under", "writer_switches_over"),
        *("args_changed_under", "args_changed_over"),
    }
)
NAMED_KEYS = frozenset(
    {"hunks_by_type_named_under", "hunks_by_type_named_over"}
)
# Keys that are a minimum or a maximum: null over no value (§6.7).
EXTREMA_KEYS = frozenset(
    {
        *("writer_duration_min_s_under", "writer_duration_min_s_over"),
        *("writer_duration_max_s_under", "writer_duration_max_s_over"),
        *("root_span_max_s_under", "root_span_max_s_over"),
        *("writer_depth_max_over", "rewrites_max_under", "rewrites_max_over"),
    }
)

TOP_KEYS = frozenset(
    {
        *("id", "title", "description", "xfail", "rules", "run", "calls"),
        *("execs", "links", "dispositions", "paths", "coverage"),
        *("call_facts", "exec_facts", "facts", "structure"),
        *("call_structure", "file_classes", "oracle_inputs"),
        *("oracle_inputs_block", "input_states", "state_counts"),
        *("final_diff", "final_files", "config", "runs", "expect"),
    }
)
EXPECT_KEYS = frozenset(
    {
        *("last_writers", "input_rewrites", "hunks", "row_counts", "bases"),
        *("preconditions", "capabilities", "metrics", "metrics_null"),
        *("diff_files_unclassified", "warnings", "peer_ratios"),
        "run_peer_ratios",
    }
)
DEFAULT_RUN_EXTRA = types.MappingProxyType(
    {"t_end": 1000.0, "run_key": "art__claude__1", "family": "claude"}
)
FINAL_FILE_KEYS = (
    *("path", "status", "size", "sha256", "in_diff", "reason_omitted"),
)
# Fields each true capability implies somewhere in the records (record,
# fields); a false capability's fields are set nowhere (as layer 1's
# cli.capabilities derives them from field presence).
IMPLIED_FIELDS = types.MappingProxyType(
    {
        "parent_links": (
            "exec",
            ("parent_command_id", "root_command_id", "depth", "orphans"),
        ),
        "exact_timing": ("exec", ("ended_at", "released_at")),
        "content_hashes": ("snapshot", ("hashes",)),
        "read_tracing": ("exec", ("reads",)),
        "start_events": ("exec", ("record_type",)),
    }
)


def scenario_paths() -> list[pathlib.Path]:
    """Lists the writer scenario files in name order.

    Returns:
        Sorted paths of ``writer_scenarios/*.yaml``.
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


def event_number(event_id: str) -> int:
    """The number of a ``w<n>`` event id.

    Args:
        event_id: A write event id.

    Returns:
        Its number.
    """
    return int(event_id[1:])


def input_number(input_id: str) -> int:
    """The number of an ``i<n>`` input id.

    Args:
        input_id: An oracle-input id.

    Returns:
        Its number.
    """
    return int(input_id[1:])


def _merge(target: dict[str, Any], overrides: Mapping[str, Any]) -> None:
    for key, value in overrides.items():
        facts_helpers._set_dotted(target, key, copy.deepcopy(value))


def _outcome(record: Mapping[str, Any] | None) -> str:
    # Layer 2's OUT1, as its spec states it.
    if record is None or (
        record["signal"] is None and record["exit_code"] is None
    ):
        return "unknown"
    if record["signal"] is not None:
        return "killed"
    return "ok" if record["exit_code"] == 0 else "failed"


def _dispositions(
    calls: list[dict[str, Any]],
    execs: list[dict[str, Any]],
    links: list[dict[str, Any]],
    overrides: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    j1 = {lk["call_id"] for lk in links if lk["link_type"] == "call_exec"}
    roots = {lk["command_id"] for lk in links if lk["link_type"] == "call_exec"}
    nested = {
        lk["command_id"] for lk in links if lk["link_type"] == "exec_parent"
    }
    rows = []
    for call in sorted(calls, key=lambda c: c["seq"]):
        if call["tool_type"] != "shell":
            continue
        row = facts_helpers._row(
            "call",
            call["call_id"],
            "matched" if call["call_id"] in j1 else "unmatched",
        )
        row["matched"] = call["call_id"] in j1
        row["match_under"] = call["call_id"] in j1
        rows.append(row)
    for record in sorted(execs, key=lambda e: e["seq"]):
        command_id = record["command_id"]
        if record["noise"]:
            disposition = "noise"
        elif command_id in roots:
            disposition = "matched_root"
        elif command_id in nested:
            disposition = "nested"
        else:
            disposition = "unrooted"
        row = facts_helpers._row("exec", command_id, disposition)
        row["snapshot_ok"] = record["snapshot"]["status"] == "ok"
        row["root_under"] = disposition in ("matched_root", "nested")
        rows.append(row)
    for row in rows:
        row.update(copy.deepcopy(dict(overrides.get(row["id"]) or {})))
    return rows


def layer1_final_writer(
    events: list[Mapping[str, Any]], final_state: str | None
) -> Mapping[str, Any] | None:
    """Layer 1's final-writer rule (its D32 and D34), for the defaults.

    Args:
        events: The path's write events.
        final_state: The path's final state.

    Returns:
        The final writer's event, or None.
    """
    ordered = sorted(
        events, key=lambda w: (w["order_t"], event_number(w["event_id"]))
    )
    live = [
        e
        for e in ordered
        if final_state not in EXISTING_STATES or e["op"] != "delete"
    ]
    pick = [e for e in live if e["possibly_edit_call"] is None] or live
    return pick[-1] if pick else None


def _paths(
    links: list[dict[str, Any]], raw: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    events: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        if link["link_type"] == "write_event":
            events.setdefault(link["path"], []).append(link)
    listed = {item["path"]: item for item in raw}
    order = [item["path"] for item in raw]
    order += sorted(set(events) - set(listed))
    rows = []
    for path in order:
        given = copy.deepcopy(dict(listed.get(path) or {}))
        found = sorted(
            events.get(path, []),
            key=lambda w: (w["order_t"], event_number(w["event_id"])),
        )
        state = given.get("final_state", "changed")
        last = layer1_final_writer(found, state)
        row = {
            "path": path,
            "in_workspace": True,
            "tracked": True,
            "events": [w["event_id"] for w in found],
            "final_writer": last["event_id"] if last else None,
            "final_writer_under": bool(last and last["writer_under"]),
            "final_state": state,
            "unattributed_candidates": [],
        }
        row.update(given)
        rows.append(row)
    return rows


def _roots(links: list[dict[str, Any]]) -> dict[str, str | None]:
    root_of: dict[str, str | None] = {}
    for link in links:
        if link["link_type"] == "call_exec":
            root_of[link["command_id"]] = link["call_id"]
        elif link["link_type"] == "exec_parent":
            root_of[link["command_id"]] = link["root_call_id"]
    return root_of


def _depth(command_id: str, parents: Mapping[str, str | None]) -> int:
    depth, seen = 0, set()
    while command_id in parents and command_id not in seen:
        seen.add(command_id)
        depth += 1
        if parents[command_id] is None:
            break
        command_id = parents[command_id]
    return depth


def _layer2(
    raw: Mapping[str, Any],
    run: Mapping[str, Any],
    calls: list[dict[str, Any]],
    execs: list[dict[str, Any]],
    linked: Mapping[str, Any],
) -> dict[str, Any]:
    links = linked["links"]
    exec_by_id = {e["command_id"]: e for e in execs}
    j1 = {
        lk["call_id"]: lk["command_id"]
        for lk in links
        if lk["link_type"] == "call_exec"
    }
    parents = {
        lk["command_id"]: lk["parent"]
        for lk in links
        if lk["link_type"] == "exec_parent"
    }
    root_of = _roots(links)
    disposition = {
        d["id"]: d for d in linked["dispositions"] if d["item_type"] == "exec"
    }
    call_rows = []
    for call in sorted(calls, key=lambda c: c["seq"]):
        if call["tool_type"] != "shell":
            continue
        record = exec_by_id.get(j1.get(call["call_id"], ""))
        duration = record["duration_s"] if record else None
        row: dict[str, Any] = dict.fromkeys(facts.CallFacts.__annotations__)
        row.update(
            call_id=call["call_id"],
            seq=call["seq"],
            command_id=record["command_id"] if record else None,
            match_rule="exact" if record else None,
            match_under=record is not None,
            t_start=call["t_start"],
            t_end=call["t_end"],
            duration_s=duration,
            complete=record["complete"] if record else None,
            span_under_s=duration,
            span_over_s=duration,
            exit_code=record["exit_code"] if record else None,
            signal=record["signal"] if record else None,
            outcome=_outcome(record),
            command_shape=call["command"],
            numeric_args=[],
            arg_count=1,
            programs=[],
            descendants_under=0,
            descendants_over=0,
            writes_under={},
            writes_over={},
            network_types_under=[],
            network_types_over=[],
            env_assigned=[],
        )
        call_rows.append(row)
    exec_rows = []
    for record in sorted(execs, key=lambda e: e["seq"]):
        if record["noise"]:
            continue
        found = disposition[record["command_id"]]
        name = found["disposition"]
        exec_rows.append(
            {
                "command_id": record["command_id"],
                "seq": record["seq"],
                "disposition": name,
                "root_call_id": root_of.get(record["command_id"]),
                "root_under": bool(found["root_under"]),
                "depth": _depth(record["command_id"], parents)
                if name in ("matched_root", "nested")
                else None,
                "outcome": _outcome(record),
                "exit_signal": None,
                "network_types": [],
                "env_added": None,
            }
        )
    call_rows = _merged(call_rows, raw.get("call_facts") or [], "call_id")
    exec_rows = _merged(exec_rows, raw.get("exec_facts") or [], "command_id")
    run_facts = {
        "schema_version": "1",
        "run_key": run["run_key"],
        "family": run["family"],
        "capabilities": dict(run["capabilities"]),
        "bases": {},
        "preconditions": {
            "C1_under": "holds",
            "C2_under": "holds",
            "C6": "holds",
            "C7_under": "holds",
        },
        "metrics": {},
        "warnings": [],
    }
    _merge(run_facts, raw.get("facts") or {})
    return {
        "call_facts": call_rows,
        "exec_facts": exec_rows,
        "path_facts": [],
        "facts": run_facts,
    }


def _merged(
    rows: list[dict[str, Any]], given: list[Mapping[str, Any]], key: str
) -> list[dict[str, Any]]:
    index = {row[key]: row for row in rows}
    for item in given:
        if item[key] in index:
            index[item[key]].update(copy.deepcopy(dict(item)))
        else:
            row = copy.deepcopy(dict(item))
            rows.append(row)
            index[item[key]] = row
    return rows


def _layer3(
    raw: Mapping[str, Any],
    run: Mapping[str, Any],
    calls: list[dict[str, Any]],
    paths: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if raw.get("structure") == "absent":
        return None
    rows = []
    for call in sorted(calls, key=lambda c: c["seq"]):
        if call["tool_type"] != "shell":
            continue
        row: dict[str, Any] = dict.fromkeys(
            structure.CallStructure.__annotations__
        )
        row.update(
            call_id=call["call_id"],
            seq=call["seq"],
            call_class="act",
            class_reason="program",
            program_key=call["command"],
        )
        rows.append(row)
    rows = _merged(rows, raw.get("call_structure") or [], "call_id")
    binary = False if run["capabilities"]["final_content"] else None
    classes = [
        {
            "path": p["path"],
            "in_workspace": p["in_workspace"],
            "extension": pathlib.PurePosixPath(p["path"]).suffix.lower(),
            "file_class": "other",
            "class_rule": "FC11",
            "mode_source": None,
            "binary_content": binary,
        }
        for p in paths
    ]
    classes = _merged(classes, raw.get("file_classes") or [], "path")
    return {
        "call_structure": rows,
        "reruns": [],
        "file_classes": classes,
        "structure": {
            "schema_version": "1",
            "run_key": run["run_key"],
            "family": run["family"],
            "preconditions": {},
            "capabilities": dict(run["capabilities"]),
            "bases": {},
            "metrics": {},
            "warnings": [],
        },
    }


def _oracle_row(
    given: Mapping[str, Any], index: int, root: str
) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(
        oracle_inputs.OracleInput.__annotations__
    )
    path = given["path"]
    inside = match.relative_path(path, root)[1]
    match_type = given.get(
        "match_type", "pattern" if given.get("pattern") else "literal"
    )
    row.update(
        input_id=f"i{index}",
        path=path,
        in_workspace=inside,
        input_scope="workspace" if inside else "outside_workspace",
        pattern=None,
        match_type=match_type,
        listed_by=[],
        in_under=match_type == "literal",
        access_types=["read"],
        checks=[],
        phases=["experiment_runs"],
        source_type="null_agent",
    )
    row.update(copy.deepcopy(dict(given)))
    return row


def writes_basis(rows: list[Mapping[str, Any]], capability_exact: bool) -> str:
    """Layer 4's D34 for bases.oracle_writes, as its spec states it.

    Args:
        rows: The §0 rows.
        capability_exact: Whether OI10's exact form applies to the run.

    Returns:
        ``exact``, ``fallback`` or ``mixed``.
    """
    found = {
        r["oracle_writes_basis"] for r in rows if r["oracle_writes"] is not None
    }
    if len(found) > 1:
        return "mixed"
    if found:
        return found.pop()
    return "exact" if capability_exact else "fallback"


def _oracle_block(
    run: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    exact: bool,
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    caps = run["capabilities"]
    block = {
        "schema_version": "1",
        "run_key": run["run_key"],
        "family": run["family"],
        "capabilities": dict(caps),
        "bases": {
            "source": "exact" if caps["oracle_reads"] else "fallback",
            "pattern_matches": "fallback",
            "oracle_writes": writes_basis(rows, exact),
        },
        "preconditions": {
            "oracle_inputs": "holds" if rows else "vacuous",
            "null_commit": "holds",
            "null_score": "holds",
            "run_score": "holds",
        },
        "metrics": {"M3.1": {}, "M3.5": {}},
        "warnings": [],
    }
    _merge(block, overrides)
    return block


def _layer4(
    raw: Mapping[str, Any],
    run: Mapping[str, Any],
    execs: list[dict[str, Any]],
    linked: Mapping[str, Any],
    final_files: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    caps = run["capabilities"]
    root = run["workspace_root"]
    oracle = None
    if raw.get("oracle_inputs") != "absent":
        rows = [
            _oracle_row(r, i, root)
            for i, r in enumerate(raw.get("oracle_inputs") or [])
        ]
        exact = bool(
            caps["oracle_reads"]
            and caps["final_content"]
            and final_files is not None
        )
        oracle = {
            "oracle_inputs": rows,
            "oracle_inputs_run": _oracle_block(
                run, rows, exact, raw.get("oracle_inputs_block") or {}
            ),
        }
    if raw.get("input_states") == "absent":
        return oracle, None
    sources = (oracle or {}).get("oracle_inputs") or []
    paths = {p["path"]: p for p in linked["paths"]}
    events = {
        lk["event_id"]: lk
        for lk in linked["links"]
        if lk["link_type"] == "write_event"
    }
    given = {r["input_id"]: r for r in raw.get("input_states") or []}
    rows = []
    for source in sources:
        if source["input_scope"] == "reference":
            continue
        listing = source["match_type"] == "pattern"
        summary = None if listing else paths.get(source["path"])
        last = events.get(summary["final_writer"] or "") if summary else None
        row: dict[str, Any] = dict.fromkeys(
            input_state.InputState.__annotations__
        )
        row.update(
            path=source["path"],
            input_id=source["input_id"],
            input_type="listing" if listing else "file",
            input_scope=source["input_scope"],
            pattern=source["pattern"],
            in_under=source["in_under"],
            source_type=source["source_type"],
            access_types=list(source["access_types"]),
            phases=list(source["phases"]),
            check_ids=list(source["checks"]),
            member_of=[],
            members=[],
            tracked=summary["tracked"] if summary else None,
            content_sources_disagree=False,
            oracle_writes=source["oracle_writes"],
            events=list(summary["events"]) if summary else [],
            edit_events=[
                e
                for e in (summary["events"] if summary else [])
                if events[e]["writer_type"] == "edit_call"
            ],
            writer_roots=[],
            final_writer=last["event_id"] if last else None,
            final_writer_type=last["writer_type"] if last else None,
            final_writer_id=last["writer_id"] if last else None,
            final_writer_root=last["root_call_id"] if last else None,
            final_writer_under=bool(summary and summary["final_writer_under"]),
            observable=True,
            blind_execs=[],
            unmonitored_calls=[],
            detached_execs=[],
            unseen_writer_possible=False,
            states_under=[],
            states_over=[],
            model_conflict=False,
            origins_over=[],
            predicates_under={},
            predicates_over={},
            late_write=False,
        )
        row.update(copy.deepcopy(dict(given.get(source["input_id"]) or {})))
        rows.append(row)
    live = [e for e in execs if e["noise"] is None]
    detached = (
        "exact"
        if caps["parent_links"]
        and all(isinstance(e["orphans"], list) for e in live)
        else "fallback"
    )
    label = (
        oracle["oracle_inputs_run"]["preconditions"]["oracle_inputs"]
        if oracle is not None
        else "fails"
    )
    counts = {
        "schema_version": "1",
        "run_key": run["run_key"],
        "family": run["family"],
        "capabilities": dict(caps),
        "bases": {
            "content": "exact" if caps["oracle_reads"] else "fallback",
            "detached": detached,
            "unmonitored": "exact" if caps["start_events"] else "fallback",
        },
        "preconditions": {
            "oracle_inputs": label,
            "log": "holds",
            "trace": "holds" if run["trace_ok"] else "fails",
            "content": "holds",
        },
        "metrics": {"M3.2": {}},
        "warnings": [],
    }
    _merge(counts, raw.get("state_counts") or {})
    return oracle, {"input_states": rows, "state_counts": counts}


def _final_files(
    raw: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    entries = []
    for item in raw:
        entry: dict[str, Any] = dict.fromkeys(FINAL_FILE_KEYS)
        entry.update(status="M", in_diff=True)
        entry.update(copy.deepcopy(dict(item)))
        entries.append(entry)
    return entries


def build_scenario(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Completes a raw scenario mapping with every default.

    Args:
        raw: A scenario as written (``read_raw``), possibly transformed,
            without ``runs``.

    Returns:
        A dict with ``id``, ``title``, ``rules``, ``raw``, ``run``,
        ``calls``, ``execs``, ``linked``, ``facts_result``,
        ``structure_result``, ``oracle_inputs``, ``input_states``,
        ``final_diff_text``, ``final_files``, ``config`` and ``expect``.
    """
    raw = copy.deepcopy(dict(raw))
    run = copy.deepcopy(dict(conftest.DEFAULT_RUN))
    run.update(DEFAULT_RUN_EXTRA)
    given = dict(raw.get("run") or {})
    run["capabilities"].update(given.pop("capabilities", None) or {})
    run.update(given)
    for key in ("t_start", "t_end"):
        if isinstance(run.get(key), int):
            run[key] = float(run[key])
    root = run["workspace_root"]
    calls = []
    for index, item in enumerate(raw.get("calls") or []):
        item = dict(item)
        if "t_end" not in item and item.get("t_start") is not None:
            item["t_end"] = item["t_start"]
        calls.append(conftest.make_call(item, index))
    plain = list(raw.get("execs") or [])
    execs = [conftest.make_exec(e, i, root) for i, e in enumerate(plain)]
    if not all("seq" in e for e in plain):
        conftest.assign_exec_seq(execs)
    links = facts_helpers._links(list(raw.get("links") or []), calls, execs)
    for link in links:
        if link["link_type"] == "write_event":
            link.setdefault("order_t", link["t_end"])
            for key in ("t_start", "t_end", "order_t"):
                if isinstance(link[key], int):
                    link[key] = float(link[key])
    dispositions = _dispositions(
        calls, execs, links, raw.get("dispositions") or {}
    )
    paths = _paths(links, raw.get("paths") or [])
    coverage = facts_helpers._coverage(
        calls,
        execs,
        links,
        dispositions,
        paths,
        run,
        {
            "integrity.C0.1": bool(run["trace_ok"]),
            **dict(raw.get("coverage") or {}),
        },
    )
    linked = {
        "links": links,
        "dispositions": dispositions,
        "paths": paths,
        "coverage": coverage,
    }
    final_files = _final_files(raw.get("final_files"))
    oracle, states = _layer4(raw, run, execs, linked, final_files)
    return {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "rules": list(raw.get("rules") or []),
        "raw": raw,
        "run": run,
        "calls": calls,
        "execs": execs,
        "linked": linked,
        "facts_result": _layer2(raw, run, calls, execs, linked),
        "structure_result": _layer3(raw, run, calls, paths),
        "oracle_inputs": oracle,
        "input_states": states,
        "final_diff_text": raw.get("final_diff"),
        "final_files": final_files,
        "config": copy.deepcopy(raw.get("config") or {}),
        "expect": raw.get("expect") or {},
    }


def run_raws(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The raw scenario of each run of a scenario.

    Args:
        raw: A scenario as written.

    Returns:
        One raw mapping per item of ``runs`` (each a top-level override
        of the scenario's keys), or the scenario itself without ``runs``.
    """
    base = {k: v for k, v in raw.items() if k != "runs"}
    if not raw.get("runs"):
        return [base]
    return [{**base, **copy.deepcopy(dict(item))} for item in raw["runs"]]


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads one scenario file (its first run) and fills in every default.

    Args:
        path: A scenario file.

    Returns:
        The value of ``build_scenario`` on the file's (first run's)
        content.
    """
    return build_scenario(run_raws(read_raw(path))[0])


def load_runs(path: pathlib.Path) -> list[dict[str, Any]]:
    """Loads every run of a scenario file.

    Args:
        path: A scenario file.

    Returns:
        One ``build_scenario`` value per run.
    """
    return [build_scenario(r) for r in run_raws(read_raw(path))]


DERIVE_KEYS = (
    *("run", "oracle_inputs", "input_states", "final_diff_text"),
    *("final_files", "config"),
)


def run_derive(scenario: Mapping[str, Any], **overrides: Any) -> Any:
    """Calls ``writer.derive`` on a loaded scenario.

    Args:
        scenario: A dict from ``build_scenario``.
        **overrides: Replacements for ``calls``, ``execs``, ``linked``,
            ``facts_result``, ``structure_result`` or any keyword argument
            of ``derive``.

    Returns:
        The result of ``writer.derive``.
    """
    from trajectory_checks import writer  # late: D41

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
    return writer.derive(
        args.pop("calls"),
        args.pop("execs"),
        args.pop("linked"),
        args.pop("facts_result"),
        args.pop("structure_result"),
        **args,
    )


def events_of(scenario: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The write events of a loaded scenario by event id.

    Args:
        scenario: A dict from ``build_scenario``.

    Returns:
        Event id -> write event.
    """
    return {
        lk["event_id"]: lk
        for lk in scenario["linked"]["links"]
        if lk["link_type"] == "write_event"
    }


def _field_present(scenario: Mapping[str, Any], name: str) -> bool:
    record_type, fields = IMPLIED_FIELDS[name]
    for record in scenario["execs"]:
        holder = record["snapshot"] if record_type == "snapshot" else record
        if any(holder.get(f) is not None for f in fields):
            return True
    return False


def validate(scenario: Mapping[str, Any], stem: str) -> list[str]:
    """Checks that a loaded scenario is internally consistent.

    Args:
        scenario: A dict from ``build_scenario``.
        stem: The file stem the scenario was read from.

    Returns:
        One message per problem; empty when consistent.
    """
    problems = []
    raw = scenario["raw"]
    if not re.fullmatch(r"W\d{2}b?_[a-z0-9_]+", stem):
        problems.append(f"file stem {stem!r} is not W<nn>[b]_<snake_case>")
    if scenario["id"] != stem.split("_", 1)[0]:
        problems.append(f"id {scenario['id']!r} does not match {stem!r}")
    if not scenario["title"]:
        problems.append("no title")
    if set(raw) - TOP_KEYS:
        problems.append(f"unknown top-level keys {sorted(set(raw) - TOP_KEYS)}")
    if set(scenario["expect"]) - EXPECT_KEYS:
        problems.append(
            "unknown expect keys "
            f"{sorted(set(scenario['expect']) - EXPECT_KEYS)}"
        )
    rules = scenario["rules"]
    if not rules:
        problems.append("no rules cited")
    if set(rules) - set(RULE_IDS):
        problems.append(
            f"unknown rule ids {sorted(set(rules) - set(RULE_IDS))}"
        )
    if len(set(rules)) != len(rules):
        problems.append("a rule id is cited twice")
    call_ids = [c["call_id"] for c in scenario["calls"]]
    exec_ids = [e["command_id"] for e in scenario["execs"]]
    if len(set(call_ids)) != len(call_ids):
        problems.append("duplicate call_id")
    if len(set(exec_ids)) != len(exec_ids):
        problems.append("duplicate command_id")
    events = events_of(scenario)
    ordered = sorted(events.values(), key=lambda w: event_number(w["event_id"]))
    for one, two in zip(ordered, ordered[1:], strict=False):
        if one["order_t"] > two["order_t"]:
            problems.append(
                f"event ids not in order_t order: {one['event_id']}, "
                f"{two['event_id']}"
            )
    for link in scenario["linked"]["links"]:
        kind = link["link_type"]
        if kind == "call_exec" and (
            link["call_id"] not in call_ids
            or link["command_id"] not in exec_ids
        ):
            problems.append(f"call_exec link to an unknown id: {link}")
        if kind == "exec_parent" and (
            link["command_id"] not in exec_ids
            or (link["parent"] is not None and link["parent"] not in exec_ids)
        ):
            problems.append(f"exec_parent link to an unknown id: {link}")
        if kind == "write_event":
            if link["writer_id"] not in set(call_ids) | set(exec_ids):
                problems.append(f"write event of an unknown writer: {link}")
            if link["op"] not in ("create", "modify", "delete", "mode"):
                problems.append(f"write event op {link['op']!r}")
    for summary in scenario["linked"]["paths"]:
        if set(summary["events"]) - set(events):
            problems.append(f"paths row {summary['path']}: unknown events")
        if summary["final_writer"] is not None and (
            summary["final_writer"] not in summary["events"]
        ):
            problems.append(f"paths row {summary['path']}: final writer")
        for event_id in summary["events"]:
            if (
                event_id in events
                and events[event_id]["path"] != summary["path"]
            ):
                problems.append(f"paths row {summary['path']}: {event_id}")
        found = [events[e] for e in summary["events"] if e in events]
        rule = layer1_final_writer(found, summary["final_state"])
        if (rule["event_id"] if rule else None) != summary["final_writer"]:
            problems.append(
                f"paths row {summary['path']}: final_writer is not layer 1's"
            )
    for record in scenario["execs"]:
        if record["t_end"] < record["t_start"]:
            problems.append(f"exec {record['command_id']} ends before start")
        if set(record) - conftest.EXEC_FIELDS:
            problems.append(f"exec {record['command_id']}: unknown fields")
    capabilities = scenario["run"]["capabilities"]
    if set(capabilities) != set(conftest.CAPABILITIES):
        problems.append("run capabilities are not the nine of layer 1")
    for name in IMPLIED_FIELDS:
        present = _field_present(scenario, name)
        if present and not capabilities[name]:
            problems.append(f"{name} false but its fields are set")
    oracle = scenario["oracle_inputs"]
    rows = (oracle or {}).get("oracle_inputs") or []
    ids = [row["input_id"] for row in rows]
    if len(set(ids)) != len(ids):
        problems.append("duplicate input_id")
    if (
        any(r["source_type"] == "graded" for r in rows)
        and not capabilities["oracle_reads"]
    ):
        problems.append("a graded row without capability oracle_reads")
    for row in rows:
        if set(row) != set(oracle_inputs.OracleInput.__annotations__):
            problems.append(f"§0 row {row['input_id']}: fields")
        if set(row["listed_by"]) - set(ids):
            problems.append(f"§0 row {row['input_id']}: listed_by")
    states = (scenario["input_states"] or {}).get("input_states") or []
    listed = scenario["raw"].get("input_states")
    given = [r["input_id"] for r in listed] if isinstance(listed, list) else []
    if set(given) - set(ids):
        problems.append(
            "input_states rows of unknown inputs "
            f"{sorted(set(given) - set(ids))}"
        )
    by_id = {r["input_id"]: r for r in rows}
    paths = {p["path"]: p for p in scenario["linked"]["paths"]}
    for state in states:
        source = by_id.get(state["input_id"])
        if source is None:
            continue
        if (state["path"], state["pattern"]) != (
            source["path"],
            source["pattern"],
        ):
            problems.append(f"input_states {state['input_id']}: path")
        if state["input_type"] == "file":
            summary = paths.get(state["path"])
            writer = summary["final_writer"] if summary else None
            if state["final_writer"] != writer:
                problems.append(
                    f"input_states {state['input_id']}: final_writer is not "
                    "the paths row's"
                )
    for item in (scenario["structure_result"] or {}).get("file_classes") or []:
        if item["path"] not in paths:
            problems.append(f"file_classes row without a paths row: {item}")
        if item["file_class"] not in structure.FILE_CLASSES:
            problems.append(f"file class {item['file_class']!r}")
    if (
        scenario["final_diff_text"] is not None
        and not capabilities["final_content"]
    ):
        problems.append("a final diff without capability final_content")
    return problems
