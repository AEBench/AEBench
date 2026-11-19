"""Independent test oracle for layer 2 (spec §2.2.3 of the facts spec).

Reads only the records given to ``facts.derive`` and the records it
returned. It imports nothing from ``trajectory_checks.facts``, so a bug in
the layer cannot also hide in the check. Every constant here is restated
from the spec, not imported.
"""

from collections.abc import Mapping
from typing import Any

# Spec §3.3, SHELL_MANAGED_ENV.
SHELL_MANAGED = frozenset({"_", "SHLVL", "PWD", "OLDPWD"})
# Spec §7: label -> metric blocks it voids entirely.
BLOCK_GUARDS = {
    "C1_under": ("M1.1", "M1.3", "M1.4", "M5.2", "M6.1"),
    "C2_under": ("M1.4",),
    "C6": ("M4.1",),
    "C7_under": ("M4.1",),
    "env_keys": ("M5.2",),
    "log": ("M1.2",),
}
# Spec §7: label -> (block, field) pairs it voids.
FIELD_GUARDS = {
    "untracked_visible": (
        ("M4.1", "created_by_final_writer_under"),
        ("M4.1", "created_by_final_writer_over"),
        ("M6.1", "network_files_created_under"),
        ("M6.1", "network_files_created_over"),
    ),
    "system_diff": (
        ("M4.4", "executables"),
        ("M4.4", "python_startup"),
        ("M4.4", "etc"),
        ("M4.4", "changed_total"),
    ),
    "log": (("M4.4", "outside_workspace_writes"),),
}
# Spec §5.4: the text that identifies each label's warning.
WARNING_KEYS = {
    "C1_under": "precondition C1_under ",
    "C2_under": "precondition C2_under ",
    "C6": "precondition C6 ",
    "C7_under": "precondition C7_under ",
    "untracked_visible": "precondition untracked_visible ",
    "env_keys": "input env_keys ",
    "system_diff": "input system_files_diff.json ",
    "log": "input log ",
}
OUTCOMES = ("ok", "failed", "killed", "unknown")


def _under_over_pairs(record: Mapping[str, Any]) -> list[tuple[str, Any, Any]]:
    pairs = []
    for key, under in record.items():
        # Percentiles of a subset may exceed those of the superset.
        if "_under" not in key or "_p50_" in key or "_p90_" in key:
            continue
        over_key = key.replace("_under", "_over")
        if over_key in record:
            pairs.append((key, under, record[over_key]))
    return pairs


def _pair_problems(where: str, record: Mapping[str, Any]) -> list[str]:
    problems = []
    for key, under, over in _under_over_pairs(record):
        if under is None or over is None:
            continue
        if isinstance(under, bool):
            if under and not over:
                problems.append(f"{where}.{key}: true but over false")
        elif isinstance(under, int | float):
            if under > over + 1e-9:
                problems.append(f"{where}.{key}: {under} > over {over}")
        elif isinstance(under, list):
            if not set(under) <= set(over):
                problems.append(f"{where}.{key}: {under} not within {over}")
        elif isinstance(under, Mapping):
            for name, value in under.items():
                if value > over.get(name, 0):
                    problems.append(f"{where}.{key}.{name}: under > over")
    return problems


