"""Host side of command monitoring: install the shim, run the broker.

Two things are done here:
1. `aeshell` stands in for /bin/bash inside the agent container
2. The broker in `monitor.py` run here as a child process on a per-run socket.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.metadata
import json
import logging
import os
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from models import FinalContentInfo, SystemFilesCheck, TrackedFilesCheck, WorkdirSnapshotsInfo
from trace_viewer.exporter import redact_secrets

from .backend import BenchRuntime

logger = logging.getLogger(__name__)

BROKER_SCRIPT = Path(__file__).with_name("shim") / "src" / "monitor.py"

# monitor.py owns a trace's on-disk layout; these mirror its LOG_BASENAME,
# STREAMS_DIRNAME and the basename of its default socket.
COMMAND_TRACE_BASENAME = "commands.jsonl"
COMMAND_SOCKET_BASENAME = "command.sock"
BROKER_LOG_BASENAME = "broker.log"
# The artifact's files as they were before the agent ran, and how they compare
# afterwards. Both live in the trace directory, which is never mounted into the
# agent's container. monitor.py mirrors TRACKED_FILES_BASENAME.
TRACKED_FILES_BASENAME = "tracked_files.json"
TRACKED_FILES_DIFF_BASENAME = "tracked_files_diff.json"
TRACE_META_BASENAME = "trace_meta.json"

# The workspace as the agent left it, taken after the container stops and
# before the oracle mounts the workspace read-write.
FINAL_DIFF_BASENAME = "final.diff"
FINAL_FILES_BASENAME = "final_files.json"
FINAL_WORKSPACE_BASENAME = "final_workspace.tar.zst"
# Written instead when no zstd binary is on PATH.
FINAL_WORKSPACE_GZIP_BASENAME = "final_workspace.tar.gz"
MAX_DIFF_FILE_BYTES = 10 * 1024 * 1024
MAX_WORKSPACE_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
# Matched against basenames; such files are left out of the diff and archive.
SECRET_FILE_PATTERNS = (".env*", "*.pem", "id_*")
# The workspace's repository is the agent's: none of its config, hooks or
# attributes may run anything on the host.
_SAFE_GIT_OPTIONS = (
	"-c",
	"core.fsmonitor=false",
	"-c",
	"core.hooksPath=/dev/null",
	"-c",
	"safe.directory=*",
	"-c",
	"core.attributesFile=/dev/null",
)
_SAFE_DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-renames", "--no-color")
# Stored as bytes, so final.diff reproduces each file exactly.
_RAW_ATTRIBUTES = "* -text -ident -filter -working-tree-encoding\n"
_ARCHIVE_TIMEOUT_SECONDS = 1800.0

# System files the agent's container changed. The oracle runs its checks
# inside a snapshot of this container, so it uses the agent's copies of
# programs like `cat` and `python3`; a replaced program or a Python startup
# hook (.pth, sitecustomize.py) could make a check pass without real results.
SYSTEM_FILES_DIFF_BASENAME = "system_files_diff.json"
_EXECUTABLE_DIRS = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin")
_PYTHON_STARTUP_FILES = ("sitecustomize.py", "usercustomize.py")
# install_shim() replaces bash with the shim.
_SHIM_INSTALLED_PATHS = frozenset({"/bin/bash", "/usr/bin/bash"})

# Compiled into the shim (see shim/src/main.rs), so neither the socket path nor
# the real shell has to appear in the agent's environment: the agent sees the
# same environment as an unmonitored run, and `env -i bash` is still monitored.
RUNTIME_SOCKET_DIR = "/run/aebench"
SHIM_IMAGE_PATH = "/usr/lib/aebench/aeshell"
REAL_SHELL_PATH = "/usr/lib/aebench/bash.real"

# sun_path is 108 bytes including the terminator, and a longer path is
# truncated rather than rejected, so it is checked before binding.
MAX_SOCKET_PATH_BYTES = 107

PROBE_SENTINEL = "AEBENCH_SHIM_PROBE_OK"

# Workdir snapshots (`case monitor --workdir-snapshots`). The frame types and the
# index basename mirror monitor.py's WORKDIR_SNAPSHOT, WORKDIR_SNAPSHOT_DONE and
# WORKDIR_SNAPSHOTS_BASENAME; EXIT_FINAL_SNAPSHOT_FAILED mirrors its exit status.
WORKDIR_SNAPSHOT_FRAME = 6
WORKDIR_SNAPSHOT_DONE_FRAME = 7
WORKDIR_SNAPSHOTS_BASENAME = "workdir_snapshots.jsonl"
_EXIT_FINAL_SNAPSHOT_FAILED = 3
_FRAME_HEADER = struct.Struct(">IB")
_SNAPSHOT_REQUEST_TIMEOUT_SECONDS = 60.0

_SOCKET_BACKLOG = 128
# The broker takes the final workdir snapshot after SIGTERM; a snapshot under
# heavy writes can take a while, and one that never finishes ends in SIGKILL.
_BROKER_STOP_TIMEOUT_SECONDS = 60.0
_SHIM_INSTALL_TIMEOUT_SECONDS = 60.0
_PROBE_TIMEOUT_SECONDS = 30.0
_PROBE_RECORD_TIMEOUT_SECONDS = 10.0
_GIT_TIMEOUT_SECONDS = 120.0
_DOCKER_DIFF_TIMEOUT_SECONDS = 120.0
_HASH_CHUNK_BYTES = 1024 * 1024

# install shim onto /bin/bash
_INSTALL_SHIM = (
	f'shim="{SHIM_IMAGE_PATH}"; real="{REAL_SHELL_PATH}"; '
	'if [ ! -x "$shim" ]; then '
	'echo "aeshell is missing from the image; rebuild the agent image" >&2; exit 2; fi; '
	"target=$(readlink -f /bin/bash); "
	'if [ -z "$target" ]; then echo "cannot resolve /bin/bash" >&2; exit 3; fi; '
	'mkdir -p "$(dirname "$real")"; '
	'if [ ! -e "$real" ]; then cp -p "$target" "$real"; fi; '
	'cp -p "$shim" "$target.aebench-new"; '
	'chmod 0755 "$target.aebench-new"; '
	'mv -f "$target.aebench-new" "$target"'
)


def install_shim(runtime: BenchRuntime) -> None:
	"""Put the shim in place of /bin/bash, preserving the real shell beside it."""
	result = runtime.run_process(
		["sh", "-e", "-c", _INSTALL_SHIM],
		timeout=_SHIM_INSTALL_TIMEOUT_SECONDS,
	)
	if result.returncode != 0:
		detail = (result.stderr or result.stdout).strip()
		raise RuntimeError(f"failed to install the command shim: {detail or result.returncode}")


@dataclass(frozen=True, slots=True)
class WorkdirSnapshotTarget:
	"""What the broker snapshots, and where to: both on the same btrfs filesystem."""

	# The subvolume holding the workspace (the workspace may be a directory inside it).
	subvolume: Path
	snapshot_dir: Path


class BrokerProcess:
	"""One broker, owning its socket directory for the life of a run."""

	def __init__(
		self,
		*,
		trace_dir: Path,
		workspace_dir: Path,
		socket_root: Path,
		workdir_snapshots: WorkdirSnapshotTarget | None = None,
	) -> None:
		self.trace_dir = trace_dir
		self.workspace_dir = workspace_dir
		self.workdir_snapshots = workdir_snapshots
		self.socket_dir: Path | None = None
		self._socket_root = socket_root
		self._process: subprocess.Popen[bytes] | None = None
		# Why the last stop() did not end cleanly (e.g. the final snapshot failed).
		self.stop_error: str | None = None

	@property
	def trace_path(self) -> Path:
		return self.trace_dir / COMMAND_TRACE_BASENAME

	@property
	def snapshot_index_path(self) -> Path:
		return self.trace_dir / WORKDIR_SNAPSHOTS_BASENAME

	@property
	def socket_path(self) -> Path | None:
		return None if self.socket_dir is None else self.socket_dir / COMMAND_SOCKET_BASENAME

	def start(self) -> None:
		if self._process is not None:
			raise RuntimeError("the command broker is already running")

		self.trace_dir.mkdir(parents=True, exist_ok=True)
		# Save a list of tracked files before actually starting the container.
		write_tracked_files(self.workspace_dir, self.trace_dir)
		write_trace_meta(self.trace_dir)
		self._socket_root.mkdir(parents=True, exist_ok=True)
		# Traversable so the container's agent can reach the socket, but not
		# listable, and the generated name is unguessable.
		self.socket_dir = Path(tempfile.mkdtemp(prefix="mon-", dir=str(self._socket_root)))
		try:
			self.socket_dir.chmod(0o711)

			socket_path = self.socket_dir / COMMAND_SOCKET_BASENAME
			length = len(os.fsencode(str(socket_path)))
			if length > MAX_SOCKET_PATH_BYTES:
				raise RuntimeError(
					f"the command socket path is {length} bytes and AF_UNIX allows "
					f"{MAX_SOCKET_PATH_BYTES}; set AEBENCH_COMMAND_SOCKET_ROOT to a shorter path"
				)

			with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
				listener.bind(str(socket_path))
				listener.listen(_SOCKET_BACKLOG)
				# bind() applies the umask, so the socket lands at 0755 and connect(2)
				# needs write permission. The container's agent is uid 1000 and the
				# host invoker generally is not.
				os.chmod(socket_path, 0o666)

				# The child keeps its own dup of the log, so the parent's copy closes
				# with the block.
				command = [
					sys.executable,
					str(BROKER_SCRIPT),
					str(self.trace_dir),
					str(self.workspace_dir),
					str(socket_path),
					str(listener.fileno()),
				]
				if self.workdir_snapshots is not None:
					command += [
						"--snapshot-subvolume",
						str(self.workdir_snapshots.subvolume),
						"--snapshot-dir",
						str(self.workdir_snapshots.snapshot_dir),
					]
				with (self.trace_dir / BROKER_LOG_BASENAME).open("wb") as log:
					self._process = subprocess.Popen(
						command,
						stdin=subprocess.DEVNULL,
						stdout=log,
						stderr=subprocess.STDOUT,
						# Keeps the descriptor open across the fork, at the same number.
						pass_fds=(listener.fileno(),),
					)
		except BaseException:
			self.stop()
			raise

		logger.info("command broker listening on %s", socket_path)

	def command_count(self) -> int:
		try:
			with self.trace_path.open("r", encoding="utf-8") as handle:
				return sum(1 for line in handle if line.strip())
		except FileNotFoundError:
			return 0

	def request_workdir_snapshot(self, kind: str) -> dict[str, object]:
		"""Asks the broker for a workdir snapshot, as the agent's hook does, and waits for it."""
		socket_path = self.socket_path
		if socket_path is None or self._process is None:
			raise RuntimeError("the command broker is not running")
		payload = json.dumps({"kind": kind}).encode("utf-8")
		with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
			client.settimeout(_SNAPSHOT_REQUEST_TIMEOUT_SECONDS)
			client.connect(str(socket_path))
			client.sendall(_FRAME_HEADER.pack(len(payload), WORKDIR_SNAPSHOT_FRAME) + payload)
			length, frame_type = _FRAME_HEADER.unpack(_recv_exactly(client, _FRAME_HEADER.size))
			reply = json.loads(_recv_exactly(client, length))
		if frame_type != WORKDIR_SNAPSHOT_DONE_FRAME or not isinstance(reply, dict):
			raise RuntimeError(f"unexpected reply to a workdir snapshot request: {reply!r}")
		if not reply.get("ok"):
			raise RuntimeError(f"workdir snapshot failed: {reply.get('error')}")
		return reply

	def stop(self) -> None:
		"""Stop the broker and remove its socket directory. Idempotent.

		SIGTERM first: a snapshotting broker takes its final snapshot before it
		exits. One that does not exit in time is killed, and stop_error says so.
		"""
		process, self._process = self._process, None
		if process is not None:
			self.stop_error = None
			process.send_signal(signal.SIGTERM)
			try:
				status = process.wait(timeout=_BROKER_STOP_TIMEOUT_SECONDS)
			except subprocess.TimeoutExpired:
				process.kill()
				process.wait()
				self.stop_error = (
					f"the command broker did not exit within "
					f"{_BROKER_STOP_TIMEOUT_SECONDS:g}s of SIGTERM"
				)
			else:
				if status == _EXIT_FINAL_SNAPSHOT_FAILED:
					self.stop_error = "the final workdir snapshot failed; see the broker log"
				elif status not in (0, -signal.SIGTERM):
					self.stop_error = f"the command broker exited with status {status}"
			if self.stop_error is not None:
				logger.warning("%s (%s)", self.stop_error, self.trace_dir / BROKER_LOG_BASENAME)

		socket_dir, self.socket_dir = self.socket_dir, None
		if socket_dir is not None:
			shutil.rmtree(socket_dir, ignore_errors=True)

	def workdir_snapshots_info(self) -> WorkdirSnapshotsInfo | None:
		"""Summarizes the snapshot index; None unless this broker took snapshots."""
		target = self.workdir_snapshots
		if target is None:
			return None
		entries: list[dict[str, object]] = []
		try:
			with self.snapshot_index_path.open("r", encoding="utf-8") as handle:
				entries = [json.loads(line) for line in handle if line.strip()]
		except FileNotFoundError:
			pass
		final = next((entry for entry in reversed(entries) if entry.get("kind") == "final"), None)
		return WorkdirSnapshotsInfo(
			index_path=str(self.snapshot_index_path),
			snapshot_dir=str(target.snapshot_dir),
			count=sum(1 for entry in entries if entry.get("ok")),
			tool_count=sum(1 for entry in entries if entry.get("kind") == "tool"),
			failed_count=sum(1 for entry in entries if not entry.get("ok")),
			final_ok=bool(final and final.get("ok")),
		)


