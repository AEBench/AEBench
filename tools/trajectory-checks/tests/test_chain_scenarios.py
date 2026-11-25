"""Layer 6: hand-written scenarios and metamorphic tests (spec §2.2.3, §2.2.5).

One YAML file per scenario in ``tests/chain_scenarios/``; the format and
the defaults are in ``chain_helpers.py``. Each scenario's expectations
are checked, then the independent invariants of
``chain_invariants.check_chain``. ``chain`` is imported inside each test
(D46); the rule coverage checks are in ``test_chain_rules.py``.
"""

import copy
import json
import pathlib
import random
import re
from collections.abc import Mapping
from typing import Any

import chain_helpers as helpers
import chain_invariants
import conftest
import pytest

SCENARIOS = helpers.scenario_paths()
SCENARIO_PARAMS = helpers.scenario_params()
RESULT_KEYS = {
    "arg_checks",
    "chain_nodes",
    "hop_reads",
    "faithfulness",
    "chain",
}
SINGLE = [p for p in SCENARIOS if not helpers.read_raw(p).get("runs")]


def _key(name: str, row: Mapping[str, Any]) -> tuple:
    return tuple(row.get(k) for k in helpers.ROW_KEYS[name])


def _rows_problems(
    name: str, rows: list[Mapping[str, Any]], want: list[Mapping[str, Any]]
) -> list[str]:
    problems = []
    index: dict[tuple, list[Mapping[str, Any]]] = {}
    for row in rows:
        index.setdefault(_key(name, row), []).append(row)
    for item in want:
        got = index.get(_key(name, item), [])
        if len(got) != 1:
            problems.append(f"{name}[{_key(name, item)}]: {len(got)} rows")
            continue
        for problem in conftest.partial_mismatches(got[0], item):
            problems.append(f"{name}[{_key(name, item)}]: {problem}")
    return problems


