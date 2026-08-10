from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from runtime.commands.trace import CommandTraceWriter, read_command_trace
from runtime.commands.types import (
	CaptureState,
	CommandOutcome,
	CommandRecord,
	CommandRequest,
	Decision,
)


def _record(command_id: str, request: CommandRequest) -> CommandRecord:
	now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
	return CommandRecord(
		command_id=command_id,
		request=request,
		decision=Decision.permit(),
		outcome=CommandOutcome(exit_code=0, stdout_state=CaptureState.CAPTURED),
		started_at=now,
		finished_at=now,
	)


def test_command_ids_are_monotonic(tmp_path: Path) -> None:
	trace = CommandTraceWriter(tmp_path)

	ids = [trace.allocate_command_id() for _ in range(3)]

	assert ids == ["cmd_000001", "cmd_000002", "cmd_000003"]


def test_a_silent_command_leaves_no_capture_file(tmp_path: Path) -> None:
	sink = CommandTraceWriter(tmp_path).open_capture("cmd_000001", "stdout")

	sink.write(b"")
	sink.close()

	assert sink.path is None
	assert not (tmp_path / "commands").exists()


def test_capture_keeps_every_byte(tmp_path: Path) -> None:
	sink = CommandTraceWriter(tmp_path).open_capture("cmd_000001", "stdout")

	sink.write(b"12345")
	sink.write(b"67890")
	sink.close()

	assert sink.path is not None
	assert sink.path.read_bytes() == b"1234567890"


def test_records_round_trip_through_the_trace_file(tmp_path: Path) -> None:
	request = CommandRequest(
		argv=("bash", "-lc", "make"), cwd="/repo", parent_command_id="cmd_000001"
	)
	with CommandTraceWriter(tmp_path) as trace:
		trace.write(_record("cmd_000002", request))

	records = read_command_trace(tmp_path)

	assert len(records) == 1
	assert records[0]["command_id"] == "cmd_000002"
	assert records[0]["parent_command_id"] == "cmd_000001"
	assert records[0]["argv"] == ["bash", "-lc", "make"]
	assert records[0]["exit_code"] == 0
	assert records[0]["stdout_state"] == "captured"


def test_a_partially_written_trailing_line_is_skipped(tmp_path: Path) -> None:
	request = CommandRequest(argv=("bash",), cwd="/repo")
	with CommandTraceWriter(tmp_path) as trace:
		trace.write(_record("cmd_000001", request))
	with (tmp_path / "commands.jsonl").open("a", encoding="utf-8") as handle:
		handle.write('{"command_id": "cmd_00')

	records = read_command_trace(tmp_path)

	assert [record["command_id"] for record in records] == ["cmd_000001"]