def _recv_exactly(client: socket.socket, size: int) -> bytes:
	data = bytearray()
	while len(data) < size:
		chunk = client.recv(size - len(data))
		if not chunk:
			raise RuntimeError("the command broker closed the connection")
		data += chunk
	return bytes(data)


def verify_monitoring(runtime: BenchRuntime, broker: BrokerProcess) -> None:
	"""Run one shell in the container and check it reached the broker."""
	before = broker.command_count()
	# Deliberately the unprivileged agent, not root: root satisfies any
	# permission bits, so a root probe would pass against a socket the agent
	# cannot open and the run would go unmonitored.
	result = runtime.run_process(
		["runuser", "--user", "agent", "--", "/bin/bash", "-c", f"echo {PROBE_SENTINEL}"],
		timeout=_PROBE_TIMEOUT_SECONDS,
	)
	if result.returncode != 0 or PROBE_SENTINEL not in result.stdout:
		detail = (result.stderr or result.stdout).strip()
		raise RuntimeError(f"the shim did not behave like a shell: {detail or result.returncode}")

	deadline = time.monotonic() + _PROBE_RECORD_TIMEOUT_SECONDS
	while time.monotonic() < deadline:
		if broker.command_count() > before:
			return
		time.sleep(0.05)
	raise RuntimeError(
		"the shim ran but never reached the command broker; the socket mount or its "
		f"permissions are wrong: {_broker_log_tail(broker.trace_dir)}"
	)


