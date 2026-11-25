"""Scenario loader and defaults for the layer-2 (per-command facts) tests.

Layer-2 scenarios (``tests/facts_scenarios/*.yaml``) are written directly
as layer-1 *output* records, so layer 1's code is never run by them
(spec ``trajectory-per-command-spec.md`` §2.2.2). Every omitted field takes
the default documented here.

Scenario file layout::

    name: F01_basic                 # equal to the file stem
    description: one line
    xfail: "cycle-2 fix: NET3"      # optional: the expectations do not
                                    # hold yet; test_scenario is marked
                                    # xfail(strict=True) with this reason
    rules: [OUT1, TIM1]             # rule ids of spec §6 exercised here
    run: {...}                      # optional, overrides DEFAULT_RUN;
                                    # run.capabilities is merged into the
                                    # default (all false)
    calls: [...]                    # Call records, completed by conftest
    execs: [...]                    # Exec records, completed by conftest
    links: [...]                    # call_exec, wrapper_duplicate,
                                    # exec_parent, write_event (partial)
    dispositions: {id: {...}}       # optional, merged into derived rows
    paths: [...]                    # optional, partial paths rows
    coverage: {dotted.key: value}   # optional overrides of the default
    env_keys: {command_id: [...]}   # optional; sets Exec.env_keys of
                                    # those execs, the others keep None
    system_diff: {...}              # optional; absent -> not passed
    config: {...}                   # optional, passed to facts.derive
    expect:
      call_facts: {call_id: {...}}  # partial call_facts.jsonl rows
      exec_facts: {command_id: {...}}
      path_facts: {path: {...}}
      absent: [id, ...]             # ids with no row in any of the three
      facts: {dotted.key: value}    # keys of facts.json; a key whose first
                                    # part is a metric id (M1.1) is looked
                                    # up under "metrics"
      warnings: [substring, ...]    # each in exactly one warning
      warnings_exact: true          # optional: no other warning

Link defaults: ``call_exec`` rule ``exact``, ``dt_s`` = exec start - call
start. ``exec_parent`` rule ``time_containment``, ``root_ambiguous``
false, ``root_candidates`` []. ``write_event``: ``event_id`` ``w<i>`` in
list order among write events, ``writer_type`` ``exec`` when
``writer_id`` names an exec and ``edit_call`` otherwise, times from the
writer (an edit call's ``t_start`` twice), ``root_call_id`` from the
writer's J1 or J2 link (an edit call's own id), ``op`` ``modify``,
``in_workspace`` true, ``scope`` ``tracked`` (``None`` for edit calls),
``depth_rule`` ``none``, ``possibly_edit_call`` ``None``,
``overlaps_sibling`` false, ``root_ambiguous`` false,
``root_candidates`` [], ``source`` ``snapshot`` or ``edit_call``,
``writer_under`` true.

Disposition defaults are derived from the links (see ``_dispositions``);
path defaults from the write events (see ``_paths``); coverage defaults
in ``_coverage``: the counts computed from the records as layer 1 computes
them, ``capabilities`` copied from ``run.capabilities`` and no
``preconditions`` block unless the scenario gives one (``coverage:
{preconditions: {...}}``), so ``facts.precondition`` evaluates the labels
from the rates (PRE6).
"""

import copy
import pathlib
import types
from collections.abc import Mapping
from typing import Any

import conftest
import pytest
import yaml

FACTS_SCENARIOS_DIR = conftest.TESTS_DIR / "facts_scenarios"

# Every rule id of spec §6 (trajectory-per-command-spec.md). The rule
# coverage test fails when one is cited by no scenario and no unit test.
RULE_IDS = (
    *("PRE1", "PRE2", "PRE3", "PRE4", "PRE5", "PRE6"),
    *("OUT1", "OUT2", "OUT3"),
    *("SHP1", "SHP2", "SHP3", "SHP4", "SHP5", "SHP6", "SHP7", "SHP8", "SHP9"),
    *("TIM1", "TIM2", "TIM3", "TIM4", "TIM5"),
    *("NST1", "NST2", "NST3", "NST4"),
    *("WRT1", "WRT2", "WRT3", "WRT4", "WRT5", "WRT6"),
    *("SYS1", "SYS2", "SYS3", "SYS4"),
    *("ENV1", "ENV2", "ENV3", "ENV4", "ENV5"),
    *("NET1", "NET2", "NET3", "NET4", "NET5"),
    *("EXT1", "EXT2", "EXT3", "EXT4", "EXT5", "EXT6"),
    "PEER1",
    "OUTF1",
    "CAP1",
)

