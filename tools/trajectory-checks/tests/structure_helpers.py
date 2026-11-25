"""Scenario loader and defaults for the layer-3 (structure) tests.

Layer-3 scenarios (``tests/structure_scenarios/*.yaml``) are written as
layer-1 *and* layer-2 output records (structure spec
``trajectory-structure-spec.md`` §2.2.2). Neither layer's ``derive`` or
``link`` is run: the helper completes the layer-1 records with the
defaults of ``facts_helpers`` and builds the layer-2 records from them by
the rules restated below. The only lower-layer code it calls is layer 2's
public ``facts.command_shape`` and ``facts.programs`` (for the default
``command_shape``, ``numeric_args``, ``arg_count`` and ``programs`` of a
call), so a layer-3 test cannot fail because of a lower-layer change.

This module imports nothing from ``trajectory_checks.structure``: it is
loaded by ``test_structure_rules.py``, which must run before the module
exists.

Scenario file layout::

    name: G01_rerun_after_failure   # equal to the file stem
    description: one line or a folded block
    xfail: "cycle-2 fix: A1"        # optional: the expectations do not
                                    # hold yet; test_scenario is marked
                                    # xfail(strict=True) with this reason
    rules: [OBS5, KEY1, RR1]        # rule ids of spec §6 exercised here
    run: {...}                      # optional RunInfo overrides; its
                                    # capabilities are merged into the
                                    # default (all nine false)
    calls: [...]                    # partial Call records (conftest)
    execs: [...]                    # partial Exec records (conftest); the
                                    # helper-only key ``call: <call_id>``
                                    # adds a call_exec link (rule exact)
                                    # and defaults ``command`` to the
                                    # call's command
    links: [...]                    # other layer-1 links (facts_helpers
                                    # defaults; write events also get
                                    # ``order_t`` = ``t_end``, layer 1 D32)
    dispositions: {id: {...}}       # optional, merged into derived rows
    paths: [...]                    # optional partial paths rows
    coverage: {dotted.key: value}   # optional coverage.json overrides
    call_facts: {call_id: {...}}    # optional layer-2 row overrides
    exec_facts: {command_id: {...}}
    path_facts: {path: {...}}
    facts: {dotted.key: value}      # optional facts.json overrides, e.g.
                                    # preconditions.C1_under: fails
    baseline: {commit, files: {path: {mode}}}   # optional
    final_diff_text: "..."          # optional monitor/final.diff text
    config: {...}                   # optional, passed to structure.derive
    expect:
      call_structure: {call_id: {...}}      # partial rows
      reruns: [{...}, ...]                  # each matches exactly one row
                                            # on the keys it lists
      reruns_exact: true                    # optional: no other edge
      file_classes: {path: {...}}           # partial rows
      absent: [id, ...]                     # no row in any per-item file
      structure: {dotted.key: value}        # keys of structure.json; a key
                                            # led by a metric id (M2.1) is
                                            # looked up under "metrics"
      warnings: [substring, ...]            # each in exactly one warning
      warnings_exact: true                  # optional: no other warning

Layer-1 defaults (unchanged from ``facts_helpers``): snapshot scope
``tracked``, ``rates.untracked_visible`` false, every universal field
``None``. One coverage default is added here: ``inputs.tracked_files`` is
whether a baseline is given (overridable).

``run_info``: the scenario's ``run`` block over ``conftest.DEFAULT_RUN``
(``run_key`` ``art__agent__1``), every capability false unless set.

Layer-2 defaults (spec §2.2.2, completed from the layer-1 records):

- ``call_facts``: one row per shell call in ``seq`` order. ``command_id``
  and ``match_rule`` from its J1 link; ``match_under`` from its
  disposition (rule ``exact`` or ``quoting_equivalent``); ``outcome`` by
  layer 2's OUT1 on the root exec (``unknown`` without one); times by
  TIM5 (``released_at``/``ended_at`` when ``exact_timing`` and the root
  exec has ``ended_at``, else ``t_start``/``t_end``; a ``start_only`` or
  ``flushed_at_stop`` record ends at the run's ``t_end``); ``None`` times
  without a root exec; shape fields from ``facts.command_shape`` and
  ``facts.programs`` on ``command_norm``; every count 0.
- ``exec_facts``: one row per non-noise exec in ``seq`` order;
  ``disposition`` and ``root_under`` from the dispositions; ``root_call_id``
  from the J1 link, the duplicated exec's J1 link, or the J2 link;
  ``depth`` the exec's own when set, else 0 for a matched root or a wrapper
  duplicate and 1 otherwise; ``outcome`` by OUT1; empty lists.
- ``path_facts``: one row per ``paths`` row; ``final_writer_role`` by
  layer 2's WRT1 from the final event (``edit_call``; ``root_exec`` when
  the writer is a matched root; ``nested_exec`` otherwise);
  ``writer_roles`` the sorted roles of all the path's events;
  ``created`` false; network fields false.
- ``facts``: ``preconditions`` all ``holds``, except ``untracked_visible``
  (holds exactly in an untracked-visible run: ``rates.untracked_visible``
  or ``capabilities.untracked_visible``, Q15), ``log`` (whether
  ``inputs.log``), ``C1_under`` (``vacuous`` with no shell call), and the
  trace-derived labels ``C1_under``, ``C2_under``, ``C6``, ``C7_under``,
  which fail when ``inputs.trace`` or ``integrity.C0.1`` is false (layer 2
  PRE3, this layer's PRE7); ``bases`` from the capabilities.
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

STRUCTURE_SCENARIOS_DIR = conftest.TESTS_DIR / "structure_scenarios"

# Every rule id of spec §6 (trajectory-structure-spec.md). PRE6 moved to
# oracle_inputs.py and FC7 is retired (D31): neither id is defined here.
RULE_IDS = (
    *("PRE1", "PRE2", "PRE3", "PRE4", "PRE5", "PRE7", "PRE8"),
    *("OBS1", "OBS2", "OBS3", "OBS4", "OBS5", "OBS6"),
    *("KEY1", "KEY2", "KEY3", "KEY4", "KEY5"),
    *("RR1", "RR2", "RR3", "RR4", "RR5", "RR6"),
    *("REP1", "REP2", "REP3"),
    *("FC0", "FC1", "FC2", "FC3", "FC4", "FC5", "FC6"),
    *("FC8", "FC9", "FC10", "FC11"),
    *("BIN1", "CLS1", "CLS2", "CLS3", "CLS4", "CLS5"),
    "OUTF1",
)
RETIRED_RULE_IDS = ("PRE6", "FC7")

# Rules exercised by the table-driven unit tests
# (test_structure_functions.py).
UNIT_TEST_RULES = frozenset(
    {
        *("OBS1", "OBS2", "OBS3", "OBS4", "OBS5", "OBS6"),
        *("KEY3", "RR3", "RR4", "BIN1"),
        *("FC0", "FC1", "FC2", "FC3", "FC4", "FC5", "FC6"),
        *("FC8", "FC9", "FC10", "FC11"),
    }
)

# The capability table of spec §1: (condition, rules it selects). A
# condition is a capability name, or "untracked_visible_run" (D33: snapshot
# scope workspace or capabilities.untracked_visible).
CAPABILITY_RULES = (
    ("parent_links", ("RR5",)),
    ("exact_timing", ("RR5", "RR6")),
    ("untracked_visible_run", ("RR6", "CLS3")),
    ("final_content", ("BIN1", "CLS5")),
)

METRIC_IDS = ("M2.1", "M2.2", "M2.3", "M4.2")
FILE_CLASSES = (
    *("ci", "build", "notebook", "test", "code", "docs", "data"),
    *("config", "other"),
)
ROLES = ("edit_call", "nested_exec", "root_exec")
STOP_RECORDS = ("start_only", "flushed_at_stop")
UNDER_RULES = ("exact", "quoting_equivalent")
WRITE_OPS = ("create", "delete", "mode", "modify")

# Fields each true capability implies (spec §3.3 IMPLIED_FIELDS, as
# cli.capabilities derives the capability): (record, field).
IMPLIED_FIELDS = types.MappingProxyType(
    {
        "parent_links": (("exec", "depth"), ("exec", "root_command_id")),
        "exact_timing": (("exec", "ended_at"), ("exec", "released_at")),
        "untracked_visible": (
            ("snapshot", "created_untracked"),
            ("snapshot", "modified_untracked"),
            ("snapshot", "deleted_untracked"),
        ),
    }
)
# Universal Exec and Snapshot fields a scenario may set (layer 1 D26).
NEW_EXEC_FIELDS = conftest.NEW_EXEC_FIELDS
NEW_SNAPSHOT_FIELDS = conftest.NEW_SNAPSHOT_FIELDS

TOP_KEYS = frozenset(
    {
        *("name", "description", "xfail", "rules", "run", "calls"),
        "execs",
        *("links", "dispositions", "paths", "coverage", "call_facts"),
        *("exec_facts", "path_facts", "facts", "baseline"),
        *("final_diff_text", "config", "expect"),
    }
)
EXPECT_KEYS = frozenset(
    {
        *("call_structure", "reruns", "reruns_exact", "file_classes"),
        *("absent", "structure", "warnings", "warnings_exact"),
    }
)


def scenario_paths() -> list[pathlib.Path]:
    """Lists the layer-3 scenario files in name order.

    Returns:
        Sorted paths of ``structure_scenarios/*.yaml``.
    """
    return sorted(STRUCTURE_SCENARIOS_DIR.glob("*.yaml"))


def scenario_params() -> list[Any]:
    """The scenario files as pytest params, xfail-marked when they say so.

    A scenario with an ``xfail`` key pins a known bug: its expectations
    fail until the fix, and strict mode fails the test once they hold, so
    the fix must remove the key.

    Returns:
        One ``pytest.param`` per ``structure_scenarios/*.yaml``, id the
        stem.
    """
    params = []
    for path in scenario_paths():
        reason = read_raw(path).get("xfail")
        marks = (
            [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        )
        params.append(pytest.param(path, id=path.stem, marks=marks))
    return params


def has_paths(raw: Mapping[str, Any]) -> bool:
    """Whether a raw scenario has a path (a ``paths`` row or a write event).

    Decided from the YAML alone, so a test can filter on it at collection
    without importing layer 3 (D36).

    Args:
        raw: A scenario as written.

    Returns:
        True when ``build_scenario`` gives at least one ``linked.paths`` row.
    """
    return bool(raw.get("paths")) or any(
        link.get("link_type") == "write_event"
        for link in raw.get("links") or []
    )


def has_execs(raw: Mapping[str, Any]) -> bool:
    """Whether a raw scenario has an exec record.

    Args:
        raw: A scenario as written.

    Returns:
        True when ``build_scenario`` gives at least one exec.
    """
    return bool(raw.get("execs"))


def read_raw(path: pathlib.Path) -> dict[str, Any]:
    """Reads one scenario file as written.

    Args:
        path: A ``structure_scenarios/*.yaml`` file.

    Returns:
        The decoded YAML mapping.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def untracked_visible_run(raw: Mapping[str, Any]) -> bool:
    """Whether a raw scenario describes an untracked-visible run (D33).

    Args:
        raw: A scenario as written.

    Returns:
        True when ``coverage`` sets ``rates.untracked_visible`` or ``run``
        sets ``capabilities.untracked_visible``.
    """
    capabilities = (raw.get("run") or {}).get("capabilities") or {}
    coverage = raw.get("coverage") or {}
    return bool(
        capabilities.get("untracked_visible")
        or coverage.get("rates.untracked_visible")
    )


def condition_holds(raw: Mapping[str, Any], condition: str) -> bool:
    """Whether a CAPABILITY_RULES condition is true in a raw scenario.

    Args:
        raw: A scenario as written.
        condition: A capability name or ``untracked_visible_run``.

    Returns:
        The condition's value.
    """
    if condition == "untracked_visible_run":
        return untracked_visible_run(raw)
    capabilities = (raw.get("run") or {}).get("capabilities") or {}
    return bool(capabilities.get(condition))


def _outcome(record: Mapping[str, Any] | None) -> str:
    """Layer 2's OUT1, restated."""
    if record is None or (
        record["signal"] is None and record["exit_code"] is None
    ):
        return "unknown"
    if record["signal"] is not None:
        return "killed"
    return "ok" if record["exit_code"] == 0 else "failed"


def _interval(
    record: Mapping[str, Any], exact: bool, run_end: float | None
) -> tuple[float, float]:
    """Layer 2's TIM5 measured interval of an exec, restated."""
    start, end = record["t_start"], record["t_end"]
    if exact and record["ended_at"] is not None:
        if record["released_at"] is not None:
            start = record["released_at"]
        end = record["ended_at"]
    if record["record_type"] in STOP_RECORDS and run_end is not None:
        end = max(end, run_end)
    return start, end


def _split_execs(
    raw_execs: list[Mapping[str, Any]], calls: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Removes the helper-only ``call`` key; returns (execs, J1 links)."""
    call_by_id = {c["call_id"]: c for c in calls}
    execs, links = [], []
    for item in raw_execs:
        record = dict(item)
        owner = record.pop("call", None)
        if owner is not None:
            record.setdefault("command", call_by_id[owner]["command"])
            links.append(
                {
                    "link_type": "call_exec",
                    "call_id": owner,
                    "command_id": record["command_id"],
                }
            )
        execs.append(record)
    return execs, links


def _call_facts(
    calls: list[dict[str, Any]],
    execs: list[dict[str, Any]],
    linked: Mapping[str, Any],
    run: Mapping[str, Any],
    overrides: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    from trajectory_checks import facts  # layer 2's public shape functions

    exact = bool(run["capabilities"].get("exact_timing"))
    exec_by_id = {e["command_id"]: e for e in execs}
    j1 = {
        lk["call_id"]: lk
        for lk in linked["links"]
        if lk["link_type"] == "call_exec"
    }
    flags = {
        row["id"]: row
        for row in linked["dispositions"]
        if row["item_type"] == "call"
    }
    rows = []
    shell = [c for c in calls if c["tool_type"] == "shell"]
    for call in sorted(shell, key=lambda c: c["seq"]):
        link = j1.get(call["call_id"])
        root = exec_by_id[link["command_id"]] if link else None
        text = call["command_norm"] or ""
        shape, numbers, count = facts.command_shape(text)
        if root is not None:
            start, end = _interval(root, exact, run["t_end"])
            duration = end - start
        else:
            start = end = duration = None
        row = {
            "call_id": call["call_id"],
            "seq": call["seq"],
            "command_id": link["command_id"] if link else None,
            "match_rule": link["rule"] if link else None,
            "match_under": bool(flags[call["call_id"]]["match_under"]),
            "t_start": start,
            "t_end": end,
            "duration_s": duration,
            "complete": root["complete"] if root else None,
            "span_under_s": duration,
            "span_over_s": duration,
            "exit_code": root["exit_code"] if root else None,
            "signal": root["signal"] if root else None,
            "outcome": _outcome(root),
            "exit_signal": None,
            "command_shape": shape,
            "numeric_args": numbers,
            "arg_count": count,
            "programs": facts.programs(text),
            "descendants_under": 0,
            "descendants_over": 0,
            "depth_max_over": 0 if root else None,
            "depth_max_under": 0 if root else None,
            "writes_under": dict.fromkeys(WRITE_OPS, 0),
            "writes_over": dict.fromkeys(WRITE_OPS, 0),
            "network_types_under": [],
            "network_types_over": [],
            "env_assigned": [],
            "env_confirmed": None,
        }
        row.update(copy.deepcopy(dict(overrides.get(call["call_id"]) or {})))
        rows.append(row)
    return rows


def _roots(links: list[Mapping[str, Any]]) -> dict[str, str | None]:
    """command_id -> root call id, by J1, a duplicate's J1, or J2."""
    j1 = {
        lk["command_id"]: lk["call_id"]
        for lk in links
        if lk["link_type"] == "call_exec"
    }
    roots: dict[str, str | None] = dict(j1)
    for lk in links:
        if lk["link_type"] == "wrapper_duplicate":
            roots[lk["command_id"]] = j1.get(lk["of"])
        elif lk["link_type"] == "exec_parent":
            roots[lk["command_id"]] = lk["root_call_id"]
    return roots


def _exec_facts(
    execs: list[dict[str, Any]],
    linked: Mapping[str, Any],
    overrides: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_id = {
        row["id"]: row
        for row in linked["dispositions"]
        if row["item_type"] == "exec"
    }
    roots = _roots(linked["links"])
    rows = []
    for record in sorted(execs, key=lambda e: e["seq"]):
        command_id = record["command_id"]
        disposition = rows_by_id[command_id]["disposition"]
        if disposition == "noise":
            continue
        depth = record["depth"]
        if depth is None and disposition not in ("unrooted", "out_of_window"):
            top = disposition in ("matched_root", "wrapper_duplicate")
            depth = 0 if top else 1
        row = {
            "command_id": command_id,
            "seq": record["seq"],
            "disposition": disposition,
            "root_call_id": roots.get(command_id),
            "root_under": bool(rows_by_id[command_id]["root_under"]),
            "depth": depth,
            "outcome": _outcome(record),
            "exit_signal": None,
            "network_types": [],
            "env_added": None,
        }
        row.update(copy.deepcopy(dict(overrides.get(command_id) or {})))
        rows.append(row)
    return rows


def _role(event: Mapping[str, Any], matched_roots: set[str]) -> str:
    """Layer 2's WRT1, restated."""
    if event["writer_type"] == "edit_call":
        return "edit_call"
    return "root_exec" if event["writer_id"] in matched_roots else "nested_exec"


def _path_facts(
    linked: Mapping[str, Any], overrides: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    event_of = {
        lk["event_id"]: lk
        for lk in linked["links"]
        if lk["link_type"] == "write_event"
    }
    matched_roots = {
        lk["command_id"]
        for lk in linked["links"]
        if lk["link_type"] == "call_exec"
    }
    rows = []
    for summary in sorted(linked["paths"], key=lambda p: p["path"]):
        events = [event_of[i] for i in summary["events"]]
        final = event_of.get(summary["final_writer"] or "")
        row = {
            "path": summary["path"],
            "in_workspace": summary["in_workspace"],
            "tracked": summary["tracked"],
            "final_state": summary["final_state"],
            "final_writer": summary["final_writer"],
            "final_writer_role": _role(final, matched_roots) if final else None,
            "final_writer_root": final["root_call_id"] if final else None,
            "final_writer_under": bool(summary["final_writer_under"]),
            "writer_roles": sorted({_role(w, matched_roots) for w in events}),
            "created": False,
            "network_created_under": False,
            "network_created_over": False,
        }
        row.update(copy.deepcopy(dict(overrides.get(summary["path"]) or {})))
        rows.append(row)
    return rows


def _run_facts(
    calls: list[dict[str, Any]],
    linked: Mapping[str, Any],
    run: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    coverage = linked["coverage"]
    capabilities = dict(run["capabilities"])
    traced = bool(coverage["inputs"]["trace"]) and bool(
        coverage["integrity"]["C0.1"]
    )
    shell = [c for c in calls if c["tool_type"] == "shell"]
    held = "holds" if traced else "fails"
    visible = bool(
        capabilities.get("untracked_visible")
        or coverage["rates"].get("untracked_visible")
    )
    facts = {
        "run_key": run.get("run_key"),
        "family": run.get("family"),
        "capabilities": capabilities,
        "bases": {
            "timing": "exact" if capabilities["exact_timing"] else "fallback",
            "depth": "exact" if capabilities["parent_links"] else "fallback",
            "running_at_stop": "exact"
            if capabilities["start_events"]
            else "fallback",
        },
        "preconditions": {
            "C1_under": ("vacuous" if not shell else "holds")
            if traced
            else "fails",
            "C2_under": held,
            "C6": held,
            "C7_under": held,
            "env_keys": "holds",
            "log": "holds" if coverage["inputs"]["log"] else "fails",
            "system_diff": "holds",
            "untracked_visible": "holds" if visible else "fails",
        },
        "metrics": {},
        "warnings": [],
    }
    for key, value in overrides.items():
        facts_helpers._set_dotted(facts, key, copy.deepcopy(value))
    return facts


def build_scenario(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Completes a raw scenario mapping with every default.

    Args:
        raw: A scenario as written (``read_raw``), possibly transformed.

    Returns:
        A dict with ``name``, ``description``, ``rules``, ``raw``,
        ``run_info``, ``calls``, ``execs``, ``linked``, ``facts_result``,
        ``baseline``, ``final_diff_text``, ``config`` and ``expect``.
    """
    raw = copy.deepcopy(dict(raw))
    run = copy.deepcopy(dict(conftest.DEFAULT_RUN))
    run.update(facts_helpers.DEFAULT_RUN_EXTRA)
    given = dict(raw.get("run") or {})
    run["capabilities"].update(given.pop("capabilities", None) or {})
    run.update(given)
    for key in ("t_start", "t_end"):
        if isinstance(run.get(key), int):
            run[key] = float(run[key])
    calls = [
        conftest.make_call(c, i) for i, c in enumerate(raw.get("calls") or [])
    ]
    plain, j1_links = _split_execs(raw.get("execs") or [], calls)
    execs = [
        conftest.make_exec(e, i, run["workspace_root"])
        for i, e in enumerate(plain)
    ]
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
    baseline = raw.get("baseline")
    coverage_overrides = {
        "inputs.tracked_files": baseline is not None,
        **dict(raw.get("coverage") or {}),
    }
    coverage = facts_helpers._coverage(
        calls, execs, links, dispositions, paths, run, coverage_overrides
    )
    linked = {
        "links": links,
        "dispositions": dispositions,
        "paths": paths,
        "coverage": coverage,
    }
    facts_result = {
        "call_facts": _call_facts(
            calls, execs, linked, run, raw.get("call_facts") or {}
        ),
        "exec_facts": _exec_facts(execs, linked, raw.get("exec_facts") or {}),
        "path_facts": _path_facts(linked, raw.get("path_facts") or {}),
        "facts": _run_facts(calls, linked, run, raw.get("facts") or {}),
    }
    return {
        "name": raw.get("name"),
        "description": raw.get("description"),
        "rules": list(raw.get("rules") or []),
        "raw": raw,
        "run_info": run,
        "calls": calls,
        "execs": execs,
        "linked": linked,
        "facts_result": facts_result,
        "baseline": copy.deepcopy(baseline),
        "final_diff_text": raw.get("final_diff_text"),
        "config": copy.deepcopy(raw.get("config")),
        "expect": raw.get("expect") or {},
    }


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads one layer-3 scenario and fills in every default.

    Args:
        path: A ``structure_scenarios/*.yaml`` file.

    Returns:
        The value of ``build_scenario`` on the file's content.
    """
    return build_scenario(read_raw(path))


def run_derive(scenario: Mapping[str, Any], **overrides: Any) -> Any:
    """Calls ``structure.derive`` on a loaded scenario.

    Args:
        scenario: A dict from ``load_scenario`` or ``build_scenario``.
        **overrides: Replacements for ``calls``, ``execs``, ``linked``,
            ``facts_result``, ``run_info``, ``baseline``,
            ``final_diff_text`` or ``config``.

    Returns:
        The result of ``structure.derive``.
    """
    from trajectory_checks import structure  # late: rules test needs none

    keys = (
        *("calls", "execs", "linked", "facts_result", "run_info"),
        *("baseline", "final_diff_text", "config"),
    )
    args = {key: copy.deepcopy(scenario[key]) for key in keys}
    args.update(overrides)
    return structure.derive(
        args["calls"],
        args["execs"],
        args["linked"],
        args["facts_result"],
        run_info=args["run_info"],
        baseline=args["baseline"],
        final_diff_text=args["final_diff_text"],
        config=args["config"],
    )


def structure_value(structure: Mapping[str, Any], key: str) -> Any:
    """Looks up an ``expect.structure`` key in a structure.json value.

    Args:
        structure: The ``structure`` part of a ``derive`` result.
        key: A dotted key; a leading metric id means ``metrics.<id>``.

    Returns:
        The value found.

    Raises:
        KeyError: When a component is missing.
    """
    if any(key == m or key.startswith(m + ".") for m in METRIC_IDS):
        head = next(m for m in METRIC_IDS if key == m or key.startswith(m))
        value = structure["metrics"][head]
        rest = key[len(head) + 1 :]
        return conftest.dotted(value, rest) if rest else value
    return conftest.dotted(structure, key)


def _field_set(
    scenario: Mapping[str, Any], record_type: str, field: str
) -> list[bool]:
    """Whether each relevant record carries a field (IMPLIED_FIELDS)."""
    live = [e for e in scenario["execs"] if e["noise"] is None]
    if record_type == "snapshot":
        return [
            e["snapshot"][field] is not None
            for e in live
            if e["snapshot"]["status"] in ("ok", "truncated")
        ]
    if field == "ended_at":  # an incomplete exec has no end (PRE8)
        live = [e for e in live if e["complete"]]
    return [e[field] is not None for e in live]


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
    if not re.fullmatch(r"G\d{2}_[a-z0-9_]+", stem):
        problems.append(f"file stem {stem!r} is not G<nn>_<snake_case>")
    if not scenario["description"]:
        problems.append("no description")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        problems.append(f"unknown top-level keys {sorted(unknown)}")
    unknown = set(scenario["expect"]) - EXPECT_KEYS
    if unknown:
        problems.append(f"unknown expect keys {sorted(unknown)}")
    rules = scenario["rules"]
    if not rules:
        problems.append("no rules cited")
    bad = set(rules) - set(RULE_IDS)
    if bad:
        problems.append(f"unknown rule ids {sorted(bad)}")
    if len(set(rules)) != len(rules):
        problems.append("a rule id is cited twice")
    call_ids = [c["call_id"] for c in scenario["calls"]]
    exec_ids = [e["command_id"] for e in scenario["execs"]]
    if len(set(call_ids)) != len(call_ids):
        problems.append("duplicate call_id")
    if len(set(exec_ids)) != len(exec_ids):
        problems.append("duplicate command_id")
    seqs = [c["seq"] for c in scenario["calls"]]
    if len(set(seqs)) != len(seqs):
        problems.append("duplicate call seq")
    for link in scenario["linked"]["links"]:
        if link["link_type"] == "call_exec" and (
            link["call_id"] not in call_ids
            or link["command_id"] not in exec_ids
        ):
            problems.append(f"call_exec link to an unknown id: {link}")
        if link["link_type"] in ("wrapper_duplicate", "exec_parent") and (
            link["command_id"] not in exec_ids
        ):
            problems.append(f"{link['link_type']} of an unknown exec")
    for record in scenario["execs"]:
        if record["t_end"] < record["t_start"]:
            problems.append(f"exec {record['command_id']} ends before start")
        unknown = set(record) - conftest.EXEC_FIELDS
        if unknown:
            problems.append(f"exec fields {sorted(unknown)} unknown")
    for call in scenario["calls"]:
        if call["t_end"] is not None and call["t_end"] < call["t_start"]:
            problems.append(f"call {call['call_id']} ends before start")
    for block, known in (
        ("call_facts", call_ids),
        ("exec_facts", exec_ids),
        ("path_facts", [p["path"] for p in scenario["linked"]["paths"]]),
    ):
        missing = set(raw.get(block) or {}) - set(known)
        if missing:
            problems.append(f"{block} overrides unknown ids {sorted(missing)}")
    problems += _capability_problems(scenario)
    coverage = scenario["linked"]["coverage"]
    if coverage["inputs"]["tracked_files"] != (
        scenario["baseline"] is not None
    ):
        problems.append("inputs.tracked_files disagrees with the baseline")
    if coverage["rates"]["untracked_visible"]:
        if coverage["rates"]["snapshot_scope"] != "workspace":
            problems.append("rates.untracked_visible without workspace scope")
        scopes = {
            e["snapshot"]["scope"]
            for e in scenario["execs"]
            if e["snapshot"]["status"] == "ok"
        }
        if scopes - {"workspace"}:
            problems.append(
                "an ok snapshot of a workspace run is not workspace"
            )
    return problems


def _capability_problems(scenario: Mapping[str, Any]) -> list[str]:
    """True capabilities have their fields somewhere, false ones nowhere."""
    problems = []
    capabilities = scenario["run_info"]["capabilities"]
    if set(capabilities) != set(conftest.CAPABILITIES):
        problems.append("run capabilities are not the nine of layer 1")
    for name, fields in IMPLIED_FIELDS.items():
        for record_type, field in fields:
            present = _field_set(scenario, record_type, field)
            if capabilities[name] and not any(present):
                problems.append(f"{name} true but no record has {field}")
            if not capabilities[name] and any(present):
                problems.append(f"{name} false but a record has {field}")
    content = scenario["run_info"]["final_diff_content"] is not None
    if capabilities["final_content"] != content:
        problems.append("final_content disagrees with final_diff_content")
    if scenario["final_diff_text"] is not None and not content:
        problems.append("final_diff_text given without final_content")
    for name in ("start_events", "content_hashes", "read_tracing"):
        field = {
            "start_events": "record_type",
            "content_hashes": "hashes",
            "read_tracing": "reads",
        }[name]
        holder = "snapshot" if field == "hashes" else "exec"
        values = [
            (e["snapshot"] if holder == "snapshot" else e)[field]
            for e in scenario["execs"]
        ]
        if capabilities[name] != any(v is not None for v in values):
            problems.append(f"{name} disagrees with field {field}")
    return problems