def _broker_log_tail(trace_dir: Path) -> str:
	try:
		text = (trace_dir / BROKER_LOG_BASENAME).read_text(encoding="utf-8")
	except OSError:
		return "(no broker log)"
	return text.strip()[-2000:] or "(broker log is empty)"


def write_tracked_files(workspace: Path, trace_dir: Path) -> Path:
	"""Record the artifact's files size, last modification time and checksum SHA-256 before the agent runs."""
	source, commit, paths = _list_artifact_files(workspace)
	files: dict[str, dict[str, object]] = {}
	for rel in paths:
		state = _file_state(workspace / rel)
		if state is not None:
			files[rel] = state

	target = trace_dir / TRACKED_FILES_BASENAME
	target.write_text(
		json.dumps({"source": source, "commit": commit, "files": files}, indent=1),
		encoding="utf-8",
	)
	return target


def write_trace_meta(trace_dir: Path) -> Path:
	"""Record which monitor wrote the trace, keyed by the run token."""
	try:
		version: str | None = importlib.metadata.version("aebench")
	except importlib.metadata.PackageNotFoundError:
		version = None
	target = trace_dir / TRACE_META_BASENAME
	target.write_text(
		json.dumps(
			{
				"broker_version": version,
				# The trace directory is keyed by the token, as is each record's run_id.
				"run_token": trace_dir.name,
				"start_events": False,
			},
			indent=1,
		),
		encoding="utf-8",
	)
	return target


