"""Host side of command monitoring: install the shim, run the broker.

Two things are done here:
1. `aeshell` stands in for /bin/bash inside the agent container
2. The broker in `monitor.py` run here as a child process on a per-run socket.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from models import SystemFilesCheck, TrackedFilesCheck

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

_SOCKET_BACKLOG = 128
_BROKER_STOP_TIMEOUT_SECONDS = 10.0
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


class BrokerProcess:
	"""One broker, owning its socket directory for the life of a run."""

	def __init__(self, *, trace_dir: Path, workspace_dir: Path, socket_root: Path) -> None:
		self.trace_dir = trace_dir
		self.workspace_dir = workspace_dir
		self.socket_dir: Path | None = None
		self._socket_root = socket_root
		self._process: subprocess.Popen[bytes] | None = None

	@property
	def trace_path(self) -> Path:
		return self.trace_dir / COMMAND_TRACE_BASENAME

	@property
	def socket_path(self) -> Path | None:
		return None if self.socket_dir is None else self.socket_dir / COMMAND_SOCKET_BASENAME

	def start(self) -> None:
		if self._process is not None:
			raise RuntimeError("the command broker is already running")

		self.trace_dir.mkdir(parents=True, exist_ok=True)
		# Save a list of tracked files before actually starting the container.
		write_tracked_files(self.workspace_dir, self.trace_dir)
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
				with (self.trace_dir / BROKER_LOG_BASENAME).open("wb") as log:
					self._process = subprocess.Popen(
						[
							sys.executable,
							str(BROKER_SCRIPT),
							str(self.trace_dir),
							str(self.workspace_dir),
							str(socket_path),
							str(listener.fileno()),
						],
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

	def stop(self) -> None:
		"""Stop the broker and remove its socket directory. Idempotent."""
		process, self._process = self._process, None
		if process is not None:
			process.kill()

		socket_dir, self.socket_dir = self.socket_dir, None
		if socket_dir is not None:
			shutil.rmtree(socket_dir, ignore_errors=True)


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
