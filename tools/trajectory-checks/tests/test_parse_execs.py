"""Kind 1: trace parser on real records (spec §5.2, §6.2, §6.3).

Each ``fixtures/trace/<name>.jsonl`` has a hand-written ``<name>.execs.json``
(see ``fixtures/trace/EDITS.md``). ``fixtures/trace/file_events/<name>.jsonl``,
when present, holds the read events of that trace (MC-14).
"""

import json
import logging
import pathlib

import conftest
import pytest

from trajectory_checks import cli, parse

TRACE_FIXTURES = sorted(conftest.TRACE_FIXTURES_DIR.glob("*.jsonl"))
NEW_FORMAT = conftest.TRACE_FIXTURES_DIR / "claude_new_format.jsonl"
OLD_FORMAT = [p for p in TRACE_FIXTURES if p != NEW_FORMAT]
# Trace keys that decision D26 reads, at the top level of a record.
NEW_RECORD_KEYS = (
    *("event", "end_reason", "parent_command_id"),
    *("root_command_id", "depth", "ppid", "pgid", "sid", "parent_link"),
    *("start_ticks", "ancestry", "ancestors", "child_pid", "orphans"),
    *("host_pid", "stdio"),
)
NEW_TIMING_KEYS = (
    *("released_at", "ended_at", "snapshot_before_ms"),
    "snapshot_after_ms",
)
NEW_SNAPSHOT_KEYS = (
    *("hashes", "created_untracked", "modified_untracked"),
    *("deleted_untracked", "total_created_untracked"),
    *("collapsed_untracked", "untracked_skipped"),
)

# Agent family of each trace fixture whose name does not start with it.
FAMILY_OF = {
    "nested_script_incomplete": "claude",
    "snapshot_absent": "deepseek",
    "workspace_scope_probe": "claude",
}


def _family(path: pathlib.Path) -> str:
    prefix = path.stem.split("_", 1)[0]
    return prefix if prefix in conftest.FAMILIES else FAMILY_OF[path.stem]


def _events(path: pathlib.Path) -> pathlib.Path | None:
    events = path.parent / "file_events" / path.name
    return events if events.is_file() else None


def _load(path: pathlib.Path) -> list[dict]:
    return parse.load_execs(path, _family(path), _events(path))


@pytest.mark.parametrize("trace", TRACE_FIXTURES, ids=lambda p: p.stem)
def test_load_execs_matches_hand_written_records(
    trace: pathlib.Path,
) -> None:
    """Every listed field of every expected exec is reproduced."""
    actual = _load(trace)
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
    actual = _load(trace)
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
        assert record["record_type"] in conftest.RECORD_TYPES | {None}
        assert record["parent_link"] in conftest.PARENT_LINKS | {None}
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
        parse.load_execs(reversed_trace, family, _events(trace)),
        sort_keys=True,
    ) == json.dumps(_load(trace), sort_keys=True)


def test_load_execs_complete_record_end_time() -> None:
    """``t_end`` = ``t_start`` + ``duration_ms`` / 1000 for complete records."""
    (record,) = parse.load_execs(
        conftest.TRACE_FIXTURES_DIR / "zcode_bg.jsonl", "zcode"
    )
    assert conftest.close(record["t_end"], 1793970842.030213 + 0.486)
    assert conftest.close(record["duration_s"], 0.486)