def expect_problems(
    result: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[str]:
    """Compares a derive result with a scenario's expect block.

    Args:
        result: ``chain.derive``'s result.
        expect: The scenario's ``expect`` (``summary`` excluded).

    Returns:
        One message per mismatch.
    """
    problems = []
    for name in helpers.ROW_KEYS:
        problems += _rows_problems(name, result[name], expect.get(name) or [])
    for name, items in (expect.get("absent") or {}).items():
        keys = {_key(name, row) for row in result[name]}
        for item in items:
            if _key(name, item) in keys:
                problems.append(f"{name}[{_key(name, item)}]: present")
    verdicts = {r["input_id"]: r for r in result["faithfulness"]}
    for input_id, clauses in (expect.get("reasons_include") or {}).items():
        for clause, text in clauses.items():
            reason = verdicts[input_id]["reasons"][clause]
            if text not in reason:
                problems.append(f"reasons[{input_id}].{clause}: {reason!r}")
    for name, count in (expect.get("row_counts") or {}).items():
        if len(result[name]) != count:
            problems.append(f"{name}: {len(result[name])} rows, want {count}")
    run = result["chain"]
    for block in ("bases", "preconditions", "capabilities", "inputs"):
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
    if "warnings" in expect and run["warnings"] != expect["warnings"]:
        problems.append(f"warnings: {run['warnings']} != {expect['warnings']}")
    for parts in expect.get("warnings_with") or []:
        if not any(all(x in w for x in parts) for w in run["warnings"]):
            problems.append(f"no warning names all of {parts}")
    for text in expect.get("warnings_include") or []:
        if text not in run["warnings"]:
            problems.append(f"warning {text!r} not in {run['warnings']}")
    return problems


def _derive_all(path: pathlib.Path) -> list[tuple[dict, dict]]:
    out = []
    for raw in helpers.run_raws(helpers.read_raw(path)):
        scenario = helpers.build_scenario(raw)
        out.append((scenario, helpers.run_derive(scenario)))
    return out


def _summary_problems(
    raw: Mapping[str, Any], pairs: list[tuple[dict, dict]]
) -> list[str]:
    want = (raw.get("expect") or {}).get("summary")
    if not want:
        return []
    from trajectory_checks import chain, cli

    rows = [cli.chain_rows(result) for _, result in pairs]
    got = {(r["group_type"], r["group"]): r for r in chain.aggregate(rows)}
    problems = []
    for item in want:
        key = (item["group_type"], item["group"])
        if key not in got:
            problems.append(f"summary[{key}]: missing")
            continue
        for problem in conftest.partial_mismatches(got[key], item):
            problems.append(f"summary[{key}]: {problem}")
    return problems


@pytest.mark.parametrize("path", SCENARIO_PARAMS)
def test_scenario(path: pathlib.Path) -> None:
    """Each scenario's expectations hold, and so do the invariants.

    A scenario with ``runs`` is derived once per run, each with its own
    expect block; its ``summary`` is chain.aggregate over all its runs.
    Rules: PRE1, PRE2.
    """
    pairs = _derive_all(path)
    problems = []
    for scenario, result in pairs:
        assert helpers.validate(scenario, path.stem) == []
        assert set(result) == RESULT_KEYS
        problems += expect_problems(result, scenario["expect"])
        problems += chain_invariants.check_chain(scenario, result)
    problems += _summary_problems(helpers.read_raw(path), pairs)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_record_fields(path: pathlib.Path) -> None:
    """Every record has exactly the fields of §3.4 and §5.

    Every metric key is written in every run, every bases key and every
    precondition label; enum fields take only their listed values.
    Rules: PRE1, PRE3, AGG1.
    """
    for scenario, result in _derive_all(path):
        for row in result["arg_checks"]:
            assert set(row) == helpers.ARG_CHECK_FIELDS
            assert row["comparison"] in helpers.COMPARISONS + helpers.ROW_TYPES
            assert row["effective"] in (*helpers.COMPARISONS, None)
            assert row["contract"] in helpers.VERDICTS
            assert row["conforms"] in helpers.VERDICTS
            assert row["marker"] in (*helpers.MARKERS, None)
            assert set(row["in_scope"]) == {
                "writer",
                "chain_under",
                "chain_over",
                "before_writer",
                "run",
            }
            assert row["in_scope"]["run"] is True
            if row["executed"] is not None:
                assert set(row["executed"]) == {"value", "value_type", "origin"}
            assert row["declared_origin"] == sorted(row["declared_origin"])
        for row in result["chain_nodes"]:
            assert set(row) == helpers.CHAIN_NODE_FIELDS
            assert row["code_state"] in (*helpers.CODE_STATES, None)
            assert row["writer_type"] in ("exec", "edit_call")
            assert row["code_paths"] == sorted(row["code_paths"])
        for row in result["hop_reads"]:
            assert set(row) == helpers.READ_EDGE_FIELDS
            assert row["state"] in helpers.READ_STATES
            assert row["read_type"] in ("file", "listing", "rename")
            assert row["code_rule"] in ("class", "binary", "program", None)
            assert row["read_lo"] <= row["read_hi"]
        for row in result["faithfulness"]:
            assert set(row) == helpers.VERDICT_FIELDS
            assert set(row["clauses"]) == set(helpers.CLAUSES)
            assert set(row["reasons"]) == set(helpers.CLAUSES)
            assert row["mode"] in ("traced", "read_free")
            assert row["faithful"] in helpers.VERDICTS
        run = result["chain"]
        assert set(run) == helpers.RUN_FIELDS
        assert run["run_key"] == scenario["run"]["run_key"]
        assert run["capabilities"] == scenario["run"]["capabilities"]
        assert set(run["bases"]) == set(helpers.BASES_KEYS)
        assert set(run["bases"].values()) <= {"exact", "fallback"}
        assert set(run["preconditions"]) == set(helpers.PRECONDITION_LABELS)
        assert set(run["preconditions"].values()) <= {
            "holds",
            "vacuous",
            "fails",
        }
        assert set(run["inputs"]) == set(helpers.INPUT_KEYS)
        assert set(run["metrics"]) == set(helpers.METRIC_KEYS)
        for block, keys in helpers.METRIC_KEYS.items():
            assert set(run["metrics"][block]) == keys, block
        states = run["metrics"]["M3.4"]["hop2_reads_by_state_over"]
        if states is not None:
            assert set(states) == set(helpers.READ_STATES)
        clauses = run["metrics"]["faithfulness"]["clause_violations"]
        if clauses is not None:
            assert set(clauses) == set(helpers.CLAUSES)


# --------------------------------------------------------------- metamorphic


def _canonical(result: Any) -> str:
    return json.dumps(result, sort_keys=True, allow_nan=False)


def _with_seqs(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The raw scenario with every exec's seq written out."""
    raw = copy.deepcopy(dict(raw))
    scenario = helpers.build_scenario(raw)
    seqs = {e["command_id"]: e["seq"] for e in scenario["execs"]}
    for record in raw.get("execs") or []:
        record["seq"] = seqs[record["command_id"]]
    rows = raw.get("oracle_inputs")
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        row.setdefault("input_id", f"i{index}")
    return raw


def _shuffled(raw: Mapping[str, Any], seed: int) -> dict[str, Any]:
    out = copy.deepcopy(dict(raw))
    rng = random.Random(seed)
    for key in ("calls", "execs", "paths", "oracle_inputs", "final_files"):
        if isinstance(out.get(key), list):
            rng.shuffle(out[key])
    links = out.get("links") or []
    writes = [lk for lk in links if lk["link_type"] == "write_event"]
    others = [lk for lk in links if lk["link_type"] != "write_event"]
    for index, link in enumerate(writes):
        link.setdefault("event_id", f"w{index}")
    rng.shuffle(others)
    rng.shuffle(writes)
    out["links"] = others + writes
    for record in out.get("execs") or []:
        if isinstance(record.get("reads"), list):
            rng.shuffle(record["reads"])
    declared = out.get("declared")
    if isinstance(declared, dict):
        items = declared.get("invocations") or []
        for index, item in enumerate(items):
            item.setdefault("decl_id", f"d{index}")
            item.setdefault("position", index)
            item.setdefault("line", index + 2)
        rng.shuffle(items)
        rng.shuffle(declared.get("options") or [])
    return out


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_shuffled_inputs_give_identical_output(path: pathlib.Path) -> None:
    """Shuffling execs, their reads, links and declared records changes nothing.

    Records keep their ids and seqs, so only list order changes.
    Rules: CH4, ARG3.
    """
    raw = _with_seqs(helpers.read_raw(path))
    want = _canonical(helpers.run_derive(helpers.build_scenario(raw)))
    for seed in (1, 2):
        got = helpers.run_derive(helpers.build_scenario(_shuffled(raw, seed)))
        assert _canonical(got) == want, seed


def _shift_raw(raw: Mapping[str, Any], delta: float) -> dict[str, Any]:
    out = copy.deepcopy(dict(raw))
    run = out.setdefault("run", {})
    run["t_start"] = run.get("t_start", 0.0) + delta
    run["t_end"] = run.get("t_end", 1000.0) + delta
    for item in run.get("unattributed_reads") or []:
        item["t"] += delta
    for record in out.get("calls") or []:
        if record.get("t_start") is not None:
            record["t_start"] += delta
        if record.get("t_end") is not None:
            record["t_end"] += delta
    for record in out.get("execs") or []:
        for key in ("t_start", "t_end", "released_at", "ended_at"):
            if record.get(key) is not None:
                record[key] += delta
    for link in out.get("links") or []:
        for key in ("t_start", "t_end", "order_t"):
            if link.get(key) is not None:
                link[key] += delta
    for row in out.get("last_writers") or []:
        if not isinstance(row, dict):
            continue
        for key in ("window_lo", "window_hi"):
            if row.get(key) is not None:
                row[key] += delta
    return out


TIME_KEYS = frozenset({"t_start", "t_end", "t_end_over", "read_lo", "read_hi"})


def _shift_out(value: Any, delta: float, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _shift_out(v, delta, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift_out(v, delta, key) for v in value]
    if key in TIME_KEYS and isinstance(value, float):
        return value + delta
    return value


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_time_shift_moves_only_absolute_times(path: pathlib.Path) -> None:
    """Shifting every timestamp changes only the absolute times.

    Rules: RD3, TM1, TM2, TM3.
    """
    delta = 1024.0
    raw = helpers.read_raw(path)
    want = _shift_out(helpers.run_derive(helpers.build_scenario(raw)), delta)
    got = helpers.run_derive(helpers.build_scenario(_shift_raw(raw, delta)))
    assert conftest.close(got, want, 1e-5), path.stem


def _rename_map(scenario: Mapping[str, Any]) -> dict[str, str]:
    """A bijection on workspace paths: each gets the folder ``pfx/``."""
    names = {p["path"] for p in scenario["linked"]["paths"]}
    names |= set((scenario["baseline"] or {}).get("files") or {})
    for row in (scenario["oracle_inputs"] or {}).get("oracle_inputs") or []:
        names.add(row["path"])
    for record in scenario["execs"]:
        for read in record["reads"] or []:
            if read.startswith("/repo/"):
                names.add(read[len("/repo/") :])
    names = {n for n in names if not n.startswith("/") and "*" not in n}
    mapping = {}
    for name in names:
        mapping[name] = f"pfx/{name}"
        mapping[f"/repo/{name}"] = f"/repo/pfx/{name}"
        mapping[f"./{name}"] = f"./pfx/{name}"
    return mapping


def _rename(text: str, mapping: Mapping[str, str]) -> str:
    keys = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w./:-])(" + "|".join(map(re.escape, keys)) + r")(?![\w.-])"
    )
    return pattern.sub(lambda m: mapping[m.group(1)], text)


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_renamed_paths_change_only_the_names(path: pathlib.Path) -> None:
    """A folder prefix added to every workspace path changes only names.

    Paths, the commands naming them and the invocation keys derived from
    them are renamed by one bijection; no rule depends on a name. This is
    the artifact-agnosticism test.
    Rules: KEY1, KEY2, RD1, RD4.
    """
    raw = helpers.read_raw(path)
    if raw.get("rename") is False:
        pytest.skip("names are part of the scenario")
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
        result["chain"]["warnings"] = sorted(result["chain"]["warnings"])
    assert conftest.close(got, want), path.stem


