"""Regression tests for bugs found while running the implementation.

Kept apart from the test-first suite in tests/ (written by another author).
"""

import json
import pathlib

import conftest

from trajectory_checks import cli, match


def _link(calls, execs, run=None, **kwargs):
    run_record = dict(conftest.DEFAULT_RUN, **(run or {}))
    calls = [conftest.make_call(c, i) for i, c in enumerate(calls)]
    execs = [conftest.make_exec(e, i, "/repo") for i, e in enumerate(execs)]
    conftest.assign_exec_seq(execs)
    return match.link(calls, execs, run=run_record, **kwargs)


def test_wrapper_duplicate_is_never_a_parent() -> None:
    """A shell inside a Codex inner duplicate is nested under the outer."""
    result = _link(
        [
            {
                "call_id": "c1",
                "family": "codex",
                "t_start": 100,
                "t_end": 110,
                "command": "make",
                "background": None,
            }
        ],
        [
            {
                "command_id": "outer",
                "t_start": 100.2,
                "t_end": 109,
                "command": "make",
                "wrapper": "codex_snapshot_exec",
            },
            {
                "command_id": "inner",
                "t_start": 100.3,
                "t_end": 108.9,
                "command": "make",
            },
            {
                "command_id": "kid",
                "t_start": 101,
                "t_end": 102,
                "command": "cc -c a.c",
            },
        ],
    )
    (parent,) = [k for k in result["links"] if k["link_type"] == "exec_parent"]
    assert parent["command_id"] == "kid"
    assert parent["parent"] == "outer"
    assert parent["root_call_id"] == "c1"


def test_noise_does_not_set_scope_or_trace_presence() -> None:
    """Only non-noise records decide snapshot_scope and trace_missing."""
    probe = {
        "command_id": "p",
        "t_start": 1,
        "command": "x",
        "noise": "shim_probe",
    }
    result = _link(
        [{"call_id": "c1", "t_start": 100, "command": "ls"}],
        [probe],
        run={"trace_ok": False},
    )
    row = next(r for r in result["dispositions"] if r["id"] == "c1")
    assert row["reason"] == "trace_missing"
    assert result["coverage"]["rates"]["snapshot_scope"] is None


def test_paths_order_workspace_first() -> None:
    """paths.jsonl lists workspace paths before absolute ones (F11)."""
    result = _link(
        [
            {
                "call_id": "w1",
                "tool_type": "write",
                "t_start": 100,
                "paths": ["/tmp/a.py"],
            },
            {
                "call_id": "w2",
                "tool_type": "write",
                "t_start": 101,
                "paths": ["/repo/z.py"],
            },
        ],
        [],
    )
    assert [p["path"] for p in result["paths"]] == ["z.py", "/tmp/a.py"]


def test_local_trace_copy_wins_over_recorded_path(
    tmp_path: pathlib.Path,
) -> None:
    """The run folder's monitor/ copy is read, not the recorded path.

    The recorded trace_path may exist on the analysing machine and hold a
    different run's records; C0.1 must be judged on the copy in the run.
    """
    elsewhere = tmp_path / "cache" / "05720569"
    elsewhere.mkdir(parents=True)
    (elsewhere / "commands.jsonl").write_text("")
    run_dir = tmp_path / "proj" / "claude" / "1"
    (run_dir / "run").mkdir(parents=True)
    (run_dir / "monitor").mkdir()
    (run_dir / "run" / "runner_output.log").write_bytes(
        (conftest.PARSE_FIXTURES_DIR / "claude" / "shell_ok.log").read_bytes()
    )
    foreign = conftest.TRACE_FIXTURES_DIR / "claude_eval_skipped.jsonl"
    lines = foreign.read_text().replace('"05720569"', '"deadbeef"')
    (run_dir / "monitor" / "commands.jsonl").write_text(lines)
    (run_dir / "run" / "result.jsonl").write_text(
        json.dumps(
            {
                "started_at": "2026-10-12T16:40:00Z",
                "finished_at": "2026-10-12T17:00:00Z",
                "agent_kind": "claude_non_api",
                "runtime": {"workspace_mount": "/repo"},
                "command_monitor": {
                    "trace_path": str(elsewhere / "commands.jsonl")
                },
            }
        )
        + "\n"
    )
    result = cli.analyse(run_dir)
    assert result["coverage"]["integrity"]["C0.1"] is False
    assert result["coverage"]["run_key"] == "proj__claude__1"


def test_execs_keep_measured_end_for_incomplete_records(
    tmp_path: pathlib.Path,
) -> None:
    """execs.jsonl t_end is t_start + duration even when incomplete (F9)."""
    run_dir = tmp_path / "p" / "claude" / "1"
    (run_dir / "run").mkdir(parents=True)
    (run_dir / "monitor").mkdir()
    (run_dir / "run" / "runner_output.log").write_bytes(
        (conftest.PARSE_FIXTURES_DIR / "claude" / "shell_ok.log").read_bytes()
    )
    trace = conftest.TRACE_FIXTURES_DIR / "nested_script_incomplete.jsonl"
    (run_dir / "monitor" / "commands.jsonl").write_bytes(trace.read_bytes())
    (run_dir / "run" / "result.jsonl").write_text(
        json.dumps(
            {
                "started_at": "2026-10-21T22:00:00Z",
                "finished_at": "2026-10-21T23:00:00Z",
                "agent_kind": "claude_non_api",
                "runtime": {"workspace_mount": "/repo"},
            }
        )
        + "\n"
    )
    (record,) = cli.analyse(run_dir)["execs"]
    assert conftest.close(
        record["t_end"], record["t_start"] + record["duration_s"]
    )
