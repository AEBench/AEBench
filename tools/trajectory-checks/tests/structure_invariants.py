"""Independent test oracle for layer 3 (structure spec §2.2.3).

Reads only the records given to ``structure.derive`` and the records it
returned. It imports nothing from ``trajectory_checks.structure``, so a bug
in the layer cannot also hide in the check. Every constant here is
restated from the spec, not imported.
"""

import collections
from collections.abc import Mapping
from typing import Any

# Spec §6.6: the nine role classes (D31: no `binary` class).
FILE_CLASSES = frozenset(
    {"ci", "build", "notebook", "test", "code", "docs", "data", "config"}
    | {"other"}
)
CLASS_RULES = frozenset(
    {"FC1", "FC2", "FC3", "FC4", "FC5", "FC6", "FC8", "FC9", "FC10", "FC11"}
)
CALL_CLASSES = frozenset({"act", "observe"})
CLASS_REASONS = frozenset(
    {"observe", "program", "redirect", "background", "no_command"}
)
ARG_CHANGE_TYPES = frozenset({"none", "numeric", "count", "words"})
OUTCOMES = frozenset({"ok", "failed", "killed", "unknown"})
FAILURES = frozenset({"failed", "killed"})
PRECONDITION_LABELS = (
    *("log", "C1_under", "final_diff", "C6", "C7_under"),
    *("untracked_visible", "tracked_files", "final_content"),
)
CAPABILITIES = (
    *("parent_links", "start_events", "exact_timing", "content_hashes"),
    *("untracked_visible", "read_tracing", "final_content", "oracle_reads"),
    *("egress_log", "stdio"),
)
M21_KEYS = (
    *("shell_calls", "act_calls", "observe_calls", "shape_classes"),
    *("repeated_shapes_all", "repeat_max_all", "repeated_shapes_act"),
    *("repeat_max_act", "exact_repeat_max_act", "program_classes_act"),
    *("repeated_programs_act", "program_repeat_max_act"),
)
M22_KEYS = (
    *("shape_reruns", "reruns_after_failure_under"),
    *("reruns_after_failure_over", "reruns_after_success_under"),
    *("reruns_after_success_over", "reruns_after_unknown"),
    "reruns_after_failure_unchanged_under",
    "reruns_after_failure_unchanged_over",
    "reruns_after_success_unchanged_under",
    "reruns_after_success_unchanged_over",
)
M23_KEYS = (
    *("program_reruns", "args_unchanged", "numeric_changed"),
    *("count_changed", "words_changed", "numeric_decreased"),
    *("numeric_increased", "count_decreased", "count_increased"),
    "narrowed",
)
M42_KEYS = (
    *("changed_by_class", "mode_only_by_class", "reverted_by_class"),
    *("changed_by_class_and_role_under", "changed_by_class_and_role_over"),
    *("shell_written_by_class_under", "shell_written_by_class_over"),
    *("created_by_class_under", "created_by_class_over"),
    *("baseline_by_class", "binary_changed_by_class"),
)
METRIC_KEYS = {
    "M2.1": M21_KEYS,
    "M2.2": M22_KEYS,
    "M2.3": M23_KEYS,
    "M4.2": M42_KEYS,
}
_CONTENT = ("changed", "deleted")
# §7 and PRE5: label -> the (block, key) pairs its failure voids, in the
# warning order of §7. final_diff voids every key counted over the final
# diff's populations (adopted reading, decision table of the spec), so C6
# and C7_under add no warning when final_diff fails (FS08).
_FINAL_DIFF_KEYS = (
    *("changed_by_class", "mode_only_by_class", "reverted_by_class"),
    *("changed_by_class_and_role_under", "changed_by_class_and_role_over"),
    *("shell_written_by_class_under", "shell_written_by_class_over"),
    "binary_changed_by_class",
)
_ROLE_KEYS = (
    *("changed_by_class_and_role_under", "changed_by_class_and_role_over"),
    *("shell_written_by_class_under", "shell_written_by_class_over"),
)
GUARDS = (
    ("log", [("M2.1", k) for k in M21_KEYS] + [("M2.3", k) for k in M23_KEYS]),
    ("C1_under", [("M2.2", k) for k in M22_KEYS]),
    ("final_diff", [("M4.2", k) for k in _FINAL_DIFF_KEYS]),
    ("C6", [("M4.2", k) for k in ("reverted_by_class", *_ROLE_KEYS)]),
    ("C7_under", [("M4.2", k) for k in _ROLE_KEYS]),
    (
        "untracked_visible",
        [("M4.2", "created_by_class_under"), ("M4.2", "created_by_class_over")],
    ),
    ("tracked_files", [("M4.2", "baseline_by_class")]),
    ("final_content", [("M4.2", "binary_changed_by_class")]),
)
# PRE8's warning form, by capability.
_PRE8 = "capability {} true but "


