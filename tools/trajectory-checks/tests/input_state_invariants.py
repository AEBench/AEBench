"""Layer 4: independent invariant checks of both modules' outputs.

Test-oracle code (spec §0.6 and §2.2.3): it reads only layer 4's output
records and the records given to the module, and imports nothing from
``oracle_inputs.py`` or ``input_state.py``. ``check_oracle_inputs`` holds
§0.6's invariants of ``oracle_inputs.jsonl`` and ``oracle_inputs.json``;
``check_input_states`` the ten invariants of §2.2.3.
"""

from collections.abc import Mapping, Sequence
from typing import Any

STATES = (
    *("pristine", "rewritten_identical", "edit_written", "command_written"),
    *("oracle_written", "deleted", "missing"),
)
PREDICATES = {
    "baseline_content": frozenset({"pristine", "rewritten_identical"}),
    "fresh": frozenset(
        {
            *("rewritten_identical", "edit_written", "command_written"),
            "oracle_written",
        }
    ),
    "agent_written": frozenset({"edit_written", "command_written"}),
    "edit_written": frozenset({"edit_written"}),
    "command_written": frozenset({"command_written"}),
    "absent": frozenset({"deleted", "missing"}),
}
# Spec §5.2: the M3.2 keys whose value is a count over file rows.
M32_COUNT_PAIRS = tuple(
    (f"{name}_under", f"{name}_over")
    for name in (*PREDICATES, "created_by_agent", "written_unseen")
)
VOIDING = ("oracle_inputs", "log")  # PRE1, PRE2 void M3.2 (§7)


def _bases_of(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        row["oracle_writes_basis"]
        for row in rows
        if row["oracle_writes"] is not None
    }