def _traced(raw: Mapping[str, Any]) -> bool:
    return bool(
        ((raw.get("run") or {}).get("capabilities") or {}).get("read_tracing")
    )


TRACED = [p for p in SINGLE if _traced(helpers.read_raw(p))]
EXTERNAL = "/usr/share/zz/table.dat"


@pytest.mark.parametrize("path", TRACED, ids=lambda p: p.stem)
def test_external_read_changes_only_reads(path: pathlib.Path) -> None:
    """A read of a path outside the workspace adds only hop_reads rows.

    The external path is a leaf (RD4) and never a node; M3.4's hop-2 read
    counts may count it (CH6, D72).
    Rules: RD4, CH6.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    want = helpers.run_derive(helpers.build_scenario(raw))
    for record in raw.get("execs") or []:
        if isinstance(record.get("reads"), list):
            record["reads"].append(EXTERNAL)
    got = helpers.run_derive(helpers.build_scenario(raw))
    for name in ("arg_checks", "chain_nodes", "faithfulness"):
        assert _canonical(got[name]) == _canonical(want[name]), name
    added = [r for r in got["hop_reads"] if r["read_path"] == EXTERNAL]
    assert all(r["state"] == "external" for r in added)
    kept = [r for r in got["hop_reads"] if r not in added]
    assert _canonical(kept) == _canonical(want["hop_reads"])
    for block in ("M5.1", "M1.5", "faithfulness"):
        assert got["chain"]["metrics"][block] == want["chain"]["metrics"][block]


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_late_exec_changes_no_chain(path: pathlib.Path) -> None:
    """An exec with a write that starts after every last writer ended.

    Chains, the non-run M5.1 scopes and M1.5 are unchanged; only the run
    scope's context keys may count the late exec (ARG6).
    Rules: CH3, CH7, ARG4, ARG6, TM3.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    scenario = helpers.build_scenario(raw)
    want = helpers.run_derive(scenario)
    end = scenario["run"]["t_end"]
    events = sorted(
        helpers.writer_helpers.events_of(scenario).values(),
        key=lambda e: helpers.number(e["event_id"]),
    )
    w_end = max([e["t_end"] for e in events], default=0.0)
    start = (w_end + end) / 2
    if not start < end - 1:
        pytest.skip("no room after the last writer")
    raw = _with_seqs(raw)
    raw.setdefault("execs", []).append(
        {
            "command_id": "ezz",
            "command": "./zz_late.sh --iters 1",
            "t_start": start,
            "duration_s": 0.5,
            "seq": 999,
            **_capability_fields(scenario["run"]["capabilities"], start),
        }
    )
    raw.setdefault("links", []).append(
        {
            "link_type": "write_event",
            "event_id": f"w{len(events)}",
            "path": "zz/late.txt",
            "writer_id": "ezz",
        }
    )
    got = helpers.run_derive(helpers.build_scenario(raw))
    assert _canonical(got["chain_nodes"]) == _canonical(want["chain_nodes"])
    for key, value in want["chain"]["metrics"]["M1.5"].items():
        if not key.startswith("chain_share"):  # the share's total grows
            assert got["chain"]["metrics"]["M1.5"][key] == value, key
    for key, value in want["chain"]["metrics"]["M5.1"].items():
        if key not in helpers.M51_RUN_KEYS:
            assert got["chain"]["metrics"]["M5.1"][key] == value, key
    for one, two in zip(want["faithfulness"], got["faithfulness"], strict=True):
        assert one["chain_time_under_s"] == two["chain_time_under_s"]


