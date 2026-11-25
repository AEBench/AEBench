"""Layer 6: the invariants of spec §2.2.4 over every scenario's output.

``chain_invariants.check_chain`` is independent test-oracle code; this
file runs it over every scenario (each run of a multi-run scenario) and
over seeded random runs, and holds seeded model-based checks of FA1's
soundness and of RD3's versions. ``chain`` is imported inside each test
(D46).
"""

import itertools
import pathlib
import random
from typing import Any

import chain_helpers as helpers
import chain_invariants
import pytest

SCENARIOS = helpers.scenario_paths()


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_invariants_hold(path: pathlib.Path) -> None:
    """Invariants 1 to 12 hold on the scenario's output.

    Rules: CH1, CH3, CH4, ARG4, TM1, TM2, TM3, FA1, FA9, CT2, CT3, SC2,
    PRE2, PRE3.
    """
    for scenario in helpers.load_runs(path):
        result = helpers.run_derive(scenario)
        problems = chain_invariants.check_chain(scenario, result)
        assert problems == [], "\n".join(problems)


def _truths(items: list[Any], rng: random.Random) -> list[bool]:
    """A world consistent with three-valued items: ⊥ is either."""
    return [
        True
        if v == "holds"
        else False
        if v == "violated"
        else rng.random() < 0.5
        for v in items
    ]


def test_must_may_evaluation_is_sound() -> None:
    """FA1 on seeded worlds: a decided verdict is the world's truth.

    For random items P(n) on a may-chain and a must-chain inside it, and
    every true chain between them (must ⊆ true ⊆ may) with every truth
    assignment consistent with the items, exists and forall never say
    holds or violated against the world (§9's soundness argument).
    Rules: FA1.
    """
    from trajectory_checks import chain

    rng = random.Random(20261120)
    for _ in range(300):
        size = rng.randint(0, 5)
        items = [rng.choice(helpers.VERDICTS) for _ in range(size)]
        must_mask = [rng.random() < 0.5 for _ in range(size)]
        must = [v for v, m in zip(items, must_mask, strict=True) if m]
        got_exists = chain.exists(must, items)
        got_forall = chain.forall(must, items)
        for _ in range(8):
            truth = _truths(items, rng)
            chosen = [
                m or rng.random() < 0.5 for m in must_mask
            ]  # the true chain: every must member, some may members
            real = [t for t, c in zip(truth, chosen, strict=True) if c]
            if got_exists == "holds":
                assert any(real)
            if got_exists == "violated":
                assert not any(real)
            if got_forall == "holds":
                assert all(real)
            if got_forall == "violated":
                assert not all(real)


def test_kleene_and_is_strong_conjunction() -> None:
    """kleene_and over every pair and triple equals the truth-table rule.

    Rules: FA1, FA8, FA9.
    """
    from trajectory_checks import chain

    for size in range(4):
        for values in itertools.product(helpers.VERDICTS, repeat=size):
            want = (
                "violated"
                if "violated" in values
                else "holds"
                if all(v == "holds" for v in values)
                else None
            )
            assert chain.kleene_and(values) == want, values


def _events(rng: random.Random) -> tuple[list[dict], dict, set[str]]:
    spans = []
    for _ in range(rng.randint(0, 5)):
        low = round(rng.uniform(0, 80), 1)
        spans.append((low, round(low + rng.uniform(0, 20), 1)))
    spans.sort(key=lambda s: s[1])  # ids in order_t order, as layer 1's
    events, windows = [], {}
    for number, (low, high) in enumerate(spans):
        event_id = f"w{number}"
        events.append(
            {
                "event_id": event_id,
                "path": "p",
                "op": rng.choice(["create", "modify", "modify"]),
                "writer_type": rng.choice(["exec", "exec", "edit_call"]),
                "writer_id": f"x{number}",
                "t_start": low,
                "t_end": high,
                "order_t": high,
                "possibly_edit_call": None,
                "writer_under": rng.random() < 0.8,
            }
        )
        windows[event_id] = (low, round(high + rng.uniform(0, 3), 1))
    own = {e["event_id"] for e in events if rng.random() < 0.2}
    return events, windows, own