def check_tracked_files(workspace: Path, trace_dir: Path) -> TrackedFilesCheck | None:
	"""Compare every tracked file against its recorded checksum and permission bits.

	A file whose content and mode both changed appears in both lists.
	"""
	try:
		recorded = json.loads((trace_dir / TRACKED_FILES_BASENAME).read_text(encoding="utf-8"))
	except FileNotFoundError:
		return None

	modified: list[str] = []
	deleted: list[str] = []
	mode_changed: list[str] = []
	for rel, before in sorted(recorded["files"].items()):
		after = _file_state(workspace / rel)
		if after is None:
			deleted.append(rel)
			continue
		if after["sha256"] != before["sha256"] or after["symlink"] != before["symlink"]:
			modified.append(rel)
		# A list written before modes were recorded has none to compare.
		if "mode" in before and after["mode"] != before["mode"]:
			mode_changed.append(rel)

	diff_path = trace_dir / TRACKED_FILES_DIFF_BASENAME
	diff_path.write_text(
		json.dumps(
			{
				"tracked": len(recorded["files"]),
				"modified": modified,
				"deleted": deleted,
				"mode_changed": mode_changed,
			},
			indent=1,
		),
		encoding="utf-8",
	)
	return TrackedFilesCheck(
		diff_path=str(diff_path),
		tracked=len(recorded["files"]),
		modified_count=len(modified),
		deleted_count=len(deleted),
		mode_changed_count=len(mode_changed),
	)


