"""Independent invariants of the chain layer (spec §2.2.4).

``check_chain(scenario, result)`` reads only a loaded scenario (the input
records) and ``chain.derive``'s result (the output records), and returns
one message per violated invariant. It imports neither ``declared`` nor
``chain``: intervals (TM1) and the read-free bound (TM3) are recomputed
here from the spec's rules.
"""

from collections.abc import Mapping
from typing import Any

import chain_helpers as helpers

from trajectory_checks import match

Problems = list[str]


def _interval(
    record: Mapping[str, Any], exact: bool, run_end: float
) -> tuple[float, float, float]:
    """TM1: (start, end, over end) of one exec."""
    if exact and record["released_at"] is not None and record["ended_at"]:
        low, high = record["released_at"], record["ended_at"]
    else:
        low, high = record["t_start"], record["t_end"]
    return low, high, high if record["complete"] else max(high, run_end)


def _bound(scenario: Mapping[str, Any]) -> float:
    """An upper bound of any chain time: every live exec's over interval."""
    run = scenario["run"]
    exact = run["capabilities"]["exact_timing"]
    spans = []
    for record in scenario["execs"]:
        if record["noise"]:
            continue
        low, _, high = _interval(record, exact, run["t_end"])
        low, high = max(low, run["t_start"]), min(high, run["t_end"])
        if high > low:
            spans.append((low, high))
    return match.union_length(spans)


def _le(one: Any, two: Any) -> bool:
    return one is None or two is None or one <= two + 1e-6


def _closure_checks(scenario: Mapping[str, Any], result: Any) -> Problems:
    problems = []
    lws = {
        r["input_id"]: r
        for r in (scenario["writer_result"] or {}).get("last_writers") or []
    }
    by_input: dict[str, dict[str, Mapping[str, Any]]] = {}
    for node in result["chain_nodes"]:
        nodes = by_input.setdefault(node["input_id"], {})
        if node["node_id"] in nodes:
            problems.append(f"1: node {node['node_id']} twice")
        nodes[node["node_id"]] = node
    reads: dict[str, list[Mapping[str, Any]]] = {}
    for row in result["hop_reads"]:
        reads.setdefault(row["input_id"], []).append(row)
    traced = result["chain"]["bases"]["chain"] == "exact"
    for input_id, nodes in by_input.items():
        lw = lws.get(input_id)
        if lw is None:
            problems.append(f"1: nodes of {input_id} without a last writer")
            continue
        starts = set(lw["last_writer_candidates"]) | {lw["last_writer"]}
        ch7 = not traced or not lw["root_candidates_complete"]
        for node in nodes.values():
            if node["hop"] == 1 and node["node_id"] not in starts:
                problems.append(
                    f"10: hop-1 node {node['node_id']} of {input_id}"
                )
            if node["hop"] > 1 and traced:
                edges = [
                    r
                    for r in reads.get(input_id, [])
                    if r["version"] == node["node_id"]
                    and nodes.get(r["reader_node"], {}).get("hop")
                    == node["hop"] - 1
                ]
                if not edges and not (ch7 and node["hop"] == 2):
                    problems.append(f"1: node {node['node_id']} unreached")
                for edge in edges:
                    if node["t_start"] is not None and not (
                        node["t_start"] <= edge["read_hi"] + 1e-6
                    ):
                        problems.append(
                            f"1: node {node['node_id']} starts late"
                        )
        must_hop1 = [n for n in nodes.values() if n["must"] and n["hop"] == 1]
        if lw["last_writer_under"] and must_hop1:
            if [n["node_id"] for n in must_hop1] != [lw["last_writer"]]:
                problems.append(f"10: must hop-1 of {input_id}")
        if not lw["last_writer_under"] and any(
            n["must"] for n in nodes.values()
        ):
            problems.append(f"10: must node of {input_id} without its writer")
        for row in reads.get(input_id, []):
            if row["reader_node"] not in nodes:
                problems.append(f"2: read of {input_id} by a non-node")
            elif row["must"] and not nodes[row["reader_node"]]["must"]:
                problems.append(
                    f"2: must read of a may node {row['reader_node']}"
                )
            if row["must"] and not row["attributed"]:
                problems.append("2: an unattributed read in the must-chain")
    states = {
        r["input_id"]: r
        for r in (scenario["input_states"] or {}).get("input_states") or []
    }
    for input_id, lw in lws.items():
        state = states.get(input_id)
        if state is not None and lw["last_writer"] != state["final_writer"]:
            problems.append(f"10: last_writer of {input_id} is not layer 4's")
    return problems


