"""Scenario loader and defaults for the input-state tests (layer 4, P1-P6).

Input-state scenarios (``tests/input_state_scenarios/*.yaml``) are
written directly as layer-1 output records, §0 rows
(``oracle_inputs.jsonl``) and run-folder contents (provenance spec
``trajectory-provenance-spec.md`` §2.2.2). Neither ``match.link`` nor
``oracle_inputs.derive`` is run, so a layer-4 test cannot fail because of
a change below it.

This module imports nothing from ``trajectory_checks.input_state`` or
``trajectory_checks.oracle_inputs``: it is loaded by
``test_input_state_rules.py``, which must pass before either exists (D44).

Scenario file layout::

    name: P04_command_wrote_tracked # equal to the file stem
    description: one line or a folded block
    xfail: "reason"                 # optional: test_scenario is marked
                                    # xfail(strict=True) with this reason
    rules: [EVD3, LWR4]             # rule ids exercised (spec §6, §7, §6.3)
    capabilities: {...}             # optional, merged into run.capabilities
    run: {...}                      # optional RunInfo overrides
    calls: [...]                    # partial Call records (conftest)
    execs: [...]                    # partial Exec records (conftest); the
                                    # helper-only key ``call: <call_id>``
                                    # adds a call_exec link (rule exact)
    links: [...]                    # other layer-1 links (facts_helpers
                                    # defaults; write events also get
                                    # ``order_t`` = ``t_end``)
    dispositions: {id: {...}}       # optional, merged into derived rows
    paths: [...]                    # optional partial paths.jsonl rows
    coverage: {dotted.key: value}   # optional coverage.json overrides
    baseline: {commit, files: {path: {sha256}}}  # optional
    final_diff: {modified, deleted, mode_changed} # optional
    final_files: [{path, status, sha256}]         # optional (MC-03)
    oracle_inputs: [{...}, ...]     # §0 rows; absent -> not passed
    oracle_inputs_block: {dotted.key: value}      # oracle_inputs.json
    phase_status: {phase: status}   # optional; null -> not passed
    config: {...}                   # optional, passed to classify
    expect:
      input_states: {path: {...}}   # partial rows; a path with a file and
                                    # a listing row is keyed path|file or
                                    # path|listing
      absent: [path, ...]           # paths with no row
      counts: {dotted.key: value}   # keys of state_counts.json; a key led
                                    # by M3.2 is looked up under "metrics"
      warnings: [substring, ...]    # each in exactly one warning
      warnings_exact: true          # optional: no other warning

Defaults (spec §2.2.2):

- ``run``: ``conftest.DEFAULT_RUN`` (``t_start`` 0, ``t_end`` 10000,
  workspace ``/repo``, ``trace_ok`` true), ``run_key`` ``art__agent__1``,
  ``family`` ``claude``; every universal field None; every capability
  false. ``RunInfo.oracle_reads`` is a path exactly when the capability
  holds.
- ``calls``, ``execs``, ``links``, ``dispositions``, ``paths``,
  ``coverage``: ``facts_helpers``' defaults unchanged, plus ``order_t`` =
  ``t_end`` on write events; snapshot scope ``tracked``,
  ``rates.untracked_visible`` false. Added here, overridable:
  ``integrity.C0.1`` is ``run.trace_ok``, ``inputs.tracked_files`` and
  ``inputs.final_diff`` whether a baseline and a final diff are given.
- ``baseline``: a file without ``sha256`` gets ``"00"``; ``commit`` c0.
- ``final_diff``: missing lists are [].
- ``final_files``: completed as in ``oracle_inputs_helpers``.
- ``oracle_inputs``: rows completed to §0.3: ``input_id`` ``i<n>`` by
  order, ``in_workspace`` and ``input_scope`` from the path,
  ``match_type`` ``literal`` (``pattern`` when a ``pattern`` is given),
  ``listed_by`` [], ``in_under`` true on literal rows, ``source_type``
  ``null_agent``, ``tracked_at_baseline`` from the baseline,
  ``oracle_writes`` false with basis ``exact`` on literal rows (null on
  the others), every other scalar null. List fields (D58):
  ``access_types`` ["read"] (["list"] on pattern and pattern_match rows),
  ``checks`` ["c"], ``phases`` ["experiment_runs"].
- ``oracle_inputs_block``: a full oracle_inputs.json with every
  precondition ``holds`` (``oracle_inputs`` ``vacuous`` with no row) and
  ``M3.1.phases_complete`` every phase of the rows -> true.
- ``phase_status``: every phase of the rows -> ``success``.
"""