def _capability_fields(caps: Mapping[str, bool], start: float) -> dict:
    """The universal fields a new exec carries under the run's capabilities."""
    fields: dict[str, Any] = {}
    if caps["read_tracing"]:
        fields["reads"] = []
    if caps["parent_links"]:
        fields.update(root_command_id="ezz", depth=0, pgid=99)
    if caps["exact_timing"]:
        fields.update(released_at=start, ended_at=start + 0.5)
    if caps["content_hashes"]:
        fields["snapshot"] = {"hashes": {}}
    if caps["start_events"]:
        fields["record_type"] = "closed"
    if caps["stdio"]:
        fields["stdio"] = ["/dev/null", "/dev/null", "/dev/null"]
    return fields


def _declared_raw(raw: Mapping[str, Any]) -> dict | None:
    declared = raw.get("declared")
    if not isinstance(declared, dict) or not declared.get("invocations"):
        return None
    return declared


WITH_DECLARED = [p for p in SINGLE if _declared_raw(helpers.read_raw(p))]


@pytest.mark.parametrize("path", WITH_DECLARED, ids=lambda p: p.stem)
def test_duplicate_declared_line_changes_nothing(path: pathlib.Path) -> None:
    """A declared line given twice leaves rows and verdicts unchanged.

    Value sets are sets; only decl_ids of chain nodes list the copy.
    Rules: ARG1.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    want = helpers.run_derive(helpers.build_scenario(raw))
    items = raw["declared"]["invocations"]
    copy_ = copy.deepcopy(items[0])
    copy_.update(
        decl_id=f"d{len(items)}",
        block_id="README.md:900",
        line=901,
        position=0,
    )
    items.append(copy_)
    got = helpers.run_derive(helpers.build_scenario(raw))
    for name in ("arg_checks", "faithfulness"):
        assert _canonical(got[name]) == _canonical(want[name]), name
    for block in ("M5.1", "M3.4", "M1.5", "faithfulness"):
        assert got["chain"]["metrics"][block] == want["chain"]["metrics"][block]


@pytest.mark.parametrize("path", WITH_DECLARED, ids=lambda p: p.stem)
def test_unrelated_declared_key_changes_nothing(path: pathlib.Path) -> None:
    """A declared invocation of a key no exec runs changes no row.

    Only declared_keys_run (the share of declared keys run) may change.
    Rules: ARG1, ARG3, FA3.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    want = helpers.run_derive(helpers.build_scenario(raw))
    items = raw["declared"]["invocations"]
    items.append(
        {
            "decl_id": f"d{len(items)}",
            "invocation_key": "zz_unrelated.sh",
            "block_id": "README.md:900",
            "line": 901,
            "position": 0,
            "text": "./zz_unrelated.sh",
        }
    )
    got = helpers.run_derive(helpers.build_scenario(raw))
    for name in ("arg_checks", "chain_nodes", "faithfulness"):
        assert _canonical(got[name]) == _canonical(want[name]), name
    for key, value in want["chain"]["metrics"]["M5.1"].items():
        if key != "declared_keys_run":
            assert got["chain"]["metrics"]["M5.1"][key] == value, key


