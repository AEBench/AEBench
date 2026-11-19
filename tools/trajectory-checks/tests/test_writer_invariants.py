"""Layer 5: the invariants of spec §2.2.3 over every scenario's output.

``writer_invariants.check_writer`` is independent test-oracle code; this
file runs it over every scenario (each run of a multi-run scenario), and
holds invariant 6's second half, which calls ``writer.last_writer``, plus
a seeded model-based check of that half on random event sets. ``writer``
is imported inside each test (D41).
"""

import pathlib
import random

import pytest
import writer_helpers as helpers
import writer_invariants

SCENARIOS = helpers.scenario_paths()


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_invariants_hold(path: pathlib.Path) -> None:
    """Invariants 1 to 15 hold on the scenario's output.

    Rules: LW1, LW3, LW4, LW5, LW6, LW9, LW10, LW11, LW12, LW16, RW2,
    RW6, HK3, HK8, FLT2, PRE3, PRE6.
    """
    for scenario in helpers.load_runs(path):
        result = helpers.run_derive(scenario)
        problems = writer_invariants.check_writer(scenario, result)
        assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_last_writer_is_layer1s_final_writer(path: pathlib.Path) -> None:
    """Invariant 6: with no cutoff, last_writer is paths.jsonl's writer.

    For every path of the run, with windows from observation_window and
    exists from layer 1's final state (true for changed, mode_only and
    reverted, else ⊥).
    Rules: LW3, LW4, LW5.
    """
    from trajectory_checks import writer

    for scenario in helpers.load_runs(path):
        events = helpers.events_of(scenario)
        execs = {e["command_id"]: e for e in scenario["execs"]}
        calls = {c["call_id"]: c for c in scenario["calls"]}
        windows = {
            i: writer.observation_window(
                e,
                execs=execs,
                calls=calls,
                exact=scenario["run"]["capabilities"]["exact_timing"],
                slop=2.0,
            )
            for i, e in events.items()
        }
        for summary in scenario["linked"]["paths"]:
            found = [events[e] for e in summary["events"]]
            exists = (
                True
                if summary["final_state"] in helpers.EXISTING_STATES
                else None
            )
            got, candidates = writer.last_writer(
                found, windows=windows, exists=exists
            )
            assert got == summary["final_writer"], summary["path"]
            if got is not None:
                assert got in candidates


def _random_events(rng: random.Random) -> tuple[list[dict], dict]:
    count = rng.randint(0, 6)
    times = sorted(round(rng.uniform(0, 100), 1) for _ in range(count))
    events, windows = [], {}
    for number, order_t in enumerate(times):
        low = round(order_t - rng.uniform(0, 30), 1)
        high = round(order_t + rng.uniform(0, 3), 1)
        writer_type = rng.choice(["exec", "exec", "edit_call"])
        event = {
            "event_id": f"w{number}",
            "path": "p",
            "op": rng.choice(["create", "modify", "modify", "delete", "mode"]),
            "writer_type": writer_type,
            "writer_id": f"x{number}",
            "t_start": low,
            "t_end": order_t,
            "order_t": order_t,
            "root_call_id": f"c{number}",
            "root_candidates": [],
            "possibly_edit_call": (
                "c9" if writer_type == "exec" and rng.random() < 0.2 else None
            ),
        }
        events.append(event)
        windows[event["event_id"]] = (low, high)
    rng.shuffle(events)
    return events, windows


def test_last_writer_model() -> None:
    """Seeded random event sets: layer 1's rule, and LW6's candidates.

    The writer is layer 1's final writer (helpers.layer1_final_writer, a
    copy of its rule from its spec); every candidate's window reaches the
    writer's start, and every non-mode, kept event that reaches it is a
    candidate. With a cutoff, no candidate's window starts at or after it.
    Rules: LW3, LW5, LW6.
    """
    from trajectory_checks import writer

    rng = random.Random(20261119)
    for _ in range(400):
        events, windows = _random_events(rng)
        state = rng.choice(["changed", "deleted", "mode_only", "untracked"])
        exists = True if state in helpers.EXISTING_STATES else None
        got, candidates = writer.last_writer(
            events, windows=windows, exists=exists
        )
        want = helpers.layer1_final_writer(events, state)
        assert got == (want["event_id"] if want else None)
        if got is None:
            assert candidates == []
            continue
        low = windows[got][0]
        kept = [e for e in events if not (exists and e["op"] == "delete")]
        plain = [e for e in kept if e["possibly_edit_call"] is None] or kept
        for event in plain:
            reaches = windows[event["event_id"]][1] >= low
            pooled = event["op"] != "mode" or event["event_id"] == got
            assert (event["event_id"] in candidates) == (reaches and pooled)
        assert candidates == sorted(candidates, key=helpers.event_number)
        cutoff = round(rng.uniform(0, 100), 1)
        got, candidates = writer.last_writer(
            events, windows=windows, exists=exists, cutoff=cutoff
        )
        for event_id in candidates:
            assert windows[event_id][0] < cutoff
        if got is not None:
            before = [e for e in events if windows[e["event_id"]][0] < cutoff]
            want = helpers.layer1_final_writer(before, state)
            assert got == want["event_id"]
