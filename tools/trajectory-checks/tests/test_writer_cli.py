"""Layer 5 end to end: what cli.py reads, routes and writes (§2.2.5, §3.4).

Run folders are built in ``tmp_path`` from the two fixtures ``test_cli``
uses (one shell call and its trace record), with the trace record's
snapshot set to report a write to ``data/a.csv``, a baseline, a final
diff and a synthetic ``--oracle-runs`` folder whose null-agent manifest
reads ``data/a.csv``; nothing depends on the corpus. ``cli`` and
``writer`` are imported inside each test (D41).
"""

import copy
import csv
import json
import pathlib
from typing import Any

import conftest
import pytest
import writer_helpers as helpers

RUN_TOKEN = "05720569"  # run_id of the trace fixture record
TRACE_PATH = f"/elsewhere/monitor/{RUN_TOKEN}/commands.jsonl"
START = "2026-10-12T16:43:26.178064+00:00"  # its timing monitor
DURATION_S = 1.284
NULL_RECORD = {
    "check_id": "read_a",
    "phase": "experiment_runs",
    "method": "read_file_text",
    "path": "/repo/data/a.csv",
    "resolved_workspace_path": "data/a.csv",
    "exists": True,
    "sha256": "aa",
    "size": 2,
    "mtime_ns": 1,
    "glob_pattern": None,
    "glob_matches": None,
    "argv": None,
    "exists_before": True,
    "sha256_before": "aa",
    "tracked_before": True,
}
DIFF = """\
diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,2 @@
 import os
-x = 1
+x = 2
"""
WRITER_FILES = ("last_writers.jsonl", "input_rewrites.jsonl", "hunks.jsonl")


def _run(
    root: pathlib.Path,
    name: str = "project__claude__1",
    *,
    diff: str | None = DIFF,
    final_files: Any = None,
) -> pathlib.Path:
    """A run folder whose one command modifies data/a.csv."""
    run_dir = root / name
    (run_dir / "run").mkdir(parents=True)
    monitor = run_dir / "monitor"
    monitor.mkdir()
    log = conftest.PARSE_FIXTURES_DIR / "claude" / "shell_ok.log"
    (run_dir / "run" / "runner_output.log").write_bytes(log.read_bytes())
    (run_dir / "run" / "result.jsonl").write_text(
        json.dumps(
            {
                "started_at": "2026-10-12T16:40:00Z",
                "finished_at": "2026-10-12T17:00:00Z",
                "agent_kind": "claude_non_api",
                "runtime": {"workspace_mount": "/repo"},
                "command_monitor": {
                    "run_token": RUN_TOKEN,
                    "trace_path": TRACE_PATH,
                },
            }
        )
        + "\n"
    )
    trace = conftest.TRACE_FIXTURES_DIR / "claude_eval_skipped.jsonl"
    record = json.loads(trace.read_text().splitlines()[0])
    record["monitors"]["file_snapshot"] = {
        "scope": "tracked",
        "modified": ["data/a.csv"],
    }
    (monitor / "commands.jsonl").write_text(json.dumps(record) + "\n")
    (monitor / "tracked_files.json").write_text(
        json.dumps(
            {
                "commit": "c0",
                "source": "git",
                "files": {"data/a.csv": {"sha256": "aa"}},
            }
        )
    )
    (monitor / "tracked_files_diff.json").write_text(
        json.dumps(
            {"modified": ["data/a.csv"], "deleted": [], "mode_changed": []}
        )
    )
    if diff is not None:
        (monitor / "final.diff").write_text(diff)
    if final_files is not None:
        (monitor / "final_files.json").write_text(json.dumps(final_files))
    return run_dir


def _oracle_runs(
    root: pathlib.Path, artifacts: tuple[str, ...] = ("project",)
) -> pathlib.Path:
    """An --oracle-runs folder: one null-agent run per artifact."""
    for artifact in artifacts:
        folder = root / "oracle_runs" / artifact
        folder.mkdir(parents=True)
        (folder / "oracle_inputs.jsonl").write_text(
            json.dumps(NULL_RECORD) + "\n"
        )
        (folder / "null_agent_score.json").write_text(
            json.dumps({"score": 0, "phases": [], "commit": "c0"})
        )
    return root / "oracle_runs"