def check(inputs: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    """Checks every layer-2 invariant.

    Args:
        inputs: ``calls``, ``execs`` and ``linked`` as given to derive.
        result: The value ``facts.derive`` returned.

    Returns:
        One message per violated invariant; empty when all hold.
    """
    problems: list[str] = []
    calls, execs = inputs["calls"], inputs["execs"]
    links = inputs["linked"]["links"]
    paths = inputs["linked"]["paths"]
    shell = sorted(
        (c for c in calls if c["tool_type"] == "shell"), key=lambda c: c["seq"]
    )
    j1 = {lk["call_id"]: lk for lk in links if lk["link_type"] == "call_exec"}
    roots = {lk["command_id"] for lk in j1.values()}
    j2 = [lk for lk in links if lk["link_type"] == "exec_parent"]
    events = [lk for lk in links if lk["link_type"] == "write_event"]
    noise = {
        row["id"]
        for row in inputs["linked"]["dispositions"]
        if row["item_type"] == "exec" and row["disposition"] == "noise"
    } | {e["command_id"] for e in execs if e.get("noise")}

    call_facts = result["call_facts"]
    # 1. One row per item, in order.
    if [r["call_id"] for r in call_facts] != [c["call_id"] for c in shell]:
        problems.append("call_facts rows are not the shell calls in seq order")
    live = sorted(
        (e for e in execs if e["command_id"] not in noise),
        key=lambda e: e["seq"],
    )
    if [r["command_id"] for r in result["exec_facts"]] != [
        e["command_id"] for e in live
    ]:
        problems.append("exec_facts rows are not the non-noise execs in order")
    if sorted(r["path"] for r in result["path_facts"]) != sorted(
        p["path"] for p in paths
    ):
        problems.append("path_facts rows are not the paths rows")

    for row in call_facts:
        where = f"call_facts[{row['call_id']}]"
        link = j1.get(row["call_id"])
        # 2. command_id and match_rule follow J1.
        if (row["command_id"] is None) != (link is None):
            problems.append(f"{where}: command_id disagrees with J1")
        if link and row["match_rule"] != link["rule"]:
            problems.append(f"{where}: match_rule != {link['rule']}")
        # 3. Outcome and exit_signal.
        problems += _outcome_problems(where, row)
        # 4. Under within over.
        problems += _pair_problems(where, row)
        # 5. Spans.
        if row["span_under_s"] is not None and row["duration_s"] is not None:
            if row["span_under_s"] + 1e-9 < row["duration_s"]:
                problems.append(f"{where}: span_under_s < duration_s")
        if row["span_under_s"] is not None and row["span_over_s"] is not None:
            if row["span_over_s"] + 1e-9 < row["span_under_s"]:
                problems.append(f"{where}: span_over_s < span_under_s")
        # 6. Descendants and writes match the links.
        owned = [lk for lk in j2 if lk["root_call_id"] == row["call_id"]]
        if row["descendants_over"] != len(owned):
            problems.append(f"{where}: descendants_over != J2 links")
        writes = [
            w
            for w in events
            if w["writer_type"] == "exec"
            and w["root_call_id"] == row["call_id"]
        ]
        if sum(row["writes_over"].values()) != len(writes):
            problems.append(f"{where}: writes_over != exec write events")
        # 10. Environment.
        if row["env_confirmed"] is not None and not set(
            row["env_confirmed"]
        ) <= set(row["env_assigned"]):
            problems.append(f"{where}: env_confirmed not within env_assigned")

    for row in result["exec_facts"]:
        where = f"exec_facts[{row['command_id']}]"
        problems += _outcome_problems(where, row)
        # 7. Depth and root.
        if row["command_id"] in roots and row["depth"] != 0:
            problems.append(f"{where}: matched root with depth {row['depth']}")
        if row["disposition"] == "nested" and not (row["depth"] or 0) >= 1:
            problems.append(f"{where}: nested exec with depth {row['depth']}")
        unrooted = row["disposition"] in ("unrooted", "out_of_window")
        if unrooted != (row["root_call_id"] is None):
            problems.append(f"{where}: root_call_id disagrees with disposition")
        if unrooted and row["depth"] is not None:
            problems.append(f"{where}: unrooted exec with a depth")
        if row["env_added"] and set(row["env_added"]) & SHELL_MANAGED:
            problems.append(f"{where}: shell-managed name in env_added")

    for row in result["path_facts"]:
        where = f"path_facts[{row['path']}]"
        # 8. Final writer role.
        if (row["final_writer_role"] is None) != (row["final_writer"] is None):
            problems.append(f"{where}: final_writer_role disagrees")
        problems += _pair_problems(where, row)

    problems += _facts_problems(result["facts"])
    problems += _capability_problems(inputs, result)
    return problems


# Spec §6.14: bases key -> capability.
BASES = {
    "timing": "exact_timing",
    "depth": "parent_links",
    "running_at_stop": "start_events",
}


def _capability_problems(
    inputs: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    problems = []
    facts = result["facts"]
    given = inputs["linked"]["coverage"]["capabilities"]
    # 11. Capabilities copied; bases exact exactly when the capability is.
    if facts["capabilities"] != given:
        problems.append("capabilities differ from the input's")
    for key, name in BASES.items():
        want = "exact" if given[name] else "fallback"
        if facts["bases"].get(key) != want:
            problems.append(f"bases.{key} {facts['bases'].get(key)} != {want}")
    # 12. depth_max_under set exactly when the form is exact and every
    # depth it covers is known (S7), and then equal to over.
    exact = facts["bases"].get("depth") == "exact"
    depth_of = {e["command_id"]: e["depth"] for e in inputs["execs"]}
    members: dict[str, list[str]] = {}
    for link in inputs["linked"]["links"]:
        call = link.get("call_id") or link.get("root_call_id")
        if link["link_type"] in ("call_exec", "exec_parent"):
            members.setdefault(call, []).append(link["command_id"])
    block = facts["metrics"]["M1.4"]
    rows = [r for r in result["call_facts"] if r["command_id"] is not None]
    known = {
        r["call_id"]: all(
            depth_of[c] is not None for c in members.get(r["call_id"], ())
        )
        for r in rows
    }
    checked = [(r, known[r["call_id"]]) for r in rows]
    if block["depth_max_over"] is not None:
        checked.append((block, all(known.values())))
    for row, every in checked:
        under, over = row["depth_max_under"], row["depth_max_over"]
        if (under is not None) != (exact and every) or (
            under is not None and under != over
        ):
            problems.append(f"depth_max_under {under} with over {over}")
    return problems


def _outcome_problems(where: str, row: Mapping[str, Any]) -> list[str]:
    problems = []
    if row["outcome"] not in OUTCOMES:
        problems.append(f"{where}: outcome {row['outcome']!r}")
    code, signal = row.get("exit_code"), row.get("signal")
    if "exit_code" in row:
        expected = (
            "killed"
            if signal is not None
            else "unknown"
            if code is None
            else "ok"
            if code == 0
            else "failed"
        )
        if row["outcome"] != expected:
            problems.append(f"{where}: outcome {row['outcome']} != {expected}")
        if row["exit_signal"] is not None and (
            signal is not None or code is None or not 129 <= code <= 192
        ):
            problems.append(f"{where}: exit_signal without a 128+n exit")
    return problems


def _facts_problems(facts: Mapping[str, Any]) -> list[str]:
    problems = []
    pre, metrics = facts["preconditions"], facts["metrics"]
    for label, value in pre.items():
        if value not in ("holds", "vacuous", "fails"):
            problems.append(f"preconditions.{label}: {value!r}")
    # 9. Voiding and warnings.
    for label, blocks in BLOCK_GUARDS.items():
        if pre.get(label) == "fails":
            for block in blocks:
                if any(v is not None for v in metrics[block].values()):
                    problems.append(f"{block} not null although {label} fails")
    for label, fields in FIELD_GUARDS.items():
        if pre.get(label) == "fails":
            for block, field in fields:
                if metrics[block][field] is not None:
                    problems.append(f"{block}.{field} not null ({label} fails)")
    for label, key in WARNING_KEYS.items():
        hits = [w for w in facts["warnings"] if w.startswith(key)]
        want = 1 if pre.get(label) == "fails" else 0
        if len(hits) != want:
            problems.append(
                f"{len(hits)} warnings for {label}, expected {want}"
            )
    for block, fields in metrics.items():
        for field, value in fields.items():
            numbers = value.values() if isinstance(value, Mapping) else [value]
            for number in numbers:
                if isinstance(number, int | float) and number < 0:
                    problems.append(f"{block}.{field} negative")
        problems += _pair_problems(block, fields)
    shapes = metrics["M1.2"]
    if shapes["root_shapes"] is not None and (
        shapes["root_shapes"] > shapes["shell_calls"]
    ):
        problems.append("M1.2 root_shapes > shell_calls")
    names = metrics["M5.2"]
    if names["names_observed"] and set(names["names_observed"]) & SHELL_MANAGED:
        problems.append("M5.2 names_observed holds a shell-managed name")
    return problems