import copy
import pathlib
import re
import types
from collections.abc import Mapping
from typing import Any

import conftest
import facts_helpers
import oracle_inputs_helpers
import pytest
import structure_helpers
import yaml

from trajectory_checks import match

SCENARIOS_DIR = conftest.TESTS_DIR / "input_state_scenarios"

# Every rule id of spec §6 (P2-P5), §7 (PRE1-PRE4) and the assumptions of
# §6.3 (S1-S5, cited by the model-based test). CON10 is reserved and the
# LWR ids other than LWR4 are withdrawn or moved: none is a rule id (D45).
SECTION6_RULES = (
    *("INP1", "INP2", "INP3", "INP4"),
    *("EVD1", "EVD2", "EVD3", "EVD4", "EVD5", "EVD6", "EVD7", "EVD8"),
    *("CON1", "CON2", "CON3", "CON4", "CON5", "CON6", "CON7", "CON8"),
    *("CON9", "CON11", "CON12", "CON13", "CON14", "CON15"),
    "LWR4",
    *("CNT1", "CNT2", "CNT3", "CNT4", "CNT5", "CNT6"),
    *("PHS1", "PHS2", "PHS3"),
)
RULE_IDS = (
    *SECTION6_RULES,
    *("PRE1", "PRE2", "PRE3", "PRE4"),
    *("S1", "S2", "S3", "S4", "S5"),
)
RETIRED_RULE_IDS = (*("LWR1", "LWR2", "LWR3", "LWR5", "LWR6", "LWR7", "CON10"),)

# Spec §6.9: (basis key, condition, rule). Each row needs a scenario
# citing the rule with the condition true and one with it false (†).
CAPABILITY_RULES = (
    ("content", "content_exact", "EVD2"),
    ("observable", "untracked_visible", "EVD6"),
    ("shim", "shim_known", "EVD6"),
    ("unmonitored", "start_events", "EVD7"),
    ("detached", "parent_links", "EVD8"),
    ("content_hash", "content_hashes", "CON7"),
    ("timing", "exact_timing", "EVD7"),
    ("oracle_writes", "exact_writes", "EVD4"),
)
BASES_KEYS = frozenset(
    {
        *("content_hash", "detached", "observable", "shim", "timing"),
        "unmonitored",
    }
)
PRECONDITION_LABELS = ("oracle_inputs", "log", "trace", "content")

