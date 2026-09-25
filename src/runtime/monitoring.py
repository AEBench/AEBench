"""Host side of command monitoring: install the shim, run the broker.

Two things are done here:
1. `aeshell` stands in for /bin/bash inside the agent container
2. The broker in `monitor.py` run here as a child process on a per-run socket.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .backend import BenchRuntime

logger = logging.getLogger(__name__)

BROKER_SCRIPT = Path(__file__).with_name("shim") / "src" / "monitor.py"

# monitor.py owns a trace's on-disk layout; these mirror its LOG_BASENAME,
# STREAMS_DIRNAME and the basename of its default socket.
COMMAND_TRACE_BASENAME = "commands.jsonl"
COMMAND_SOCKET_BASENAME = "command.sock"
BROKER_LOG_BASENAME = "broker.log"

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
		self._socket_root.mkdir(parents=True, exist_ok=True)
		# Traversable so the container's agent can reach the socket, but not
		# listable, and the generated name is unguessable.
		self.socket_dir = Path(tempfile.mkdtemp(prefix="mon-", dir=str(self._socket_root)))
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
			try:
				process.wait(timeout=_BROKER_STOP_TIMEOUT_SECONDS)
			except subprocess.TimeoutExpired:
				logger.warning("the command broker did not exit after SIGKILL")

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
