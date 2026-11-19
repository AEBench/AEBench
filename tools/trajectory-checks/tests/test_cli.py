"""End to end: ``cli.analyse``, ``cli.write_outputs`` and ``cli.main``.

The run folder is assembled in a temporary directory from two fixtures of
the same real run: one Claude shell call and its trace record.
"""

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
    for name in OUTPUT_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    for name in OUTPUT_FILES[:-1]:
        for line in (first / name).read_text().splitlines():
            record = json.loads(line)
            assert list(record) == sorted(record)


def test_main_exit_status(tmp_path: pathlib.Path) -> None:
    """Exit status 0 when every run was processed (§7.4)."""
    run_dir = _make_run(tmp_path / "in")
    out = tmp_path / "out"
    assert cli.main([str(run_dir), "--out", str(out)]) == 0
    assert any(out.rglob("coverage.json"))


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