STATES = (
    *("pristine", "rewritten_identical", "edit_written", "command_written"),
    *("oracle_written", "deleted", "missing"),
)
PREDICATE_NAMES = (
    *("baseline_content", "fresh", "agent_written", "edit_written"),
    *("command_written", "absent"),
)
UNDER_REASONS = (
    *("model_conflict", "no_content_evidence", "writers_unobserved"),
    *("no_baseline_evidence", "final_writer_uncertain", "underdetermined"),
)
PHASE_FLAGS = (
    *("status", "inputs", "inputs_complete", "baseline_content_all_under"),
    *("baseline_content_all_over", "agent_written_any_under"),
    "agent_written_any_over",
)
# Spec §3.4: the 47 fields of InputState.
ROW_FIELDS = frozenset(
    {
        *("path", "input_id", "input_type", "input_scope", "pattern"),
        *("in_under", "source_type", "access_types", "phases", "check_ids"),
        *("member_of", "members", "tracked", "baseline_exists"),
        *("baseline_sha256", "baseline_source", "grading_exists"),
        *("grading_sha256", "content_cmp", "content_source"),
        *("content_sources_disagree", "oracle_writes", "events"),
        *("edit_events", "writer_roots", "final_writer", "final_writer_type"),
        *("final_writer_id", "final_writer_root", "final_writer_under"),
        *("observable", "blind_execs", "unmonitored_calls", "detached_execs"),
        *("unseen_writer_possible", "late_write", "rewrite_type"),
        "states_under",
        *("states_over", "state_under", "under_reason", "model_conflict"),
        *("origin_under", "origins_over", "predicates_under"),
        *("predicates_over", "listing_changed_under", "listing_changed_over"),
    }
)
# Spec §5.2: the keys of the M3.2 block.
M32_KEYS = frozenset(
    {
        *("oracle_inputs", "oracle_inputs_under", "listing_inputs"),
        *("reference_inputs_excluded", "by_state_under", "state_unknown_under"),
        "by_state_over",
        *(f"{p}_{v}" for p in PREDICATE_NAMES for v in ("under", "over")),
        *("created_by_agent_under", "created_by_agent_over"),
        *("written_unseen_under", "written_unseen_over"),
        *("rewritten_same_content", "rewritten_restored", "ever_edit_written"),
        *("unseen_writer_possible", "model_conflicts"),
        *("listing_changed_under", "listing_changed_over"),
        *("final_writer_roots_under", "detached_possible", "phases"),
        *("passing_phases", "passing_phases_baseline_content_under"),
        "passing_phases_baseline_content_over",
    }
)
COUNTS_FIELDS = frozenset(
    {
        *("schema_version", "run_key", "family", "capabilities", "bases"),
        *("preconditions", "metrics", "warnings"),
    }
)
WRITER_ROOT_FIELDS = frozenset(
    {"event_id", "writer_type", "root_call_id", "root_under"}
)

TOP_KEYS = frozenset(
    {
        *("name", "description", "xfail", "rules", "capabilities", "run"),
        *("calls", "execs", "links", "dispositions", "paths", "coverage"),
        *("baseline", "final_diff", "final_files", "oracle_inputs"),
        *("oracle_inputs_block", "phase_status", "config", "expect"),
    }
)
EXPECT_KEYS = frozenset(
    {"input_states", "absent", "counts", "warnings", "warnings_exact"}
)
# Fields each true capability implies, as cli.capabilities derives them
# (record, field); a false capability's fields are set nowhere.
IMPLIED_FIELDS = types.MappingProxyType(
    {
        "parent_links": ("exec", "depth"),
        "start_events": ("exec", "record_type"),
        "exact_timing": ("exec", "ended_at"),
        "content_hashes": ("snapshot", "hashes"),
        "untracked_visible": ("snapshot", "created_untracked"),
        "read_tracing": ("exec", "reads"),
    }
)


