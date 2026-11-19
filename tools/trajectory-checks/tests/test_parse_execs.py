"""Kind 1: trace parser on real records (spec §5.2, §6.2, §6.3).

Each ``fixtures/trace/<name>.jsonl`` has a hand-written ``<name>.execs.json``
(see ``fixtures/trace/EDITS.md``).
"""

import json
import pathlib

import conftest
import pytest

from trajectory_checks import parse

TRACE_FIXTURES = sorted(conftest.TRACE_FIXTURES_DIR.glob("*.jsonl"))

# Agent family of each trace fixture whose name does not start with it.
FAMILY_OF = {
    "nested_script_incomplete": "claude",
    "snapshot_absent": "deepseek",
    "workspace_scope_probe": "claude",
}


def _family(path: pathlib.Path) -> str:
    prefix = path.stem.split("_", 1)[0]
    return prefix if prefix in conftest.FAMILIES else FAMILY_OF[path.stem]


@pytest.mark.parametrize("trace", TRACE_FIXTURES, ids=lambda p: p.stem)
def test_load_execs_matches_hand_written_records(
    trace: pathlib.Path,
) -> None:
    """Every listed field of every expected exec is reproduced."""
    actual = parse.load_execs(trace, _family(trace))
    expected = conftest.load_json(trace.with_suffix(".execs.json"))
    assert [e["command_id"] for e in actual] == [
        e["command_id"] for e in expected
    ]
    for got, want in zip(actual, expected, strict=True):
        problems = conftest.partial_mismatches(got, want)
        assert problems == [], (want["command_id"], problems)


@pytest.mark.parametrize("trace", TRACE_FIXTURES, ids=lambda p: p.stem)
def test_load_execs_record_schema(trace: pathlib.Path) -> None:
    """Records carry exactly the §5.2 fields, sorted, ``seq`` assigned."""
    actual = parse.load_execs(trace, _family(trace))
    for index, record in enumerate(actual):
        assert set(record) == conftest.EXEC_FIELDS, set(record) ^ (
            conftest.EXEC_FIELDS
        )
        assert set(record["snapshot"]) == conftest.SNAPSHOT_FIELDS
        assert record["seq"] == index
        assert record["snapshot"]["status"] in (
            "ok",
            "skipped",
            "truncated",
            "absent",
        )
        assert record["command_norm"] == parse.normalize(record["command"])
    keys = [(e["t_start"], e["command_id"]) for e in actual]
    assert keys == sorted(keys)


@pytest.mark.parametrize("trace", TRACE_FIXTURES, ids=lambda p: p.stem)
def test_load_execs_ignores_file_order(
    trace: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Reversing the lines of the trace gives identical output (§2.3)."""
    lines = trace.read_text(encoding="utf-8").splitlines(keepends=True)
    reversed_trace = tmp_path / trace.name
    reversed_trace.write_text("".join(reversed(lines)), encoding="utf-8")
    family = _family(trace)
    assert json.dumps(
        parse.load_execs(reversed_trace, family), sort_keys=True
    ) == json.dumps(parse.load_execs(trace, family), sort_keys=True)


def test_load_execs_complete_record_end_time() -> None:
    """``t_end`` = ``t_start`` + ``duration_ms`` / 1000 for complete records."""
    (record,) = parse.load_execs(
        conftest.TRACE_FIXTURES_DIR / "zcode_bg.jsonl", "zcode"
    )
    assert conftest.close(record["t_end"], 1793970842.030213 + 0.486)
    assert conftest.close(record["duration_s"], 0.486)
