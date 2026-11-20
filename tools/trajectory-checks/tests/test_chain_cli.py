"""Layer 6 end to end: what cli.py reads, routes and writes (§2.2.7, §3.4).

Run folders are built in ``tmp_path`` as ``test_writer_cli.py`` builds
them (the shell call ``sleep 1; echo waiting`` whose trace record writes
``data/a.csv``), with a ``cases/<artifact>/case.toml`` and a checkout
``checkouts/<artifact>/`` holding a README; nothing depends on the
corpus. ``cli``, ``declared`` and ``chain`` are imported inside each test
(D46).
"""

import copy
import csv
import json
import pathlib
from typing import Any

import chain_helpers as helpers
import conftest
import pytest

RUN_TOKEN = "05720569"
TRACE_PATH = f"/elsewhere/monitor/{RUN_TOKEN}/commands.jsonl"
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
COMMIT = "c0" * 20  # a 40-hex sha
CASE = f"""\
[run.instructions]
path = "README.md"

[upstream]
url = "https://example.org/owner/repo.git"
ref = "{COMMIT}"
"""
README = "# Run\n\n```sh\nsleep 2\n```\n"
CHAIN_FILES = (
    *("arg_checks.jsonl", "chain_nodes.jsonl", "hop_reads.jsonl"),
    *("faithfulness.jsonl", "chain.json"),
)


def _run(root: pathlib.Path, name: str = "project__claude__1") -> pathlib.Path:
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
                "commit": COMMIT,
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
            json.dumps({"score": 0, "phases": [], "commit": COMMIT})
        )
    return root / "oracle_runs"