def scenario_paths() -> list[pathlib.Path]:
    """Lists the input-state scenario files in name order.

    Returns:
        Sorted paths of ``input_state_scenarios/*.yaml``.
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


def _capabilities(raw: Mapping[str, Any]) -> dict[str, bool]:
    merged = dict((raw.get("run") or {}).get("capabilities") or {})
    merged.update(raw.get("capabilities") or {})
    return merged


def condition_holds(raw: Mapping[str, Any], condition: str) -> bool:
    """Whether a CAPABILITY_RULES condition is true in a raw scenario.

    Args:
        raw: A scenario as written.
        condition: A capability name; ``content_exact`` (``oracle_reads``,
            or ``final_content`` with ``final_files`` given);
            ``exact_writes`` (``oracle_reads``, ``final_content`` and
            ``final_files``); ``shim_known`` (``run.shim_intact`` set).

    Returns:
        The condition's value.
    """
    capabilities = _capabilities(raw)
    files = raw.get("final_files") is not None
    if condition == "content_exact":
        return bool(
            capabilities.get("oracle_reads")
            or (capabilities.get("final_content") and files)
        )
    if condition == "exact_writes":
        return bool(
            capabilities.get("oracle_reads")
            and capabilities.get("final_content")
            and files
        )
    if condition == "shim_known":
        return (raw.get("run") or {}).get("shim_intact") is not None
    return bool(capabilities.get(condition))


def make_input_row(
    raw: Mapping[str, Any],
    index: int,
    baseline: Mapping[str, Any] | None,
    workspace_root: str,
) -> dict[str, Any]:
    """Completes a partial §0 row (oracle_inputs.jsonl) with the defaults.

    Args:
        raw: The row as written.
        index: Its position in the scenario's list.
        baseline: The completed baseline, or None.
        workspace_root: The run's workspace root.

    Returns:
        A row with every field of §0.3.
    """
    given = copy.deepcopy(dict(raw))
    path = given["path"]
    inside = match.relative_path(path, workspace_root)[1]
    match_type = given.get(
        "match_type", "pattern" if given.get("pattern") else "literal"
    )
    literal = match_type == "literal"
    files = (baseline or {}).get("files") or {}
    row: dict[str, Any] = dict.fromkeys(oracle_inputs_helpers.ROW_FIELDS)
    row.update(
        input_id=f"i{index}",
        path=path,
        in_workspace=inside,
        input_scope="workspace" if inside else "outside_workspace",
        match_type=match_type,
        listed_by=[],
        in_under=literal,
        access_types=["read"] if literal else ["list"],
        checks=["c"],
        phases=["experiment_runs"],
        source_type="null_agent",
        at_baseline=False if match_type == "pattern_match" else None,
        tracked_at_baseline=(path in files) if baseline is not None else None,
        oracle_writes=False if literal else None,
        oracle_writes_basis="exact" if literal else None,
    )
    row.update(given)
    return row


def default_block(
    run: Mapping[str, Any],
    rows: list[Mapping[str, Any]] | None,
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    """A full oracle_inputs.json for the given §0 rows.

    Args:
        run: The scenario's RunInfo.
        rows: The completed §0 rows, or None.
        overrides: Dotted-key overrides.

    Returns:
        The block.
    """
    rows = rows or []
    phases = sorted({p for row in rows for p in row["phases"]})
    m31 = dict.fromkeys(oracle_inputs_helpers.M31_KEYS)
    m31.update(
        source_type="graded"
        if run["capabilities"]["oracle_reads"]
        else "null_agent",
        phases_complete=dict.fromkeys(phases, True),
    )
    block = {
        "schema_version": "1",
        "run_key": run["run_key"],
        "family": run["family"],
        "capabilities": dict(run["capabilities"]),
        "bases": {
            "source": "exact"
            if run["capabilities"]["oracle_reads"]
            else "fallback",
            "pattern_matches": "fallback",
            "oracle_writes": "exact",
        },
        "preconditions": {
            "oracle_inputs": "holds" if rows else "vacuous",
            "null_commit": "holds",
            "null_score": "holds",
            "run_score": "holds",
        },
        "metrics": {
            "M3.1": m31,
            "M3.5": dict.fromkeys(oracle_inputs_helpers.M35_KEYS),
        },
        "warnings": [],
    }
    for key, value in overrides.items():
        _set(block, key, copy.deepcopy(value))
    return block


def _set(target: dict[str, Any], key: str, value: Any) -> None:
    head = next(
        (m for m in ("M3.1", "M3.5") if key == m or key.startswith(m + ".")),
        None,
    )
    if head is not None:
        target = target["metrics"]
        if key == head:
            target[head] = value
            return
        target = target[head]
        key = key[len(head) + 1 :]
    facts_helpers._set_dotted(target, key, value)


def build_scenario(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Completes a raw scenario mapping with every default.

    Args:
        raw: A scenario as written (``read_raw``), possibly transformed.

    Returns:
        A dict with ``name``, ``description``, ``rules``, ``raw``, ``run``,
        ``calls``, ``execs``, ``linked``, ``baseline``, ``final_diff``,
        ``final_files``, ``oracle_inputs`` (an OracleInputsResult or None),
        ``phase_status``, ``config`` and ``expect``.
    """
    raw = copy.deepcopy(dict(raw))
    run = copy.deepcopy(dict(conftest.DEFAULT_RUN))
    run.update(facts_helpers.DEFAULT_RUN_EXTRA)
    given = dict(raw.get("run") or {})
    given.pop("capabilities", None)
    run["capabilities"].update(_capabilities(raw))
    run.update(given)
    if run["capabilities"]["oracle_reads"] and run["oracle_reads"] is None:
        run["oracle_reads"] = "run/oracle_inputs.jsonl"
    for key in ("t_start", "t_end"):
        if isinstance(run.get(key), int):
            run[key] = float(run[key])
    root = run["workspace_root"]
    calls = [
        conftest.make_call(c, i) for i, c in enumerate(raw.get("calls") or [])
    ]
    plain, j1_links = structure_helpers._split_execs(
        raw.get("execs") or [], calls
    )
    execs = [conftest.make_exec(e, i, root) for i, e in enumerate(plain)]
    conftest.assign_exec_seq(execs)
    links = facts_helpers._links(
        j1_links + list(raw.get("links") or []), calls, execs
    )
    for link in links:
        if link["link_type"] == "write_event":
            link.setdefault("order_t", link["t_end"])
    dispositions = facts_helpers._dispositions(
        calls, execs, links, raw.get("dispositions") or {}
    )
    paths = facts_helpers._paths(links, raw.get("paths") or [])
    baseline = oracle_inputs_helpers.make_baseline(raw.get("baseline"))
    final_diff = raw.get("final_diff")
    if final_diff is not None:
        final_diff = {
            "modified": [],
            "deleted": [],
            "mode_changed": [],
            **final_diff,
        }
    coverage = facts_helpers._coverage(
        calls,
        execs,
        links,
        dispositions,
        paths,
        run,
        {
            "integrity.C0.1": bool(run["trace_ok"]),
            "inputs.tracked_files": baseline is not None,
            "inputs.final_diff": final_diff is not None,
            **dict(raw.get("coverage") or {}),
        },
    )
    rows = None
    if raw.get("oracle_inputs") is not None:
        rows = [
            make_input_row(r, i, baseline, root)
            for i, r in enumerate(raw["oracle_inputs"])
        ]
    oracle_inputs = None
    if rows is not None:
        oracle_inputs = {
            "oracle_inputs": rows,
            "oracle_inputs_run": default_block(
                run, rows, raw.get("oracle_inputs_block") or {}
            ),
        }
    if "phase_status" in raw:
        phase_status = copy.deepcopy(raw["phase_status"])
    else:
        phase_status = {
            p: "success" for row in rows or [] for p in row["phases"]
        }
    return {
        "name": raw.get("name"),
        "description": raw.get("description"),
        "rules": list(raw.get("rules") or []),
        "raw": raw,
        "run": run,
        "calls": calls,
        "execs": execs,
        "linked": {
            "links": links,
            "dispositions": dispositions,
            "paths": paths,
            "coverage": coverage,
        },
        "baseline": baseline,
        "final_diff": copy.deepcopy(final_diff),
        "final_files": oracle_inputs_helpers.make_final_files(
            raw.get("final_files")
        ),
        "oracle_inputs": oracle_inputs,
        "phase_status": phase_status,
        "config": copy.deepcopy(raw.get("config")),
        "expect": raw.get("expect") or {},
    }


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads one scenario file and fills in every default.

    Args:
        path: A scenario file.

    Returns:
        The value of ``build_scenario`` on the file's content.
    """
    return build_scenario(read_raw(path))


CLASSIFY_ARGS = (
    *("oracle_inputs", "baseline", "final_diff", "final_files"),
    *("phase_status", "config"),
)


def run_classify(scenario: Mapping[str, Any], **overrides: Any) -> Any:
    """Calls ``input_state.classify`` on a loaded scenario.

    Args:
        scenario: A dict from ``build_scenario``.
        **overrides: Replacements for ``calls``, ``execs``, ``linked``,
            ``run`` or any keyword argument of ``classify``.

    Returns:
        The result of ``input_state.classify``.
    """
    from trajectory_checks import input_state  # late: D44

    keys = ("calls", "execs", "linked", "run", *CLASSIFY_ARGS)
    args = {
        key: copy.deepcopy(scenario[key])
        for key in keys
        if key not in overrides
    }
    args.update(overrides)
    return input_state.classify(
        args.pop("calls"), args.pop("execs"), args.pop("linked"), **args
    )


def counts_value(counts: Mapping[str, Any], key: str) -> Any:
    """Looks up an ``expect.counts`` key in a state_counts.json value.

    Args:
        counts: The ``state_counts`` part of a ``classify`` result.
        key: A dotted key; a leading ``M3.2`` means ``metrics.M3.2``.

    Returns:
        The value found.

    Raises:
        KeyError: When a component is missing.
    """
    if key == "M3.2" or key.startswith("M3.2."):
        value = counts["metrics"]["M3.2"]
        rest = key[len("M3.2") + 1 :]
        return conftest.dotted(value, rest) if rest else value
    return conftest.dotted(counts, key)


def row_index(
    rows: list[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Rows by ``path`` and by ``path|input_type``.

    Args:
        rows: ``input_states.jsonl`` rows.

    Returns:
        Key -> the rows it names.
    """
    index: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        index.setdefault(row["path"], []).append(row)
        index.setdefault(f"{row['path']}|{row['input_type']}", []).append(row)
    return index


