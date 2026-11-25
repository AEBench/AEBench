"""Kind 1: agent-log parsers on real excerpts (spec §5.1, §6.1).

Each ``fixtures/parse/<agent>/<name>.log`` has a hand-written
``<name>.calls.json`` (see ``fixtures/parse/EDITS.md``).
"""

import json
import math
import pathlib
from typing import Any

import conftest
import pytest

from trajectory_checks import parse

LOG_FIXTURES = sorted(conftest.PARSE_FIXTURES_DIR.glob("*/*.log"))


def _fixture_id(path: pathlib.Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def _expected(log_path: pathlib.Path) -> list[dict[str, Any]]:
    return conftest.load_json(log_path.with_suffix(".calls.json"))


def _time_close(actual: Any, expected: Any, precision: float) -> bool:
    if expected is None or actual is None:
        return actual is expected
    return math.fabs(actual - expected) < precision


@pytest.mark.parametrize("log_path", LOG_FIXTURES, ids=_fixture_id)
def test_load_calls_matches_hand_written_records(
    log_path: pathlib.Path,
) -> None:
    """Every listed field of every expected call is reproduced."""
    family = log_path.parent.name
    actual = parse.load_calls(log_path, family)
    expected = _expected(log_path)
    assert [c["call_id"] for c in actual] == [c["call_id"] for c in expected]
    for got, want in zip(actual, expected, strict=True):
        precision = want["t_precision_s"]
        for key in ("t_start", "t_end"):
            if key in want:
                assert _time_close(got[key], want[key], precision), (
                    want["call_id"],
                    key,
                    got[key],
                )
        rest = {k: v for k, v in want.items() if k not in ("t_start", "t_end")}
        problems = conftest.partial_mismatches(got, rest)
        assert problems == [], (want["call_id"], problems)


@pytest.mark.parametrize("log_path", LOG_FIXTURES, ids=_fixture_id)
def test_load_calls_record_schema(log_path: pathlib.Path) -> None:
    """Records carry exactly the §5.1 fields and are ordered by ``seq``."""
    family = log_path.parent.name
    actual = parse.load_calls(log_path, family)
    for index, call in enumerate(actual):
        keys = set(call)
        assert conftest.CALL_FIELDS <= keys, conftest.CALL_FIELDS - keys
        extra = keys - conftest.CALL_FIELDS - conftest.OPTIONAL_CALL_FIELDS
        assert not extra, extra
        assert call["seq"] == index
        assert call["family"] == family
        assert call[conftest.TOOL_TYPE_FIELD] in conftest.TOOL_TYPES
        assert call["actor"] in ("main", "subagent", "side")
        assert isinstance(call["paths"], list)
        if call[conftest.TOOL_TYPE_FIELD] != "shell":
            assert call["command"] is None
            assert call["command_norm"] is None
        else:
            assert call["command_norm"] == parse.normalize(call["command"])


@pytest.mark.parametrize("log_path", LOG_FIXTURES, ids=_fixture_id)
def test_load_calls_is_deterministic(log_path: pathlib.Path) -> None:
    """Two parses of the same log give identical records."""
    family = log_path.parent.name
    first = parse.load_calls(log_path, family)
    second = parse.load_calls(log_path, family)
    assert json.dumps(first, sort_keys=True) == json.dumps(
        second, sort_keys=True
    )


@pytest.mark.parametrize("log_path", LOG_FIXTURES, ids=_fixture_id)
def test_meta_file_present(log_path: pathlib.Path) -> None:
    """Every excerpt has its metadata file with ``agent_version``."""
    meta = conftest.load_json(log_path.with_suffix(".meta.json"))
    assert "agent_version" in meta


def _write_run(
    tmp_path: pathlib.Path, log_source: pathlib.Path, agent_kind: str | None
) -> pathlib.Path:
    run_dir = tmp_path / "run_folder"
    (run_dir / "run").mkdir(parents=True)
    (run_dir / "run" / "runner_output.log").write_bytes(log_source.read_bytes())
    result: dict[str, Any] = {"started_at": "2026-10-01T00:00:00Z"}
    if agent_kind is not None:
        result["agent_kind"] = agent_kind
    (run_dir / "run" / "result.jsonl").write_text(json.dumps(result) + "\n")
    return run_dir


@pytest.mark.parametrize(
    ("agent_kind", "family"),
    [
        ("claude_non_api", "claude"),
        ("codex_non_api", "codex"),
        ("deepseek", "deepseek"),
        ("zcode", "zcode"),
    ],
)
def test_detect_family_from_agent_kind(
    tmp_path: pathlib.Path, agent_kind: str, family: str
) -> None:
    """The ``result.jsonl`` agent kind decides the family."""
    log = conftest.PARSE_FIXTURES_DIR / "claude" / "shell_ok.log"
    assert parse.detect_family(_write_run(tmp_path, log, agent_kind)) == family


@pytest.mark.parametrize(
    ("log_name", "family"),
    [
        ("claude/shell_ok.log", "claude"),
        ("codex/no_commands.log", "codex"),
        ("deepseek/shell_ok.log", "deepseek"),
        ("zcode/shell_two_calls.log", "zcode"),
    ],
)
def test_detect_family_from_log_events(
    tmp_path: pathlib.Path, log_name: str, family: str
) -> None:
    """Without an agent kind, the first log events decide the family."""
    log = conftest.PARSE_FIXTURES_DIR / log_name
    assert parse.detect_family(_write_run(tmp_path, log, None)) == family


def test_detect_family_undetected_is_an_error(tmp_path: pathlib.Path) -> None:
    """An empty log with no agent kind is reported, not guessed (§6.1)."""
    log = conftest.PARSE_FIXTURES_DIR / "claude" / "empty.log"
    with pytest.raises(ValueError):
        parse.detect_family(_write_run(tmp_path, log, None))
