"""Layer 5: hand-written scenarios and metamorphic tests (spec §2.2.2-3).

One YAML file per scenario in ``tests/writer_scenarios/``; the format and
the defaults are in ``writer_helpers.py``. Each scenario's expectations
are checked, then the independent invariants of
``writer_invariants.check_writer``. ``writer`` (and ``cli``) are imported
inside each test (D41); the rule coverage checks, which need no layer-5
code, are in ``test_writer_rules.py``.
"""

import copy
import json
import pathlib
import random
import re
from collections.abc import Mapping
from typing import Any

import conftest
import pytest
import writer_helpers as helpers
import writer_invariants

SCENARIOS = helpers.scenario_paths()
SCENARIO_PARAMS = helpers.scenario_params()
RESULT_KEYS = {"last_writers", "input_rewrites", "hunks", "writer"}


def _by_id(name: str) -> pathlib.Path:
    return next(p for p in SCENARIOS if p.stem.split("_", 1)[0] == name)


def _rows_problems(
    name: str,
    rows: list[Mapping[str, Any]],
    want: list[Mapping[str, Any]],
    key: Any,
) -> list[str]:
    problems = []
    index: dict[Any, list[Mapping[str, Any]]] = {}
    for row in rows:
        index.setdefault(key(row), []).append(row)
    for item in want:
        got = index.get(key(item), [])
        if len(got) != 1:
            problems.append(f"{name}[{key(item)}]: {len(got)} rows")
            continue
        for problem in conftest.partial_mismatches(got[0], item):
            problems.append(f"{name}[{key(item)}]: {problem}")
    return problems