def _scope_checks(result: Any) -> Problems:
    problems = []
    for row in result["arg_checks"]:
        scope = row["in_scope"]
        chain = [
            s for s in ("chain_under", "chain_over") if scope[s] is not None
        ]
        order = ["writer", *chain, "before_writer"]
        for one, two in zip(order, order[1:], strict=False):
            if scope[one] is None or scope[two] is None:
                continue
            if set(scope[one]) - set(scope[two]):
                problems.append(
                    f"4: {one} not within {two}: {row['command_id']}"
                )
        if scope["run"] is not True:
            problems.append("4: run scope not true")
        if row["marker"] is None and row["contract"] is not None:
            problems.append("11: a contract without a marker")
        if row["conforms"] == "violated" and row["marker"] is None:
            problems.append("11: violated conformance without a marker")
        if row["scale_reduced"] is True and not (
            row["scale_option"] and row["effective"] == "below"
        ):
            problems.append("12: scale_reduced on a row that is not below")
    return problems


def _count_checks(result: Any) -> Problems:
    problems = []
    metrics = result["chain"]["metrics"]
    m51 = metrics["M5.1"]
    for version in ("under", "over"):
        total = m51[f"options_compared_{version}"]
        parts = [m51[f"args_{c}_{version}"] for c in helpers.COMPARISONS]
        if total is not None and None not in parts and sum(parts) != total:
            problems.append(f"5: M5.1 {version} counts do not add up")
    for block in ("M5.1", "M3.4", "M1.5"):
        values = metrics[block]
        for key, value in values.items():
            if not key.endswith("_under") and "_under_" not in key:
                continue
            other = key.replace("_under", "_over")
            if other in values and isinstance(value, int | float):
                if not _le(value, values[other]):
                    problems.append(f"2: {block}.{key} > {other}")
    return problems


def _time_checks(scenario: Mapping[str, Any], result: Any) -> Problems:
    problems = []
    bound = _bound(scenario)
    rows = list(result["faithfulness"])
    m15 = result["chain"]["metrics"]["M1.5"]
    for row in [*rows, m15]:
        under, over = row["chain_time_under_s"], row["chain_time_over_s"]
        if not _le(under, over):
            problems.append(f"3: chain time under > over ({under}, {over})")
        if not _le(over, bound):
            problems.append(f"3: chain time over {over} > bound {bound}")
    exact = scenario["run"]["capabilities"]["exact_timing"]
    lws = {
        r["input_id"]: r
        for r in (scenario["writer_result"] or {}).get("last_writers") or []
    }
    for row in rows:
        lw = lws.get(row["input_id"])
        duration = lw["writer_duration_s"] if lw else None
        if exact or duration is None or row["chain_time_under_s"] is None:
            continue
        if duration > row["chain_time_under_s"] + 1e-6:
            problems.append("3: under bound below the writer's duration")
    return problems


def _kleene(values: list[Any]) -> Any:
    if "violated" in values:
        return "violated"
    return "holds" if all(v == "holds" for v in values) else None


def _verdict_checks(scenario: Mapping[str, Any], result: Any) -> Problems:
    problems = []
    run = result["chain"]
    reads_fail = run["preconditions"]["reads"] == "fails"
    for row in result["faithfulness"]:
        if row["faithful"] != _kleene(list(row["clauses"].values())):
            problems.append(f"6: faithful of {row['input_id']} is not ∧")
        if reads_fail and row["faithful"] == "holds":
            problems.append("7: a holds verdict without read tracing")
        if reads_fail and row["mode"] != "read_free":
            problems.append("7: traced mode without read tracing")
        if row["clauses"]["derived"] == "violated":
            witness = row["witness"]
            violated = any(
                r["conforms"] == "violated" for r in result["arg_checks"]
            )
            if witness is not None and not violated:
                problems.append(f"11: clause 2 of {row['input_id']} violated")
    if reads_fail:
        live = [k for k, v in run["metrics"]["M3.4"].items() if v is not None]
        if live:
            problems.append(f"7: M3.4 keys without read tracing: {live}")
    return problems


def _null_checks(result: Any) -> Problems:
    problems = []
    run = result["chain"]
    text = " ".join(run["warnings"])
    for block, values in run["metrics"].items():
        for key, value in values.items():
            if value is not None or key in helpers.UNVOIDED_NULL_KEYS:
                continue
            if block not in text and key not in text:
                problems.append(f"8: {block}.{key} null without a warning")
    for key, value in run["bases"].items():
        capability = helpers.BASIS_CAPABILITY[key]
        if value == "exact" and not run["capabilities"][capability]:
            problems.append(f"9: bases.{key} exact without {capability}")
    return problems


def check_chain(scenario: Mapping[str, Any], result: Any) -> Problems:
    """Checks invariants 1 to 12 of spec §2.2.4 on one run's output.

    Args:
        scenario: A loaded scenario (``chain_helpers.build_scenario``).
        result: ``chain.derive``'s result for it.

    Returns:
        One message per violated invariant, prefixed by its number.
    """
    problems = []
    problems += _closure_checks(scenario, result)
    problems += _scope_checks(result)
    problems += _count_checks(result)
    problems += _time_checks(scenario, result)
    problems += _verdict_checks(scenario, result)
    problems += _null_checks(result)
    return problems
