"""Layer 4, P1-P6: metamorphic and model-based tests (§2.2.3, §2.2.4).

The metamorphic relations of §2.2.3 run on every scenario of
``tests/input_state_scenarios/``. The model-based test generates random
ground-truth histories, observes each twice (every capability false,
every capability true with its fields filled) and checks Lemma O, Lemma U
and evidence monotonicity (§8). Everything is synthetic: no dataset, no
label, no null-agent run.
"""

import copy
import json
import pathlib
import random
import types
from collections.abc import Callable, Mapping
from typing import Any

import conftest
import input_state_helpers as helpers
import input_state_invariants
import pytest

SCENARIOS = helpers.scenario_paths()
MONOTONE_PARAMS = [pytest.param(path, id=path.stem) for path in SCENARIOS]
LABEL_FIELDS = ("states_under", "states_over", "state_under")
SOURCE_DROPS = ("drop grading fields", "drop final_diff", "drop final_files")


def _run(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return helpers.run_classify(scenario)


def _canonical(result: Mapping[str, Any]) -> str:
    return conftest.canonical(result)


def _file_rows(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        row["input_id"]: row
        for row in result["input_states"]
        if row["input_type"] == "file"
    }


# ------------------------------------------------------- transformations


def _shuffled(scenario: Mapping[str, Any], seed: int) -> dict[str, Any]:
    changed = copy.deepcopy(dict(scenario))
    rng = random.Random(seed)
    for key in ("calls", "execs"):
        rng.shuffle(changed[key])
    for key in ("links", "dispositions", "paths"):
        rng.shuffle(changed["linked"][key])
    if changed["oracle_inputs"] is not None:
        rng.shuffle(changed["oracle_inputs"]["oracle_inputs"])
    if changed["final_files"]:
        rng.shuffle(changed["final_files"])
    if changed["baseline"] is not None:
        files = list(changed["baseline"]["files"].items())
        rng.shuffle(files)
        changed["baseline"]["files"] = dict(files)
    return changed


def _shifted(scenario: Mapping[str, Any], delta: float) -> dict[str, Any]:
    changed = copy.deepcopy(dict(scenario))

    def shift(record: dict[str, Any], keys: tuple[str, ...]) -> None:
        for key in keys:
            if record.get(key) is not None:
                record[key] = record[key] + delta

    shift(changed["run"], ("t_start", "t_end"))
    for call in changed["calls"]:
        shift(call, ("t_start", "t_end"))
    for record in changed["execs"]:
        shift(record, ("t_start", "t_end", "released_at", "ended_at"))
    for link in changed["linked"]["links"]:
        if link["link_type"] == "write_event":
            shift(link, ("t_start", "t_end", "order_t"))
    return changed


def _renamer(prefix: str) -> Callable[[Any], Any]:
    def rename(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return [rename(v) for v in value]
        return prefix + value

    return rename


def _renamed(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Renames call, command and event ids by order-keeping bijections."""
    changed = copy.deepcopy(dict(scenario))
    call, cmd, event = _renamer("c_"), _renamer("x_"), _renamer("v_")
    calls = {c["call_id"] for c in changed["calls"]}

    def any_id(value: Any) -> Any:
        return call(value) if value in calls else cmd(value)

    for record in changed["calls"]:
        record["call_id"] = call(record["call_id"])
    for record in changed["execs"]:
        record["command_id"] = cmd(record["command_id"])
        for key in ("parent_command_id", "root_command_id"):
            record[key] = cmd(record[key])
    for link in changed["linked"]["links"]:
        kind = link["link_type"]
        if "call_id" in link:
            link["call_id"] = call(link["call_id"])
        if "command_id" in link:
            link["command_id"] = cmd(link["command_id"])
        if kind == "exec_parent":
            link["parent"] = cmd(link["parent"])
            link["root_call_id"] = call(link["root_call_id"])
            link["root_candidates"] = call(link["root_candidates"])
        if kind == "wrapper_duplicate":
            link["of"] = cmd(link["of"])
        if kind == "write_event":
            link["event_id"] = event(link["event_id"])
            link["writer_id"] = any_id(link["writer_id"])
            link["root_call_id"] = call(link["root_call_id"])
            link["root_candidates"] = call(link["root_candidates"])
            if link["possibly_edit_call"] is not None:
                link["possibly_edit_call"] = call(link["possibly_edit_call"])
    for row in changed["linked"]["dispositions"]:
        row["id"] = (
            call(row["id"]) if row["item_type"] == "call" else cmd(row["id"])
        )
    for summary in changed["linked"]["paths"]:
        summary["events"] = event(summary["events"])
        summary["final_writer"] = event(summary["final_writer"])
    return changed


def _rename_output(
    result: Mapping[str, Any], calls: set[str]
) -> dict[str, Any]:
    out = copy.deepcopy(dict(result))
    call, cmd, event = _renamer("c_"), _renamer("x_"), _renamer("v_")
    for row in out["input_states"]:
        for key in ("events", "edit_events", "final_writer"):
            row[key] = event(row[key])
        for key in ("blind_execs", "detached_execs"):
            row[key] = cmd(row[key])
        row["unmonitored_calls"] = call(row["unmonitored_calls"])
        row["final_writer_root"] = call(row["final_writer_root"])
        writer = row["final_writer_id"]
        if writer is not None:
            row["final_writer_id"] = (
                call(writer) if writer in calls else cmd(writer)
            )
        for root in row["writer_roots"]:
            root["event_id"] = event(root["event_id"])
            root["root_call_id"] = call(root["root_call_id"])
    m32 = out["state_counts"]["metrics"]["M3.2"]
    if m32["final_writer_roots_under"] is not None:
        m32["final_writer_roots_under"] = call(m32["final_writer_roots_under"])
    return out


def _all_capabilities(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Every layer-4 capability on, no new data (fields stay null).

    untracked_visible is left as it is (D52): EVD6 reads it as a statement
    about the snapshots, so turning it on is new data.
    """
    changed = copy.deepcopy(dict(scenario))
    for name in (
        *("parent_links", "start_events", "exact_timing", "content_hashes"),
        *("final_content", "oracle_reads"),
    ):
        changed["run"]["capabilities"][name] = True
        changed["linked"]["coverage"]["capabilities"][name] = True
    return changed


def _non_input_write(scenario: Mapping[str, Any]) -> dict[str, Any] | None:
    writers = [e["command_id"] for e in scenario["execs"]]
    if not writers:
        return None
    changed = copy.deepcopy(dict(scenario))
    record = next(e for e in changed["execs"] if e["command_id"] == writers[0])
    link = {
        "link_type": "write_event",
        "event_id": "w_extra",
        "path": "zz/not_an_input.txt",
        "in_workspace": True,
        "op": "modify",
        "scope": "tracked",
        "writer_type": "exec",
        "writer_id": writers[0],
        "t_start": record["t_start"],
        "t_end": record["t_end"],
        "order_t": record["t_end"],
        "root_call_id": None,
        "root_ambiguous": False,
        "root_candidates": [],
        "depth_rule": "none",
        "possibly_edit_call": None,
        "overlaps_sibling": False,
        "source": "snapshot",
        "writer_under": True,
    }
    changed["linked"]["links"].append(link)
    changed["linked"]["paths"].append(
        {
            "path": "zz/not_an_input.txt",
            "in_workspace": True,
            "tracked": True,
            "events": ["w_extra"],
            "final_writer": "w_extra",
            "final_writer_under": True,
            "final_state": "changed",
            "unattributed_candidates": [],
        }
    )
    return changed


def evidence_removals(scenario: Mapping[str, Any]) -> list[str]:
    """The "remove evidence" transformations of §2.2.3 that apply.

    Args:
        scenario: A built scenario.

    Returns:
        Names for ``remove_evidence``; every one keeps oracle_writes.
    """
    names = []
    rows = (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []
    if any(r["source_type"] == "graded" for r in rows):
        names.append("drop grading fields")
    for key in ("final_diff", "final_files"):
        if scenario[key] is not None:
            names.append(f"drop {key}")
    if any(e["snapshot"]["hashes"] is not None for e in scenario["execs"]):
        names.append("drop snapshot.hashes")
    if scenario["run"]["trace_ok"]:
        names.append("trace_ok false")
    for index, record in enumerate(scenario["execs"]):
        if record["snapshot"]["status"] == "ok":
            names.append(f"skip {index}")
    if scenario["run"]["capabilities"]["exact_timing"]:
        names.append("exact_timing off")
    return names


def remove_evidence(scenario: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Applies one transformation named by ``evidence_removals``.

    Args:
        scenario: A built scenario.
        name: The transformation.

    Returns:
        A transformed copy.
    """
    changed = {
        key: value if key in ("raw", "expect") else copy.deepcopy(value)
        for key, value in scenario.items()
    }
    if name == "drop grading fields":
        for row in changed["oracle_inputs"]["oracle_inputs"]:
            if row["source_type"] == "graded":
                row.update(
                    source_type="null_agent",
                    grading_exists=None,
                    grading_sha256=None,
                    grading_mtime_ns=None,
                )
    elif name.startswith("drop final_"):
        changed[name.split()[1]] = None
    elif name == "drop snapshot.hashes":
        for record in changed["execs"]:
            record["snapshot"]["hashes"] = None
    elif name == "trace_ok false":
        changed["run"]["trace_ok"] = False
        changed["linked"]["coverage"]["integrity"]["C0.1"] = False
    elif name.startswith("skip "):
        changed["execs"][int(name.split()[1])]["snapshot"]["status"] = "skipped"
    elif name == "exact_timing off":
        changed["run"]["capabilities"]["exact_timing"] = False
        changed["linked"]["coverage"]["capabilities"]["exact_timing"] = False
    else:
        raise ValueError(name)
    return changed


def oracle_weakenings(
    scenario: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """The "weaken the oracle flag" transformations of §2.2.3.

    Args:
        scenario: A built scenario.

    Returns:
        (name, transformed scenario) pairs.
    """
    out = []
    if scenario["oracle_inputs"] is None:
        return out
    rows = scenario["oracle_inputs"]["oracle_inputs"]
    for index, row in enumerate(rows):
        if row["oracle_writes_basis"] == "exact":
            changed = copy.deepcopy(dict(scenario))
            changed["oracle_inputs"]["oracle_inputs"][index][
                "oracle_writes_basis"
            ] = "fallback"
            out.append((f"{row['path']} exact -> fallback", changed))
        if row["oracle_writes"] is not None:
            changed = copy.deepcopy(dict(scenario))
            changed["oracle_inputs"]["oracle_inputs"][index].update(
                oracle_writes=None, oracle_writes_basis=None
            )
            out.append((f"{row['path']} -> null", changed))
    return out


def monotonicity_problems(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    name: str,
    *,
    under: bool,
) -> list[str]:
    """Lemma M on two results: the new label sets contain the old ones.

    Rows whose content sources disagreed are exempt from the drop of a
    content source: the source used is replaced, not refined (D52).

    Args:
        old: The result before the transformation.
        new: The result after it.
        name: The transformation, for messages.
        under: Whether states_under is compared too (D40).

    Returns:
        One message per row that shrank.
    """
    problems = []
    after = _file_rows(new)
    for input_id, row in _file_rows(old).items():
        if name in SOURCE_DROPS and row["content_sources_disagree"]:
            continue
        got = after.get(input_id)
        if got is None:
            problems.append(f"{name}: {row['path']} lost its row")
            continue
        if not set(row["states_over"]) <= set(got["states_over"]):
            problems.append(
                f"{name}: {row['path']} states_over {row['states_over']} -> "
                f"{got['states_over']}"
            )
        if under and not set(row["states_under"]) <= set(got["states_under"]):
            problems.append(
                f"{name}: {row['path']} states_under {row['states_under']} "
                f"-> {got['states_under']}"
            )
    return problems


# ----------------------------------------------------- metamorphic tests


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_shuffled_inputs(path: pathlib.Path) -> None:
    """Shuffling every input list gives identical output.

    Rules: INP3, EVD5, LWR4, CNT3.
    """
    scenario = helpers.load_scenario(path)
    base = _canonical(_run(scenario))
    for seed in range(3):
        assert _canonical(_run(_shuffled(scenario, seed))) == base, seed


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_shifted_times(path: pathlib.Path) -> None:
    """Shifting every time by the same amount gives identical output.

    No absolute time is an output of this layer.
    Rules: EVD7, LWR4.
    """
    scenario = helpers.load_scenario(path)
    assert _canonical(_run(_shifted(scenario, 1024.0))) == _canonical(
        _run(scenario)
    )


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_renamed_ids(path: pathlib.Path) -> None:
    """Renaming call, command and event ids renames them in the output.

    Rules: LWR4, EVD7, EVD8, CNT6.
    """
    scenario = helpers.load_scenario(path)
    calls = {c["call_id"] for c in scenario["calls"]}
    want = _rename_output(_run(scenario), calls)
    assert _canonical(_run(_renamed(scenario))) == _canonical(want)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_capabilities_without_data(path: pathlib.Path) -> None:
    """Turning capabilities on with no new data changes only bases.

    Every row is identical; the M3.2 block too, except the rewrite-type
    counts, which are null exactly when content_hashes is false (CNT5).
    Rules: EVD2, EVD7, EVD8, CON7.
    """
    scenario = helpers.load_scenario(path)
    base, changed = _run(scenario), _run(_all_capabilities(scenario))
    assert changed["input_states"] == base["input_states"]
    skip = {"rewritten_same_content", "rewritten_restored"}
    old = base["state_counts"]["metrics"]["M3.2"]
    new = changed["state_counts"]["metrics"]["M3.2"]
    assert {k: v for k, v in new.items() if k not in skip} == {
        k: v for k, v in old.items() if k not in skip
    }
    assert (
        changed["state_counts"]["preconditions"]
        == (base["state_counts"]["preconditions"])
    )


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_write_to_a_non_input(path: pathlib.Path) -> None:
    """A write event to a path that is not an input changes nothing.

    Rules: INP1, EVD5.
    """
    scenario = helpers.load_scenario(path)
    changed = _non_input_write(scenario)
    if changed is None:
        pytest.skip("no exec to write with")
    base, got = _run(scenario), _run(changed)
    assert got["input_states"] == base["input_states"]
    assert got["state_counts"]["metrics"] == base["state_counts"]["metrics"]


@pytest.mark.parametrize("path", MONOTONE_PARAMS)
def test_removing_evidence_is_monotone(path: pathlib.Path) -> None:
    """Lemma M: removing evidence never shrinks either label set.

    Rules: EVD2, EVD6, EVD7, CON7, CON8, CON9, S1, S4.
    """
    scenario = helpers.load_scenario(path)
    base = _run(scenario)
    problems = []
    for name in evidence_removals(scenario):
        changed = _run(remove_evidence(scenario, name))
        problems += monotonicity_problems(base, changed, name, under=True)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_weakening_the_oracle_flag(path: pathlib.Path) -> None:
    """Weakening oracle_writes never shrinks states_over (D40).

    states_under is not compared: S5 reads null as false in M_s.
    Rules: CON1, S5.
    """
    scenario = helpers.load_scenario(path)
    base = _run(scenario)
    problems = []
    for name, changed in oracle_weakenings(scenario):
        problems += monotonicity_problems(
            base, _run(changed), name, under=False
        )
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_twice_byte_identical(path: pathlib.Path) -> None:
    """Running twice gives byte-identical output; inputs are not modified.

    Rules: CNT2.
    """
    scenario = helpers.load_scenario(path)
    before = copy.deepcopy(scenario)
    own = {
        key: scenario[key]
        for key in ("calls", "execs", "linked", "run", *helpers.CLASSIFY_ARGS)
    }
    first = json.dumps(helpers.run_classify(scenario, **own), sort_keys=True)
    second = json.dumps(helpers.run_classify(scenario, **own), sort_keys=True)
    assert first == second
    assert scenario == before


# --------------------------------------------------- model-based testing

ACTIONS = (
    *("edit", "command", "command", "detached", "unmonitored", "oracle"),
    *("delete", "identical", "background"),
)
# Extended histories (S3 of the cycle-1 critic): an input outside the
# workspace, a listing input, final_files entries without a hash, and two
# overlapping execs of different roots whose measured ends fall within
# snapshot_slop_s (layer 1's final writer is then uncertain, A1).
OUTSIDE = "/home/agent/out.log"
SNAPSHOT_KEYS = types.MappingProxyType(
    {"create": "created", "modify": "modified", "delete": "deleted"}
)


class History:
    """One random ground-truth history and its labels (§2.2.4 step 1)."""

    def __init__(self, rng: random.Random, extended: bool = False) -> None:
        """Draws the workspace and the actions.

        Args:
            rng: The random source.
            extended: Also draw the extended cases (an outside input, a
                listing, omitted final_files hashes, overlapping execs).
                False draws exactly the original histories.
        """
        size = rng.randint(3, 6)
        self.paths = [f"w/p{i}.dat" for i in range(size)]
        self.outside = extended and rng.random() < 0.5
        if self.outside:
            self.paths.append(OUTSIDE)
        self.overlaps = extended and rng.random() < 0.5
        self.tracked = {p: rng.random() < 0.6 for p in self.paths}
        if self.outside:
            self.tracked[OUTSIDE] = False
        self.base = {
            p: (f"b{i}" if self.tracked[p] or rng.random() < 0.3 else None)
            for i, p in enumerate(self.paths)
        }
        self.content = dict(self.base)
        self.wrote = dict.fromkeys(self.paths, False)
        self.last: dict[str, str | None] = dict.fromkeys(self.paths)
        self.oracle = dict.fromkeys(self.paths, False)
        self.unseen = False
        self.steps: list[dict[str, Any]] = []
        for slot in range(1, rng.randint(1, 12) + 1):
            self._step(rng, slot)
        self.end = dict(self.content)
        inside = [p for p in self.paths if p != OUTSIDE]
        # Observation choices of the extended cases: the members of a
        # listing input (None: no listing), and the final_files entries
        # written without a hash (a partial final_files.json).
        self.listed = None
        self.omitted: set[str] = set()
        if extended:
            if rng.random() < 0.5:
                self.listed = [p for p in inside if rng.random() < 0.6]
            self.omitted = {p for p in inside if rng.random() < 0.3}
        self.grading = {
            p: (f"o{p}" if self.oracle[p] else self.end[p]) for p in self.paths
        }
        # The null-agent history: right most of the time, else another
        # answer, or no answer (§2.2.4 step 2).
        self.null = {}
        for p in self.paths:
            draw = rng.random()
            self.null[p] = (
                None
                if draw < 0.15
                else self.oracle[p]
                if draw < 0.75
                else rng.random() < 0.5
            )
        self.skipped = {s["slot"] for s in self.steps if s.get("skipped")}

    def _step(self, rng: random.Random, slot: int) -> None:
        action = rng.choice((*ACTIONS, "overlap") if self.overlaps else ACTIONS)
        path = rng.choice(self.paths)
        before = self.content[path]
        step = {"slot": slot, "action": action, "path": path}
        if action == "oracle":
            self.oracle[path] = True
            return
        if path == OUTSIDE and action in ("edit", "overlap"):
            action = step["action"] = "command"
        if action == "overlap":
            self._overlap(rng, step, before)
            return
        if action in ("delete", "identical") and before is None:
            action = step["action"] = "background"
        step["skipped"] = rng.random() < 0.2
        if action == "background":
            self.steps.append(step)
            return
        if action == "delete":
            self.content[path] = None
            step["op"] = "delete"
        elif action == "identical":
            step["op"] = "modify"
        else:
            self.content[path] = f"h{slot}"
            step["op"] = "create" if before is None else "modify"
        step["after"] = self.content[path]
        self.wrote[path] = True
        self.last[path] = "edit" if action == "edit" else "command"
        if action in ("detached", "unmonitored"):
            self.unseen = True
        self.steps.append(step)

    def _overlap(
        self, rng: random.Random, step: dict[str, Any], before: str | None
    ) -> None:
        """Two execs of different roots write the path, in a true order.

        Each exec's ok snapshot lists the change between its start and
        its snapshot (S1): the first one's own write, then the net change
        of both. Their measured ends are within snapshot_slop_s, in either
        order (named_last: the true last ends last).
        """
        parts, state = [], before
        for index in range(2):
            kind = rng.choice(("write", "delete", "identical"))
            if state is None:
                kind = "write"
            after = (
                None
                if kind == "delete"
                else state
                if kind == "identical"
                else f"h{step['slot']}{'ab'[index]}"
            )
            start = before if index else state
            if start is None and after is None:
                op = None  # created and deleted again: no change seen
            elif after is None:
                op = "delete"
            else:
                op = "create" if start is None else "modify"
            parts.append({"op": op, "after": after})
            state = after
        step.update(parts=parts, named_last=rng.random() < 0.5, skipped=False)
        self.content[step["path"]] = state
        self.wrote[step["path"]] = True
        self.last[step["path"]] = "command"
        self.steps.append(step)

    def label(self, path: str) -> str:
        """λ*(path), by the definitions of spec §6.3."""
        if self.oracle[path]:
            return "oracle_written"
        base, now = self.base[path], self.end[path]
        if base is not None and now is None:
            return "deleted"
        if base is None and now is None:
            return "missing"
        if base is not None and now == base:
            return "rewritten_identical" if self.wrote[path] else "pristine"
        return (
            "edit_written" if self.last[path] == "edit" else "command_written"
        )


def _end_of_agent(history: History, path: str) -> tuple:
    if path == OUTSIDE:
        return (None, None)
    listed = None if path in history.omitted else history.end[path]
    if history.tracked[path] and history.end[path] != history.base[path]:
        return (history.end[path] is not None, listed)
    if not history.tracked[path] and history.end[path] is not None:
        return (True, listed)
    if history.tracked[path]:
        return (True, history.base[path])
    return (None, None)


def _exact_writes(history: History, path: str) -> bool | None:
    """OI10's exact form, restated: end-of-agent state vs grading read."""
    end_exists, end_sha = _end_of_agent(history, path)
    grade = history.grading[path]
    grade_exists = grade is not None
    if end_exists is not None and end_exists != grade_exists:
        return True
    if end_sha is not None and grade is not None and end_sha != grade:
        return True
    if end_exists is not None and end_exists == grade_exists:
        if not end_exists or (end_sha is not None and grade is not None):
            return False
    return None


def observe(history: History, on: bool) -> dict[str, Any]:
    """A raw scenario of the history (§2.2.4 step 2).

    The observation satisfies S1, S2 and S4: an ok snapshot lists exactly
    the watched paths changed while its exec ran, edit calls are logged,
    hashes are exact; detached and unmonitored writes are unseen; layer 1
    names the final writer by measured end.

    Args:
        history: The ground truth.
        on: Every layer-4 capability true (fields filled), else all false.

    Returns:
        A scenario mapping for ``input_state_helpers.build_scenario``.
    """
    calls, execs, links = [], [], []
    watched = {
        p: p != OUTSIDE and (on or history.tracked[p]) for p in history.paths
    }
    events: dict[str, list[tuple[str, str, int]]] = {
        p: [] for p in history.paths
    }
    overlapped = set()
    for step in history.steps:
        slot, action, path = step["slot"], step["action"], step["path"]
        call_id, exec_id = f"c{slot}", f"e{slot}"
        start = 10.0 * slot
        if action == "overlap":
            overlapped.add(slot)
            order = (0, 1) if step["named_last"] else (1, 0)
            for rank, index in enumerate(order):  # by measured end
                part = step["parts"][index]
                name = f"{slot}{'ab'[index]}"
                seen = watched[path] and part["op"] is not None
                snapshot: dict[str, Any] = {"status": "ok"}
                snapshot.update(
                    {key: [] for key in SNAPSHOT_KEYS.values()}
                    | ({SNAPSHOT_KEYS[part["op"]]: [path]} if seen else {})
                )
                record: dict[str, Any] = {
                    "command_id": f"e{name}",
                    "call": f"c{name}",
                    "t_start": start + 0.1,
                    "duration_s": 1.0 + 0.05 * rank,
                    "snapshot": snapshot,
                }
                if on:
                    record.update(
                        depth=0,
                        root_command_id=f"e{name}",
                        parent_link="none",
                        orphans=[],
                        record_type="closed",
                        released_at=start + 0.1,
                        ended_at=start + 1.1 + 0.05 * rank,
                    )
                    snapshot.update(
                        created_untracked=[],
                        modified_untracked=[],
                        deleted_untracked=[],
                        hashes={path: part["after"]}
                        if seen and part["op"] != "delete"
                        else {},
                    )
                calls.append(
                    {
                        "call_id": f"c{name}",
                        "command": "./step.sh",
                        "t_start": start,
                        "t_end": start + 2,
                    }
                )
                execs.append(record)
                if seen:
                    links.append(
                        {
                            "link_type": "write_event",
                            "path": path,
                            "writer_id": f"e{name}",
                            "op": part["op"],
                        }
                    )
                    events[path].append((f"w{len(links)}", part["op"], slot))
            continue
        if action == "edit":
            tool = "write" if step["op"] == "create" else "edit"
            calls.append(
                {
                    "call_id": call_id,
                    "tool_type": tool,
                    "paths": [f"/repo/{path}"],
                    "t_start": start,
                }
            )
            links.append(
                {
                    "link_type": "write_event",
                    "path": path,
                    "writer_id": call_id,
                    "op": step["op"],
                }
            )
            events[path].append((f"w{len(links)}", step["op"], slot))
            continue
        command = {
            "detached": "./job.sh &",
            "background": "./serve.sh &",
            "unmonitored": "./side.sh",
        }.get(action, "./step.sh")
        calls.append(
            {
                "call_id": call_id,
                "command": command,
                "t_start": start,
                "t_end": start + 2,
            }
        )
        if action == "unmonitored":
            continue
        skipped = step["skipped"] and action not in ("detached", "background")
        listed = {"created": [], "modified": [], "deleted": []}
        writes_here = action not in ("detached", "background")
        seen = writes_here and not skipped and watched[path]
        if seen:
            key = {
                "create": "created",
                "modify": "modified",
                "delete": "deleted",
            }
            listed[key[step["op"]]].append(path)
        snapshot: dict[str, Any] = {"status": "skipped" if skipped else "ok"}
        if not skipped:
            snapshot.update(listed)
        record: dict[str, Any] = {
            "command_id": exec_id,
            "call": call_id,
            "t_start": start + 0.1,
            "duration_s": 1.0,
            "snapshot": snapshot,
        }
        if on:
            record.update(
                depth=0,
                root_command_id=exec_id,
                parent_link="none",
                orphans=[{"pid": 9, "comm": "job"}]
                if action == "detached"
                else [],
                record_type="closed",
                released_at=start + 0.1,
                ended_at=start + 1.1,
            )
            if not skipped:
                snapshot.update(
                    created_untracked=[],
                    modified_untracked=[],
                    deleted_untracked=[],
                    hashes={},
                )
                if seen and step["op"] != "delete":
                    snapshot["hashes"] = {path: step["after"]}
        execs.append(record)
        if seen:
            links.append(
                {
                    "link_type": "write_event",
                    "path": path,
                    "writer_id": exec_id,
                    "op": step["op"],
                }
            )
            events[path].append((f"w{len(links)}", step["op"], slot))
    tail: dict[str, Any] = {
        "command_id": "e_tail",
        "call": "c_tail",
        "t_start": 140.1,
        "snapshot": {"status": "ok"},
    }
    calls.append({"call_id": "c_tail", "command": "true", "t_start": 140.0})
    if on:
        tail.update(
            depth=0,
            root_command_id="e_tail",
            parent_link="none",
            orphans=[],
            record_type="closed",
            released_at=140.1,
            ended_at=141.1,
        )
        tail["snapshot"].update(
            created_untracked=[],
            modified_untracked=[],
            deleted_untracked=[],
            hashes={},
        )
    execs.append(tail)
    rows, paths = [], []
    baseline = {
        p: {"sha256": history.base[p]}
        for p in history.paths
        if history.tracked[p]
    }
    final_files = []
    for path in history.paths:
        base, end = history.base[path], history.end[path]
        tracked = history.tracked[path]
        sha = None if path in history.omitted else end
        if path == OUTSIDE:
            state = "untracked"
        elif tracked:
            state = (
                "deleted"
                if end is None
                else "changed"
                if end != base
                else "reverted"
                if events[path]
                else None
            )
            if end is None:
                final_files.append({"path": path, "status": "D"})
            elif end != base:
                final_files.append({"path": path, "status": "M", "sha256": sha})
        else:
            state = "untracked"
            if end is not None:
                final_files.append({"path": path, "status": "A", "sha256": sha})
        row = {
            "path": path,
            "at_baseline": base is not None,
            "baseline_sha256": base,
        }
        exact = _exact_writes(history, path) if on else None
        if on:
            grade = history.grading[path]
            row.update(
                source_type="graded",
                grading_exists=grade is not None,
                grading_sha256=grade,
            )
        if exact is not None:
            row.update(oracle_writes=exact, oracle_writes_basis="exact")
        else:
            null = history.null[path]
            row.update(
                oracle_writes=null,
                oracle_writes_basis=None if null is None else "fallback",
            )
        rows.append(row)
        if (state is None or path == OUTSIDE) and not events[path]:
            continue
        candidates = list(events[path])
        if state in ("changed", "reverted"):
            candidates = [e for e in candidates if e[1] != "delete"]
        paths.append(
            {
                "path": path,
                "tracked": tracked,
                "final_state": state,
                "events": [e[0] for e in events[path]],
                "final_writer": candidates[-1][0] if candidates else None,
                # Layer 1 orders two exec events whose ends are within
                # snapshot_slop_s without certainty.
                "final_writer_under": bool(candidates)
                and candidates[-1][2] not in overlapped,
            }
        )
    if history.listed is not None:  # a listing input, last: id i<n>
        listing = f"i{len(rows)}"
        for row in rows:
            if row["path"] in history.listed:
                row["listed_by"] = [listing]
        rows.append(
            {
                "path": "w",
                "pattern": "*.dat",
                "source_type": "graded" if on else "null_agent",
            }
        )
    for index, link in enumerate(links):
        link["event_id"] = f"w{index + 1}"
    final_diff = {
        "modified": sorted(
            p
            for p in history.paths
            if history.tracked[p]
            and history.end[p] is not None
            and history.end[p] != history.base[p]
        ),
        "deleted": sorted(
            p
            for p in history.paths
            if history.tracked[p] and history.end[p] is None
        ),
    }
    layer4 = (
        *("parent_links", "start_events", "exact_timing", "content_hashes"),
        *("untracked_visible", "final_content", "oracle_reads"),
    )
    raw: dict[str, Any] = {
        "name": "model",
        "description": "a generated history",
        "rules": ["S1"],
        "capabilities": dict.fromkeys(layer4, on),
        "run": {
            "t_start": 0.0,
            "t_end": 10.0 * 15,
            "shim_intact": True if on else None,
        },
        "calls": calls,
        "execs": execs,
        "links": links,
        "paths": paths,
        "baseline": {"files": baseline},
        "final_diff": final_diff,
        "oracle_inputs": rows,
    }
    if on:
        raw["final_files"] = final_files
    return raw


def _histories(count: int, seed: int, extended: bool = False) -> list[History]:
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        out.append(History(rng, extended))
    return out


def _uncertain(history: History) -> bool:
    """Whether the history has two overlapping execs (A1's shape)."""
    return any(step["action"] == "overlap" for step in history.steps)


HISTORIES = 2000
RUN_KEYS = ("calls", "execs", "linked", "run", *helpers.CLASSIFY_ARGS)


def _soundness(
    histories: list[History], seen_only: bool = False
) -> tuple[list[str], dict[str, int]]:
    """Lemmas O and U and monotonicity on the histories (§2.2.4 step 3).

    Args:
        histories: The ground truths.
        seen_only: Compare states_under in the monotonicity check only on
            histories with no detached or unmonitored write.

    Returns:
        The problems (at most about 20) and the number of checks made.
    """
    rng = random.Random(7)
    problems: list[str] = []
    checked = {"O": 0, "U": 0}
    for number, history in enumerate(histories):
        for on in (False, True):
            raw = observe(history, on)
            scenario = helpers.build_scenario(raw)
            own = {key: scenario[key] for key in RUN_KEYS}
            result = helpers.run_classify(scenario, **own)
            rows = {r["path"]: r for r in result["input_states"]}
            problems += input_state_invariants.check_input_states(
                scenario, result
            )
            for row in raw["oracle_inputs"]:
                if row.get("pattern") is not None:
                    continue  # a listing input has no label
                path = row["path"]
                truth = history.label(path)
                got = rows[path]
                checked["O"] += 1
                if truth not in got["states_over"]:
                    problems.append(
                        f"history {number} on={on} {path}: Lemma O, "
                        f"{truth} not in {got['states_over']}"
                    )
                agrees = (
                    row["oracle_writes_basis"] == "exact"
                    or bool(row["oracle_writes"]) == history.oracle[path]
                )
                if not history.unseen and agrees:
                    checked["U"] += 1
                    if truth not in got["states_under"]:
                        problems.append(
                            f"history {number} on={on} {path}: Lemma U, "
                            f"{truth} not in {got['states_under']}"
                        )
            removals = evidence_removals(scenario)
            if removals:
                name = rng.choice(removals)
                changed = remove_evidence(scenario, name)
                own = {key: changed[key] for key in RUN_KEYS}
                problems += monotonicity_problems(
                    result,
                    helpers.run_classify(changed, **own),
                    name,
                    under=not (seen_only and history.unseen),
                )
        if len(problems) > 20:
            break
    return problems, checked


def test_model_based_soundness() -> None:
    """Lemmas O and U and monotonicity on generated histories (§8).

    Lemma O: the true label is in states_over for every path of every
    history (S1, S2, S4 hold by construction). Lemma U: it is in
    states_under for every path of a history with no detached or
    unmonitored write whose fallback or null oracle_writes agrees with the
    truth, null counting as false (S3, S5). Each history is observed with
    every capability false and with every capability true; a random
    evidence removal keeps both label sets as large.
    Rules: S1, S2, S3, S4, S5, CON1, CON2, CON3, CON4, CON5, CON6, CON7,
    CON8, CON9, CON12, CON15, EVD2, EVD4, EVD6, EVD7, EVD8.
    """
    problems, checked = _soundness(_histories(HISTORIES, 2026))
    assert problems == [], "\n".join(problems[:20])
    assert checked["O"] > 5 * HISTORIES and checked["U"] > HISTORIES


def test_model_based_soundness_extended() -> None:
    """The lemmas on the extended histories with a certain final writer.

    An input outside the workspace, a listing input, final_files entries
    without a hash (cycle-1 critic, S3); the histories with overlapping
    execs are checked by the next test. states_under is compared for
    monotonicity only when S3 holds: with an unseen write after a
    rewrite to the baseline hash, dropping the grading read lets CON7
    turn command_written into rewritten_identical (D72: Lemma M's
    states_under half needs S3).
    Rules: S1, S2, S3, S4, S5, INP3, EVD2, EVD6, CON7, CON15.
    """
    histories = [
        h
        for h in _histories(HISTORIES, 2027, extended=True)
        if not _uncertain(h)
    ]
    problems, checked = _soundness(histories, seen_only=True)
    assert problems == [], "\n".join(problems[:20])
    assert len(histories) > HISTORIES // 2
    assert checked["U"] > len(histories)
    assert any(h.outside for h in histories)
    assert any(h.listed for h in histories)
    assert any(h.omitted for h in histories)


def test_model_based_soundness_uncertain_writer() -> None:
    """The lemmas when layer 1's final writer is uncertain (A1).

    Two execs of different roots write one path and their measured ends
    are within snapshot_slop_s, in either order, so final_writer_under is
    false and the writer layer 1 names may not be the last one. CON7 and
    CON15 then must not apply (extends D61).
    Rules: S1, S3, CON7, CON15, LWR4.
    """
    histories = [
        h for h in _histories(HISTORIES, 2027, extended=True) if _uncertain(h)
    ]
    problems, checked = _soundness(histories, seen_only=True)
    assert problems == [], "\n".join(problems[:20])
    assert len(histories) > HISTORIES // 5
    assert checked["U"] > len(histories)


def test_model_histories_are_consistent() -> None:
    """The generator's scenarios pass the helper's consistency checks.

    Rules: S1, S2.
    """
    for extended in (False, True):
        for history in _histories(200, 11, extended):
            for on in (False, True):
                scenario = helpers.build_scenario(observe(history, on))
                assert helpers.validate(scenario, "model") == [
                    "file stem 'model' is not P<nn>[b]_<snake_case>"
                ]