def check_oracle_inputs(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    """§0.6's invariants of one oracle_inputs.derive result.

    Args:
        scenario: A dict from ``oracle_inputs_helpers.build_scenario``.
        result: The value of ``oracle_inputs.derive`` on it.

    Returns:
        One message per violated invariant; empty when all hold.
    """
    problems = []
    rows = result["oracle_inputs"]
    block = result["oracle_inputs_run"]
    run = scenario["run"]
    keys = [(row["path"], row["pattern"]) for row in rows]
    if len(set(keys)) != len(keys):
        problems.append("two rows share (path, pattern)")
    order = [
        (not r["in_workspace"], r["path"], r["pattern"] is not None)
        for r in rows
    ]
    if order != sorted(order):
        problems.append(f"rows not in OI7 order: {keys}")
    if [row["input_id"] for row in rows] != [f"i{n}" for n in range(len(rows))]:
        problems.append("input_id is not i<n> in row order")
    patterns = {r["input_id"] for r in rows if r["match_type"] == "pattern"}
    capability_exact = bool(
        run["capabilities"]["oracle_reads"]
        and run["capabilities"]["final_content"]
        and scenario["final_files"] is not None
    )
    for row in rows:
        where = f"row {row['input_id']} {row['path']}"
        if set(row["listed_by"]) - patterns:
            problems.append(f"{where}: listed_by names a non-pattern row")
        if row["listed_by"] != sorted(
            row["listed_by"], key=lambda i: int(i[1:])
        ):
            problems.append(f"{where}: listed_by not sorted by id")
        if row["match_type"] == "pattern_match" and not row["listed_by"]:
            problems.append(f"{where}: pattern_match row listed by nothing")
        if row["in_under"] != (row["match_type"] == "literal"):
            problems.append(f"{where}: in_under is not 'literal row'")
        grading = (
            row["grading_exists"],
            row["grading_sha256"],
            row["grading_mtime_ns"],
        )
        if row["source_type"] == "null_agent" and grading != (None,) * 3:
            problems.append(f"{where}: grading field on a null_agent row")
        if row["source_type"] == "graded" and row["null_sha256"] is not None:
            problems.append(f"{where}: null_sha256 on a graded row")
        if (row["oracle_writes_basis"] is None) != (
            row["oracle_writes"] is None
        ):
            problems.append(f"{where}: basis null iff oracle_writes null")
        if row["match_type"] != "literal" and (
            row["oracle_writes_basis"] is not None
        ):
            problems.append(f"{where}: basis on a non-literal row")
        if row["oracle_writes_basis"] == "exact" and not (
            row["in_workspace"]
            and row["source_type"] == "graded"
            and capability_exact
        ):
            problems.append(f"{where}: exact basis without its capability")
        for name in ("access_types", "checks", "phases"):
            if row[name] != sorted(set(row[name])):
                problems.append(f"{where}: {name} not a sorted union")
    bases = _bases_of(rows)
    want = (
        "mixed"
        if len(bases) == 2
        else next(iter(bases))
        if bases
        else ("exact" if capability_exact else "fallback")
    )
    if block["bases"].get("oracle_writes") != want:
        problems.append(f"bases.oracle_writes {block['bases']} != {want}")
    m31 = block["metrics"]["M3.1"]
    m35 = block["metrics"]["M3.5"]
    if m31["inputs_under"] is not None and not (
        m31["inputs_under"] <= m31["inputs_over"]
    ):
        problems.append("inputs_under > inputs_over")
    if m35["score_gain"] is not None and m35["score_gain"] != (
        m35["run_score"] - m35["null_score"]
    ):
        problems.append("score_gain != run_score - null_score")
    if m35["phases_passed_by_null"] is not None and not set(
        m35["phases_passed_by_null"]
    ) <= set(m35["null_phases_passed"] or ()):
        problems.append("phases_passed_by_null not within null_phases_passed")
    warnings = block["warnings"]
    for label, metrics in (
        ("oracle_inputs", ("M3.1",)),
        ("null_score", ("M3.5",)),
    ):
        if block["preconditions"].get(label) == "fails":
            problems += _void_problems(block, label, metrics, warnings)
    if block["preconditions"].get("null_commit") == "fails":
        voided = (
            ("M3.5",)
            if block["bases"]["source"] == "exact"
            else (
                "M3.1",
                "M3.5",
            )
        )
        problems += _void_problems(block, "null_commit", voided, warnings)
    if block["preconditions"].get("oracle_inputs") == "fails" and rows:
        problems.append("rows written although M3.1 is void")
    return problems


def _void_problems(
    block: Mapping[str, Any],
    label: str,
    metrics: Sequence[str],
    warnings: Sequence[str],
) -> list[str]:
    problems = []
    for metric in metrics:
        values = block["metrics"][metric]
        if any(value is not None for value in values.values()):
            problems.append(f"{label} fails but {metric} has a value")
    naming = [w for w in warnings if label in w]
    if len(naming) != 1:
        problems.append(f"{len(naming)} warnings name failed {label}")
    return problems


def _order_key(row: Mapping[str, Any]) -> tuple:
    return (
        row["input_type"] != "file",
        row["input_scope"] != "workspace",
        row["path"],
        row["pattern"] or "",
    )


def check_input_states(
    scenario: Mapping[str, Any], result: Mapping[str, Any]
) -> list[str]:
    """§2.2.3's ten invariants of one input_state.classify result.

    Args:
        scenario: A dict from ``input_state_helpers.build_scenario``.
        result: The value of ``input_state.classify`` on it.

    Returns:
        One message per violated invariant; empty when all hold.
    """
    problems = []
    rows = result["input_states"]
    counts = result["state_counts"]
    given = scenario["oracle_inputs"]
    links = scenario["linked"]["links"]
    events = {
        lk["event_id"]: lk for lk in links if lk["link_type"] == "write_event"
    }
    paths = {p["path"]: p for p in scenario["linked"]["paths"]}
    exec_ids = {e["command_id"] for e in scenario["execs"]}
    call_ids = {c["call_id"] for c in scenario["calls"]}
    source = {r["input_id"]: r for r in (given or {}).get("oracle_inputs", [])}
    kept = [r for r in source.values() if r["input_scope"] != "reference"]
    # 1. One row per non-reference §0 row, unique, ordered (D53).
    if given is not None and rows and len(rows) != len(kept):
        problems.append(f"{len(rows)} rows for {len(kept)} non-reference rows")
    keys = [(r["path"], r["input_type"], r["pattern"]) for r in rows]
    if len(set(keys)) != len(keys):
        problems.append("(path, input_type, pattern) not unique")
    if [_order_key(r) for r in rows] != sorted(_order_key(r) for r in rows):
        problems.append("rows not in §5.1 order")
    files = [r for r in rows if r["input_type"] == "file"]
    for row in rows:
        where = f"{row['path']} ({row['input_type']})"
        under, over = row["states_under"], row["states_over"]
        if row["input_type"] == "listing":
            if under or over or row["state_under"] or row["model_conflict"]:
                problems.append(f"{where}: a listing row carries labels")
            continue
        # 2.
        if not set(under) <= set(over):
            problems.append(f"{where}: states_under not within states_over")
        if not over:
            problems.append(f"{where}: states_over empty")
        for name, labels in (("states_under", under), ("states_over", over)):
            if list(labels) != [s for s in STATES if s in labels]:
                problems.append(f"{where}: {name} not in STATES order")
        # 3.
        single = under[0] if len(under) == 1 else None
        if row["state_under"] != single:
            problems.append(f"{where}: state_under {row['state_under']!r}")
        if row["model_conflict"] != (not under):
            problems.append(f"{where}: model_conflict iff states_under empty")
        # 4. (D33)
        problems += _oracle_flag_problems(
            where, row, source.get(row["input_id"])
        )
        # 5.
        if "pristine" in over and row["events"]:
            problems.append(f"{where}: pristine possible with events")
        log = scenario["linked"]["coverage"]["inputs"]["log"]
        if log and "edit_written" in over:
            if not (
                row["final_writer_type"] == "edit_call"
                or (not row["final_writer_under"] and row["edit_events"])
            ):
                problems.append(f"{where}: edit_written without an edit")
        # 6.
        summary = paths.get(row["path"])
        want = summary["final_writer"] if summary else None
        if row["final_writer"] != want:
            problems.append(f"{where}: final_writer is not layer 1's")
        if row["final_writer"] is not None and (
            row["final_writer"] not in row["events"]
        ):
            problems.append(f"{where}: final_writer not in events")
        if set(row["events"]) - set(events):
            problems.append(f"{where}: unknown event ids")
        # 7.
        if row["detached_execs"] and not row["unseen_writer_possible"]:
            problems.append(f"{where}: detached but no unseen writer")
        if (set(row["blind_execs"]) | set(row["detached_execs"])) - exec_ids:
            problems.append(f"{where}: unknown exec ids")
        if set(row["unmonitored_calls"]) - call_ids:
            problems.append(f"{where}: unknown call ids")
        # 8. per row
        for name, labels in PREDICATES.items():
            p_under = bool(under) and set(under) <= labels
            p_over = bool(set(over) & labels)
            if row["predicates_under"][name] != p_under:
                problems.append(f"{where}: predicates_under.{name}")
            if row["predicates_over"][name] != p_over:
                problems.append(f"{where}: predicates_over.{name}")
            if p_under and not p_over:
                problems.append(f"{where}: {name}_under without _over")
    m32 = counts["metrics"]["M3.2"]
    failed = [
        label
        for label in VOIDING
        if counts["preconditions"].get(label) == "fails"
    ]
    # 10.
    for label in failed:
        if any(value is not None for value in m32.values()):
            problems.append(f"{label} fails but an M3.2 key has a value")
        naming = [w for w in counts["warnings"] if label in w]
        if len(naming) != 1:
            problems.append(f"{len(naming)} warnings name failed {label}")
    if failed or m32.get("oracle_inputs") is None:
        return problems
    # 8. counts
    for low, high in M32_COUNT_PAIRS:
        if m32[low] > m32[high]:
            problems.append(f"{low} > {high}")
    for label in STATES:
        if m32["by_state_under"][label] > m32["by_state_over"][label]:
            problems.append(f"by_state_under.{label} > by_state_over")
    # 9.
    if (
        sum(m32["by_state_under"].values()) + m32["state_unknown_under"]
        != m32["oracle_inputs"]
    ):
        problems.append("by_state_under + state_unknown_under != inputs")
    if m32["oracle_inputs"] != len(files):
        problems.append("oracle_inputs is not the number of file rows")
    return problems


def _oracle_flag_problems(
    where: str, row: Mapping[str, Any], given: Mapping[str, Any] | None
) -> list[str]:
    if given is None:
        return []
    value, basis = given["oracle_writes"], given["oracle_writes_basis"]
    under, over = row["states_under"], row["states_over"]
    if value is True and basis == "exact":
        ok = under == over == ["oracle_written"]
    elif value is True:
        ok = under == ["oracle_written"] and "oracle_written" in over
    elif value is False and basis == "exact":
        ok = "oracle_written" not in over
    else:
        ok = "oracle_written" not in under
    return [] if ok else [f"{where}: oracle flag ({value}, {basis}) broken"]