def capture_final_workspace(
	workspace: Path,
	trace_dir: Path,
	secrets: Mapping[str, str] | None = None,
) -> FinalContentInfo | None:
	"""Write final.diff against the baseline commit, final_files.json and an archive.

	The diff is built on a scratch repository whose objects fall back to the
	workspace's, so the workspace's .git is only read. Values of `secrets`
	are replaced with [REDACTED:<NAME>] in the diff.
	"""
	try:
		recorded = json.loads((trace_dir / TRACKED_FILES_BASENAME).read_text(encoding="utf-8"))
	except FileNotFoundError:
		return None
	commit = recorded.get("commit")
	objects = workspace / ".git" / "objects"
	if not commit or not objects.is_dir():
		return None

	present, omitted = _final_paths(workspace)
	with tempfile.TemporaryDirectory(prefix="aebench-final-") as scratch:
		git = _GitCall(Path(scratch), workspace, objects)
		git("read-tree", commit)
		baseline = {os.fsdecode(item) for item in git("ls-files", "-z").split(b"\0") if item}
		# Present files are staged; baseline files gone from the workspace are removed.
		staged = sorted(set(present) | (baseline - set(present) - set(omitted)))
		if staged:
			git(
				"add",
				"-f",
				"-A",
				"--pathspec-from-file=-",
				"--pathspec-file-nul",
				stdin=b"\0".join(os.fsencode(rel) for rel in staged),
			)
		diff = git("diff", "--cached", "--binary", "--full-index", *_SAFE_DIFF_OPTIONS, commit)
		names = git("diff", "--cached", "--name-status", "-z", *_SAFE_DIFF_OPTIONS, commit)

	if secrets:
		diff = os.fsencode(redact_secrets(os.fsdecode(diff), secrets))
	diff_path = trace_dir / FINAL_DIFF_BASENAME
	diff_path.write_bytes(diff)

	entries = _diff_entries(workspace, names)
	before = recorded.get("files") or {}
	for rel, reason in sorted(omitted.items()):
		state = _file_state(workspace / rel)
		if state is None or (rel in before and before[rel].get("sha256") == state["sha256"]):
			continue
		entries.append(
			{
				"path": rel,
				"status": "M" if rel in baseline else "A",
				"size": state["size"],
				# A secret's hash could be brute-forced.
				"sha256": None if reason == "secret_pattern" else state["sha256"],
				"in_diff": False,
				"reason_omitted": reason,
			}
		)
	entries.sort(key=lambda entry: str(entry["path"]))
	files_path = trace_dir / FINAL_FILES_BASENAME
	files_path.write_text(json.dumps(entries, indent=1), encoding="utf-8")

	try:
		archive = _archive_workspace(workspace, trace_dir)
	except (OSError, subprocess.SubprocessError, tarfile.TarError):
		logger.warning("failed to archive the final workspace %s", workspace, exc_info=True)
		archive = None

	return FinalContentInfo(
		diff_path=str(diff_path),
		files_path=str(files_path),
		workspace_path=None if archive is None else str(archive),
		files=len(entries),
		omitted=sum(1 for entry in entries if entry["reason_omitted"] is not None),
		bytes=len(diff),
	)