ORDER = {"violated": 0, None: 1, "holds": 2}


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_free_markers_never_move_towards_violated(path: pathlib.Path) -> None:
    """A free marker on every deviating row: its contract and conformance hold.

    No clause moves towards violated (formal topics: monotonicity).
    Rules: CT1, CT2, CT3.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    if raw.get("spec") == "absent":
        pytest.skip("no spec")
    want = helpers.run_derive(helpers.build_scenario(raw))
    deviating = [
        r
        for r in want["arg_checks"]
        if r["comparison"] in helpers.DEVIATIONS and r["option"] is not None
    ]
    if not deviating:
        pytest.skip("no deviating row")
    spec = raw.setdefault("spec", {})
    options = spec.setdefault("options", {})
    for row in deviating:
        options[f"{row['invocation_key']} {row['option']}"] = "free"
    got = helpers.run_derive(helpers.build_scenario(raw))
    rows = {
        (r["command_id"], r["invocation"], r["option"]): r
        for r in got["arg_checks"]
    }
    for row in deviating:
        found = rows[(row["command_id"], row["invocation"], row["option"])]
        assert found["contract"] == found["conforms"] == "holds"
    for one, two in zip(want["faithfulness"], got["faithfulness"], strict=True):
        for clause in helpers.CLAUSES:
            assert (
                ORDER[two["clauses"][clause]] >= ORDER[one["clauses"][clause]]
            ), (one["input_id"], clause)


@pytest.mark.parametrize("path", TRACED, ids=lambda p: p.stem)
def test_removed_must_read_never_refutes_less(path: pathlib.Path) -> None:
    """Removing one read of the last writer never turns violated into holds.

    Rules: FA1, CH3.
    """
    raw = copy.deepcopy(helpers.read_raw(path))
    scenario = helpers.build_scenario(raw)
    want = helpers.run_derive(scenario)
    writers = {
        lw["writer_command_id"]
        for lw in (scenario["writer_result"] or {}).get("last_writers") or []
        if lw["last_writer_under"]
    }
    target = next(
        (
            e
            for e in raw.get("execs") or []
            if e["command_id"] in writers and e.get("reads")
        ),
        None,
    )
    if target is None:
        pytest.skip("no read of a certain last writer")
    target["reads"] = target["reads"][:-1]
    got = helpers.run_derive(helpers.build_scenario(raw))
    for one, two in zip(want["faithfulness"], got["faithfulness"], strict=True):
        assert not (
            one["faithful"] == "violated" and two["faithful"] == "holds"
        )


# (capability, the fields set to null in every exec)
CAPABILITY_FIELDS = (
    ("read_tracing", ("reads",)),
    ("parent_links", ("parent_command_id", "root_command_id", "depth", "pgid")),
    ("exact_timing", ("released_at", "ended_at", "snapshot_after_s")),
    ("content_hashes", ("snapshot.hashes",)),
    ("start_events", ("record_type",)),
    ("stdio", ("stdio",)),
)


@pytest.mark.parametrize(
    ("pair", "capability", "fields"),
    [
        (row[2], cap, fields)
        for row in helpers.CAPABILITY_PAIRS
        for cap, fields in CAPABILITY_FIELDS
        if row[0] == cap
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_capability_off_falls_back(
    pair: str, capability: str, fields: tuple[str, ...]
) -> None:
    """One capability set false with its fields null: only its bases move.

    The bases keys of that capability become fallback; every other bases
    key is unchanged (D29).
    Rules: PRE3, CH7, CH2, CH5, TM1, RD4, CH8.
    """
    raw = copy.deepcopy(helpers.read_raw(helpers.by_id(pair)))
    want = helpers.run_derive(helpers.build_scenario(raw))
    raw["run"]["capabilities"][capability] = False
    for record in raw.get("execs") or []:
        for field in fields:
            if field.startswith("snapshot."):
                (record.get("snapshot") or {}).pop(field[9:], None)
            else:
                record.pop(field, None)
    got = helpers.run_derive(helpers.build_scenario(raw))
    for key, value in got["chain"]["bases"].items():
        if helpers.BASIS_CAPABILITY[key] == capability:
            assert value == "fallback", key
        else:
            assert value == want["chain"]["bases"][key], key


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_same_input_twice_is_byte_identical(path: pathlib.Path) -> None:
    """Deriving the same input twice gives byte-identical output.

    The inputs are not modified, and floats are rounded to 6 decimals.
    Rules: AGG1.
    """
    for raw in helpers.run_raws(helpers.read_raw(path)):
        scenario = helpers.build_scenario(raw)
        before = _canonical(scenario)
        from trajectory_checks import chain

        first = _canonical(helpers.run_derive(scenario))
        second = _canonical(
            chain.derive(
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


def _scale_free(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Every scale option name replaced by a non-scale name (invariant 12)."""
    text = json.dumps(raw)
    names = "|".join(sorted(helpers.SCALE_NAMES, key=len, reverse=True))
    text = re.sub(
        rf"(?<=[\"' ])(-{{1,2}})({names})(?=[\"' =])", r"\1zzwidth", text
    )
    return json.loads(text)