@pytest.mark.parametrize("trace", OLD_FORMAT, ids=lambda p: p.stem)
def test_old_format_has_every_new_field_none(trace: pathlib.Path) -> None:
    """D26: old records carry the new fields, all None but env_keys."""
    raw = [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    has_keys = {r["command_id"]: "env_keys" in r for r in raw}
    for record in parse.load_execs(trace, _family(trace)):
        for field in conftest.NEW_EXEC_FIELDS:
            if field == "env_keys" and has_keys[record["command_id"]]:
                keys = record["env_keys"]
                assert isinstance(keys, list) and keys, record["command_id"]
                assert all(isinstance(k, str) for k in keys)
            else:
                assert record[field] is None, (record["command_id"], field)
        for field in conftest.NEW_SNAPSHOT_FIELDS:
            assert record["snapshot"][field] is None, field


def test_env_keys_kept_in_recorded_order() -> None:
    """Q2 of layer 2: env_keys is copied, not sorted or deduplicated."""
    trace = conftest.TRACE_FIXTURES_DIR / "claude_eval_skipped.jsonl"
    raw = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    (record,) = parse.load_execs(trace, "claude")
    assert record["env_keys"] == raw["env_keys"]


def _strip(raw: dict) -> dict:
    """The record as the old monitor would have written it."""
    record = {k: v for k, v in raw.items() if k not in NEW_RECORD_KEYS}
    monitors = record.get("monitors") or {}
    for key in NEW_TIMING_KEYS:
        monitors.get("timing", {}).pop(key, None)
    for key in NEW_SNAPSHOT_KEYS:
        monitors.get("file_snapshot", {}).pop(key, None)
    if monitors.get("file_snapshot", {}).get("scope") == "tracked+untracked":
        monitors["file_snapshot"]["scope"] = "tracked"
    return record


def test_stripped_new_format_parses_as_old(tmp_path: pathlib.Path) -> None:
    """Metamorphic: a stripped new-format trace parses as the old format.

    The same records with every D26 field None; start-only records are
    lost, since the old broker never wrote them.
    """
    raw = [
        json.loads(line)
        for line in NEW_FORMAT.read_text(encoding="utf-8").splitlines()
    ]
    old = tmp_path / NEW_FORMAT.name
    old.write_text(
        "".join(
            json.dumps(_strip(r)) + "\n"
            for r in raw
            if r.get("event") != "start"
        ),
        encoding="utf-8",
    )
    stripped = parse.load_execs(old, "claude")
    expected = [
        e for e in _load(NEW_FORMAT) if e["record_type"] != "start_only"
    ]
    for seq, record in enumerate(expected):
        record["seq"] = seq
        record.update(dict.fromkeys(conftest.NEW_EXEC_FIELDS))
        record["env_keys"] = ["HOME", "PATH"]
        snapshot = record["snapshot"]
        snapshot.update(dict.fromkeys(conftest.NEW_SNAPSHOT_FIELDS))
        if snapshot["scope"] == "tracked+untracked":
            snapshot["scope"] = "tracked"
    assert json.dumps(stripped, sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )


def test_start_event_superseded_in_any_order(tmp_path: pathlib.Path) -> None:
    """MC-04: an end record replaces its start event wherever it is."""
    lines = NEW_FORMAT.read_text(encoding="utf-8").splitlines(keepends=True)
    for order in (lines, list(reversed(lines)), lines[1:] + lines[:1]):
        trace = tmp_path / NEW_FORMAT.name
        trace.write_text("".join(order), encoding="utf-8")
        records = parse.load_execs(trace, "claude", _events(NEW_FORMAT))
        ids = [r["command_id"] for r in records]
        assert len(ids) == len(set(ids)) == 4
        assert [r["record_type"] for r in records] == [
            "closed",
            "closed",
            "flushed_at_stop",
            "start_only",
        ]


def test_no_file_events_gives_reads_none() -> None:
    """Without file_events.jsonl, reads is None (not recorded)."""
    records = parse.load_execs(NEW_FORMAT, "claude")
    assert [r["reads"] for r in records] == [None] * 4


def _timing_at_root(raw: dict) -> dict:
    """The record with MC-10's released_at and ended_at at its root."""
    record = json.loads(json.dumps(raw))
    timing = record["monitors"]["timing"]
    for key in ("released_at", "ended_at"):
        if key in timing:
            record[key] = timing.pop(key)
    return record


def test_timing_under_monitors_gives_exact_timing() -> None:
    """MC-10: released_at and ended_at under monitors.timing are read.

    The record with both turns exact_timing on; no layout warning.
    """
    (record,) = [
        r
        for r in parse.load_execs(NEW_FORMAT, "claude")
        if r["command_id"].startswith("a")
    ]
    assert record["released_at"] is not None
    assert record["ended_at"] is not None
    run = {"final_diff_content": None, "oracle_reads": None}
    caps = cli.capabilities([record], run, NEW_FORMAT.parent)
    assert caps["exact_timing"] is True


def test_timing_at_record_root_warns(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """MC-10 keys at the record root are not read, and say so once."""
    lines = NEW_FORMAT.read_text(encoding="utf-8").splitlines()
    trace = tmp_path / NEW_FORMAT.name
    trace.write_text(
        "".join(
            json.dumps(_timing_at_root(json.loads(x))) + "\n" for x in lines
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger=parse.logger.name):
        records = parse.load_execs(trace, "claude")
    assert [r["ended_at"] for r in records] == [None] * 4
    logged = [r.getMessage() for r in caplog.records]
    assert len(logged) == 1 and "monitors.timing" in logged[0], logged
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=parse.logger.name):
        parse.load_execs(NEW_FORMAT, "claude")
    assert caplog.records == []


def _snapshot_of(tmp_path: pathlib.Path, **fields: object) -> dict:
    """The parsed snapshot of the first new-format record with fields set."""
    raw = json.loads(NEW_FORMAT.read_text(encoding="utf-8").splitlines()[0])
    raw["monitors"]["file_snapshot"].update(fields)
    trace = tmp_path / NEW_FORMAT.name
    trace.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    (record,) = parse.load_execs(trace, "claude")
    return record["snapshot"]


def test_collapsed_untracked_read(tmp_path: pathlib.Path) -> None:
    """A collapsed folder is kept with its counts; status stays ok."""
    entry = {"dir": "data/", "files": 2100, "created": 0}
    entry |= {"modified": 5, "deleted": 0}
    snapshot = _snapshot_of(tmp_path, collapsed_untracked=[entry])
    assert snapshot["status"] == "ok"
    assert snapshot["collapsed_untracked"] == [
        {"dir": "data", "files": 2100, "created": 0}
        | {"modified": 5, "deleted": 0}
    ]
    assert snapshot["untracked_skipped"] is None
    assert snapshot["created_untracked"] == []


def test_collapsed_untracked_empty_list(tmp_path: pathlib.Path) -> None:
    """[] is kept as [], distinct from a missing field."""
    assert (
        _snapshot_of(tmp_path, collapsed_untracked=[])["collapsed_untracked"]
        == []
    )


_COUNTS = {"files": 1, "created": 1, "modified": 0, "deleted": 0}


@pytest.mark.parametrize(
    "value",
    [
        "data",
        {"dir": "data"},
        [_COUNTS],
        [{"dir": ""} | _COUNTS],
        [{"dir": 3} | _COUNTS],
        [{"dir": "v", "files": "1", "created": 1, "modified": 0}],
        [{"dir": "v"} | _COUNTS | {"created": True}],
        [{"dir": "v"} | _COUNTS | {"created": -1}],
        [{"dir": "/abs"} | _COUNTS],
        [{"dir": "../up"} | _COUNTS],
        [{"dir": "v"} | _COUNTS, "data"],
    ],
)
def test_collapsed_untracked_malformed_is_none(
    tmp_path: pathlib.Path, value: object
) -> None:
    """A malformed collapsed_untracked becomes None, status stays ok."""
    snapshot = _snapshot_of(tmp_path, collapsed_untracked=value)
    assert snapshot["status"] == "ok"
    assert snapshot["collapsed_untracked"] is None


def test_untracked_skipped_read(tmp_path: pathlib.Path) -> None:
    """A skipped untracked walk keeps status ok and the tracked lists."""
    snapshot = _snapshot_of(
        tmp_path,
        untracked_skipped="too many",
        created_untracked=None,
        modified_untracked=None,
        deleted_untracked=None,
        collapsed_untracked=None,
        total_created_untracked=None,
    )
    assert snapshot["status"] == "ok"
    assert snapshot["untracked_skipped"] == "too many"
    assert snapshot["modified"] == ["src/a.c"]
    assert snapshot["created_untracked"] is None
    assert snapshot["collapsed_untracked"] is None


def test_untracked_skipped_nulls_untracked_lists(
    tmp_path: pathlib.Path,
) -> None:
    """With untracked_skipped set, stray untracked lists are not read."""
    snapshot = _snapshot_of(
        tmp_path, untracked_skipped="limit", created_untracked=["x"]
    )
    assert snapshot["untracked_skipped"] == "limit"
    for key in ("created", "modified", "deleted"):
        assert snapshot[f"{key}_untracked"] is None, key
    assert snapshot["collapsed_untracked"] is None


@pytest.mark.parametrize("value", [1, True, "", ["x"], {"why": "x"}])
def test_untracked_skipped_malformed_is_none(
    tmp_path: pathlib.Path, value: object
) -> None:
    """A non-string or empty untracked_skipped becomes None."""
    snapshot = _snapshot_of(tmp_path, untracked_skipped=value)
    assert snapshot["untracked_skipped"] is None
    assert snapshot["created_untracked"] == []


def test_untracked_visible_with_skipped_walks() -> None:
    """A skipped untracked walk does not turn untracked_visible off."""
    records = parse.load_execs(NEW_FORMAT, "claude")
    skipped = json.loads(json.dumps(records[0]))
    skipped["snapshot"].update(
        untracked_skipped="too many",
        created_untracked=None,
        modified_untracked=None,
        deleted_untracked=None,
    )
    run = {"final_diff_content": None, "oracle_reads": None}
    caps = cli.capabilities([skipped, records[1]], run, NEW_FORMAT.parent)
    assert caps["untracked_visible"] is True
    caps = cli.capabilities([records[1], skipped], run, NEW_FORMAT.parent)
    assert caps["untracked_visible"] is True