def _rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _lines(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_writer_files_written(tmp_path: pathlib.Path) -> None:
    """The four per-run files and the two CSVs are written.

    One sorted-key object per JSON Lines line; analyse returns the four
    layer-5 keys; BLOCKS and _OWNERS gain the writer's rows.
    Rules: LW1, HK1, OUT1.
    """
    from trajectory_checks import cli, match

    runs = _oracle_runs(tmp_path)
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    assert (
        cli.main([str(run_dir), "--out", str(out), "--oracle-runs", str(runs)])
        == 0
    )
    folder = out / "project__claude__1"
    for name in (*WRITER_FILES, "writer.json"):
        assert (folder / name).is_file(), name
    for name in ("writer.csv", "writer_inputs.csv"):
        assert (out / name).is_file(), name
    for name in WRITER_FILES:
        for record in _lines(folder / name):
            assert list(record) == sorted(record)
    (row,) = _lines(folder / "last_writers.jsonl")
    assert (row["path"], row["last_writer_type"]) == ("data/a.csv", "exec")
    (hunk,) = _lines(folder / "hunks.jsonl")
    assert (hunk["path"], hunk["hunk_type"]) == ("src/a.py", "numeric_only")
    block = json.loads((folder / "writer.json").read_text())
    assert set(block) == helpers.WRITER_FIELDS
    assert cli.WRITER_FILES == ("last_writers", "input_rewrites", "hunks")
    assert ("writer", "writer") in cli.BLOCKS
    assert cli._OWNERS["writer"] == (match.DEFAULTS,)
    result = cli.analyse(run_dir, oracle_runs=runs)
    assert {"last_writers", "input_rewrites", "hunks", "writer"} <= set(result)


def _header_set(
    root: pathlib.Path, order: tuple[str, ...], *, diffs: bool, oracle: bool
) -> dict[str, bytes]:
    """Runs main on one run per artifact, in that order of paths."""
    runs = root / "runs"
    for index, artifact in enumerate(order):
        made = _run(runs / str(index), diff=DIFF if diffs else None)
        made.rename(made.parent / f"{artifact}__claude__1")
    args = [str(runs), "--out", str(root / "out")]
    if oracle:
        args += ["--oracle-runs", str(_oracle_runs(root, order))]
    from trajectory_checks import cli

    assert cli.main(args) == 0
    return {
        name: (root / "out" / name).read_bytes().splitlines()[0]
        for name in ("writer.csv", "writer_inputs.csv")
    }


def test_writer_csv_header_is_fixed(tmp_path: pathlib.Path) -> None:
    """Both headers are byte-identical for four run sets (D40).

    A set with final diffs and hunks, the same set in reverse order, a
    set without final diffs (M4.3 ⊥) and a set without oracle inputs
    (M3.3, M2.4 ⊥); writer_inputs.csv keeps its header with no row.
    Rules: PEER1, PEER2, PRE3.
    """
    from trajectory_checks import writer

    forward = _header_set(
        tmp_path / "a", ("alpha", "beta"), diffs=True, oracle=True
    )
    backward = _header_set(
        tmp_path / "b", ("beta", "alpha"), diffs=True, oracle=True
    )
    plain = _header_set(
        tmp_path / "c", ("alpha", "beta"), diffs=False, oracle=True
    )
    bare = _header_set(
        tmp_path / "d", ("alpha", "beta"), diffs=True, oracle=False
    )
    assert forward == backward == plain == bare
    inputs = forward["writer_inputs.csv"].decode().split(",")
    assert inputs == [*writer.INPUT_COLUMNS, *helpers.RATIO_COLUMNS]
    assert _rows(tmp_path / "d" / "out" / "writer_inputs.csv") == []
    assert len(_rows(tmp_path / "a" / "out" / "writer_inputs.csv")) == 2


def test_writer_csv_cells(tmp_path: pathlib.Path) -> None:
    """Dict metrics by fixed sub-keys; a voided dict is empty, absent 0.

    diff_files_unclassified is no column; every bases and preconditions
    key is; every M… column has a peer-ratio column.
    Rules: PEER1, PRE3, PRE6.
    """
    _header_set(tmp_path / "a", ("alpha", "beta"), diffs=True, oracle=True)
    _header_set(tmp_path / "c", ("alpha", "beta"), diffs=False, oracle=True)
    with_diff = {r["run_key"]: r for r in _rows(tmp_path / "a/out/writer.csv")}
    without = {r["run_key"]: r for r in _rows(tmp_path / "c/out/writer.csv")}
    row = with_diff["alpha__claude__1"]
    columns = list(row)
    assert "M4.3.hunks_by_class_and_type.code.numeric_only" in columns
    assert "M3.3.last_writer_by_role_over.root_exec" in columns
    assert "M3.3.last_writer_by_role_over.root_exec_peer_ratio" in columns
    assert not any("diff_files_unclassified" in c for c in columns)
    for key in helpers.BASES_KEYS:
        assert f"bases.{key}" in columns, key
    for label in helpers.PRECONDITION_LABELS:
        assert f"preconditions.{label}" in columns, label
    assert row["M4.3.hunks_by_type.numeric_only"] == "1"
    assert row["M4.3.hunks_by_class_and_type.ci.general"] == "0"
    assert row["M3.3.last_writer_by_role_over.root_exec"] == "1"
    assert row["M3.3.last_writer_by_role_over.edit_call"] == "0"
    other = without["alpha__claude__1"]
    assert other["M4.3.hunks_by_type.numeric_only"] == ""
    assert other["M4.3.hunks_by_class_and_type.code.numeric_only"] == ""
    assert other["preconditions.final_diff_content"] == "fails"
    for column in columns:
        if column.startswith("M") and not column.endswith("_peer_ratio"):
            assert f"{column}_peer_ratio" in columns, column


def test_writer_inputs_csv_peer_ratios(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W37's three runs through cli.main give its per-input ratios.

    cli.analyse is wrapped so that each run folder's layer-5 result is
    W37's run of the same run_key; main builds writer_inputs.csv from
    them with writer.input_peer_ratios.
    Rules: PEER2.
    """
    from trajectory_checks import cli

    path = next(p for p in helpers.scenario_paths() if p.stem.startswith("W37"))
    scenarios = {s["run"]["run_key"]: s for s in helpers.load_runs(path)}
    original = cli.analyse

    def analyse(
        run_dir: Any, config: Any = None, oracle_runs: Any = None
    ) -> dict:
        result = original(run_dir, config, oracle_runs)
        key = pathlib.Path(run_dir).name
        return {**result, **helpers.run_derive(scenarios[key])}

    monkeypatch.setattr(cli, "analyse", analyse)
    for key in scenarios:
        _run(tmp_path / "in", key)
    out = tmp_path / "out"
    assert cli.main([str(tmp_path / "in"), "--out", str(out)]) == 0
    rows = {
        (r["run_key"], r["input_id"]): r
        for r in _rows(out / "writer_inputs.csv")
    }
    want = {
        ("art__claude__1", "i0"): "0.166667",
        ("art__claude__2", "i3"): "0.363636",
        ("art__codex__1", "i1"): "6.666667",
    }
    assert set(rows) == set(want)
    for key, ratio in want.items():
        assert rows[key]["M3.3.writer_duration_s_peer_ratio"] == ratio
        assert rows[key]["M3.3.root_span_over_s_peer_ratio"] == ratio
        assert rows[key]["M2.4.rewrites_over_peer_ratio"] == ""
        assert rows[key]["pattern"] == ""
        assert rows[key]["path"] == "results/t.json"


def test_writer_config_routing(tmp_path: pathlib.Path) -> None:
    """snapshot_slop_s is layer 1's and reaches LW4; others are rejected.

    The key changes the window in last_writers.jsonl and is routed to
    match.link too; an unknown key exits 2 once, before any run;
    writer.derive raises ValueError for a key outside match.DEFAULTS.
    Rules: LW4, PRE6.
    """
    from trajectory_checks import cli, writer

    runs = _oracle_runs(tmp_path)
    run_dir = _run(tmp_path / "in")
    spans = {}
    for slop in (None, 0.5):
        out = tmp_path / f"out_{slop}"
        args = [str(run_dir), "--out", str(out), "--oracle-runs", str(runs)]
        if slop is not None:
            config = tmp_path / "config.toml"
            config.write_text(f"snapshot_slop_s = {slop}\n")
            args += ["--config", str(config)]
        assert cli.main(args) == 0
        (row,) = _lines(out / "project__claude__1" / "last_writers.jsonl")
        spans[slop] = row["window_hi"] - row["window_lo"]
    assert spans[None] == pytest.approx(DURATION_S + 2.0)
    assert spans[0.5] == pytest.approx(DURATION_S + 0.5)
    routed = cli.route_config({"snapshot_slop_s": 0.5})
    assert routed["writer"] == {"snapshot_slop_s": 0.5}
    assert routed["match"] == {"snapshot_slop_s": 0.5}
    assert cli.route_config({"min_peers": 3})["writer"] == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("no_such_key = 1\n")
    out = tmp_path / "out_bad"
    with pytest.raises(SystemExit) as raised:
        cli.main([str(run_dir), "--out", str(out), "--config", str(bad)])
    assert raised.value.code == 2
    assert not out.exists()
    scenario = helpers.load_scenario(helpers.scenario_paths()[0])
    for key in ("min_peers", "no_such_key", "passing_statuses"):
        with pytest.raises(ValueError):
            helpers.run_derive(scenario, config={key: 1})
    assert not hasattr(writer, "DEFAULTS")


def test_writer_failure_is_isolated(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose writer.derive raises fails alone, exit 1 (D56).

    Rules: PRE3.
    """
    from trajectory_checks import cli, writer

    runs = _oracle_runs(tmp_path)
    dirs = [
        _run(tmp_path / name, f"project__{name}__1") for name in ("bad", "good")
    ]
    original = writer.derive

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if kwargs["run"]["run_key"].endswith("bad__1"):
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(writer, "derive", flaky)
    out = tmp_path / "out"
    code = cli.main(
        [*map(str, dirs), "--out", str(out), "--oracle-runs", str(runs)]
    )
    assert code == 1
    assert not (out / "project__bad__1").exists()
    assert (out / "project__good__1" / "writer.json").is_file()
    assert [r["run_key"] for r in _rows(out / "writer.csv")] == [
        "project__good__1"
    ]
    assert [r["run_key"] for r in _rows(out / "writer_inputs.csv")] == [
        "project__good__1"
    ]


def test_writer_mc03_read_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """final.diff and final_files.json reach layers 3, 4 and 5 alike.

    The same text reaches structure.derive and writer.derive, the same
    list input_state.classify and writer.derive; a malformed entry is
    skipped once, with layer 4's note.
    Rules: HK1, HK4, LW11.
    """
    from trajectory_checks import cli, input_state, structure, writer

    seen: dict[str, list] = {"structure": [], "input_state": [], "writer": []}
    for module, name, key in (
        (structure, "derive", "structure"),
        (input_state, "classify", "input_state"),
        (writer, "derive", "writer"),
    ):
        original = getattr(module, name)

        def spy(*args: Any, _original=original, _key=key, **kwargs: Any) -> Any:
            seen[_key].append(copy.deepcopy(kwargs))
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, spy)
    entries = [
        {
            "path": "src/a.py",
            "status": "M",
            "size": 9,
            "sha256": "ab",
            "in_diff": True,
            "reason_omitted": None,
        },
        "not an entry",
    ]
    runs = _oracle_runs(tmp_path)
    result = cli.analyse(
        _run(tmp_path / "in", final_files=entries), oracle_runs=runs
    )
    (layer3,), (layer4,), (layer5,) = (
        seen["structure"],
        seen["input_state"],
        seen["writer"],
    )
    assert layer5["final_diff_text"] == layer3["final_diff_text"] == DIFF
    assert layer5["final_files"] == layer4["final_files"] == [entries[0]]
    note = "final_files.json: malformed entries skipped"
    warnings = [
        w
        for block in ("oracle_inputs_run", "state_counts", "writer")
        for w in result[block]["warnings"]
    ]
    assert warnings.count(note) == 1


def test_final_files_note_keeps_layer4_order(tmp_path: pathlib.Path) -> None:
    """Reading final_files.json once leaves oracle_inputs.json unchanged.

    Before layer 5, layer 4 noted a malformed final_files.json after the
    scores and before the graded manifest; moving the read into
    cli._final_files (D37) keeps that order of its warnings.
    Rules: HK4, LW11.
    """
    from trajectory_checks import cli

    runs = _oracle_runs(tmp_path)
    (runs / "project" / "null_agent_score.json").write_text(
        json.dumps({"score": "high", "phases": [], "commit": "c0"})
    )
    run_dir = _run(tmp_path / "in", final_files=[{"path": "src/a.py"}, 7])
    (run_dir / "run" / "oracle_result.json").write_text(
        json.dumps({"score": 1, "phases": ["bad"]})
    )
    warnings = cli.analyse(run_dir, oracle_runs=runs)["oracle_inputs_run"][
        "warnings"
    ]
    notes = [w for w in warnings if ": malformed" in w]
    assert notes == [
        "null_agent_score.json: malformed phases or score skipped",
        "oracle_result.json: malformed phases or score skipped",
        "final_files.json: malformed entries skipped",
    ]