@pytest.mark.parametrize("path", SINGLE, ids=lambda p: p.stem)
def test_scale_reduced_changes_no_clause(path: pathlib.Path) -> None:
    """Invariant 12's second half: scale names never move a clause.

    The scenario with every scale option renamed to a non-scale name has
    no scale_reduced row and the same clauses.
    Rules: SC1, SC2, SC3.
    """
    raw = helpers.read_raw(path)
    want = helpers.run_derive(helpers.build_scenario(raw))
    if not any(r["scale_reduced"] for r in want["arg_checks"]):
        pytest.skip("no scale_reduced row")
    got = helpers.run_derive(helpers.build_scenario(_scale_free(raw)))
    assert not any(r["scale_reduced"] for r in got["arg_checks"])
    for one, two in zip(want["faithfulness"], got["faithfulness"], strict=True):
        assert one["clauses"] == two["clauses"]


# Critic cycle 1: synthetic cases for the bugs it found, one per test.
def _inline_problems(raw: Mapping[str, Any]) -> list[str]:
    """expect_problems and the invariants of one inline scenario."""
    scenario = helpers.build_scenario(raw)
    result = helpers.run_derive(scenario)
    return expect_problems(
        result, scenario["expect"]
    ) + chain_invariants.check_chain(scenario, result)