def _case(
    root: pathlib.Path,
    artifact: str = "project",
    *,
    extra: str = "",
    checkout: bool = True,
    text: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """cases/<artifact>/case.toml and checkouts/<artifact>/ with a .git."""
    cases = root / "cases"
    (cases / artifact).mkdir(parents=True, exist_ok=True)
    (cases / artifact / "case.toml").write_text(
        text if text is not None else CASE + extra
    )
    checkouts = root / "checkouts"
    checkouts.mkdir(exist_ok=True)
    if checkout:
        repo = checkouts / artifact
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text(COMMIT + "\n")
        (repo / "README.md").write_text(README)
    return cases, checkouts


def _rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _lines(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _main(args: list[Any]) -> int:
    from trajectory_checks import chain, cli, declared  # late: D46

    assert chain.derive and declared.declare  # fail alone without them
    return cli.main([str(a) for a in args])


def test_chain_files_written(tmp_path: pathlib.Path) -> None:
    """The per-run files, chain.csv, chain_summary.csv; the cli tables.

    One sorted-key object per JSON Lines line; analyse returns the five
    layer-6 keys; BLOCKS and _OWNERS gain layer 6's rows.
    Rules: ARG3, ARG4, AGG1, DEC7.
    """
    from trajectory_checks import cli, declared, facts, input_state, match

    runs = _oracle_runs(tmp_path)
    cases, checkouts = _case(tmp_path)
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    code = _main(
        [run_dir, "--out", out, "--oracle-runs", runs, "--cases", cases]
        + ["--checkouts", checkouts]
    )
    assert code == 0
    folder = out / "project__claude__1"
    for name in CHAIN_FILES:
        assert (folder / name).is_file(), name
    for name in ("chain.csv", "chain_summary.csv"):
        assert (out / name).is_file(), name
    for name in CHAIN_FILES[:-1]:
        for record in _lines(folder / name):
            assert list(record) == sorted(record)
    block = json.loads((folder / "chain.json").read_text())
    assert set(block) == helpers.RUN_FIELDS
    assert block["preconditions"]["declared"] == "holds"
    rows = _lines(folder / "arg_checks.jsonl")
    sleep = next(r for r in rows if r["invocation_key"] == "sleep")
    assert (sleep["option"], sleep["comparison"]) == ("pos:1", "below")
    assert sleep["in_scope"]["writer"] == ["i0"]
    (verdict,) = _lines(folder / "faithfulness.jsonl")
    assert (verdict["path"], verdict["mode"]) == ("data/a.csv", "read_free")
    assert cli.CHAIN_FILES == (
        *("arg_checks", "chain_nodes", "hop_reads", "faithfulness"),
    )
    assert cli.DECLARED_FILES == ("declared_invocations", "declared_options")
    assert ("chain", "chain") in cli.BLOCKS
    assert cli._OWNERS["declared"] == (declared.DEFAULTS,)
    assert cli._OWNERS["chain"] == (
        match.DEFAULTS,
        facts.DEFAULTS,
        input_state.DEFAULTS,
    )
    result = cli.analyse(run_dir, oracle_runs=runs)
    assert {*CHAIN_FILES_KEYS, "chain"} <= set(result)
    (summary,) = [
        r for r in _rows(out / "chain_summary.csv") if r["group_type"] == "all"
    ]
    assert summary["runs"] == "1"


CHAIN_FILES_KEYS = ("arg_checks", "chain_nodes", "hop_reads", "faithfulness")


def test_declared_written_once_per_artifact(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs of one artifact: T6.1 runs once, its files are written once.

    Rules: DEC1, DEC7.
    """
    from trajectory_checks import declared

    calls = []
    original = declared.declare

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("artifact"))
        return original(*args, **kwargs)

    monkeypatch.setattr(declared, "declare", counted)
    cases, checkouts = _case(tmp_path)
    dirs = [_run(tmp_path / "in", f"project__claude__{n}") for n in (1, 2)]
    out = tmp_path / "out"
    assert (
        _main([*dirs, "--out", out, "--cases", cases, "--checkouts", checkouts])
        == 0
    )
    assert calls == ["project"]
    folder = out / "declared" / "project"
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "declared.json",
        "declared_invocations.jsonl",
        "declared_options.jsonl",
    ]
    (row,) = _lines(folder / "declared_invocations.jsonl")
    assert (row["invocation_key"], row["decl_id"]) == ("sleep", "d0")
    meta = json.loads((folder / "declared.json").read_text())
    assert (meta["commit"], meta["commit_source"]) == (COMMIT, "checkout")
    assert meta["artifact"] == "project"


def test_chain_without_cases(tmp_path: pathlib.Path) -> None:
    """No --cases: chain files written, declared fails, no marker, A = ∅.

    Rules: PRE1, PRE2.
    """
    runs = _oracle_runs(tmp_path)
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    assert _main([run_dir, "--out", out, "--oracle-runs", runs]) == 0
    folder = out / "project__claude__1"
    block = json.loads((folder / "chain.json").read_text())
    assert block["preconditions"]["declared"] == "fails"
    assert block["preconditions"]["hunks"] == "vacuous"
    assert block["inputs"]["declared"] is False
    assert all(v is None for v in block["metrics"]["M5.1"].values())
    for row in _lines(folder / "arg_checks.jsonl"):
        assert row["marker"] is None
    assert not (out / "declared").exists()


def test_case_without_checkout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--cases without the artifact's checkout: declared fails, spec given.

    The [declared] table still reaches chain.derive with its markers and
    A; declared.declare is not called.
    Rules: CT1, CT4, PRE1.
    """
    from trajectory_checks import chain, declared

    seen: list[dict] = []
    original = chain.derive

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(copy.deepcopy(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(chain, "derive", spy)
    monkeypatch.setattr(
        declared, "declare", lambda *a, **k: pytest.fail("declare called")
    )
    extra = (
        '\n[declared]\nallowed_diff_classes = ["whitespace_only"]\n'
        '\n[declared.options]\n"sleep pos:1" = "min"\n'
    )
    cases, checkouts = _case(tmp_path, extra=extra, checkout=False)
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    assert (
        _main(
            [run_dir, "--out", out, "--cases", cases, "--checkouts", checkouts]
        )
        == 0
    )
    (kwargs,) = seen
    assert kwargs["declared_result"] is None
    assert kwargs["spec"]["markers"] == {"sleep": {"pos:1": "min"}}
    assert kwargs["spec"]["allowed_diff_classes"] == ["whitespace_only"]
    block = json.loads((out / "project__claude__1" / "chain.json").read_text())
    assert block["preconditions"]["declared"] == "fails"
    assert block["preconditions"]["hunks"] != "vacuous"
    assert block["inputs"]["spec"] is True


def test_malformed_case_toml(tmp_path: pathlib.Path) -> None:
    """A case.toml that does not parse: a warning, an empty spec, exit 0.

    Rules: CT5.
    """
    cases, checkouts = _case(tmp_path, text="[run.instructions\npath = 1\n")
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    assert (
        _main(
            [run_dir, "--out", out, "--cases", cases, "--checkouts", checkouts]
        )
        == 0
    )
    block = json.loads((out / "project__claude__1" / "chain.json").read_text())
    assert "case.toml: unreadable" in block["warnings"]
    assert block["preconditions"]["declared"] == "fails"
    assert block["preconditions"]["hunks"] == "vacuous"


def _patched(monkeypatch: pytest.MonkeyPatch, by_key: dict[str, dict]) -> None:
    """cli.analyse returns, for each run key, a scenario's layer-6 result."""
    from trajectory_checks import cli

    original = cli.analyse

    def analyse(
        run_dir: Any,
        config: Any = None,
        oracle_runs: Any = None,
        declared_input: Any = None,
    ) -> dict:
        result = original(run_dir, config, oracle_runs, declared_input)
        key = pathlib.Path(run_dir).name
        if key in by_key:
            derived = helpers.run_derive(by_key[key])
            derived["chain"]["run_key"] = key
            result.update(derived)
        return result

    monkeypatch.setattr(cli, "analyse", analyse)


def _header_set(
    root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    order: tuple[str, ...],
    *,
    oracle: bool,
) -> dict[str, bytes]:
    """Main on one run per name (traced H75, read-free H30, …)."""
    sources = {
        "alpha": helpers.load_scenario(helpers.by_id("H75")),
        "beta": helpers.load_scenario(helpers.by_id("H30")),
    }
    by_key = {}
    for index, name in enumerate(order):
        key = f"{name}__claude__1"
        _run(root / "runs" / str(index), key)
        if oracle:
            by_key[key] = sources[name]
    _patched(monkeypatch, by_key)
    args = [root / "runs", "--out", root / "out"]
    assert _main(args) == 0
    return {
        name: (root / "out" / name).read_bytes().splitlines()[0]
        for name in ("chain.csv", "chain_summary.csv")
    }


def test_chain_csv_header_is_fixed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both headers are byte-identical for three run sets (layer 5's D40).

    A traced and a read-free run, the same set reversed, and a set
    without oracle inputs (the plain pipeline).
    Rules: AGG1, AGG4.
    """
    forward = _header_set(
        tmp_path / "a", monkeypatch, ("alpha", "beta"), oracle=True
    )
    backward = _header_set(
        tmp_path / "b", monkeypatch, ("beta", "alpha"), oracle=True
    )
    plain = _header_set(
        tmp_path / "c", monkeypatch, ("alpha", "beta"), oracle=False
    )
    assert forward == backward == plain


def test_chain_csv_cells(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dict metrics by fixed sub-keys; a phase the run lacks is empty.

    Every bases and preconditions key is a column; every M… column has
    a peer-ratio column, the faithfulness columns none.
    Rules: AGG1, AGG2, FA9.
    """
    _header_set(tmp_path, monkeypatch, ("alpha", "beta"), oracle=True)
    rows = {r["run_key"]: r for r in _rows(tmp_path / "out" / "chain.csv")}
    row = rows["alpha__claude__1"]
    columns = list(row)
    assert "faithfulness.clause_violations.fresh" in columns
    assert "faithfulness.faithful_by_phase.experiment_runs" in columns
    assert row["faithfulness.faithful_by_phase.experiment_runs"] == ""
    assert row["faithfulness.faithful_by_phase.artifact_build"] == ""
    assert row["faithfulness.clause_violations.fresh"] == "0"
    assert row["M3.4.hop2_reads_by_state_over.derived"] == "1"
    for key in helpers.BASES_KEYS:
        assert f"bases.{key}" in columns, key
    for label in helpers.PRECONDITION_LABELS:
        assert f"preconditions.{label}" in columns, label
    assert row["bases.chain"] == "exact"
    assert rows["beta__claude__1"]["bases.chain"] == "fallback"
    assert rows["beta__claude__1"]["M3.4.chain_nodes_under"] == ""
    for column in columns:
        if column.startswith("M") and not column.endswith("_peer_ratio"):
            assert f"{column}_peer_ratio" in columns, column
    assert not any(
        c.startswith("faithfulness.") and c.endswith("_peer_ratio")
        for c in columns
    )


def test_chain_peer_ratios(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H44's runs through cli.main: peer ratios and the summary row.

    Ratios by facts.peer_ratios over the same artifact's other runs with
    a value; the ⊥ run has none; chain_summary.csv counts three defined
    values of four runs.
    Rules: AGG2, AGG4.
    """
    path = helpers.by_id("H44")
    scenarios = {s["run"]["run_key"]: s for s in helpers.load_runs(path)}
    for key in scenarios:
        _run(tmp_path / "in", key)
    _patched(monkeypatch, scenarios)
    out = tmp_path / "out"
    assert _main([tmp_path / "in", "--out", out]) == 0
    rows = {r["run_key"]: r for r in _rows(out / "chain.csv")}
    column = "M1.5.chain_time_over_s_peer_ratio"
    assert rows["art__claude__1"][column] == "0.018182"
    assert rows["art__claude__2"][column] == "0.19802"
    assert rows["art__claude__3"][column] == "18.181818"
    assert rows["art__claude__4"][column] == ""
    summary = {
        (r["group_type"], r["group"]): r
        for r in _rows(out / "chain_summary.csv")
    }
    row = summary[("artifact_family", "art__claude")]
    assert row["runs"] == "4"
    assert row["M1.5.chain_time_over_s_n"] == "3"
    assert row["M1.5.chain_time_over_s_median"] == "100.0"
    assert row["M1.5.chain_time_over_s_p90"] == "1000.0"


def test_chain_config_routing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys reach their owners only; unknown keys fail early (D45).

    link_follow_depth reaches declared.declare only; snapshot_slop_s
    reaches layer 1 and chain.derive; an unknown key exits 2 once, before
    any run; chain.derive raises ValueError for any other key.
    Rules: DEC1, PRE1.
    """
    from trajectory_checks import chain, cli, declared

    seen: dict[str, list] = {"declare": [], "derive": []}
    original_declare, original_derive = declared.declare, chain.derive

    def spy_declare(*args: Any, **kwargs: Any) -> Any:
        seen["declare"].append(dict(kwargs.get("config") or {}))
        return original_declare(*args, **kwargs)

    def spy_derive(*args: Any, **kwargs: Any) -> Any:
        seen["derive"].append(dict(kwargs.get("config") or {}))
        return original_derive(*args, **kwargs)

    monkeypatch.setattr(declared, "declare", spy_declare)
    monkeypatch.setattr(chain, "derive", spy_derive)
    cases, checkouts = _case(tmp_path)
    run_dir = _run(tmp_path / "in")
    config = tmp_path / "config.toml"
    config.write_text("link_follow_depth = 0\nsnapshot_slop_s = 0.5\n")
    out = tmp_path / "out"
    args = [run_dir, "--out", out, "--cases", cases, "--checkouts", checkouts]
    assert _main([*args, "--config", config]) == 0
    assert seen["declare"] == [{"link_follow_depth": 0}]
    assert seen["derive"] == [{"snapshot_slop_s": 0.5}]
    routed = cli.route_config({"link_follow_depth": 0, "snapshot_slop_s": 0.5})
    assert routed["declared"] == {"link_follow_depth": 0}
    assert routed["chain"] == {"snapshot_slop_s": 0.5}
    assert routed["match"] == {"snapshot_slop_s": 0.5}
    assert cli.route_config({"min_peers": 3})["chain"] == {"min_peers": 3}
    assert cli.route_config({"passing_statuses": ["ok"]})["chain"] == {
        "passing_statuses": ["ok"]
    }
    bad = tmp_path / "bad.toml"
    bad.write_text("no_such_key = 1\n")
    with pytest.raises(SystemExit) as raised:
        _main([run_dir, "--out", tmp_path / "out_bad", "--config", bad])
    assert raised.value.code == 2
    assert not (tmp_path / "out_bad").exists()
    scenario = helpers.load_scenario(helpers.scenario_paths()[0])
    for key in ("link_follow_depth", "no_such_key", "recursion_depth"):
        with pytest.raises(ValueError):
            helpers.run_derive(scenario, config={key: 1})
    for key, value in (("join_rate_warn", 0.5), ("min_peers", 3)):
        helpers.run_derive(scenario, config={key: value})


def test_chain_failure_is_isolated(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose chain.derive raises fails alone, exit 1 (layer 4's D56).

    Rules: PRE1.
    """
    from trajectory_checks import chain

    runs = _oracle_runs(tmp_path)
    dirs = [
        _run(tmp_path / name, f"project__{name}__1") for name in ("bad", "good")
    ]
    original = chain.derive

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if kwargs["run"]["run_key"].endswith("bad__1"):
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(chain, "derive", flaky)
    out = tmp_path / "out"
    assert _main([*dirs, "--out", out, "--oracle-runs", runs]) == 1
    assert not (out / "project__bad__1").exists()
    assert (out / "project__good__1" / "chain.json").is_file()
    assert [r["run_key"] for r in _rows(out / "chain.csv")] == [
        "project__good__1"
    ]


# Critic cycle 2.
def test_declare_failure_is_cached_per_artifact(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """declared.declare raising: called once, every run still written.

    Each run gets the declared precondition failure and one warning that
    names the failure; the spec still reaches chain.derive; the error is
    logged once (D84).
    Rules: PRE1, PRE2.
    """
    from trajectory_checks import declared

    calls = []

    def broken(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("artifact"))
        raise RuntimeError("boom")

    monkeypatch.setattr(declared, "declare", broken)
    extra = '\n[declared]\nallowed_diff_classes = ["whitespace_only"]\n'
    cases, checkouts = _case(tmp_path, extra=extra)
    dirs = [_run(tmp_path / "in", f"project__claude__{n}") for n in (1, 2)]
    out = tmp_path / "out"
    args = [*dirs, "--out", out, "--cases", cases, "--checkouts", checkouts]
    assert _main(args) == 0
    assert calls == ["project"]
    assert not (out / "declared").exists()
    for n in (1, 2):
        block = json.loads(
            (out / f"project__claude__{n}" / "chain.json").read_text()
        )
        assert block["preconditions"]["declared"] == "fails"
        assert block["preconditions"]["hunks"] != "vacuous"
        found = [w for w in block["warnings"] if "T6.1 failed" in w]
        assert found == ["declared: T6.1 failed (RuntimeError: boom)"]
    logged = [r for r in caplog.records if "T6.1" in r.getMessage()]
    assert len(logged) == 1


def test_case_toml_not_utf8_is_unreadable(tmp_path: pathlib.Path) -> None:
    """A case.toml that is not UTF-8 is unreadable (D69); the run is written.

    Rules: CT5.
    """
    cases, checkouts = _case(tmp_path, text="")
    (cases / "project" / "case.toml").write_bytes(b'a = "\xff\xfe"\n')
    out = tmp_path / "out"
    run_dir = _run(tmp_path / "in")
    args = [run_dir, "--out", out, "--cases", cases, "--checkouts", checkouts]
    assert _main(args) == 0
    block = json.loads((out / "project__claude__1" / "chain.json").read_text())
    assert "case.toml: unreadable" in block["warnings"]


@pytest.mark.parametrize(
    ("line", "want"),
    [
        (f'upstream_ref=ref = "{COMMIT}"', COMMIT),
        (f"upstream_ref={COMMIT}", COMMIT),
        (f"upstream_ref= {COMMIT} ", COMMIT),
        ('upstream_ref=ref = "main"', "main"),
        ("upstream_ref=ref = 'x'", "ref = 'x'"),
        ('upstream_ref=ref = "a" b', 'ref = "a" b'),
        ('upstream_ref=ref="x"', 'ref="x"'),
        ("upstream_ref=", None),
        ('upstream_ref=ref = ""', None),
    ],
)
def test_upstream_ref_strips_the_toml_wrapper(
    tmp_path: pathlib.Path, line: str, want: str | None
) -> None:
    """run_meta.txt's upstream_ref=ref = "<v>" gives <v> (D85).

    Only that exact form is unwrapped; any other value is unchanged.
    Rules: DEC7.
    """
    from trajectory_checks import cli

    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "run_meta.txt").write_text(f"x=1\n{line}\n")
    assert cli._upstream_ref(tmp_path) == want
