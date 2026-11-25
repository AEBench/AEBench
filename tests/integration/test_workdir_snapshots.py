"""Workdir snapshots: the broker's snapshot requests, the Claude hook client, the hook install.

The broker runs for real over a unix socket. Without a btrfs filesystem every
snapshot fails, which is still enough to exercise the protocol, the index and
the fail-closed paths; the btrfs round trip runs only when
AEBENCH_BTRFS_WORKSPACE_ROOT names a usable directory, mounted with
user_subvol_rm_allowed and with btrfs-progs installed so the test can clean up.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from runtime import btrfs
from runtime.agent_runner import (
	TOOL_HOOK_SETTINGS,
	WORKDIR_SNAPSHOT_HOOK,
	install_workdir_snapshot_hook,
)
from runtime.agents import AgentConfig, AgentDefinition, ToolHookFormat
from runtime.monitoring import BrokerProcess, WorkdirSnapshotTarget
from runtime.workspace import SNAPSHOTS_DIRNAME, create_workspace

pytestmark = pytest.mark.sanity

HOOK = Path(__file__).resolve().parents[2] / "src/runtime/agent_support" / WORKDIR_SNAPSHOT_HOOK
_HEADER = struct.Struct(">IB")


def _request(socket_path: Path, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
	body = json.dumps(payload).encode("utf-8")
	with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
		client.settimeout(10)
		client.connect(str(socket_path))
		client.sendall(_HEADER.pack(len(body), 5) + body)
		length, kind = _HEADER.unpack(client.recv(_HEADER.size))
		return kind, json.loads(client.recv(length))


def _index(broker: BrokerProcess) -> list[dict[str, object]]:
	text = broker.snapshot_index_path.read_text(encoding="utf-8")
	return [json.loads(line) for line in text.splitlines()]


def _run_hook(socket_path: Path, event: dict[str, object]) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		[sys.executable, str(HOOK), str(socket_path), "10"],
		input=json.dumps(event),
		capture_output=True,
		text=True,
		timeout=30,
		check=False,
	)


@pytest.fixture(name="snapshotting_broker")
def _snapshotting_broker(tmp_path: Path) -> object:
	"""A broker set to snapshot a directory that is not a subvolume, so every snapshot fails."""
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=workspace,
		socket_root=tmp_path / "sockets",
		workdir_snapshots=WorkdirSnapshotTarget(
			subvolume=workspace, snapshot_dir=tmp_path / "snapshots"
		),
	)
	broker.start()
	try:
		yield broker
	finally:
		broker.stop()


def test_a_broker_without_snapshots_refuses_snapshot_requests(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace", workspace_dir=workspace, socket_root=tmp_path / "sockets"
	)
	broker.start()
	try:
		assert broker.socket_path is not None
		kind, reply = _request(broker.socket_path, {"kind": "tool"})
	finally:
		broker.stop()

	assert kind == 6
	assert reply["ok"] is False
	assert "not enabled" in str(reply["error"])
	# Not a shell session, so no command record either.
	assert broker.command_count() == 0
	assert broker.stop_error is None


def test_a_failed_snapshot_is_indexed_answered_and_reported(
	snapshotting_broker: BrokerProcess,
) -> None:
	broker = snapshotting_broker
	assert broker.socket_path is not None
	kind, reply = _request(
		broker.socket_path,
		{"kind": "tool", "tool_use_id": "toolu_1", "tool_name": "Read"},
	)

	assert kind == 6
	assert reply["ok"] is False
	assert reply["seq"] == 0
	[entry] = _index(broker)
	assert entry["tool_use_id"] == "toolu_1"
	assert entry["tool_name"] == "Read"
	assert entry["ok"] is False

	broker.stop()
	# The final snapshot on SIGTERM failed too, and stop() says so.
	assert broker.stop_error is not None and "final workdir snapshot" in broker.stop_error
	entries = _index(broker)
	assert [entry["kind"] for entry in entries] == ["tool", "final"]
	info = broker.workdir_snapshots_info()
	assert info is not None
	assert (info.count, info.tool_count, info.failed_count, info.final_ok) == (0, 1, 2, False)


def test_snapshots_are_numbered_in_the_order_they_are_taken(
	snapshotting_broker: BrokerProcess,
) -> None:
	broker = snapshotting_broker
	assert broker.socket_path is not None
	for index in range(5):
		_request(broker.socket_path, {"kind": "tool", "tool_use_id": f"toolu_{index}"})

	entries = _index(broker)
	assert [entry["seq"] for entry in entries] == list(range(5))
	assert [entry["tool_use_id"] for entry in entries] == [f"toolu_{i}" for i in range(5)]


def test_the_hook_stops_the_agent_when_the_snapshot_fails(
	snapshotting_broker: BrokerProcess,
) -> None:
	assert snapshotting_broker.socket_path is not None
	result = _run_hook(
		snapshotting_broker.socket_path,
		{"tool_use_id": "toolu_9", "tool_name": "Bash", "tool_input": {"command": "make"}},
	)

	assert result.returncode == 0
	decision = json.loads(result.stdout)
	assert decision["continue"] is False
	assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
	[entry] = _index(snapshotting_broker)
	assert entry["tool_use_id"] == "toolu_9"
	# The hook sends the call's metadata, never its input.
	assert "tool_input" not in entry


def test_the_hook_stops_the_agent_when_the_broker_is_unreachable(tmp_path: Path) -> None:
	result = _run_hook(tmp_path / "missing.sock", {"tool_use_id": "toolu_1", "tool_name": "Read"})

	assert result.returncode == 0
	decision = json.loads(result.stdout)
	assert decision["continue"] is False
	assert "workdir snapshot failed" in decision["stopReason"]


def _agent(tmp_path: Path, tool_hooks: ToolHookFormat | None) -> AgentDefinition:
	return AgentDefinition(
		name="claude", directory=tmp_path, config=AgentConfig(tool_hooks=tool_hooks)
	)


def test_the_hook_is_installed_as_a_pre_tool_use_hook_on_every_tool(tmp_path: Path) -> None:
	hooks_dir = tmp_path / "tool-hooks"
	settings_path = install_workdir_snapshot_hook(
		_agent(tmp_path, ToolHookFormat.CLAUDE_CODE),
		hooks_dir,
		"/run/aebench-hooks",
		runtime_socket_path="/run/aebench/command.sock",
	)

	assert settings_path == f"/run/aebench-hooks/{TOOL_HOOK_SETTINGS}"
	settings = json.loads((hooks_dir / TOOL_HOOK_SETTINGS).read_text())
	assert settings["disableAllHooks"] is False
	[group] = settings["hooks"]["PreToolUse"]
	assert group["matcher"] == "*"
	[hook] = group["hooks"]
	assert hook["type"] == "command"
	assert hook["onFailure"] == "block"
	assert hook["command"].startswith(
		f"python3 /run/aebench-hooks/{WORKDIR_SNAPSHOT_HOOK} /run/aebench/command.sock "
	)
	assert (hooks_dir / WORKDIR_SNAPSHOT_HOOK).read_text() == HOOK.read_text()


def test_an_agent_without_tool_hooks_cannot_install_the_hook(tmp_path: Path) -> None:
	with pytest.raises(ValueError, match="tool_hooks"):
		install_workdir_snapshot_hook(
			_agent(tmp_path, None),
			tmp_path / "tool-hooks",
			"/run/aebench-hooks",
			runtime_socket_path="/x",
		)


def test_a_workspace_root_that_is_not_btrfs_fails_with_a_pointer_to_the_setup(
	tmp_path: Path,
) -> None:
	if (
		subprocess.run(
			["stat", "-f", "-c", "%T", str(tmp_path)], capture_output=True, text=True
		).stdout.strip()
		== "btrfs"
	):
		pytest.skip("tmp_path is on btrfs")
	with pytest.raises(btrfs.BtrfsError, match="not on btrfs; see scripts/setup_btrfs"):
		create_workspace("snapshot-test", tmp_path, subvolume=True)


def _btrfs_root() -> Path | None:
	root = os.environ.get("AEBENCH_BTRFS_WORKSPACE_ROOT")
	if not root or not os.path.isdir(root) or shutil.which("btrfs") is None:
		return None
	fs_type = subprocess.run(
		["stat", "-f", "-c", "%T", root], capture_output=True, text=True, check=False
	).stdout.strip()
	return Path(root) if fs_type == "btrfs" else None


def _delete_subvolumes(tmp_root: Path) -> None:
	"""Removes a test workspace: its read-only snapshots, the subvolume, then the rest.

	Unprivileged, on a filesystem mounted with user_subvol_rm_allowed.
	"""
	subvolumes = sorted((tmp_root / SNAPSHOTS_DIRNAME).glob("*")) + [tmp_root / "workspace"]
	for subvolume in subvolumes:
		subprocess.run(
			["btrfs", "property", "set", "-ts", str(subvolume), "ro", "false"],
			check=True,
			capture_output=True,
		)
		subprocess.run(
			["btrfs", "subvolume", "delete", str(subvolume)], check=True, capture_output=True
		)
	shutil.rmtree(tmp_root)


@pytest.mark.skipif(_btrfs_root() is None, reason="AEBENCH_BTRFS_WORKSPACE_ROOT is not usable")
def test_snapshots_on_btrfs_hold_the_workspace_as_each_tool_found_it(tmp_path: Path) -> None:
	root = _btrfs_root()
	assert root is not None
	workspace = create_workspace("snapshot-test", root, subvolume=True)
	try:
		broker = BrokerProcess(
			trace_dir=tmp_path / "trace",
			workspace_dir=workspace,
			socket_root=tmp_path / "sockets",
			workdir_snapshots=WorkdirSnapshotTarget(
				subvolume=workspace, snapshot_dir=workspace.parent / SNAPSHOTS_DIRNAME
			),
		)
		broker.start()
		try:
			assert broker.socket_path is not None
			broker.request_workdir_snapshot("initial")
			(workspace / "a.txt").write_text("one")
			assert _run_hook(broker.socket_path, {"tool_use_id": "toolu_1"}).stdout == ""
			(workspace / "a.txt").write_text("two")
		finally:
			broker.stop()

		assert broker.stop_error is None
		snapshots = workspace.parent / SNAPSHOTS_DIRNAME
		assert not (snapshots / "000000" / "a.txt").exists()
		assert (snapshots / "000001" / "a.txt").read_text() == "one"
		assert (snapshots / "000002" / "a.txt").read_text() == "two"
		info = broker.workdir_snapshots_info()
		assert info is not None
		assert (info.count, info.tool_count, info.failed_count, info.final_ok) == (3, 1, 0, True)
	finally:
		_delete_subvolumes(workspace.parent)