def test_partial_start_events_fall_back() -> None:
    """A record without record_type makes records_at_stop fallback (§6.14).

    Rules: PRE3, CH8.
    """
    raw = helpers.read_raw(helpers.SCENARIOS_DIR / "H51a_start_events.yaml")
    del raw["execs"][1]["record_type"]
    raw["expect"] = {
        "bases": {"records_at_stop": "fallback"},
        "warnings_include": ["capability start_events partial: 1 records"],
    }
    assert _inline_problems(raw) == []


def test_run_scope_scale_count_survives_per_input_voiding() -> None:
    """scale_reduced_run is a *_run key: no layer-5 label voids it (ARG6).

    Rules: SC3, PRE2.
    """
    raw = helpers.read_raw(
        helpers.SCENARIOS_DIR / "H02_scale_below_default.yaml"
    )
    raw["last_writers"] = "absent"
    raw["expect"] = {
        "preconditions": {"last_writers": "fails"},
        "metrics": {
            "M5.1": {
                "scale_reduced_run": 1,
                "args_below_run": 1,
                "scale_reduced_under": None,
            }
        },
    }
    assert _inline_problems(raw) == []


def test_changed_pipe_sibling_program_blocks_built() -> None:
    """A pipe member's changed program keeps clause 3 from holding (FA4).

    The member writes nothing, so it is no node; its invocations are
    may-chain invocations (CH5), and their program paths are checked.

    Rules: CH5, FA4.
    """
    raw = helpers.read_raw(helpers.SCENARIOS_DIR / "H47a_pipes_stdio.yaml")
    raw["calls"].append({"call_id": "c0", "tool_type": "edit", "t_start": 5.0})
    raw["links"].append(
        {"link_type": "write_event", "path": "gen.sh", "writer_id": "c0"}
    )
    raw["baseline_files"] = ["run.sh", "gen.sh", "agg.sh"]
    raw["classes"] = {"gen.sh": "code"}
    raw["expect"] = {
        "faithfulness": [{"input_id": "i0", "clauses": {"built": None}}]
    }
    assert _inline_problems(raw) == []