def _sum(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(_sum(v) for v in value.values())
    return int(value or 0)


def _shell_calls(inputs: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return sorted(
        (c for c in inputs["calls"] if c["tool_type"] == "shell"),
        key=lambda c: c["seq"],
    )


def _row_problems(
    inputs: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    """Invariants 1 to 4 and the per-row restatements."""
    problems = []
    shell = _shell_calls(inputs)
    rows = result["call_structure"]
    # 1. One call_structure row per shell call, in seq order.
    if [r["call_id"] for r in rows] != [c["call_id"] for c in shell]:
        problems.append("call_structure rows are not the shell calls in order")
        return problems
    path_rows = inputs["facts_result"]["path_facts"]
    if [r["path"] for r in result["file_classes"]] != [
        p["path"] for p in path_rows
    ]:
        problems.append("file_classes rows are not the path_facts rows")
    by_call = {r["call_id"]: r for r in rows}
    shell_by_id = {c["call_id"]: c for c in shell}
    for row in rows:
        where = f"call_structure[{row['call_id']}]"
        if row["call_class"] not in CALL_CLASSES:
            problems.append(f"{where}: call_class {row['call_class']!r}")
        if row["class_reason"] not in CLASS_REASONS:
            problems.append(f"{where}: class_reason {row['class_reason']!r}")
        if (row["call_class"] == "observe") != (
            row["class_reason"] == "observe"
        ):
            problems.append(f"{where}: class_reason disagrees with call_class")
        if row["seq"] != shell_by_id[row["call_id"]]["seq"]:
            problems.append(f"{where}: seq differs from the call's")
        if row["call_class"] == "observe" and (
            row["shape_index"] is not None or row["program_index"] is not None
        ):
            problems.append(f"{where}: an observe call has a class index")
    # 2. Partition, and KEY5 numbering by first member.
    for field, prefix in (
        ("exact_class", "x"),
        ("shape_class", "s"),
        ("program_class", "p"),
    ):
        first_seen: list[str] = []
        for row in rows:
            if row[field] not in first_seen:
                first_seen.append(row[field])
        if first_seen != [f"{prefix}{i}" for i in range(len(first_seen))]:
            problems.append(f"{field} ids not numbered by first member")
    # KEY1: the exact class is the command_norm.
    norm_class: dict[str, str] = {}
    for row in rows:
        norm = shell_by_id[row["call_id"]]["command_norm"] or ""
        if (
            norm_class.setdefault(norm, row["exact_class"])
            != row["exact_class"]
        ):
            problems.append(
                f"equal command_norm in two exact classes: {norm!r}"
            )
    # 3. Refinement exact ⊑ shape ⊑ program, and one key per program class.
    for finer, coarser in (
        ("exact_class", "shape_class"),
        ("shape_class", "program_class"),
    ):
        seen: dict[str, str] = {}
        for row in rows:
            if seen.setdefault(row[finer], row[coarser]) != row[coarser]:
                problems.append(f"{finer} {row[finer]} spans two {coarser}")
    keys: dict[str, str] = {}
    for row in rows:
        if (
            keys.setdefault(row["program_class"], row["program_key"])
            != (row["program_key"])
        ):
            problems.append(f"program class {row['program_class']}: two keys")
    shapes = {
        f["call_id"]: f["command_shape"]
        for f in inputs["facts_result"]["call_facts"]
    }
    shape_class: dict[str, str] = {}
    for row in rows:
        shape = shapes[row["call_id"]]
        if (
            shape_class.setdefault(shape, row["shape_class"])
            != (row["shape_class"])
        ):
            problems.append(f"equal command_shape in two classes: {shape!r}")
    # 4. Edges.
    problems += _edge_problems(rows, by_call, result["reruns"])
    return problems


def _edge_problems(
    rows: list[Mapping[str, Any]],
    by_call: Mapping[str, Mapping[str, Any]],
    reruns: list[Mapping[str, Any]],
) -> list[str]:
    problems = []
    order = [(r["key_type"] != "shape", r["seq"]) for r in reruns]
    if order != sorted(order):
        problems.append("reruns not sorted by key_type then seq")
    if [r["rerun_id"] for r in reruns] != [f"r{i}" for i in range(len(reruns))]:
        problems.append("rerun_id not r<n> in sort order")
    for key_type, field, index, prev in (
        ("shape", "shape_class", "shape_index", "prev_shape_call"),
        ("program", "program_class", "program_index", "prev_program_call"),
    ):
        members = collections.defaultdict(list)
        for row in rows:
            if row["call_class"] == "act":
                members[row[field]].append(row)
        expected = set()
        for class_id, act in members.items():
            for i, row in enumerate(act):
                if row[index] != i + 1:
                    problems.append(f"{row['call_id']}: {index} != {i + 1}")
                want = act[i - 1]["call_id"] if i else None
                if row[prev] != want:
                    problems.append(f"{row['call_id']}: {prev} != {want}")
                if i:
                    expected.add((class_id, want, row["call_id"]))
        got = {
            (r["class_id"], r["prev_call_id"], r["call_id"])
            for r in reruns
            if r["key_type"] == key_type
        }
        if got != expected:
            problems.append(
                f"{key_type} edges {sorted(got)} != {sorted(expected)}"
            )
    for edge in reruns:
        where = f"reruns[{edge['rerun_id']}]"
        a, b = by_call.get(edge["prev_call_id"]), by_call.get(edge["call_id"])
        if a is None or b is None:
            problems.append(f"{where}: unknown call")
            continue
        if not edge["prev_seq"] < edge["seq"]:
            problems.append(f"{where}: prev_seq >= seq")
        if (a["seq"], b["seq"]) != (edge["prev_seq"], edge["seq"]):
            problems.append(f"{where}: seqs differ from the calls'")
        if a["call_class"] != "act" or b["call_class"] != "act":
            problems.append(f"{where}: joins a non-act call")
    return problems


def _edge_value_problems(
    inputs: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    """Invariants 7, 8, 12, 13 and restated per-edge counts."""
    problems = []
    shell = _shell_calls(inputs)
    by_call = {r["call_id"]: r for r in result["call_structure"]}
    call_by_id = {c["call_id"]: c for c in shell}
    facts_of = {f["call_id"]: f for f in inputs["facts_result"]["call_facts"]}
    exec_by_id = {e["command_id"]: e for e in inputs["execs"]}
    calls_seq = {c["call_id"]: c["seq"] for c in inputs["calls"]}
    edit_events = [
        lk
        for lk in inputs["linked"]["links"]
        if lk["link_type"] == "write_event" and lk["writer_type"] == "edit_call"
    ]
    capabilities = inputs["run_info"]["capabilities"]
    visible_run = bool(
        capabilities.get("untracked_visible")
        or inputs["linked"]["coverage"]["rates"].get("untracked_visible")
    )
    for edge in result["reruns"]:
        where = f"reruns[{edge['rerun_id']}]"
        if edge["prev_call_id"] not in facts_of or edge["call_id"] not in (
            facts_of
        ):
            continue
        prev, cur = facts_of[edge["prev_call_id"]], facts_of[edge["call_id"]]
        lo, hi = edge["prev_seq"], edge["seq"]
        between = [c for c in shell if lo < c["seq"] < hi]
        act_between = [
            c for c in between if by_call[c["call_id"]]["call_class"] == "act"
        ]
        if edge["calls_between"] != len(between):
            problems.append(f"{where}: calls_between != {len(between)}")
        if edge["act_calls_between"] != len(act_between):
            problems.append(f"{where}: act_calls_between != {len(act_between)}")
        edits = sum(
            1
            for w in edit_events
            if lo < calls_seq.get(w["writer_id"], -1) < hi
        )
        if edge["edit_calls_between"] != edits:
            problems.append(f"{where}: edit_calls_between != {edits}")
        if edge["prev_outcome"] != prev["outcome"]:
            problems.append(f"{where}: prev_outcome differs from call_facts")
        if edge["prev_outcome"] not in OUTCOMES:
            problems.append(f"{where}: prev_outcome {edge['prev_outcome']!r}")
        if edge["prev_match_under"] is not bool(prev["match_under"]):
            problems.append(f"{where}: prev_match_under differs")
        norm_a = call_by_id[edge["prev_call_id"]]["command_norm"] or ""
        norm_b = call_by_id[edge["call_id"]]["command_norm"] or ""
        if edge["identical"] is not (norm_a == norm_b):
            problems.append(f"{where}: identical disagrees with command_norm")
        if edge["arg_count_delta"] != cur["arg_count"] - prev["arg_count"]:
            problems.append(f"{where}: arg_count_delta")
        if edge["arg_change_type"] not in ARG_CHANGE_TYPES:
            problems.append(f"{where}: arg_change_type")
        # 8. numeric exactly with numeric_changes; identical implies none.
        if (edge["arg_change_type"] == "numeric") != bool(
            edge["numeric_changes"]
        ):
            problems.append(f"{where}: numeric disagrees with numeric_changes")
        if edge["identical"] and edge["arg_change_type"] != "none":
            problems.append(f"{where}: identical but {edge['arg_change_type']}")
        if edge["narrowed"] is not bool(
            edge["words_removed"] and not edge["words_added"]
        ):
            problems.append(f"{where}: narrowed disagrees with the words")
        for key in ("words_removed", "words_added"):
            if edge[key] != sorted(edge[key]):
                problems.append(f"{where}: {key} not sorted")
        if (
            edge["numeric_direction"] is not None
            and not edge["numeric_changes"]
        ):
            problems.append(f"{where}: a direction without numeric changes")
        # 7. Under within over.
        if edge["unchanged_under"] and not edge["unchanged_over"]:
            problems.append(f"{where}: unchanged_under but not _over")
        if edge["writes_between_under"] > edge["writes_between_over"]:
            problems.append(f"{where}: writes under > over")
        over = edge["edit_calls_between"] == 0 and (
            edge["writes_between_under"] == 0
        )
        if edge["unchanged_over"] is not over:
            problems.append(f"{where}: unchanged_over != RR6's formula")
        if edge["unchanged_under"] and not (
            edge["edit_calls_between"] == 0
            and edge["writes_between_over"] == 0
            and edge["gap_observed"]
            and edge["untracked_writes_between"] in (0, None)
        ):
            problems.append(f"{where}: unchanged_under with a change")
        # 12. untracked_writes_between non-null exactly with the capability.
        if (edge["untracked_writes_between"] is not None) is not bool(
            capabilities.get("untracked_visible")
        ):
            problems.append(f"{where}: untracked_writes_between vs capability")
        # 13. The exact form needs certain, complete act calls in between.
        if edge["unchanged_under"] and edge["act_calls_between"] > 0:
            if not visible_run:
                problems.append(f"{where}: unchanged_under past an act call")
            for call in act_between:
                facts = facts_of[call["call_id"]]
                record = exec_by_id.get(facts["command_id"] or "")
                if not facts["match_under"] or record is None:
                    problems.append(f"{where}: uncertain act call between")
                elif record["snapshot"]["status"] != "ok":
                    problems.append(f"{where}: incomplete snapshot between")
        if (
            edge["unchanged_under"]
            and result["structure"]["bases"].get("unchanged_under")
            == "fallback"
            and edge["act_calls_between"] != 0
        ):
            problems.append(f"{where}: fallback unchanged_under past act call")
    return problems


def _file_problems(
    inputs: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    """Invariants 9 and 12 on file_classes and M4.2."""
    problems = []
    final_content = bool(
        inputs["run_info"]["capabilities"].get("final_content")
    ) and (inputs["final_diff_text"] is not None)
    for row in result["file_classes"]:
        where = f"file_classes[{row['path']}]"
        if row["file_class"] not in FILE_CLASSES:
            problems.append(f"{where}: class {row['file_class']!r}")
        if row["class_rule"] not in CLASS_RULES:
            problems.append(f"{where}: class_rule {row['class_rule']!r}")
        if row["extension"] != row["extension"].lower():
            problems.append(f"{where}: extension not lower-case")
        if row["binary_content"] is not None and not final_content:
            problems.append(f"{where}: binary_content without final content")
        if row["mode_source"] not in (None, "baseline"):
            problems.append(f"{where}: mode_source {row['mode_source']!r}")
    m42 = result["structure"]["metrics"]["M4.2"]
    classes = {r["path"]: r["file_class"] for r in result["file_classes"]}
    changed = [
        p
        for p in inputs["facts_result"]["path_facts"]
        if p["in_workspace"] and p["final_state"] in _CONTENT
    ]
    if m42["changed_by_class"] is not None:
        if _sum(m42["changed_by_class"]) != len(changed):
            problems.append(
                "changed_by_class does not sum to the changed paths"
            )
        want = collections.Counter(classes[p["path"]] for p in changed)
        if dict(want) != m42["changed_by_class"]:
            problems.append("changed_by_class != the rows' classes")
    for key, value in m42.items():
        if isinstance(value, Mapping):
            for name, count in value.items():
                if name not in FILE_CLASSES:
                    problems.append(f"M4.2.{key}: unknown class {name!r}")
                if count == 0:
                    problems.append(f"M4.2.{key}.{name}: 0 not omitted")
    binary, base = m42["binary_changed_by_class"], m42["changed_by_class"]
    if binary is not None and base is not None:
        for name, count in binary.items():
            if count > base.get(name, 0):
                problems.append(f"binary_changed_by_class.{name} > changed")
    for under_key in (
        "changed_by_class_and_role_under",
        "shell_written_by_class_under",
        "created_by_class_under",
    ):
        under = m42[under_key]
        over = m42[under_key.replace("_under", "_over")]
        if (under is None) != (over is None):
            problems.append(f"M4.2.{under_key}: one of under, over is null")
        elif under is not None:
            for name, value in under.items():
                if isinstance(value, Mapping):
                    for role, count in value.items():
                        if count > over.get(name, {}).get(role, 0):
                            problems.append(f"{under_key}.{name}.{role} > over")
                elif value > over.get(name, 0):
                    problems.append(f"{under_key}.{name} > over")
    return problems


def _structure_problems(
    inputs: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    """Invariants 5, 6, 7, 10 and 11 on structure.json."""
    problems = []
    structure = result["structure"]
    metrics = structure["metrics"]
    if structure["schema_version"] != "1":
        problems.append("schema_version != '1'")
    for block, keys in METRIC_KEYS.items():
        if set(metrics.get(block) or {}) != set(keys):
            problems.append(f"{block}: keys differ from §5.4")
    if set(structure["preconditions"]) != set(PRECONDITION_LABELS):
        problems.append("preconditions: labels differ from §7")
    for label, state in structure["preconditions"].items():
        if state not in ("holds", "vacuous", "fails"):
            problems.append(f"precondition {label}: {state!r}")
    # 5. M2.2 buckets partition the shape edges.
    m22 = metrics["M2.2"]
    if m22["shape_reruns"] is not None:
        total = (
            m22["reruns_after_failure_over"]
            + m22["reruns_after_success_over"]
            + m22["reruns_after_unknown"]
        )
        if total != m22["shape_reruns"]:
            problems.append("M2.2 buckets do not sum to shape_reruns")
        shape_edges = [r for r in result["reruns"] if r["key_type"] == "shape"]
        if m22["shape_reruns"] != len(shape_edges):
            problems.append("M2.2.shape_reruns != shape edges")
        problems += _pair_problems("M2.2", m22)
    # 6. M2.3 change types partition the program edges.
    m23 = metrics["M2.3"]
    if m23["program_reruns"] is not None:
        total = sum(
            m23[k]
            for k in (
                *("args_unchanged", "numeric_changed"),
                *("count_changed", "words_changed"),
            )
        )
        if total != m23["program_reruns"]:
            problems.append("M2.3 change types do not sum to program_reruns")
        program_edges = [
            r for r in result["reruns"] if r["key_type"] == "program"
        ]
        if m23["program_reruns"] != len(program_edges):
            problems.append("M2.3.program_reruns != program edges")
        if m23["narrowed"] > m23["count_decreased"]:
            problems.append("M2.3.narrowed > count_decreased")
    m21 = metrics["M2.1"]
    if m21["shell_calls"] is not None:
        rows = result["call_structure"]
        if m21["shell_calls"] != len(rows):
            problems.append("M2.1.shell_calls != rows")
        acts = sum(r["call_class"] == "act" for r in rows)
        if (m21["act_calls"], m21["observe_calls"]) != (acts, len(rows) - acts):
            problems.append("M2.1 act/observe counts != rows")
        if m21["repeat_max_act"] > m21["repeat_max_all"]:
            problems.append("M2.1.repeat_max_act > repeat_max_all")
        if m21["exact_repeat_max_act"] > m21["repeat_max_act"]:
            problems.append("exact ⊑ shape broken in M2.1 maxima")
        if m21["repeat_max_act"] > m21["program_repeat_max_act"]:
            problems.append("shape ⊑ program broken in M2.1 maxima")
    problems += _void_problems(structure)
    problems += _basis_problems(inputs, structure)
    return problems


def _pair_problems(where: str, block: Mapping[str, Any]) -> list[str]:
    problems = []
    for key, under in block.items():
        if not key.endswith("_under"):
            continue
        over = block.get(key[: -len("_under")] + "_over")
        if under is not None and over is not None and under > over:
            problems.append(f"{where}.{key}: {under} > over {over}")
    return problems


def _void_problems(structure: Mapping[str, Any]) -> list[str]:
    """Invariant 10: voided keys and one warning per voiding label."""
    problems = []
    states = structure["preconditions"]
    warnings = structure["warnings"]
    voided: set[tuple[str, str]] = set()
    for label, keys in GUARDS:
        if states.get(label) != "fails":
            continue
        for block, key in keys:
            if structure["metrics"][block][key] is not None:
                problems.append(f"{label} fails but {block}.{key} is set")
        new = set(keys) - voided
        voided |= set(keys)
        named = [
            w
            for w in warnings
            if f" {label} " in f" {w} " and not w.startswith("capability ")
        ]
        if new and len(named) != 1:
            problems.append(f"{label}: {len(named)} warnings, expected 1")
        if not new and named:
            problems.append(f"{label}: a warning for keys already void")
    return problems


def _basis_problems(
    inputs: Mapping[str, Any], structure: Mapping[str, Any]
) -> list[str]:
    """Invariant 11."""
    problems = []
    given = inputs["run_info"]["capabilities"]
    if structure["capabilities"] != {
        n: bool(given.get(n)) for n in CAPABILITIES
    }:
        problems.append("structure capabilities != run_info capabilities")
    bases = structure["bases"]
    if set(bases) != {"writes_between", "gap", "unchanged_under"}:
        problems.append(f"bases keys {sorted(bases)}")
    for key in bases:
        if bases[key] not in ("exact", "fallback"):
            problems.append(f"bases.{key}: {bases[key]!r}")
    visible = bool(
        given.get("untracked_visible")
        or inputs["linked"]["coverage"]["rates"].get("untracked_visible")
    )
    for key, name in (
        ("writes_between", "parent_links"),
        ("gap", "exact_timing"),
    ):
        if bases[key] == "exact" and not given.get(name):
            problems.append(f"bases.{key} exact without {name}")
        warned = any(
            w.startswith(_PRE8.format(name)) for w in structure["warnings"]
        )
        if warned and bases[key] != "fallback":
            problems.append(f"bases.{key} exact despite a PRE8 warning")
    warned = any(
        w.startswith(_PRE8.format("untracked_visible"))
        for w in structure["warnings"]
    )
    if bases["unchanged_under"] == "exact" and not visible:
        problems.append("bases.unchanged_under exact in a tracked-only run")
    if visible and not warned and bases["unchanged_under"] != "exact":
        problems.append("bases.unchanged_under fallback in a visible run")
    return problems


def check(inputs: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    """Checks every layer-3 invariant of spec §2.2.3.

    Args:
        inputs: A loaded scenario (the records given to derive).
        result: The value ``structure.derive`` returned.

    Returns:
        One message per violated invariant; empty when all hold.
    """
    problems = _row_problems(inputs, result)
    if problems:
        return problems
    problems += _edge_value_problems(inputs, result)
    problems += _file_problems(inputs, result)
    problems += _structure_problems(inputs, result)
    return problems