# Rules exercised by the table-driven unit tests (test_facts_functions.py).
UNIT_TEST_RULES = frozenset(
    {
        *("SHP1", "SHP2", "SHP3", "SHP4", "SHP5", "SHP6", "SHP7", "SHP8"),
        *("NET1", "ENV1", "OUT1", "OUT2", "PRE1", "PRE2", "PRE3", "PRE6"),
        "PEER1",
    }
)

DEFAULT_RUN_EXTRA = types.MappingProxyType(
    {"run_key": "art__agent__1", "family": "claude"}
)
METRIC_IDS = ("M1.1", "M1.2", "M1.3", "M1.4", "M4.1", "M4.4", "M5.2", "M6.1")
UNDER_RULES = ("exact", "quoting_equivalent")


def scenario_paths() -> list[pathlib.Path]:
    """Lists the layer-2 scenario files in name order.

    Returns:
        Sorted paths of ``facts_scenarios/*.yaml``.
    """
    return sorted(FACTS_SCENARIOS_DIR.glob("*.yaml"))


def scenario_params() -> list[Any]:
    """The scenario files as pytest params, xfail-marked when they say so.

    A scenario with an ``xfail`` key pins a known bug: its expectations
    fail until the fix, and strict mode fails the test once they hold, so
    the fix must remove the key.

    Returns:
        One ``pytest.param`` per ``facts_scenarios/*.yaml``, id the stem.
    """
    params = []
    for path in scenario_paths():
        reason = yaml.safe_load(path.read_text(encoding="utf-8")).get("xfail")
        marks = (
            [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        )
        params.append(pytest.param(path, id=path.stem, marks=marks))
    return params


def _links(
    raw: list[Mapping[str, Any]],
    calls: list[dict[str, Any]],
    execs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    call_by_id = {c["call_id"]: c for c in calls}
    exec_by_id = {e["command_id"]: e for e in execs}
    links: list[dict[str, Any]] = []
    for item in raw:
        link = dict(item)
        if link["link_type"] == "call_exec":
            link.setdefault("rule", "exact")
            link.setdefault(
                "dt_s",
                round(
                    exec_by_id[link["command_id"]]["t_start"]
                    - call_by_id[link["call_id"]]["t_start"],
                    6,
                ),
            )
        elif link["link_type"] == "exec_parent":
            link.setdefault("rule", "time_containment")
            link.setdefault("root_ambiguous", False)
            link.setdefault("root_candidates", [])
        links.append(link)
    root_of: dict[str, str] = {}
    for link in links:
        if link["link_type"] == "call_exec":
            root_of[link["command_id"]] = link["call_id"]
        elif link["link_type"] == "exec_parent":
            root_of[link["command_id"]] = link["root_call_id"]
    events = 0
    for link in links:
        if link["link_type"] != "write_event":
            continue
        writer = link["writer_id"]
        is_exec = writer in exec_by_id
        link.setdefault("event_id", f"w{events}")
        events += 1
        link.setdefault("writer_type", "exec" if is_exec else "edit_call")
        if is_exec:
            link.setdefault("t_start", exec_by_id[writer]["t_start"])
            link.setdefault("t_end", exec_by_id[writer]["t_end"])
            link.setdefault("root_call_id", root_of.get(writer))
            link.setdefault("scope", "tracked")
        else:
            link.setdefault("t_start", call_by_id[writer]["t_start"])
            link.setdefault("t_end", link["t_start"])
            link.setdefault("root_call_id", writer)
            link.setdefault("scope", None)
        link.setdefault("op", "modify")
        link.setdefault("in_workspace", True)
        link.setdefault("depth_rule", "none")
        link.setdefault("possibly_edit_call", None)
        link.setdefault("overlaps_sibling", False)
        link.setdefault("root_ambiguous", False)
        link.setdefault("root_candidates", [])
        link.setdefault("source", "snapshot" if is_exec else "edit_call")
        link.setdefault("writer_under", True)
    return links


def _row(item_type: str, item_id: str, disposition: str) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "id": item_id,
        "disposition": disposition,
        "reason": None,
        "matched": None,
        "match_under": None,
        "snapshot_ok": None,
        "root_under": None,
    }


def _dispositions(
    calls: list[dict[str, Any]],
    execs: list[dict[str, Any]],
    links: list[dict[str, Any]],
    overrides: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    j1 = {lk["call_id"]: lk for lk in links if lk["link_type"] == "call_exec"}
    roots = {lk["command_id"] for lk in j1.values()}
    dups = {
        lk["command_id"]
        for lk in links
        if lk["link_type"] == "wrapper_duplicate"
    }
    j2 = {
        lk["command_id"]: lk for lk in links if lk["link_type"] == "exec_parent"
    }

    def root_under(command_id: str) -> bool:
        if command_id in roots or command_id in dups:
            return True
        link = j2.get(command_id)
        if link is None or link["rule"] == "reparenting":
            return False
        if link["root_ambiguous"]:
            return False
        return link["parent"] is None or root_under(link["parent"])

    rows = []
    for call in sorted(calls, key=lambda c: c["seq"]):
        if call["tool_type"] != "shell":
            continue
        link = j1.get(call["call_id"])
        row = _row("call", call["call_id"], "matched" if link else "unmatched")
        row["matched"] = link is not None
        row["match_under"] = bool(link and link["rule"] in UNDER_RULES)
        row["reason"] = None if link else "no_candidate"
        rows.append(row)
    for record in sorted(execs, key=lambda e: e["seq"]):
        command_id = record["command_id"]
        if record.get("noise"):
            disposition = "noise"
        elif command_id in roots:
            disposition = "matched_root"
        elif command_id in dups:
            disposition = "wrapper_duplicate"
        elif command_id in j2:
            disposition = "nested"
        else:
            disposition = "unrooted"
        row = _row("exec", command_id, disposition)
        row["snapshot_ok"] = record["snapshot"]["status"] == "ok"
        row["root_under"] = disposition != "noise" and root_under(command_id)
        rows.append(row)
    for row in rows:
        row.update(overrides.get(row["id"]) or {})
    return rows


def _paths(
    links: list[dict[str, Any]], raw: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    events: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        if link["link_type"] == "write_event":
            events.setdefault(link["path"], []).append(link)
    listed = {item["path"]: item for item in raw}
    rows = []
    for path in sorted(set(events) | set(listed)):
        ordered = sorted(
            events.get(path, []),
            key=lambda w: (w["t_end"], w["t_start"], w["event_id"]),
        )
        last = ordered[-1] if ordered else None
        row = {
            "path": path,
            "in_workspace": last["in_workspace"] if last else True,
            "tracked": True,
            "events": [w["event_id"] for w in ordered],
            "final_writer": last["event_id"] if last else None,
            "final_writer_under": bool(last and last["writer_under"]),
            "final_state": "changed",
            "unattributed_candidates": [],
        }
        row.update(copy.deepcopy(dict(listed.get(path) or {})))
        rows.append(row)
    return rows


def _set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    # Keys may contain dots ("C0.1"): the last level takes the remainder.
    node = target
    while (
        len(parts) > 1 and parts[0] in node and isinstance(node[parts[0]], dict)
    ):
        node = node[parts.pop(0)]
    node[".".join(parts)] = value


def _coverage(
    calls: list[dict[str, Any]],
    execs: list[dict[str, Any]],
    links: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    run: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    shell = [c for c in calls if c["tool_type"] == "shell"]
    status = {"absent": 0, "ok": 0, "skipped": 0, "truncated": 0}
    for record in execs:
        status[record["snapshot"]["status"]] += 1
    by_disposition: dict[str, int] = {}
    for row in dispositions:
        if row["item_type"] == "exec":
            name = row["disposition"]
            by_disposition[name] = by_disposition.get(name, 0) + 1
    coverage = {
        "run_key": run.get("run_key"),
        "family": run.get("family"),
        "inputs": {
            "log": True,
            "trace": True,
            "tracked_files": True,
            "final_diff": True,
        },
        "integrity": {"C0.1": True, "C0.2": 1.0, "C0.5": True},
        "counts": {
            "shell_calls": len(shell),
            "calls_running_at_stop": sum(
                1
                for row in dispositions
                if row["item_type"] == "call"
                and row["reason"] == "running_at_stop"
            ),
            "execs_total": len(execs),
            "execs_by_disposition": by_disposition,
            "final_diff_files": sum(
                1
                for p in paths
                if p["final_state"] in ("changed", "deleted", "mode_only")
            ),
            "snapshot_status_counts": status,
            "write_events_outside_workspace": sum(
                1
                for link in links
                if link["link_type"] == "write_event"
                and link["in_workspace"] is False
            ),
        },
        "rates": {
            "call_match_rate_under": 1.0,
            "call_match_rate_over": 1.0,
            "exec_explained_rate_under": 1.0,
            "exec_explained_rate_over": 1.0,
            "listed_path_rate": 1.0,
            "final_diff_attributed_rate_under": 1.0,
            "final_diff_attributed_rate_over": 1.0,
            "snapshot_scope": "tracked",
            "untracked_visible": False,
        },
        "capabilities": dict(run["capabilities"]),
        "warnings": [],
    }
    for key, value in overrides.items():
        _set_dotted(coverage, key, value)
    return coverage


def load_scenario(path: pathlib.Path) -> dict[str, Any]:
    """Loads one layer-2 scenario and fills in every default.

    Args:
        path: A ``facts_scenarios/*.yaml`` file.

    Returns:
        A dict with ``name``, ``description``, ``rules``, ``run``,
        ``calls``, ``execs`` (with ``env_keys`` set), ``linked`` (links,
        dispositions, paths, coverage), ``system_diff``, ``config`` and
        ``expect``.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    run = copy.deepcopy(dict(conftest.DEFAULT_RUN))
    run.update(DEFAULT_RUN_EXTRA)
    given = dict(raw.get("run") or {})
    run["capabilities"].update(given.pop("capabilities", None) or {})
    run.update(given)
    for key in ("t_start", "t_end"):
        if isinstance(run.get(key), int):
            run[key] = float(run[key])
    calls = [
        conftest.make_call(c, i) for i, c in enumerate(raw.get("calls") or [])
    ]
    execs = [
        conftest.make_exec(e, i, run["workspace_root"])
        for i, e in enumerate(raw.get("execs") or [])
    ]
    conftest.assign_exec_seq(execs)
    for record in execs:
        names = (raw.get("env_keys") or {}).get(record["command_id"])
        if names is not None:
            record["env_keys"] = list(names)
    links = _links(raw.get("links") or [], calls, execs)
    dispositions = _dispositions(
        calls, execs, links, raw.get("dispositions") or {}
    )
    paths = _paths(links, raw.get("paths") or [])
    coverage = _coverage(
        calls, execs, links, dispositions, paths, run, raw.get("coverage") or {}
    )
    return {
        "name": raw["name"],
        "description": raw["description"],
        "rules": list(raw.get("rules") or []),
        "run": run,
        "calls": calls,
        "execs": execs,
        "linked": {
            "links": links,
            "dispositions": dispositions,
            "paths": paths,
            "coverage": coverage,
        },
        "system_diff": raw.get("system_diff"),
        "config": raw.get("config"),
        "expect": raw.get("expect") or {},
    }


def run_derive(scenario: Mapping[str, Any], **overrides: Any) -> Any:
    """Calls ``facts.derive`` on a loaded scenario.

    Args:
        scenario: A dict from ``load_scenario``.
        **overrides: Replacements for ``calls``, ``execs``, ``linked``,
            ``run``, ``system_diff`` or ``config``.

    Returns:
        The result of ``facts.derive``.
    """
    from trajectory_checks import facts  # late, so test_facts_rules needs none

    keys = ("calls", "execs", "linked", "run", "system_diff")
    args = {key: copy.deepcopy(scenario[key]) for key in keys}
    args["config"] = copy.deepcopy(scenario["config"])
    args.update(overrides)
    return facts.derive(
        args["calls"],
        args["execs"],
        args["linked"],
        run=args["run"],
        system_diff=args["system_diff"],
        config=args["config"],
    )


def facts_value(facts: Mapping[str, Any], key: str) -> Any:
    """Looks up an ``expect.facts`` key in a facts.json value.

    Args:
        facts: The ``facts`` part of a ``derive`` result.
        key: A dotted key; a leading metric id means ``metrics.<id>``.

    Returns:
        The value found.

    Raises:
        KeyError: When a component is missing.
    """
    if any(key == m or key.startswith(m + ".") for m in METRIC_IDS):
        key = "metrics." + key
    return conftest.dotted(facts, key)