def test_read_versions_model() -> None:
    """RD3 and RD7 on seeded event sets.

    The may versions are the events (less own) that can be the version:
    the last writer before the interval and every event whose window
    meets the interval; the must version is one of them, only when it is
    the only one, ends before the interval, and is writer_under.
    Rules: RD3, RD7.
    """
    from trajectory_checks import chain

    rng = random.Random(20261121)
    for _ in range(400):
        events, windows, own = _events(rng)
        low = round(rng.uniform(0, 100), 1)
        high = round(low + rng.choice([0.0, rng.uniform(0, 20)]), 1)
        must, may = chain.read_versions(
            events, windows=windows, interval=(low, high), own=own
        )
        pool = [e for e in events if e["event_id"] not in own]
        ids = {e["event_id"] for e in pool}
        assert set(may) <= ids
        assert may == sorted(may, key=helpers.number)
        meets = {
            e["event_id"]
            for e in pool
            if windows[e["event_id"]][0] <= high
            and windows[e["event_id"]][1] >= low
        }
        assert meets <= set(may)
        before = [e for e in pool if windows[e["event_id"]][0] < low]
        if before:
            assert before[-1]["event_id"] in may
        if not pool:
            assert (must, may) == (None, [])
        if must is not None:
            assert may == [must]
            event = next(e for e in pool if e["event_id"] == must)
            assert event["writer_under"]
            assert windows[must][1] <= low
            assert must not in own


def _random_run(rng: random.Random) -> dict[str, Any]:
    """A seeded traced (or read-free) run: a few execs writing and reading."""
    traced = rng.random() < 0.7
    count = rng.randint(1, 5)
    execs, links, paths = [], [], {}
    t = 10.0
    names = ["data/a.csv", "data/b.csv", "out/r.json"]
    written: list[str] = []
    dispositions = {}
    for number in range(count):
        duration = round(rng.uniform(1, 30), 1)
        target = names[-1] if number == count - 1 else rng.choice(names[:2])
        reads = [f"/repo/{p}" for p in written if rng.random() < 0.7]
        if rng.random() < 0.3:
            reads.append("/repo/src/tool.py")
        record = {
            "command_id": f"e{number}",
            "command": rng.choice(
                ["./step.sh --runs 3", "python3 src/tool.py -n 5", "make all"]
            ),
            "t_start": t,
            "duration_s": duration,
            "complete": rng.random() < 0.9,
        }
        if traced:
            record["reads"] = sorted(set(reads))
        execs.append(record)
        dispositions[f"e{number}"] = {
            "disposition": "matched_root",
            "root_under": rng.random() < 0.9,
        }
        links.append(
            {
                "link_type": "write_event",
                "path": target,
                "writer_id": f"e{number}",
                "writer_under": rng.random() < 0.9,
            }
        )
        written.append(target)
        paths[target] = {"path": target, "tracked": False}
        t += duration + round(rng.uniform(-5, 10), 1)
        t = max(t, 1.0)
    return {
        "id": "H99",
        "title": "random",
        "rules": ["CH4"],
        "run": {"capabilities": {"read_tracing": traced}},
        "execs": execs,
        "dispositions": dispositions,
        "links": links,
        "paths": list(paths.values()),
        "baseline_files": ["src/tool.py", "step.sh"],
        "oracle_inputs": [{"path": "out/r.json"}],
        "declared": {
            "invocations": [
                {
                    "invocation_key": "step.sh",
                    "text": "./step.sh --runs 10",
                    "options": {"--runs": {"value": "10", "value_type": "int"}},
                },
                {"invocation_key": "make:all", "text": "make all"},
            ]
        },
        "spec": {"options": {"step.sh --runs": rng.choice(["min", "free"])}},
    }


def test_random_runs_keep_the_invariants() -> None:
    """Seeded random runs: invariants 1 to 12 hold on every output.

    Rules: CH3, CH4, RD3, ARG4, TM2, TM3, FA1, SC2.
    """
    rng = random.Random(20261122)
    for index in range(150):
        raw = _random_run(rng)
        scenario = helpers.build_scenario(raw)
        events = helpers.writer_helpers.events_of(scenario)
        ordered = sorted(
            events.values(), key=lambda e: helpers.number(e["event_id"])
        )
        if any(
            a["order_t"] > b["order_t"]
            for a, b in zip(ordered, ordered[1:], strict=False)
        ):
            continue  # layer 1 numbers events by order_t
        result = helpers.run_derive(scenario)
        problems = chain_invariants.check_chain(scenario, result)
        assert problems == [], (index, problems)
