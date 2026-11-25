"""Layer 4 end to end: what cli.py reads and writes (§0.8, §3.4).

The run folder is the one ``test_cli.py`` assembles from two fixtures (one
shell call and its trace record), plus synthetic run-folder files and a
synthetic ``--oracle-runs`` folder; nothing depends on the corpus. The
layer-4 modules are imported inside each test (D44).
"""

import csv
import json
import pathlib
from typing import Any

import pytest
import test_cli

from trajectory_checks import cli

ARTIFACT = "project"  # the run_key of test_cli._make_run starts with it
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
REFERENCE_RECORD = {
    **NULL_RECORD,
    "check_id": "expected",
    "path": "/refs/x.json",
    "resolved_workspace_path": None,
    "sha256": "ee",
    "exists_before": None,
    "sha256_before": None,
    "tracked_before": None,
}
PHASES = ("env_setup", "experiment_runs")
LAYER4_FILES = (
    "oracle_inputs.jsonl",
    "oracle_inputs.json",
    "input_states.jsonl",
    "state_counts.json",
)


def _result(phases: dict[str, str]) -> dict[str, Any]:
    passed = sum(status == "success" for status in phases.values())
    return {
        "artifacts": {},
        "error": None,
        "phases": [
            {"error": None, "phase": p, "status": s, "summary": ""}
            for p, s in phases.items()
        ],
        "score": passed,
        "status": "success",
        "summary": f"Passed {passed}/{len(phases)} phases.",
    }


def _run(root: pathlib.Path, *, baseline: bool = True) -> pathlib.Path:
    """A run folder with a baseline, a final diff and an oracle result."""
    run_dir = test_cli._make_run(root)
    monitor = run_dir / "monitor"
    if baseline:
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
        json.dumps({"modified": [], "deleted": [], "mode_changed": []})
    )
    (run_dir / "run" / "oracle_result.json").write_text(
        json.dumps(_result(dict.fromkeys(PHASES, "success")))
    )
    return run_dir


def _oracle_runs(
    root: pathlib.Path,
    records: list[dict[str, Any]] | None = None,
    commit: str = "c0",
) -> pathlib.Path:
    """An --oracle-runs folder holding one null-agent run of the artifact."""
    folder = root / "oracle_runs" / ARTIFACT
    folder.mkdir(parents=True)
    lines = [json.dumps(r) for r in records or [NULL_RECORD]]
    (folder / "oracle_inputs.jsonl").write_text("\n".join(lines) + "\n")
    score = _result({"env_setup": "error", "experiment_runs": "success"})
    score.update(commit=commit, case_image_digest=None, oracle_digest=None)
    (folder / "null_agent_score.json").write_text(json.dumps(score))
    return folder.parent


def test_analyse_without_oracle_runs(tmp_path: pathlib.Path) -> None:
    """Without any manifest both layer-4 blocks report the missing input.

    Rules: OI1, PRE1.
    """
    result = cli.analyse(_run(tmp_path))
    for key in ("oracle_inputs", "oracle_inputs_run", "input_states"):
        assert key in result, key
    assert result["oracle_inputs"] == []
    assert result["input_states"] == []
    block = result["oracle_inputs_run"]
    assert block["preconditions"]["oracle_inputs"] == "fails"
    assert block["run_key"] == result["coverage"]["run_key"]
    counts = result["state_counts"]
    assert counts["preconditions"]["oracle_inputs"] == "fails"
    assert all(v is None for v in counts["metrics"]["M3.2"].values())
    assert any("input oracle_inputs missing" in w for w in counts["warnings"])


def test_analyse_with_a_null_agent_run(tmp_path: pathlib.Path) -> None:
    """The null-agent files, the run result and the baseline are read.

    Rules: OI1, OI9, NS1, NS2, PHS1, EVD1.
    """
    runs = _oracle_runs(tmp_path)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    (row,) = result["oracle_inputs"]
    assert (row["path"], row["source_type"]) == ("data/a.csv", "null_agent")
    block = result["oracle_inputs_run"]
    assert block["preconditions"]["null_commit"] == "holds"
    m35 = block["metrics"]["M3.5"]
    assert (m35["null_score"], m35["run_score"], m35["score_gain"]) == (1, 2, 1)
    assert m35["phases_gained"] == ["env_setup"]
    (state,) = result["input_states"]
    assert state["path"] == "data/a.csv"
    assert state["baseline_source"] == "tracked_files"
    assert state["content_cmp"] == "equal"
    m32 = result["state_counts"]["metrics"]["M3.2"]
    assert m32["oracle_inputs"] == 1
    assert m32["baseline_content_under"] == 1
    assert m32["phases"]["experiment_runs"]["status"] == "success"
    assert m32["phases"]["env_setup"]["status"] == "success"