class _GitCall:
	"""Runs git on a scratch GIT_DIR over the workspace, in a clean environment."""

	def __init__(self, scratch: Path, workspace: Path, objects: Path) -> None:
		self.scratch = scratch
		self.workspace = workspace
		self.env = {
			"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
			"HOME": str(scratch),
			"XDG_CONFIG_HOME": str(scratch),
			"GIT_CONFIG_NOSYSTEM": "1",
			"GIT_CONFIG_GLOBAL": os.devnull,
			"GIT_DIR": str(scratch / "repo" / ".git"),
			"GIT_WORK_TREE": str(workspace),
			"GIT_INDEX_FILE": str(scratch / "index"),
			"GIT_OBJECT_DIRECTORY": str(scratch / "objects"),
			"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects),
			"GIT_LITERAL_PATHSPECS": "1",
			"GIT_TERMINAL_PROMPT": "0",
			"GIT_PAGER": "cat",
			"LC_ALL": "C",
		}
		(scratch / "objects").mkdir()
		self("init", "-q", "--template=", str(scratch / "repo"), init=True)
		(scratch / "repo" / ".git" / "info").mkdir(exist_ok=True)
		(scratch / "repo" / ".git" / "info" / "attributes").write_text(
			_RAW_ATTRIBUTES, encoding="utf-8"
		)

	def __call__(self, *args: str, stdin: bytes | None = None, init: bool = False) -> bytes:
		env = self.env
		if init:
			env = {
				key: value
				for key, value in env.items()
				if not key.startswith(("GIT_DIR", "GIT_WORK", "GIT_OBJ", "GIT_ALT", "GIT_INDEX"))
			}
		result = subprocess.run(
			["git", "--no-pager", *_SAFE_GIT_OPTIONS, *args],
			input=stdin,
			capture_output=True,
			env=env,
			cwd=self.scratch if init else self.workspace,
			timeout=_GIT_TIMEOUT_SECONDS,
			check=False,
		)
		if result.returncode != 0:
			raise RuntimeError(f"git {args[0]} failed: {os.fsdecode(result.stderr).strip()}")
		return result.stdout


def _is_secret(rel: str) -> bool:
	name = os.path.basename(rel)
	return any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_FILE_PATTERNS)


def _walk_files(workspace: Path) -> Iterator[tuple[str, os.stat_result]]:
	"""Yields (relative path, lstat) of every file and symlink outside any .git."""
	for directory, subdirectories, filenames in os.walk(workspace):
		kept = []
		for name in subdirectories:
			if name == ".git":
				continue
			if os.path.islink(os.path.join(directory, name)):
				filenames.append(name)
			else:
				kept.append(name)
		subdirectories[:] = kept
		for name in filenames:
			path = os.path.join(directory, name)
			try:
				info = os.lstat(path)
			except OSError:
				continue
			if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
				yield os.path.relpath(path, workspace), info


