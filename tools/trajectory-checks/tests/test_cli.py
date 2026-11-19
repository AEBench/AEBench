"""End to end: ``cli.analyse``, ``cli.write_outputs`` and ``cli.main``.

The run folder is assembled in a temporary directory from two fixtures of
the same real run: one Claude shell call and its trace record.
"""

import csv
import json
import pathlib
from typing import Any

import conftest
import pytest

from trajectory_checks import cli

RUN_TOKEN = "05720569"  # run_id of the trace fixture record
OUTPUT_FILES = (
    "calls.jsonl",
    "execs.jsonl",
    "links.jsonl",
    "dispositions.jsonl",
    "paths.jsonl",
    "call_facts.jsonl",
    "exec_facts.jsonl",
    "path_facts.jsonl",
    "coverage.json",
)


def _result_line(token: str) -> dict[str, Any]:
    return {
        "started_at": "2026-10-12T16:40:00Z",
        "finished_at": "2026-10-12T17:00:00Z",
        "agent_kind": "claude_non_api",
        "runtime": {"workspace_mount": "/repo"},
        "command_monitor": {
            "run_token": token,
            "trace_path": f"/elsewhere/monitor/{token}/commands.jsonl",
        },
    }


def _make_run(root: pathlib.Path, token: str = RUN_TOKEN) -> pathlib.Path:
    run_dir = root / "project__claude__1"
    (run_dir / "run").mkdir(parents=True)
    (run_dir / "monitor").mkdir()
    log = conftest.PARSE_FIXTURES_DIR / "claude" / "shell_ok.log"
    trace = conftest.TRACE_FIXTURES_DIR / "claude_eval_skipped.jsonl"
    (run_dir / "run" / "runner_output.log").write_bytes(log.read_bytes())
    (run_dir / "run" / "result.jsonl").write_text(
        json.dumps(_result_line(token)) + "\n"
    )
    (run_dir / "monitor" / "commands.jsonl").write_bytes(trace.read_bytes())
    return run_dir


def test_analyse_links_the_real_pair(tmp_path: pathlib.Path) -> None:
    """The call and its record are joined by rule exact."""
    result = cli.analyse(_make_run(tmp_path))
    assert set(result) == {
        "calls",
        "execs",
        "links",
        "dispositions",
        "paths",
        "coverage",
        "call_facts",
        "exec_facts",
        "path_facts",
        "facts",
        "call_structure",
        "reruns",
        "file_classes",
        "structure",
        "oracle_inputs",
        "oracle_inputs_run",
        "input_states",
        "state_counts",
        "last_writers",
        "input_rewrites",
        "hunks",
        "writer",
    }
    links = [lk for lk in result["links"] if lk["link_type"] == "call_exec"]
    assert len(links) == 1
    problems = conftest.partial_mismatches(
        links[0],
        {
            "call_id": "toolu_01R38ma8PSmiMshCwE8EDb4Q",
            "command_id": "8bec62e8b8a949acbe8f00dc7cc98903",
            "rule": "exact",
            "dt_s": 0.178064,
        },
    )
    assert problems == []
    assert result["coverage"]["integrity"]["C0.1"] is True
    assert result["coverage"]["inputs"]["log"] is True
    assert set(result["coverage"]["integrity"]) == {"C0.1", "C0.2", "C0.5"}


def test_analyse_detects_foreign_trace(tmp_path: pathlib.Path) -> None:
    """A trace whose run_id differs from the run token fails C0.1 (F1)."""
    result = cli.analyse(_make_run(tmp_path, token="deadbeef"))
    assert result["coverage"]["integrity"]["C0.1"] is False
    assert not [lk for lk in result["links"] if lk["link_type"] == "call_exec"]