def test_graded_manifest_is_read(tmp_path: pathlib.Path) -> None:
    """run/oracle_inputs.jsonl gives the graded source; bad lines skipped.

    Rules: OI1, EVD2.
    """
    run_dir = _run(tmp_path)
    graded = {**NULL_RECORD, "sha256": "ab", "mtime_ns": 5}
    for key in ("exists_before", "sha256_before", "tracked_before"):
        graded[key] = None
    (run_dir / "run" / "oracle_inputs.jsonl").write_text(
        json.dumps(graded) + "\n{not json\n"
    )
    result = cli.analyse(run_dir)
    assert result["coverage"]["capabilities"]["oracle_reads"] is True
    (row,) = result["oracle_inputs"]
    assert (row["source_type"], row["grading_sha256"]) == ("graded", "ab")
    assert result["oracle_inputs_run"]["bases"]["source"] == "exact"
    (state,) = result["input_states"]
    assert state["content_source"] == "grading_read"
    assert state["content_cmp"] == "differs"


def test_final_files_are_read(tmp_path: pathlib.Path) -> None:
    """monitor/final_files.json gives OI10's exact form on a graded run.

    Rules: OI10, EVD4.
    """
    run_dir = _run(tmp_path)
    graded = {**NULL_RECORD, "sha256": "ab"}
    (run_dir / "run" / "oracle_inputs.jsonl").write_text(json.dumps(graded))
    (run_dir / "monitor" / "final.diff").write_text("")
    (run_dir / "monitor" / "final_files.json").write_text(
        json.dumps(
            [
                {
                    "path": "data/a.csv",
                    "status": "M",
                    "size": 2,
                    "sha256": "a1",
                    "in_diff": True,
                    "reason_omitted": None,
                }
            ]
        )
    )
    result = cli.analyse(run_dir)
    (row,) = result["oracle_inputs"]
    assert (row["oracle_writes"], row["oracle_writes_basis"]) == (True, "exact")
    assert result["oracle_inputs_run"]["bases"]["oracle_writes"] == "exact"
    (state,) = result["input_states"]
    assert state["states_under"] == state["states_over"] == ["oracle_written"]


def test_final_files_not_a_list(tmp_path: pathlib.Path) -> None:
    """A final_files.json that is not a JSON list is not passed.

    Rules: OI10.
    """
    run_dir = _run(tmp_path)
    (run_dir / "run" / "oracle_inputs.jsonl").write_text(
        json.dumps(NULL_RECORD)
    )
    (run_dir / "monitor" / "final.diff").write_text("")
    (run_dir / "monitor" / "final_files.json").write_text('{"path": "x"}')
    result = cli.analyse(run_dir)
    (row,) = result["oracle_inputs"]
    assert row["oracle_writes_basis"] != "exact"
    assert result["oracle_inputs_run"]["bases"]["oracle_writes"] == "fallback"


def test_run_meta_gives_the_commit(tmp_path: pathlib.Path) -> None:
    """Without tracked_files.json the commit comes from run_meta.txt.

    Rules: OI9.
    """
    run_dir = _run(tmp_path / "in", baseline=False)
    (run_dir / "run" / "run_meta.txt").write_text("x=1\nupstream_ref=c0\n")
    result = cli.analyse(run_dir, oracle_runs=_oracle_runs(tmp_path))
    block = result["oracle_inputs_run"]
    assert block["metrics"]["M3.1"]["run_commit"] == "c0"
    assert not any("unverified" in w for w in block["warnings"])
    other = _oracle_runs(tmp_path / "other", commit="c1")
    voided = cli.analyse(run_dir, oracle_runs=other)["oracle_inputs_run"]
    assert voided["preconditions"]["null_commit"] == "fails"