def _final_paths(workspace: Path) -> tuple[list[str], dict[str, str]]:
	"""Returns the paths to stage and {path: reason} for those left out."""
	present: list[str] = []
	omitted: dict[str, str] = {}
	for rel, info in _walk_files(workspace):
		if _is_secret(rel):
			omitted[rel] = "secret_pattern"
		elif stat.S_ISREG(info.st_mode) and info.st_size > MAX_DIFF_FILE_BYTES:
			omitted[rel] = "over_cap"
		else:
			present.append(rel)
	return present, omitted


def _diff_entries(workspace: Path, names: bytes) -> list[dict[str, object]]:
	"""final_files.json entries from `git diff --name-status -z`."""
	fields = [os.fsdecode(item) for item in names.split(b"\0")]
	entries: list[dict[str, object]] = []
	for status, rel in zip(fields[0::2], fields[1::2]):
		if not status:
			continue
		# T, a type change such as file to symlink, is a modification.
		kind = status[0] if status[0] in "AD" else "M"
		state = None if kind == "D" else _file_state(workspace / rel)
		entries.append(
			{
				"path": rel,
				"status": kind,
				"size": None if state is None else state["size"],
				"sha256": None if state is None else state["sha256"],
				"in_diff": True,
				"reason_omitted": None,
			}
		)
	return entries


def _archive_workspace(workspace: Path, trace_dir: Path) -> Path:
	"""Archive the workspace without .git/objects or secrets, up to the size cap."""
	zstd = shutil.which("zstd")
	if zstd is None:
		target = trace_dir / FINAL_WORKSPACE_GZIP_BASENAME
		with tarfile.open(target, "w:gz") as archive:
			_fill_archive(archive, workspace)
		return target

	target = trace_dir / FINAL_WORKSPACE_BASENAME
	with subprocess.Popen(
		[zstd, "-q", "-f", "-T0", "-o", str(target)],
		stdin=subprocess.PIPE,
		stderr=subprocess.DEVNULL,
	) as compressor:
		assert compressor.stdin is not None
		try:
			with tarfile.open(fileobj=compressor.stdin, mode="w|") as archive:
				_fill_archive(archive, workspace)
		finally:
			compressor.stdin.close()
		if compressor.wait(timeout=_ARCHIVE_TIMEOUT_SECONDS) != 0:
			raise subprocess.SubprocessError(f"zstd exited with {compressor.returncode}")
	return target