def test_write_outputs_is_byte_identical(tmp_path: pathlib.Path) -> None:
    """Writing the same result twice gives byte-identical files (§1)."""
    result = cli.analyse(_make_run(tmp_path / "in"))
    first, second = tmp_path / "out1", tmp_path / "out2"
    cli.write_outputs(result, first)
    cli.write_outputs(cli.analyse(_make_run(tmp_path / "in2")), second)
    # Spec §5: files go to <out>/<run_key>/.
    (first,) = {p.parent for p in first.rglob("coverage.json")}
    (second,) = {p.parent for p in second.rglob("coverage.json")}
    for name in (*OUTPUT_FILES, "facts.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    for name in OUTPUT_FILES[:-1]:
        for line in (first / name).read_text().splitlines():
            record = json.loads(line)
            assert list(record) == sorted(record)


def test_main_facts_csv_is_byte_identical(tmp_path: pathlib.Path) -> None:
    """Writing facts.csv twice from the same run gives the same bytes (§1)."""
    written = []
    for name in ("one", "two"):
        out = tmp_path / f"out_{name}"
        assert (
            cli.main([str(_make_run(tmp_path / name)), "--out", str(out)]) == 0
        )
        written.append((out / "facts.csv").read_bytes())
    assert written[0] == written[1]
    assert written[0].count(b"\n") == 2  # the header and one run


def test_main_exit_status(tmp_path: pathlib.Path) -> None:
    """Exit status 0 when every run was processed (§7.4)."""
    run_dir = _make_run(tmp_path / "in")
    out = tmp_path / "out"
    assert cli.main([str(run_dir), "--out", str(out)]) == 0
    assert any(out.rglob("coverage.json"))
    assert any(out.rglob("facts.json"))
    assert (out / "facts.csv").is_file()


def test_main_reports_unparseable_run(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit status 1 and the run on stderr when a run cannot be parsed."""
    run_dir = tmp_path / "broken__claude__1"
    (run_dir / "run").mkdir(parents=True)
    (run_dir / "run" / "result.jsonl").write_text("{}\n")
    (run_dir / "run" / "runner_output.log").write_text("")
    assert cli.main([str(run_dir), "--out", str(tmp_path / "out")]) == 1
    assert "broken__claude__1" in capsys.readouterr().err


# ---- RunInfo and capabilities (spec §5.6, decision D26). -----------------

NEW_TOKEN = "5a1c0de0"  # run_id of the new-format trace fixture


def _make_new_run(root: pathlib.Path, *, extras: bool) -> pathlib.Path:
    """A run folder whose trace is the new-format fixture.

    Args:
        root: Where to create it.
        extras: Whether to add every optional to-change.md file.

    Returns:
        The run folder.
    """
    run_dir = _make_run(root, NEW_TOKEN)
    monitor = run_dir / "monitor"
    trace = conftest.TRACE_FIXTURES_DIR / "claude_new_format.jsonl"
    (monitor / "commands.jsonl").write_bytes(trace.read_bytes())
    result = _result_line(NEW_TOKEN)
    (monitor / "tracked_files.json").write_text(
        json.dumps({"source": "git", "commit": "c0ffee", "files": {}})
    )
    if extras:
        events = trace.parent / "file_events" / trace.name
        (monitor / "file_events.jsonl").write_bytes(events.read_bytes())
        (monitor / "trace_meta.json").write_text(
            json.dumps(
                {
                    "run_token": NEW_TOKEN,
                    "schema_version": 2,
                    "broker_version": "0.2.0",
                }
            )
        )
        (monitor / "final.diff").write_text("")
        (monitor / "final_workspace.tar.zst").write_bytes(b"")
        (monitor / "egress.jsonl").write_text("")
        (run_dir / "run" / "oracle_inputs.jsonl").write_text("")
        result["command_monitor"]["shim_intact"] = True
        result["runtime"]["image_digest"] = "sha256:" + "0" * 64
    (run_dir / "run" / "result.jsonl").write_text(json.dumps(result) + "\n")
    return run_dir


def _captured_run(
    run_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    from trajectory_checks import match

    seen: dict[str, Any] = {}
    original = match.link

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs["run"])
        return original(*args, **kwargs)

    monkeypatch.setattr(match, "link", spy)
    cli.analyse(run_dir)
    return seen


def test_old_run_has_every_capability_false(tmp_path: pathlib.Path) -> None:
    """On today's traces all nine capabilities are false (§5.6)."""
    coverage = cli.analyse(_make_run(tmp_path))["coverage"]
    assert coverage["capabilities"] == dict.fromkeys(
        conftest.CAPABILITIES, False
    )
    assert set(coverage["preconditions"]) == conftest.PRECONDITION_LABELS


def test_run_info_of_an_old_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every RunInfo field is present; the new ones are None."""
    run = _captured_run(_make_run(tmp_path), monkeypatch)
    assert set(run) == conftest.RUN_INFO_FIELDS
    for field in (
        *("monitor_version", "schema_version", "trace_meta", "commit"),
        *("final_diff_content", "final_workspace", "oracle_reads"),
        *("case_image_digest", "shim_intact", "unattributed_reads"),
    ):
        assert run[field] is None, field
    assert run["trace_ok"] is True
    assert run["run_key"] == "project__claude__1"


def test_new_format_trace_alone(tmp_path: pathlib.Path) -> None:
    """Capabilities read from the records only: no extra files."""
    result = cli.analyse(_make_new_run(tmp_path, extras=False))
    assert result["coverage"]["capabilities"] == {
        **dict.fromkeys(conftest.CAPABILITIES, False),
        "parent_links": True,
        "start_events": True,
        "exact_timing": True,
        "content_hashes": True,
        "untracked_visible": True,
    }
    assert [e["reads"] for e in result["execs"]] == [None] * 4
    assert result["execs"][0]["orphans"] == [{"pid": 140, "comm": "python3"}]


def test_new_format_run_with_every_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each optional file of a to-change.md item sets its capability."""
    run_dir = _make_new_run(tmp_path, extras=True)
    run = _captured_run(run_dir, monkeypatch)
    assert run["capabilities"] == dict.fromkeys(conftest.CAPABILITIES, True)
    assert run["schema_version"] == 2
    assert run["monitor_version"] == "0.2.0"
    assert run["trace_meta"]["run_token"] == NEW_TOKEN
    assert run["commit"] == "c0ffee"
    assert run["shim_intact"] is True
    assert run["case_image_digest"] == "sha256:" + "0" * 64
    assert run["final_diff_content"].endswith("monitor/final.diff")
    assert run["final_workspace"].endswith("final_workspace.tar.zst")
    assert run["oracle_reads"].endswith("run/oracle_inputs.jsonl")
    assert [e["path"] for e in run["unattributed_reads"]] == ["/etc/passwd"]
    result = cli.analyse(run_dir)
    assert result["execs"][1]["reads"] == [
        "/tmp/ws/project/include/a.h",
        "/tmp/ws/project/src/a.c",
    ]
    assert not [w for w in result["coverage"]["warnings"] if "schema" in w]


def test_unknown_schema_version_warns(tmp_path: pathlib.Path) -> None:
    """An unknown schema_version is a warning, never an error (D26)."""
    run_dir = _make_new_run(tmp_path, extras=False)
    trace = run_dir / "monitor" / "commands.jsonl"
    lines = [
        json.dumps({**json.loads(line), "schema_version": 9})
        for line in trace.read_text().splitlines()
    ]
    trace.write_text("\n".join(lines) + "\n")
    result = cli.analyse(run_dir)
    assert [w for w in result["coverage"]["warnings"] if "schema" in w] == [
        "schema_version 9 unknown: known fields read, others ignored"
    ]
    assert result["coverage"]["capabilities"]["parent_links"] is True


def test_csv_row_has_capabilities_and_preconditions(
    tmp_path: pathlib.Path,
) -> None:
    """coverage.csv flattens the two new blocks."""
    row = cli.csv_row(cli.analyse(_make_run(tmp_path))["coverage"])
    assert row["capabilities.parent_links"] is False
    assert row["preconditions.C1_under"] in ("holds", "vacuous", "fails")


def test_main_min_peers_override(tmp_path: pathlib.Path) -> None:
    """--config min_peers reaches facts.csv's peer ratios (§6.12)."""
    first = _make_run(tmp_path / "in")
    second = first.parent / "project__claude__2"
    second.mkdir()
    for name in ("run/runner_output.log", "run/result.jsonl"):
        (second / name).parent.mkdir(exist_ok=True)
        (second / name).write_bytes((first / name).read_bytes())
    (second / "monitor").mkdir()
    trace = first / "monitor" / "commands.jsonl"
    (second / "monitor" / "commands.jsonl").write_bytes(trace.read_bytes())
    config = tmp_path / "config.toml"
    config.write_text("min_peers = 1\n")
    out = tmp_path / "out"
    args = [str(tmp_path / "in"), "--out", str(out), "--config", str(config)]
    assert cli.main(args) == 0
    with (out / "facts.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert [r["M1.2.shell_calls_peer_ratio"] for r in rows] == ["1.0", "1.0"]


def test_config_keys_routed_by_owner(tmp_path: pathlib.Path) -> None:
    """Each --config key reaches the layer that owns it (spec §3.8 L2).

    A layer-3 key and a layer-4 key no longer fail match.link; a key no
    layer knows is rejected once, before any run.
    """
    run_dir = _make_run(tmp_path / "in")
    result = cli.analyse(
        run_dir,
        {
            "recursion_depth": 2,
            "passing_statuses": ["success"],
            "reference_prefixes": ["/refs"],
            "min_peers": 1,
            "join_rate_warn": 0.8,
        },
    )
    assert result["coverage"]["preconditions"]["C7_under"] == "fails"
    routed = cli.route_config({"recursion_depth": 2, "join_rate_warn": 0.8})
    assert routed["match"] == {"join_rate_warn": 0.8}
    assert routed["structure"] == {"recursion_depth": 2, "join_rate_warn": 0.8}
    assert routed["facts"] == {"join_rate_warn": 0.8}
    assert routed["oracle_inputs"] == routed["input_state"] == {}
    with pytest.raises(ValueError, match="no_such_key"):
        cli.analyse(run_dir, {"no_such_key": 1})
    config = tmp_path / "config.toml"
    config.write_text("recursion_depth = 2\n")
    out = tmp_path / "out"
    args = [str(run_dir), "--out", str(out), "--config", str(config)]
    assert cli.main(args) == 0
    config.write_text("no_such_key = 1\n")
    with pytest.raises(SystemExit) as stopped:
        cli.main([str(run_dir), "--out", str(tmp_path / "bad"), *args[3:]])
    assert stopped.value.code == 2
    assert not (tmp_path / "bad").exists()


def test_join_rate_warn_decides_and_reports(tmp_path: pathlib.Path) -> None:
    """T2: layer 1's join_rate_warn is the bound facts.json reports."""
    result = cli.analyse(_make_run(tmp_path), {"join_rate_warn": 0.8})
    assert result["coverage"]["preconditions"]["C7_under"] == "fails"
    warnings = result["facts"]["warnings"]
    assert any(
        w.startswith("precondition C7_under >= 0.8 failed") for w in warnings
    )
    assert not any(">= 0.9" in w for w in warnings)