def expect_problems(
    result: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    """Compares a derive result with a scenario's expect block.

    Args:
        result: ``writer.derive``'s result.
        expect: The scenario's ``expect`` (peer ratios excluded).

    Returns:
        One message per mismatch.
    """
    problems = []
    for name in ("last_writers", "input_rewrites"):
        problems += _rows_problems(
            name, result[name], expect.get(name) or [], lambda r: r["input_id"]
        )
    problems += _rows_problems(
        "hunks",
        result["hunks"],
        expect.get("hunks") or [],
        lambda r: (r["path"], r["hunk_index"]),
    )
    for name, count in (expect.get("row_counts") or {}).items():
        if len(result[name]) != count:
            problems.append(f"{name}: {len(result[name])} rows, want {count}")
    run = result["writer"]
    for block in ("bases", "preconditions", "capabilities"):
        for problem in conftest.partial_mismatches(
            run[block], expect.get(block) or {}
        ):
            problems.append(f"{block}.{problem}")
    for block, want in (expect.get("metrics") or {}).items():
        for problem in conftest.partial_mismatches(run["metrics"][block], want):
            problems.append(f"{block}.{problem}")
    for block in expect.get("metrics_null") or []:
        live = [k for k, v in run["metrics"][block].items() if v is not None]
        if live:
            problems.append(f"{block}: not null {sorted(live)}")
    if "diff_files_unclassified" in expect and not conftest.close(
        run["diff_files_unclassified"], expect["diff_files_unclassified"]
    ):
        problems.append(
            f"diff_files_unclassified: {run['diff_files_unclassified']}"
        )
    if "warnings" in expect and run["warnings"] != expect["warnings"]:
        problems.append(f"warnings: {run['warnings']} != {expect['warnings']}")
    return problems


def _derive_all(path: pathlib.Path) -> list[tuple[dict, dict]]:
    out = []
    for raw in helpers.run_raws(helpers.read_raw(path)):
        scenario = helpers.build_scenario(raw)
        out.append((scenario, helpers.run_derive(scenario)))
    return out


def _peer_problems(
    raw: Mapping[str, Any], pairs: list[tuple[dict, dict]]
) -> list[str]:
    from trajectory_checks import cli, facts, writer

    expect = raw.get("expect") or {}
    problems = []
    if expect.get("peer_ratios"):
        rows = [r for _, result in pairs for r in cli.writer_input_rows(result)]
        ratios = writer.input_peer_ratios(rows)
        problems += _rows_problems(
            "peer_ratios",
            ratios,
            expect["peer_ratios"],
            lambda r: (r["run_key"], r["input_id"]),
        )
    if expect.get("run_peer_ratios"):
        rows = [cli.writer_rows(result) for _, result in pairs]
        problems += _rows_problems(
            "run_peer_ratios",
            facts.peer_ratios(rows),
            expect["run_peer_ratios"],
            lambda r: r["run_key"],
        )
    return problems


@pytest.mark.parametrize("path", SCENARIO_PARAMS)
def test_scenario(path: pathlib.Path) -> None:
    """Each scenario's expectations hold, and so do the invariants.

    A scenario with ``runs`` is derived once per run, each with its own
    expect block; its peer ratios are taken over all its runs.
    Rules: PRE1, PRE2.
    """
    pairs = _derive_all(path)
    problems = []
    for scenario, result in pairs:
        assert helpers.validate(scenario, path.stem) == []
        assert set(result) == RESULT_KEYS
        problems += expect_problems(result, scenario["expect"])
        problems += writer_invariants.check_writer(scenario, result)
    problems += _peer_problems(helpers.read_raw(path), pairs)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_record_fields(path: pathlib.Path) -> None:
    """Every record has exactly the fields of §3.4 and §5.

    Every metric key is written in every run, every basis key and
    precondition label; capabilities are coverage.json's.
    Rules: PRE3, PRE6, OUT1.
    """
    for scenario, result in _derive_all(path):
        for row in result["last_writers"]:
            assert set(row) == helpers.LAST_WRITER_FIELDS, sorted(
                set(row) ^ helpers.LAST_WRITER_FIELDS
            )
            for risk in row["later_writer_risk"]:
                assert set(risk) == helpers.RISK_FIELDS
            assert row["read_version"] in (
                "agent_final",
                "differs",
                "unverified",
            )
            assert row["last_writer_type"] in (
                None,
                "exec",
                "edit_call",
                "none",
            )
            assert row["input_type"] in ("file", "listing")
        for row in result["input_rewrites"]:
            assert set(row) == helpers.REWRITES_FIELDS
            for member in row["members"] or []:
                assert set(member) == helpers.MEMBER_FIELDS
                assert member["member_type"] in ("shell", "edit")
            for pair in row["pairs"]:
                assert set(pair) == helpers.PAIR_FIELDS
        for hunk in result["hunks"]:
            assert set(hunk) == helpers.HUNK_FIELDS
        run = result["writer"]
        assert set(run) == helpers.WRITER_FIELDS
        assert run["run_key"] == scenario["run"]["run_key"]
        assert run["family"] == scenario["run"]["family"]
        assert (
            run["capabilities"]
            == scenario["linked"]["coverage"]["capabilities"]
        )
        assert set(run["bases"]) == set(helpers.BASES_KEYS)
        assert set(run["preconditions"]) == set(helpers.PRECONDITION_LABELS)
        assert set(run["metrics"]) == {"M3.3", "M2.4", "M4.3"}
        for block, keys in helpers.METRIC_KEYS.items():
            assert set(run["metrics"][block]) == keys, block
        m33 = run["metrics"]["M3.3"]
        for key in ("last_writer_by_role_under", "last_writer_by_role_over"):
            if m33[key] is not None:
                assert set(m33[key]) == {
                    "edit_call",
                    "nested_exec",
                    "root_exec",
                }
        m43 = run["metrics"]["M4.3"]
        for key in (
            "hunks_by_type",
            "files_by_type",
            "hunks_by_type_named_under",
            "hunks_by_type_named_over",
            "hunks_by_type_read",
        ):
            if m43[key] is not None:
                assert set(m43[key]) == set(helpers.HUNK_TYPES), key
        for counts in (m43["hunks_by_class_and_type"] or {}).values():
            assert set(counts) == set(helpers.HUNK_TYPES)
        for item in run["diff_files_unclassified"]:
            assert set(item) == {"path", "reason"}
            assert item["reason"] in ("binary", "omitted")


# --------------------------------------------------------------- metamorphic


def _canonical(result: Any) -> str:
    """The JSON text of a result; NaN and infinities are not JSON."""
    return json.dumps(result, sort_keys=True, allow_nan=False)


def _shuffled(raw: Mapping[str, Any], seed: int) -> dict[str, Any]:
    out = copy.deepcopy(dict(raw))
    rng = random.Random(seed)
    for key in (
        *("calls", "execs", "links", "paths", "call_facts", "exec_facts"),
        *("call_structure", "file_classes", "oracle_inputs", "input_states"),
        "final_files",
    ):
        if isinstance(out.get(key), list):
            rng.shuffle(out[key])
    return out


def _single(path: pathlib.Path) -> list[pathlib.Path]:
    return [path] if not helpers.read_raw(path).get("runs") else []


SINGLE = [p for p in SCENARIOS if not helpers.read_raw(p).get("runs")]


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_shuffled_inputs_give_identical_output(path: pathlib.Path) -> None:
    """Shuffling every input list leaves the output byte-identical.

    Calls keep their seq and execs their seq (or start time), so only the
    list order changes.
    Rules: OUT1.
    """
    raw = helpers.read_raw(path)
    if not all("seq" in e for e in raw.get("execs") or []):
        raw = copy.deepcopy(raw)
        scenario = helpers.build_scenario(raw)
        seqs = {e["command_id"]: e["seq"] for e in scenario["execs"]}
        for e in raw.get("execs") or []:
            e["seq"] = seqs[e["command_id"]]
    want = _canonical(helpers.run_derive(helpers.build_scenario(raw)))
    for seed in (1, 2):
        got = helpers.run_derive(helpers.build_scenario(_shuffled(raw, seed)))
        assert _canonical(got) == want, seed


def _shift_raw(raw: Mapping[str, Any], delta: float) -> dict[str, Any]:
    out = copy.deepcopy(dict(raw))
    run = out.setdefault("run", {})
    run["t_start"] = run.get("t_start", 0.0) + delta
    run["t_end"] = run.get("t_end", 1000.0) + delta
    for record in out.get("calls") or []:
        for key in ("t_start", "t_end"):
            if record.get(key) is not None:
                record[key] += delta
    for record in out.get("execs") or []:
        for key in ("t_start", "t_end", "released_at", "ended_at"):
            if record.get(key) is not None:
                record[key] += delta
    for link in out.get("links") or []:
        for key in ("t_start", "t_end", "order_t"):
            if link.get(key) is not None:
                link[key] += delta
    for row in out.get("oracle_inputs") or []:
        if isinstance(row, dict) and row.get("grading_mtime_ns") is not None:
            row["grading_mtime_ns"] += int(delta * 1e9)
    return out


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_time_shift_moves_only_absolute_times(path: pathlib.Path) -> None:
    """Shifting every timestamp changes only the windows' absolute times.

    Rules: LW4, RW2.
    """
    delta = 1024.0
    raw = helpers.read_raw(path)
    want = helpers.run_derive(helpers.build_scenario(raw))
    got = helpers.run_derive(helpers.build_scenario(_shift_raw(raw, delta)))
    for row in want["last_writers"]:
        for key in ("window_lo", "window_hi"):
            if row[key] is not None:
                row[key] += delta
    for row in want["input_rewrites"]:
        for member in row["members"] or []:
            for key in ("first_lo", "last_hi"):  # null: unbounded (D46)
                if member[key] is not None:
                    member[key] += delta
    assert conftest.close(got, want, 1e-5), path.stem


def _rename_map(scenario: Mapping[str, Any]) -> dict[str, str]:
    names = {p["path"] for p in scenario["linked"]["paths"]}
    names |= {
        r["path"]
        for r in (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []
    }
    names |= set(
        re.findall(
            r"^diff --git a/(\S+) b/", scenario["final_diff_text"] or "", re.M
        )
    )
    names = {n for n in names if not n.startswith("/")}
    mapping = {}
    for name in names:
        mapping[name] = f"pfx/{name}"
        mapping[f"/repo/{name}"] = f"/repo/pfx/{name}"
        mapping[f"a/{name}"] = f"a/pfx/{name}"
        mapping[f"b/{name}"] = f"b/pfx/{name}"
    return mapping


def _rename(text: str, mapping: Mapping[str, str]) -> str:
    keys = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w./-])(" + "|".join(map(re.escape, keys)) + r")(?![\w.-])"
    )
    return pattern.sub(lambda m: mapping[m.group(1)], text)


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_renamed_paths_change_only_the_names(path: pathlib.Path) -> None:
    """A prefix added to every workspace path changes only those paths.

    No rule depends on a name: windows, writers, rewrites, hunk types and
    counts are the same once the output's paths are renamed alike.
    Rules: LW1, LW14, HK2, HK5.
    """
    raw = helpers.read_raw(path)
    scenario = helpers.build_scenario(raw)
    mapping = _rename_map(scenario)
    if not mapping:
        pytest.skip("no workspace path")
    renamed = json.loads(_rename(json.dumps(raw), mapping))
    want = json.loads(
        _rename(_canonical(helpers.run_derive(scenario)), mapping)
    )
    got = json.loads(
        _canonical(helpers.run_derive(helpers.build_scenario(renamed)))
    )
    for result in (want, got):
        result["hunks"].sort(key=lambda h: (h["path"], h["hunk_index"]))
    assert conftest.close(got, want), path.stem


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_unrelated_writes_change_nothing(path: pathlib.Path) -> None:
    """Writes to a path that is no oracle input leave rows and hunks alone.

    Rules: LW1, RW1, RW7, HK1.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    want = helpers.run_derive(helpers.build_scenario(raw))
    scenario = helpers.build_scenario(raw)
    end = scenario["run"]["t_end"]
    seq = max([c["seq"] for c in scenario["calls"]], default=-1) + 1
    eseq = max([e["seq"] for e in scenario["execs"]], default=-1) + 1
    number = max(
        [helpers.event_number(e) for e in helpers.events_of(scenario)],
        default=-1,
    )
    raw.setdefault("calls", []).append(
        {
            "call_id": "cz",
            "seq": seq,
            "command": "./other.sh",
            "t_start": end - 50.5,
            "t_end": end - 49.0,
        }
    )
    exec_record = {
        "command_id": "ez",
        "command": "./other.sh",
        "t_start": end - 50.0,
        "duration_s": 1.0,
    }
    if all("seq" in e for e in raw.get("execs") or []):
        exec_record["seq"] = eseq
    raw.setdefault("execs", []).append(exec_record)
    raw.setdefault("links", []).extend(
        [
            {"link_type": "call_exec", "call_id": "cz", "command_id": "ez"},
            {
                "link_type": "write_event",
                "event_id": f"w{number + 1}",
                "path": "zz/extra.txt",
                "writer_id": "ez",
            },
        ]
    )
    got = helpers.run_derive(helpers.build_scenario(raw))
    for name in ("last_writers", "input_rewrites", "hunks"):
        assert _canonical(got[name]) == _canonical(want[name]), name


def _diff_scenario(text: str, root: str = "") -> dict[str, Any]:
    return helpers.build_scenario(
        {
            "id": "WX",
            "title": "diff only",
            "rules": ["HK3"],
            "run": {
                "capabilities": {"final_content": True},
                "final_diff_content": "monitor/final.diff",
            },
            "final_diff": text,
        }
    )


@pytest.mark.parametrize("context", ["u0", "u3", "u10"])
def test_context_size_does_not_change_hunks(context: str) -> None:
    """The same edit diffed with -U0, -U3 and -U10 gives the same hunks.

    The expected hunks are written by hand (fixtures/diff/EDITS.md).
    Rules: HK3, HK8.
    """
    text = (helpers.DIFF_FIXTURES_DIR / f"context.{context}.diff").read_text()
    result = helpers.run_derive(_diff_scenario(text))
    want = json.loads(
        (helpers.DIFF_FIXTURES_DIR / "context.hunks.json").read_text()
    )
    got = [{k: h[k] for k in want[0]} for h in result["hunks"]]
    assert got == want


def reverse_diff(text: str) -> str:
    """Swaps the old and new sides of a git diff.

    Args:
        text: A git diff.

    Returns:
        The diff of the same files from new to old.
    """
    out = []
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            a, _, b = line[len("diff --git ") :].partition(" b/")
            line = f"diff --git a/{b} b/{a[2:]}"
        elif line.startswith("--- "):
            line = "+++ " + line[4:].replace("a/", "b/", 1)
        elif line.startswith("+++ "):
            line = "--- " + line[4:].replace("b/", "a/", 1)
        elif line.startswith("@@ "):
            found = re.match(r"@@ -(\S+) \+(\S+) @@(.*)", line)
            line = f"@@ -{found[2]} +{found[1]} @@{found[3]}"
        elif line.startswith("new file mode"):
            line = "deleted" + line[3:]
        elif line.startswith("deleted file mode"):
            line = "new" + line[7:]
        elif line.startswith("-"):
            line = "+" + line[1:]
        elif line.startswith("+"):
            line = "-" + line[1:]
        out.append(line)
    # A --- line must come before its +++ line again.
    fixed = []
    for line in out:
        if line.startswith("--- ") and fixed and fixed[-1].startswith("+++ "):
            fixed.insert(len(fixed) - 1, line)
        else:
            fixed.append(line)
    return "\n".join(fixed)


KEPT = {"whitespace_only", "comment_only", "comment_toggle", "numeric_only"}


@pytest.mark.parametrize("name", ["W32", "W56"])
def test_reversed_diff_swaps_additions_and_deletions(name: str) -> None:
    """Swapping old and new keeps the symmetric types and swaps the pure.

    A pure addition with no check line becomes a pure deletion and back;
    no hunk stays comparison_relaxed (a tightening is no relaxation).
    Rules: HK8.
    """
    scenario = helpers.load_scenario(_by_id(name))
    forward = helpers.run_derive(scenario)["hunks"]
    swapped = copy.deepcopy(scenario)
    swapped["final_diff_text"] = reverse_diff(scenario["final_diff_text"])
    backward = {
        (h["path"], h["hunk_index"]): h
        for h in helpers.run_derive(swapped)["hunks"]
    }
    assert len(backward) == len(forward)
    for hunk in forward:
        other = backward[(hunk["path"], hunk["hunk_index"])]
        assert (other["lines_removed"], other["lines_added"]) == (
            hunk["lines_added"],
            hunk["lines_removed"],
        )
        assert (other["old_start"], other["new_start"]) == (
            hunk["new_start"],
            hunk["old_start"],
        )
        if hunk["hunk_type"] in KEPT:
            assert other["hunk_type"] == hunk["hunk_type"], hunk
        if hunk["hunk_type"] == "pure_deletion":
            assert other["hunk_type"] == "pure_addition", hunk
        if hunk["hunk_type"] == "pure_addition" and not hunk["touches_check"]:
            assert other["hunk_type"] == "pure_deletion", hunk
        assert other["hunk_type"] != "comparison_relaxed", hunk


def _reordered(text: str) -> str:
    sections = re.split(r"(?=^diff --git )", text, flags=re.M)
    head, body = sections[0], sections[1:]
    body = [s if s.endswith("\n") else s + "\n" for s in body]
    return head + "".join(reversed(body))


@pytest.mark.parametrize("name", ["W32", "W36", "W56", "W63"])
def test_reordered_diff_files_give_identical_output(name: str) -> None:
    """The order of the files in the diff does not matter.

    Rules: HK1, OUT1.
    """
    scenario = helpers.load_scenario(_by_id(name))
    want = _canonical(helpers.run_derive(scenario))
    other = copy.deepcopy(scenario)
    other["final_diff_text"] = _reordered(scenario["final_diff_text"])
    assert other["final_diff_text"] != scenario["final_diff_text"]
    assert _canonical(helpers.run_derive(other)) == want


def _changed_keys(one: Any, two: Any, prefix: str = "") -> set[str]:
    """The leaf field names (last component) where two outputs differ."""
    if isinstance(one, dict) and isinstance(two, dict):
        out: set[str] = set()
        for key in set(one) | set(two):
            out |= _changed_keys(one.get(key), two.get(key), key)
        return out
    if (
        isinstance(one, list)
        and isinstance(two, list)
        and len(one) == len(two)
        and all(isinstance(x, dict) for x in one + two)
    ):
        out = set()
        for a, b in zip(one, two, strict=True):
            out |= _changed_keys(a, b, prefix)
        return out
    return set() if one == two else {prefix}


# (scenario, capability, fields set to null, output keys allowed to change)
CAPABILITY_OFF = (
    (
        "W19",
        "content_hashes",
        ("snapshot.hashes",),
        {"hash_confirms_writer", "content_hashes"},
    ),
    (
        "W35",
        "read_tracing",
        ("reads",),
        {
            "in_read",
            "hunks_by_type_read",
            "read_events",
            "read_tracing",
            "warnings",
        },
    ),
    (
        "W43",
        "exact_timing",
        ("released_at", "ended_at", "snapshot_after_s"),
        {
            "window_lo",
            "window_hi",
            "last_writer_candidates",
            "last_writer_under",
            "not_under",
            "window",
            "exact_timing",
            "inputs_written_under",
            "first_lo",
            "last_hi",
            "root_candidates",
            "root_exec",
            "last_writer_by_role_under",
            "writer_duration_min_s_under",
            "writer_duration_max_s_under",
            "root_span_max_s_under",
            "writer_calls_under",
            "inputs_writer_code_changed_under",
        },
    ),
    ("W14", "untracked_visible", (), {"untracked_visible"}),
)


@pytest.mark.parametrize(
    ("name", "capability", "fields", "allowed"),
    CAPABILITY_OFF,
    ids=[row[1] for row in CAPABILITY_OFF],
)
def test_capability_off_changes_only_its_keys(
    name: str, capability: str, fields: tuple[str, ...], allowed: set[str]
) -> None:
    """Turning one capability off changes only its rules' keys (D24).

    Its fields are set to null in every record; its bases entries become
    fallback. untracked_visible changes nothing but the capability: the
    writer takes visibility from layer 4 (VIS1, D23).
    Rules: PRE6, VIS1, LW4, LW11, FLT3.
    """
    raw = copy.deepcopy(helpers.read_raw(_by_id(name)))
    want = helpers.run_derive(helpers.build_scenario(raw))
    raw["run"]["capabilities"][capability] = False
    for record in raw.get("execs") or []:
        for field in fields:
            if field.startswith("snapshot."):
                (record.get("snapshot") or {}).pop(field[9:], None)
            else:
                record.pop(field, None)
    got = helpers.run_derive(helpers.build_scenario(raw))
    changed = _changed_keys(want, got)
    assert changed <= allowed, sorted(changed - allowed)
    for key, value in got["writer"]["bases"].items():
        if want["writer"]["bases"][key] != value:
            assert value == "fallback", key


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_late_write_false_changes_only_causes(path: pathlib.Path) -> None:
    """Layer 4's late_write set to false changes only differs_causes.

    Rules: LW11.
    """
    for raw in helpers.run_raws(helpers.read_raw(path)):
        scenario = helpers.build_scenario(raw)
        want = helpers.run_derive(scenario)
        other = copy.deepcopy(scenario)
        for row in (other["input_states"] or {}).get("input_states") or []:
            row["late_write"] = False
        got = helpers.run_derive(other)
        assert _changed_keys(want, got) <= {"differs_causes"}


def test_missing_late_write_reads_as_true() -> None:
    """Until layer 4 exposes late_write, a missing key reads as true (L4-a).

    W58's third run (no MC-03, no late-write cause) names the oracle
    only; without the key the late process is named too.
    Rules: LW11.
    """
    raws = helpers.run_raws(helpers.read_raw(_by_id("W58")))
    scenario = helpers.build_scenario(raws[2])
    (row,) = helpers.run_derive(scenario)["last_writers"]
    assert row["differs_causes"] == ["oracle"]
    for state in scenario["input_states"]["input_states"]:
        del state["late_write"]
    (row,) = helpers.run_derive(scenario)["last_writers"]
    assert row["differs_causes"] == ["oracle", "late_process"]


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_same_input_twice_is_byte_identical(path: pathlib.Path) -> None:
    """Deriving the same input twice gives byte-identical output.

    The inputs are not modified, and floats are rounded to 6 decimals.
    Rules: OUT1.
    """
    for raw in helpers.run_raws(helpers.read_raw(path)):
        scenario = helpers.build_scenario(raw)
        before = _canonical(scenario)
        first = _canonical(helpers.run_derive(scenario, **copy.deepcopy({})))
        from trajectory_checks import writer

        second = _canonical(
            writer.derive(
                scenario["calls"],
                scenario["execs"],
                scenario["linked"],
                scenario["facts_result"],
                scenario["structure_result"],
                **{k: scenario[k] for k in helpers.DERIVE_KEYS},
            )
        )
        assert first == second
        assert _canonical(scenario) == before, "derive modified its input"
        for value in re.findall(r"\d+\.\d+", first):
            assert len(value.split(".")[1]) <= 6, value
