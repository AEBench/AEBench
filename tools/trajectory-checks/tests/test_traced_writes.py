"""Layers 1 and 6 together on writes from file_events.jsonl (MC-26).

``scenarios/S54_traced_write_outside_workspace.yaml`` is linked by
``match.link``; its links, paths and dispositions are then handed to
layer 6 unchanged (as a chain scenario's hand-written records), so the
chain reads exactly the write events layer 1 made. The benchmark's write
of a log outside the workspace must be the version the copy read, so the
chain's second step reaches the benchmark.
"""

from typing import Any

import chain_helpers as helpers
import chain_invariants
import conftest
import yaml
from test_chain_scenarios import expect_problems

S54 = conftest.SCENARIOS_DIR / "S54_traced_write_outside_workspace.yaml"
OUTSIDE = "/home/agent/benchroot/logs/tt.log"


def _chain_raw(path: Any, expect: dict[str, Any]) -> dict[str, Any]:
    """A chain scenario whose layer-1 records are match.link's output."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    linked = conftest.run_link(conftest.load_scenario(path))
    return {
        "id": "H00",
        "title": "layer 1's traced write events through layer 6",
        "rules": ["RD3"],
        "run": raw["run"],
        "calls": raw["calls"],
        "execs": raw["execs"],
        "links": linked["links"],
        "paths": linked["paths"],
        "dispositions": {r["id"]: r for r in linked["dispositions"]},
        "baseline": {"files": raw["baseline"]["files"]},
        "oracle_inputs": [{"path": "evaluation/logs/tt.log"}],
        "expect": expect,
    }


def test_outside_write_is_the_version_the_copy_read() -> None:
    """The second step from the copy's read reaches the benchmark.

    w1 (the copy, e2) is the last writer of evaluation/logs/tt.log; its
    read of the outside log resolves to w0 (the benchmark, e1), so w0 is
    a must node at hop 2.
    Rules: RD3.
    """
    expect = {
        "chain_nodes": [
            {"input_id": "i0", "node_id": "w1", "hop": 1, "must": True},
            {"input_id": "i0", "node_id": "w0", "hop": 2, "must": True},
        ],
        "hop_reads": [
            {
                "input_id": "i0",
                "reader_node": "w1",
                "read_path": OUTSIDE,
                "version": "w0",
                "state": "derived",
                "must": True,
            }
        ],
        "faithfulness": [{"input_id": "i0", "mode": "traced"}],
    }
    scenario = helpers.build_scenario(_chain_raw(S54, expect))
    events = {
        k["event_id"]: k
        for k in scenario["linked"]["links"]
        if k["link_type"] == "write_event"
    }
    assert (events["w0"]["writer_id"], events["w0"]["path"]) == ("e1", OUTSIDE)
    assert events["w1"]["writer_id"] == "e2"
    result = helpers.run_derive(scenario)
    problems = expect_problems(result, scenario["expect"])
    problems += chain_invariants.check_chain(scenario, result)
    assert problems == [], "\n".join(problems)