def test_write_outputs_writes_layer4_files(tmp_path: pathlib.Path) -> None:
    """The four layer-4 files, byte-identical when written twice.

    One sorted-key object per JSON Lines line.
    Rules: OI7, CNT2.
    """
    runs = _oracle_runs(tmp_path)
    folders = []
    for name in ("one", "two"):
        result = cli.analyse(_run(tmp_path / name), oracle_runs=runs)
        folders.append(cli.write_outputs(result, tmp_path / f"out_{name}"))
    for name in LAYER4_FILES:
        first = (folders[0] / name).read_bytes()
        assert first == (folders[1] / name).read_bytes(), name
    for name in ("oracle_inputs.jsonl", "input_states.jsonl"):
        lines = (folders[0] / name).read_text().splitlines()
        assert lines, name
        for line in lines:
            record = json.loads(line)
            assert list(record) == sorted(record)
    block = json.loads((folders[0] / "oracle_inputs.json").read_text())
    assert block["schema_version"] == "1"
    counts = json.loads((folders[0] / "state_counts.json").read_text())
    assert set(counts["metrics"]) == {"M3.2"}


def _rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_main_writes_both_csvs(tmp_path: pathlib.Path) -> None:
    """--oracle-runs is read; oracle_inputs.csv and state_counts.csv.

    One row per run, flattened, peer ratios, list cells left out (D38).
    Rules: OI8, CNT3, PHS1.
    """
    runs = _oracle_runs(tmp_path)
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    args = [str(run_dir), "--out", str(out), "--oracle-runs", str(runs)]
    assert cli.main(args) == 0
    (oracle,) = _rows(out / "oracle_inputs.csv")
    assert oracle["run_key"] == "project__claude__1"
    assert oracle["M3.1.inputs_under"] == "1"
    assert "M3.1.inputs_under_peer_ratio" in oracle
    assert "M3.1.phases_complete.experiment_runs" in oracle
    assert "M3.5.null_score" in oracle
    assert "preconditions.null_commit" in oracle
    assert "bases.oracle_writes" in oracle
    assert "capabilities.oracle_reads" in oracle
    assert "M3.5.null_phases_passed" not in oracle
    (state,) = _rows(out / "state_counts.csv")
    assert state["M3.2.oracle_inputs"] == "1"
    assert "M3.2.by_state_under.pristine" in state
    assert "M3.2.by_state_over.oracle_written" in state
    for phase in PHASES:
        assert f"M3.2.phases.{phase}.baseline_content_all_under" in state
    assert "M3.2.oracle_inputs_peer_ratio" in state
    assert "bases.detached" in state
    assert "M3.2.final_writer_roots_under" not in state
    for name in LAYER4_FILES:
        assert (out / "project__claude__1" / name).is_file(), name
    again = tmp_path / "again"
    args[2] = str(again)
    assert cli.main(args) == 0
    for name in ("oracle_inputs.csv", "state_counts.csv"):
        assert (out / name).read_bytes() == (again / name).read_bytes()


def test_main_routes_layer4_config(tmp_path: pathlib.Path) -> None:
    """Layer-4 config keys reach their module and no lower layer (D42).

    Rules: OI2, PHS3.
    """
    runs = _oracle_runs(tmp_path, [NULL_RECORD, REFERENCE_RECORD])
    config = tmp_path / "config.toml"
    config.write_text(
        'reference_prefixes = ["/refs"]\npassing_statuses = ["success"]\n'
    )
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    args = [str(run_dir), "--out", str(out), "--oracle-runs", str(runs)]
    assert cli.main([*args, "--config", str(config)]) == 0
    rows = [
        json.loads(line)
        for line in (out / "project__claude__1" / "oracle_inputs.jsonl")
        .read_text()
        .splitlines()
    ]
    scopes = {row["path"]: row["input_scope"] for row in rows}
    assert scopes == {"data/a.csv": "workspace", "/refs/x.json": "reference"}
    counts = json.loads(
        (out / "project__claude__1" / "state_counts.json").read_text()
    )
    assert counts["metrics"]["M3.2"]["reference_inputs_excluded"] == 1