def _field_present(scenario: Mapping[str, Any], name: str) -> bool:
    record_type, field = IMPLIED_FIELDS[name]
    for record in scenario["execs"]:
        holder = record["snapshot"] if record_type == "snapshot" else record
        if holder[field] is not None:
            return True
    return False


def validate(scenario: Mapping[str, Any], stem: str) -> list[str]:
    """Checks that a loaded scenario is internally consistent.

    Args:
        scenario: A dict from ``load_scenario``.
        stem: The file stem the scenario was read from.

    Returns:
        One message per problem; empty when consistent.
    """
    problems = []
    raw = scenario["raw"]
    if scenario["name"] != stem:
        problems.append(f"name {scenario['name']!r} != file stem {stem!r}")
    if not re.fullmatch(r"P\d{2}b?_[a-z0-9_]+", stem):
        problems.append(f"file stem {stem!r} is not P<nn>[b]_<snake_case>")
    if not scenario["description"]:
        problems.append("no description")
    if set(raw) - TOP_KEYS:
        problems.append(f"unknown top-level keys {sorted(set(raw) - TOP_KEYS)}")
    if set(scenario["expect"]) - EXPECT_KEYS:
        problems.append("unknown expect keys")
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
    events = {
        lk["event_id"]: lk
        for lk in scenario["linked"]["links"]
        if lk["link_type"] == "write_event"
    }
    for link in scenario["linked"]["links"]:
        if link["link_type"] == "call_exec" and (
            link["call_id"] not in call_ids
            or link["command_id"] not in exec_ids
        ):
            problems.append(f"call_exec link to an unknown id: {link}")
        if link["link_type"] == "write_event" and link["writer_id"] not in (
            set(call_ids) | set(exec_ids)
        ):
            problems.append(f"write event of an unknown writer: {link}")
    for summary in scenario["linked"]["paths"]:
        if set(summary["events"]) - set(events):
            problems.append(f"paths row {summary['path']}: unknown events")
        if summary["final_writer"] is not None and (
            summary["final_writer"] not in summary["events"]
        ):
            problems.append(f"paths row {summary['path']}: final writer")
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
        if capabilities[name] != present:
            problems.append(f"{name} {capabilities[name]} but field {present}")
    if capabilities["egress_log"]:
        problems.append("egress_log is not a layer-4 capability")
    result = scenario["oracle_inputs"]
    rows = (result or {}).get("oracle_inputs") or []
    graded = any(row["source_type"] == "graded" for row in rows)
    if graded and not capabilities["oracle_reads"]:
        problems.append("a graded row without capability oracle_reads")
    ids = [row["input_id"] for row in rows]
    if len(set(ids)) != len(ids):
        problems.append("duplicate input_id")
    patterns = {r["input_id"] for r in rows if r["match_type"] == "pattern"}
    for row in rows:
        if set(row) != oracle_inputs_helpers.ROW_FIELDS:
            problems.append(f"§0 row {row['path']}: fields")
        if set(row["listed_by"]) - patterns:
            problems.append(f"§0 row {row['path']}: listed_by")
    coverage = scenario["linked"]["coverage"]
    if coverage["rates"]["untracked_visible"] and (
        coverage["rates"]["snapshot_scope"] != "workspace"
    ):
        problems.append("rates.untracked_visible without workspace scope")
    return problems