def test_listing_without_states_is_not_refuted_fresh() -> None:
    """Layer 4 gives a listing row no state: clause 1 is None (FA2).

    Its predicates over an empty label set are all false, which is no
    evidence that the oracle read stale content (D79).

    Rules: FA2, CH1.
    """
    raw = helpers.read_raw(helpers.SCENARIOS_DIR / "H69_pattern_and_stat.yaml")
    false = dict.fromkeys(("fresh", "absent", "agent_written"), False)
    raw["input_states"] = [
        {
            "input_id": "i0",
            "state_under": None,
            "states_over": [],
            "predicates_under": false,
            "predicates_over": false,
        }
    ]
    raw["expect"] = {
        "faithfulness": [
            {"input_id": "i0", "clauses": {"fresh": None}, "faithful": None}
        ],
        "reasons_include": {"i0": {"fresh": "no possible state"}},
    }
    assert _inline_problems(raw) == []


def test_derived_reason_names_the_deciding_part() -> None:
    """A refuted witness names the witness, not an undecided deviation.

    The only declared invocation exited 1 (no witness: clause 2 is
    violated) and also deviates without a marker (conformance None).

    Rules: FA3, CT3.
    """
    raw = helpers.read_raw(helpers.SCENARIOS_DIR / "H01_equal_argument.yaml")
    del raw["runs"]
    raw["execs"][0].update(command="./tool.sh --k 4", exit_code=1)
    raw["expect"] = {
        "faithfulness": [
            {"input_id": "i0", "clauses": {"derived": "violated"}}
        ],
        "reasons_include": {"i0": {"derived": "no witness"}},
    }
    assert _inline_problems(raw) == []


# Critic cycle 2.
def _running_call(command: str, edited: bool) -> dict[str, Any]:
    """H51b traced: a call running at stop, bench.sh edited or not."""
    raw = helpers.read_raw(helpers.SCENARIOS_DIR / "H51b_start_events_off.yaml")
    raw["run"] = {"capabilities": {"read_tracing": True}}
    raw["calls"][0]["command"] = command
    raw["execs"][0]["reads"] = []
    raw["baseline_files"] = ["bench.sh", "agg.py"]
    raw["classes"] = {"bench.sh": "code"}
    if edited:
        raw["calls"].append(
            {"call_id": "c0", "tool_type": "edit", "t_start": 5.0}
        )
        raw["links"].append(
            {"link_type": "write_event", "path": "bench.sh", "writer_id": "c0"}
        )
    return raw


def test_running_call_changed_program_blocks_built() -> None:
    """A call running at stop runs its program too: clause 3 checks it.

    Its cwd is unknown (a call has none), so ./bench.sh cannot be
    resolved and clause 3 cannot hold; edited or not, it is None (D83).

    Rules: CH8, FA4.
    """
    for edited in (True, False):
        raw = _running_call("./bench.sh", edited)
        raw["expect"] = {
            "bases": {"chain": "exact", "records_at_stop": "fallback"},
            "faithfulness": [{"input_id": "i0", "clauses": {"built": None}}],
        }
        assert _inline_problems(raw) == [], edited


def test_running_call_absolute_program_is_checked() -> None:
    """An absolute program path of a running call is checked (CH8, FA4).

    Pristine, clause 3 holds; edited before the call started, it is a
    may-chain program path that changed (not allowed): None.

    Rules: CH8, FA4.
    """
    for edited, want in ((False, "holds"), (True, None)):
        raw = _running_call("bash /repo/bench.sh", edited)
        raw["expect"] = {
            "faithfulness": [{"input_id": "i0", "clauses": {"built": want}}]
        }
        assert _inline_problems(raw) == [], edited


def test_running_call_with_unknown_cwd_never_refutes_derived() -> None:
    """A running call's path key depends on its unknown cwd (CH8, D83).

    The declared key is sub/bench.sh; the call ran ./bench.sh from a cwd
    the trace does not give, so it may be that key: clause 2 is None,
    not violated.

    Rules: CH8, FA3.
    """
    raw = _running_call("./bench.sh", False)
    raw["declared"]["invocations"][0]["invocation_key"] = "sub/bench.sh"
    raw["expect"] = {
        "faithfulness": [{"input_id": "i0", "clauses": {"derived": None}}]
    }
    assert _inline_problems(raw) == []