def test_module_failure_fails_only_that_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure inside layer 4 fails its run; the other runs go on.

    Rules: PRE1, OI1.
    """
    from trajectory_checks import input_state

    runs = _oracle_runs(tmp_path)
    dirs = []
    for name in ("bad", "good"):
        made = _run(tmp_path / name)
        dirs.append(made.rename(made.parent / f"{ARTIFACT}__{name}__1"))
    original = input_state.classify

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if kwargs["run"]["run_key"].endswith("bad__1"):
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(input_state, "classify", flaky)
    out = tmp_path / "out"
    code = cli.main(
        [*map(str, dirs), "--out", str(out), "--oracle-runs", str(runs)]
    )
    assert code == 1
    assert not (out / f"{ARTIFACT}__bad__1").exists()
    assert (out / f"{ARTIFACT}__good__1" / "state_counts.json").is_file()
    assert len(_rows(out / "state_counts.csv")) == 1


# ------------------------------------------- cycle-1 critic: A2, A3, A4


def _null_folder(
    root: pathlib.Path,
    manifest: bytes | None = None,
    score: dict[str, Any] | None = None,
    artifact: str = ARTIFACT,
) -> pathlib.Path:
    """An --oracle-runs folder with raw manifest bytes and a raw score."""
    folder = root / "oracle_runs" / artifact
    folder.mkdir(parents=True)
    if manifest is not None:
        (folder / "oracle_inputs.jsonl").write_bytes(manifest)
    if score is not None:
        (folder / "null_agent_score.json").write_text(json.dumps(score))
    return folder.parent


def _lines(*records: dict[str, Any]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode()


def _warnings(result: dict[str, Any]) -> list[str]:
    return [
        *result["oracle_inputs_run"]["warnings"],
        *result["state_counts"]["warnings"],
    ]


def test_undecodable_manifest_lines_warn(tmp_path: pathlib.Path) -> None:
    """Skipped JSON Lines lines give a warning; the good records are kept.

    Blank lines are not counted; the two lines that hold no object are.
    Rules: OI1.
    """
    data = _lines(NULL_RECORD) + b"\n   \n\xff\xfe{not json\n[1, 2]\n\n"
    runs = _null_folder(tmp_path, data)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    (row,) = result["oracle_inputs"]
    assert row["path"] == "data/a.csv"
    warnings = result["oracle_inputs_run"]["warnings"]
    hits = [w for w in warnings if "undecodable lines skipped" in w]
    assert hits == ["oracle_inputs.jsonl: 2 undecodable lines skipped"]


def test_line_separator_inside_a_string(tmp_path: pathlib.Path) -> None:
    """U+2028 inside a JSON string does not split the record (B3).

    JSON Lines ends a record only at a line feed.
    Rules: OI1.
    """
    record = {**NULL_RECORD, "check_id": "read\u2028a\u0085"}
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    runs = _null_folder(tmp_path, data)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    (row,) = result["oracle_inputs"]
    assert row["checks"] == ["read\u2028a\u0085"]
    assert not any("undecodable" in w for w in _warnings(result))


@pytest.mark.parametrize("source", ["null_agent", "graded"])
def test_manifest_with_no_object_is_not_passed(
    tmp_path: pathlib.Path, source: str
) -> None:
    """A non-empty manifest with no decodable object is ⊥, not empty.

    The precondition fails (M3.1 and M3.2 null) instead of reading as a
    vacuous manifest that says the oracle reads nothing.
    Rules: OI1, PRE1.
    """
    data = b"\xff\xfe\x00garbage\n{not json\n"
    run_dir = _run(tmp_path / "in")
    runs = None
    if source == "graded":
        (run_dir / "run" / "oracle_inputs.jsonl").write_bytes(data)
    else:
        runs = _null_folder(tmp_path, data)
    result = cli.analyse(run_dir, oracle_runs=runs)
    block = result["oracle_inputs_run"]
    assert block["metrics"]["M3.1"]["source_type"] is None
    assert block["preconditions"]["oracle_inputs"] == "fails"
    counts = result["state_counts"]
    assert counts["preconditions"]["oracle_inputs"] == "fails"
    assert counts["metrics"]["M3.2"]["oracle_inputs"] is None


def test_empty_manifest_is_vacuous(tmp_path: pathlib.Path) -> None:
    """An empty manifest stays a manifest with no input (guard for A2).

    Rules: OI1.
    """
    runs = _null_folder(tmp_path, b"")
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    assert result["oracle_inputs_run"]["preconditions"]["oracle_inputs"] == (
        "vacuous"
    )
    assert not any("undecodable" in w for w in _warnings(result))


LAYER_KEYS = (
    *("links", "paths", "coverage", "facts", "structure"),
    *("oracle_inputs", "oracle_inputs_run", "input_states", "state_counts"),
)


def _survives(result: dict[str, Any], out: pathlib.Path) -> list[str]:
    """Layers 1-4 are produced and written; returns the A3 warnings."""
    for key in LAYER_KEYS:
        assert key in result, key
    cli.layer4_rows(result)
    cli.write_outputs(result, out)
    for name in LAYER4_FILES:
        assert (out / name).is_file(), name
    return [w for w in _warnings(result) if "malformed" in w]


@pytest.mark.parametrize("method", ["read_bytes", None, "missing"])
def test_record_with_unknown_method_is_skipped(
    tmp_path: pathlib.Path, method: str | None
) -> None:
    """A record whose method is unknown or missing is skipped, warned.

    Rules: OI2, OI6.
    """
    bad = {**NULL_RECORD, "check_id": "b", "path": "/repo/data/b.csv"}
    bad["resolved_workspace_path"] = "data/b.csv"
    if method == "missing":
        del bad["method"]
    else:
        bad["method"] = method
    runs = _null_folder(tmp_path, _lines(NULL_RECORD, bad), None)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    assert [r["path"] for r in result["oracle_inputs"]] == ["data/a.csv"]
    assert _survives(result, tmp_path / "out")


SKIPPED_ONE = "malformed manifest records: 1 skipped, 0 glob_matches dropped"
WRONG_TYPES = (
    ("check_id", 5),
    ("phase", 5),
    ("path", 5),
    ("method", ["read_file_text"]),
    ("resolved_workspace_path", 5),
    ("glob_pattern", ["*"]),
    ("sha256", ["bb"]),
    ("sha256_before", {"x": 1}),
)


@pytest.mark.parametrize(
    ("field", "value"), WRONG_TYPES, ids=[f for f, _ in WRONG_TYPES]
)
def test_record_with_a_wrong_type_is_skipped(
    tmp_path: pathlib.Path, field: str, value: Any
) -> None:
    """A record whose name or string field has a wrong type is skipped.

    phase and check_id must be strings; path, resolved_workspace_path,
    glob_pattern, sha256 and sha256_before strings or null (D68).
    Rules: OI2, OI3.
    """
    bad = {**NULL_RECORD, "check_id": "b", "path": "/repo/data/b.csv"}
    bad["resolved_workspace_path"] = "data/b.csv"
    bad[field] = value
    runs = _null_folder(tmp_path, _lines(NULL_RECORD, bad), None)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    assert [r["path"] for r in result["oracle_inputs"]] == ["data/a.csv"]
    assert _survives(result, tmp_path / "out") == [SKIPPED_ONE]


@pytest.mark.parametrize("source", ["null_agent", "graded"])
def test_manifest_of_malformed_records_is_not_passed(
    tmp_path: pathlib.Path, source: str
) -> None:
    """A non-empty manifest whose every record is malformed is ⊥ (B2).

    It is not a vacuous manifest that says the oracle reads nothing.
    Rules: OI1, PRE1.
    """
    bad = {**NULL_RECORD, "method": "read_bytes"}
    data = _lines(bad, {**bad, "phase": None})
    run_dir = _run(tmp_path / "in")
    runs = None
    if source == "graded":
        (run_dir / "run" / "oracle_inputs.jsonl").write_bytes(data)
    else:
        runs = _null_folder(tmp_path, data)
    result = cli.analyse(run_dir, oracle_runs=runs)
    block = result["oracle_inputs_run"]
    assert block["preconditions"]["oracle_inputs"] == "fails"
    assert result["state_counts"]["preconditions"]["oracle_inputs"] == "fails"
    note = "malformed manifest records: 2 skipped, 0 glob_matches dropped"
    assert _survives(result, tmp_path / "out") == [note]


def test_record_without_phase_is_skipped(tmp_path: pathlib.Path) -> None:
    """A record with no phase is skipped with a warning.

    Rules: OI2, PHS1.
    """
    bad = {**NULL_RECORD, "check_id": "b", "path": "/repo/data/b.csv"}
    bad["resolved_workspace_path"] = "data/b.csv"
    del bad["phase"]
    runs = _null_folder(tmp_path, _lines(NULL_RECORD, bad), None)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    assert [r["path"] for r in result["oracle_inputs"]] == ["data/a.csv"]
    assert _survives(result, tmp_path / "out")


@pytest.mark.parametrize(
    "entries",
    [["data/a.csv", "data/b.csv"], [{"status": "M", "sha256": "a1"}]],
    ids=["strings", "no path"],
)
def test_malformed_final_files_entries_are_skipped(
    tmp_path: pathlib.Path, entries: list
) -> None:
    """final_files.json entries that are not objects with a path: skipped.

    Rules: OI10, EVD2.
    """
    run_dir = _run(tmp_path)
    (run_dir / "run" / "oracle_inputs.jsonl").write_text(
        json.dumps({**NULL_RECORD, "sha256": "ab"})
    )
    (run_dir / "monitor" / "final.diff").write_text("")
    (run_dir / "monitor" / "final_files.json").write_text(json.dumps(entries))
    result = cli.analyse(run_dir)
    (row,) = result["oracle_inputs"]
    assert row["path"] == "data/a.csv"
    assert _survives(result, tmp_path / "out")


def test_null_score_phases_of_strings_are_skipped(
    tmp_path: pathlib.Path,
) -> None:
    """Score phases that are not objects are skipped with a warning.

    Rules: NS1, NS3.
    """
    score = {"score": 1, "phases": ["env_setup"], "commit": "c0"}
    runs = _null_folder(tmp_path, _lines(NULL_RECORD), score)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    assert result["oracle_inputs_run"]["metrics"]["M3.5"]["null_score"] == 1
    assert _survives(result, tmp_path / "out")


SCORE_NOTE = "null_agent_score.json: malformed phases or score skipped"


@pytest.mark.parametrize("value", ["4", True], ids=["string", "boolean"])
def test_string_score_is_skipped(tmp_path: pathlib.Path, value: Any) -> None:
    """A score that is not a number is ⊥, with a warning; a bool is none.

    Rules: NS1, NS2.
    """
    score = {"score": value, "phases": [], "commit": "c0"}
    runs = _null_folder(tmp_path, _lines(NULL_RECORD), score)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    m35 = result["oracle_inputs_run"]["metrics"]["M3.5"]
    assert m35["null_score"] is None
    assert m35["score_gain"] is None
    assert SCORE_NOTE in result["oracle_inputs_run"]["warnings"]
    assert _survives(result, tmp_path / "out")


def test_score_phase_without_a_name_is_skipped(tmp_path: pathlib.Path) -> None:
    """A phases entry that is an object with no phase name is skipped.

    Rules: NS1, NS3.
    """
    phases = [{"phase": "experiment_runs", "status": "success"}]
    phases.append({"status": "success"})
    score = {"score": 1, "phases": phases, "commit": "c0"}
    runs = _null_folder(tmp_path, _lines(NULL_RECORD), score)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    m35 = result["oracle_inputs_run"]["metrics"]["M3.5"]
    assert m35["null_phases_passed"] == ["experiment_runs"]
    assert SCORE_NOTE in result["oracle_inputs_run"]["warnings"]
    assert _survives(result, tmp_path / "out")


def test_run_result_phases_not_a_list(tmp_path: pathlib.Path) -> None:
    """oracle_result.json phases written as a dict are skipped, warned.

    Rules: NS1, PHS1.
    """
    run_dir = _run(tmp_path / "in")
    (run_dir / "run" / "oracle_result.json").write_text(
        json.dumps({"score": 2, "phases": {"env_setup": "success"}})
    )
    runs = _oracle_runs(tmp_path)
    result = cli.analyse(run_dir, oracle_runs=runs)
    assert _survives(result, tmp_path / "out")


def test_glob_matches_not_a_list(tmp_path: pathlib.Path) -> None:
    """A string glob_matches gives no row per character, and a warning.

    Rules: OI4.
    """
    glob = {
        **NULL_RECORD,
        "check_id": "g",
        "method": "glob",
        "path": "/repo/data",
        "resolved_workspace_path": "data",
        "glob_pattern": "*",
        "glob_matches": "ab",
    }
    runs = _null_folder(tmp_path, _lines(NULL_RECORD, glob), None)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    paths = {r["path"] for r in result["oracle_inputs"]}
    assert "data/a.csv" in paths
    assert not paths & {"a", "b"}, paths
    assert _survives(result, tmp_path / "out")


@pytest.mark.parametrize("matches", [[1], ["/repo/data/a.csv", None]])
def test_glob_matches_not_strings(
    tmp_path: pathlib.Path, matches: list
) -> None:
    """A glob_matches with a non-string member is dropped as a whole.

    The pattern row is kept, with no match rows (D68).
    Rules: OI4.
    """
    glob = {
        **NULL_RECORD,
        "check_id": "g",
        "method": "glob",
        "path": "/repo/out",
        "resolved_workspace_path": "out",
        "glob_pattern": "*",
        "glob_matches": matches,
    }
    runs = _null_folder(tmp_path, _lines(NULL_RECORD, glob), None)
    result = cli.analyse(_run(tmp_path / "in"), oracle_runs=runs)
    rows = [(r["path"], r["pattern"]) for r in result["oracle_inputs"]]
    assert sorted(rows, key=str) == [("data/a.csv", None), ("out", "*")]
    note = "malformed manifest records: 0 skipped, 1 glob_matches dropped"
    assert _survives(result, tmp_path / "out") == [note]


def _header_set(
    root: pathlib.Path, order: tuple[str, ...], manifests: bool
) -> dict[str, bytes]:
    """Runs main on runs of the given artifacts, in that order of paths.

    Each artifact's null-agent manifest reads data/a.csv in a phase of
    its own, so its phases_complete keys differ from the other's.
    Returns the header line of oracle_inputs.csv and state_counts.csv.
    """
    runs = root / "runs"
    for index, artifact in enumerate(order):
        made = _run(runs / str(index))
        made.rename(made.parent / f"{artifact}__claude__1")
    args = [str(runs), "--out", str(root / "out")]
    if manifests:
        for artifact, phase in (("alpha", "env_setup"), ("beta", "x_phase")):
            record = {**NULL_RECORD, "phase": phase}
            _null_folder(root, _lines(record), None, artifact)
        args += ["--oracle-runs", str(root / "oracle_runs")]
    assert cli.main(args) == 0
    return {
        name: (root / "out" / name).read_bytes().splitlines()[0]
        for name in ("oracle_inputs.csv", "state_counts.csv")
    }


def test_layer4_csv_headers_are_fixed(tmp_path: pathlib.Path) -> None:
    """The layer-4 CSV headers do not depend on the runs or their order.

    Phase sub-keys are input_state.PHASES, list metrics are dropped by
    name; a set with manifests, the same set reversed and a set with none
    give byte-identical headers. A phase outside PHASES stays out of the
    CSV (it is in the JSON).
    Rules: OI8, CNT3, PHS1.
    """
    from trajectory_checks import input_state

    forward = _header_set(tmp_path / "a", ("alpha", "beta"), True)
    backward = _header_set(tmp_path / "b", ("beta", "alpha"), True)
    without = _header_set(tmp_path / "c", ("alpha", "beta"), False)
    assert forward == backward
    assert forward == without
    assert input_state.PHASES == (
        *("env_setup", "artifact_build", "benchmark_prep", "experiment_runs"),
    )
    oracle = forward["oracle_inputs.csv"].decode().split(",")
    state = forward["state_counts.csv"].decode().split(",")
    for phase in input_state.PHASES:
        assert f"M3.1.phases_complete.{phase}" in oracle, phase
        assert f"M3.2.phases.{phase}.status" in state, phase
    assert not any("x_phase" in column for column in oracle + state)
    lists = (
        *("null_phases_passed", "phases_passed_by_null", "phases_gained"),
        *("phases_lost", "final_writer_roots_under"),
    )
    for column in oracle + state:
        assert column.split(".")[-1] not in lists, column


def test_layer4_csv_absent_phase_is_empty(tmp_path: pathlib.Path) -> None:
    """A phase the run lacks gives an empty cell, never 0.

    Rules: OI8, PHS1.
    """
    _header_set(tmp_path, ("alpha", "beta"), True)
    rows = {r["run_key"]: r for r in _rows(tmp_path / "out/oracle_inputs.csv")}
    alpha = rows["alpha__claude__1"]
    assert alpha["M3.1.phases_complete.env_setup"] == "True"
    assert alpha["M3.1.phases_complete.experiment_runs"] == ""
    assert alpha["M3.1.phases_complete.artifact_build"] == ""
    states = {r["run_key"]: r for r in _rows(tmp_path / "out/state_counts.csv")}
    beta = states["beta__claude__1"]
    assert beta["M3.2.phases.env_setup.status"] == "success"
    assert beta["M3.2.phases.benchmark_prep.status"] == ""
    assert beta["M3.2.phases.benchmark_prep.inputs"] == ""