def _fill_archive(archive: tarfile.TarFile, workspace: Path) -> None:
	total = 0
	skipped = 0
	for directory, subdirectories, filenames in os.walk(workspace):
		rel_dir = os.path.relpath(directory, workspace)
		if rel_dir == ".git":
			subdirectories[:] = [name for name in subdirectories if name != "objects"]
		for name in sorted(subdirectories) + sorted(filenames):
			path = os.path.join(directory, name)
			rel = os.path.normpath(os.path.join(rel_dir, name))
			if _is_secret(rel):
				continue
			try:
				info = os.lstat(path)
			except OSError:
				continue
			if stat.S_ISREG(info.st_mode):
				if total + info.st_size > MAX_WORKSPACE_ARCHIVE_BYTES:
					skipped += 1
					continue
				total += info.st_size
			elif not (stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
				continue
			# Not recursive, and tarfile stores a symlink as a link.
			archive.add(path, arcname=rel, recursive=False)
	if skipped:
		logger.warning("final workspace archive is capped; %d files left out", skipped)


def check_system_files(container: str, trace_dir: Path) -> SystemFilesCheck | None:
	"""Record the executables, Python startup hooks and /etc files the container changed.

	Each entry is `docker diff`'s kind (A added, C changed, D deleted) and path,
	relative to the image the container started from.
	"""
	try:
		result = subprocess.run(
			["docker", "diff", container],
			capture_output=True,
			text=True,
			timeout=_DOCKER_DIFF_TIMEOUT_SECONDS,
			check=False,
		)
	except (OSError, subprocess.TimeoutExpired):
		logger.warning("docker diff of %s failed", container, exc_info=True)
		return None
	if result.returncode != 0:
		logger.warning("docker diff of %s failed: %s", container, result.stderr.strip())
		return None

	entries = [
		(kind, path)
		for kind, _, path in (line.partition(" ") for line in result.stdout.splitlines())
		if path
	]
	# A directory is listed as changed whenever an entry inside it is.
	parents = {os.path.dirname(path) for _, path in entries}
	executables: list[str] = []
	python_startup: list[str] = []
	etc: list[str] = []
	for kind, path in sorted(entries, key=lambda entry: entry[1]):
		if path in parents or path in _SHIM_INSTALLED_PATHS:
			continue
		entry = f"{kind} {path}"
		name = os.path.basename(path)
		if os.path.dirname(path) in _EXECUTABLE_DIRS:
			executables.append(entry)
		elif name.endswith(".pth") or name in _PYTHON_STARTUP_FILES:
			python_startup.append(entry)
		elif path.startswith("/etc/"):
			etc.append(entry)

	diff_path = trace_dir / SYSTEM_FILES_DIFF_BASENAME
	diff_path.write_text(
		json.dumps(
			{
				"changed": len(entries),
				"executables": executables,
				"python_startup": python_startup,
				"etc": etc,
			},
			indent=1,
		),
		encoding="utf-8",
	)
	return SystemFilesCheck(
		diff_path=str(diff_path),
		changed=len(entries),
		executables_count=len(executables),
		python_startup_count=len(python_startup),
		etc_count=len(etc),
	)


def _list_artifact_files(workspace: Path) -> tuple[str, str | None, list[str]]:
	"""Returns (source, commit, workspace relative paths) for the artifact's own files."""
	try:
		listed = subprocess.run(
			["git", "-C", str(workspace), "ls-files", "-z"],
			capture_output=True,
			timeout=_GIT_TIMEOUT_SECONDS,
			check=False,
		)
		# An empty listing means the workspace is not a checkout of its own, only
		# a directory inside some enclosing repository that does not track it.
		if listed.returncode == 0 and listed.stdout:
			head = subprocess.run(
				["git", "-C", str(workspace), "rev-parse", "HEAD"],
				capture_output=True,
				text=True,
				timeout=_GIT_TIMEOUT_SECONDS,
				check=False,
			)
			commit = head.stdout.strip() if head.returncode == 0 else None
			# Workspace relative paths, keep like this.
			paths = [os.fsdecode(item) for item in listed.stdout.split(b"\0") if item]
			return "git", commit, paths
	except (OSError, subprocess.TimeoutExpired):
		# No usable git on the host, so track every file instead.
		logger.warning("git ls-files failed in %s; tracking every file", workspace, exc_info=True)

	paths = []
	for directory, subdirectories, filenames in os.walk(workspace):
		subdirectories[:] = [name for name in subdirectories if name != ".git"]
		for filename in filenames:
			paths.append(os.path.relpath(os.path.join(directory, filename), workspace))
	return "tree", None, paths


def _file_state(path: Path) -> dict[str, object] | None:
	"""Return size, modification time, permission bits and checksum SHA-256 of the file."""
	try:
		info = os.lstat(path)
	except OSError:
		return None

	symlink = stat.S_ISLNK(info.st_mode)
	if symlink:
		digest = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
	elif stat.S_ISREG(info.st_mode):
		digest = _sha256(path)
	else:
		return None
	return {
		"size": info.st_size,
		"mtime_ns": info.st_mtime_ns,
		# Permission bits only (e.g. 0o755); the checksum does not see a chmod.
		"mode": stat.S_IMODE(info.st_mode),
		"sha256": digest,
		"symlink": symlink,
	}


def _sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		while chunk := handle.read(_HASH_CHUNK_BYTES):
			digest.update(chunk)
	return digest.hexdigest()
