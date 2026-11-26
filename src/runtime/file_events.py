"""Records the file, exec and fork events of a run's container with eBPF.

The recorder is bpftrace running ebpf_recorder/file_events.bt in its own
privileged container next to the agent's (never inside it), filtered to the
agent container's cgroup. Its raw lines are then linked to the commands of
commands.jsonl, giving file_events.jsonl.

A process is (container pid, start in clock ticks). Each event goes to the
nearest command that owns its process or an ancestor at fork time: the
shim, its bash.real child, or an orphan it adopted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import signal
import subprocess
import threading
import time
from collections.abc import Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import IO, Any

from models import FileEventsInfo

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "ebpf_recorder"
ASSET_NAMES = ("Dockerfile", "file_events.bt")
IMAGE = "aebench-ebpf-recorder"
CGROUP_ROOT = Path("/sys/fs/cgroup")

RAW_BASENAME = "file_events.raw.tsv"
LOG_BASENAME = "file_events.recorder.log"
EVENTS_BASENAME = "file_events.jsonl"

ATTACH_TIMEOUT_SECONDS = 60.0
STOP_TIMEOUT_SECONDS = 30.0
BUILD_TIMEOUT_SECONDS = 1800.0
DOCKER_TIMEOUT_SECONDS = 60.0

# The kernel's /proc/<pid>/stat start time is start_boottime in USER_HZ ticks.
NS_PER_TICK = 10_000_000
# Raw kinds, and the event_type each is written as; the reader of file_events.jsonl
# counts close_write (and moves) as writes and every other type as a read.
EVENT_TYPES = {"open_write": "close_write", "open_read": "access", "open_exec": "open_exec"}
FILE_KINDS = tuple(EVENT_TYPES)
SKIPPED_PREFIXES = ("/proc/", "/sys/", "/dev/")

Process = tuple[int, int]
# A line of commands.jsonl, or of file_events.jsonl.
Record = dict[str, Any]


def image_tag(assets_dir: Path = ASSETS_DIR) -> str:
	"""The recorder image, tagged by its script and Dockerfile so a change rebuilds it."""
	digest = hashlib.sha256()
	for name in ASSET_NAMES:
		digest.update(name.encode() + b"\0" + (assets_dir / name).read_bytes() + b"\0")
	return f"{IMAGE}:{digest.hexdigest()[:12]}"


def ensure_image(tag: str, *, log: Path, assets_dir: Path = ASSETS_DIR) -> None:
	"""Builds the recorder image from the packaged assets unless Docker has it."""
	inspect = subprocess.run(
		["docker", "image", "inspect", tag],
		capture_output=True,
		check=False,
		timeout=DOCKER_TIMEOUT_SECONDS,
	)
	if inspect.returncode == 0:
		return
	logger.info("building the eBPF recorder image %s (first use)", tag)
	log.parent.mkdir(parents=True, exist_ok=True)
	with log.open("a", encoding="utf-8") as handle:
		handle.write(f"\nbuilding the eBPF recorder image {tag} from {assets_dir}\n")
		handle.flush()
		result = subprocess.run(
			["docker", "build", "-t", tag, str(assets_dir)],
			stdin=subprocess.DEVNULL,
			stdout=handle,
			stderr=subprocess.STDOUT,
			check=False,
			timeout=BUILD_TIMEOUT_SECONDS,
		)
	if result.returncode != 0:
		raise RuntimeError(f"docker build of {tag} failed (exit {result.returncode}); see {log}")


def container_id(name: str) -> str:
	"""The full id of a container, from its name or a short id."""
	result = subprocess.run(
		["docker", "inspect", "--format", "{{.Id}}", name],
		capture_output=True,
		text=True,
		check=True,
		timeout=DOCKER_TIMEOUT_SECONDS,
	)
	return result.stdout.strip()


def cgroup_id(full_id: str, root: Path = CGROUP_ROOT) -> int:
	"""The cgroup v2 id of a container: the inode of its scope directory."""
	# Docker with the systemd cgroup driver; the cgroupfs driver uses docker/<id>.
	for scope in (root / "system.slice" / f"docker-{full_id}.scope", root / "docker" / full_id):
		if scope.is_dir():
			return scope.stat().st_ino
	raise FileNotFoundError(f"no cgroup v2 directory for container {full_id} under {root}")


def clock_header() -> str:
	return f"clock\t{time.time_ns()}\t{time.monotonic_ns()}\n"


def recorder_name(cgroup: int) -> str:
	return f"aebench-ebpf-{cgroup}"


def recorder_command(cgroup: int, image: str) -> list[str]:
	"""docker run of the recorder; tracefs is mounted for the tracepoints."""
	return [
		*("docker", "run", "--rm", "--name", recorder_name(cgroup)),
		*("--privileged", "--pid=host"),
		*("-v", "/sys/kernel/tracing:/sys/kernel/tracing"),
		*(image, str(cgroup)),
	]


class FileEventsRecorder:
	"""One recorder container: start, wait until its probes attach, stop."""

	def __init__(
		self,
		raw_path: Path,
		*,
		log_path: Path | None = None,
		build_log: Path | None = None,
		assets_dir: Path = ASSETS_DIR,
	) -> None:
		self.raw_path = raw_path
		self.log_path = log_path or raw_path.with_name(LOG_BASENAME)
		self.build_log = build_log or self.log_path
		self.assets_dir = assets_dir
		self.name: str | None = None
		self._process: subprocess.Popen[str] | None = None
		self._raw: IO[str] | None = None
		self._reader: threading.Thread | None = None
		self._attached = threading.Event()

	def start(self, container: str, *, timeout: float = ATTACH_TIMEOUT_SECONDS) -> None:
		"""Starts recording `container`; raises unless the probes attach in time."""
		tag = image_tag(self.assets_dir)
		ensure_image(tag, log=self.build_log, assets_dir=self.assets_dir)
		cgroup = cgroup_id(container_id(container))
		self.name = recorder_name(cgroup)
		self.raw_path.parent.mkdir(parents=True, exist_ok=True)
		self._raw = self.raw_path.open("w", encoding="utf-8")
		self._raw.write(clock_header())
		self._raw.flush()
		self._process = subprocess.Popen(
			recorder_command(cgroup, tag),
			stdin=subprocess.DEVNULL,
			stdout=self._raw,
			stderr=subprocess.PIPE,
			text=True,
			errors="replace",
		)
		self._reader = threading.Thread(
			target=self._read_log, args=(self._process.stderr,), daemon=True
		)
		self._reader.start()
		deadline = time.monotonic() + timeout
		while not self._attached.wait(0.1):
			if self._process.poll() is not None:
				self._reader.join(timeout=2)
				raise RuntimeError(
					f"the recorder exited ({self._process.returncode}) before attaching: "
					f"{self._log_tail()}"
				)
			if time.monotonic() > deadline:
				raise TimeoutError(
					f"the recorder did not attach within {timeout:.0f} s: {self._log_tail()}"
				)

	def _read_log(self, stream: IO[str] | None) -> None:
		# bpftrace prints "Attached N probes" (older: "Attaching N probes...") on stderr.
		if stream is None:
			return
		with self.log_path.open("a", encoding="utf-8") as log:
			for line in stream:
				log.write(line)
				log.flush()
				if line.startswith(("Attached ", "Attaching ")):
					self._attached.set()

	def _log_tail(self) -> str:
		try:
			text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
		except OSError:
			return "(no recorder log)"
		return text[-1000:] or "(the recorder printed nothing)"

	def saw_exec(self, *, timeout: float = 5.0) -> bool:
		"""Whether the raw file has an exec inside the container yet."""
		deadline = time.monotonic() + timeout
		while True:
			try:
				with self.raw_path.open(encoding="utf-8", errors="replace") as handle:
					for line in handle:
						fields = line.split("\t", 4)
						if fields[0] == "open_exec" and len(fields) > 3 and fields[3] != "0":
							return True
			except OSError:
				pass
			if time.monotonic() > deadline:
				return False
			time.sleep(0.1)

	def stop(self, *, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
		"""SIGINT (bpftrace flushes and reports lost events), then kill and remove."""
		process = self._process
		try:
			if process is not None and process.poll() is None:
				# docker run proxies the signal to bpftrace.
				process.send_signal(signal.SIGINT)
				try:
					process.wait(timeout=timeout)
				except subprocess.TimeoutExpired:
					process.kill()
					process.wait(timeout=10)
		finally:
			if self.name is not None:
				subprocess.run(
					["docker", "rm", "-f", self.name],
					capture_output=True,
					check=False,
					timeout=DOCKER_TIMEOUT_SECONDS,
				)
			if self._reader is not None:
				self._reader.join(timeout=5)
			if self._raw is not None:
				self._raw.close()
			self._process = None
			self.name = None

	def link(self, commands_path: Path) -> FileEventsInfo:
		"""Writes file_events.jsonl next to the raw file."""
		return link_files(
			self.raw_path,
			commands_path,
			self.raw_path.with_name(EVENTS_BASENAME),
			extra=[self.log_path],
		)


def link_files(
	raw_path: Path, commands_path: Path, out_path: Path, *, extra: Iterable[Path] = ()
) -> FileEventsInfo:
	"""Links a raw file (and lines of `extra`, e.g. bpftrace's lost-event reports)."""
	records: list[Record] = []
	if commands_path.is_file():
		with commands_path.open(encoding="utf-8") as handle:
			records = [json.loads(line) for line in handle if line.strip()]
	handles = [raw_path.open(encoding="utf-8", errors="replace")]
	handles += [p.open(encoding="utf-8", errors="replace") for p in extra if p.is_file()]
	header: Record = {}
	count = 0
	try:
		with out_path.open("w", encoding="utf-8") as out:
			for row in link(chain(*handles), records):
				if "recorder" in row:
					header = row
				else:
					count += 1
				out.write(json.dumps(row) + "\n")
	finally:
		for handle in handles:
			handle.close()
	return FileEventsInfo(
		status="recorded",
		raw_path=str(raw_path),
		path=str(out_path),
		events=count,
		lost=int(header.get("lost", 0)),
	)


def process(nspid: str, start_ns: str) -> Process:
	return int(nspid), int(start_ns) // NS_PER_TICK


def owners(records: Iterable[Record]) -> dict[Process, str]:
	"""Each process a command record names, mapped to its command_id."""
	found: dict[Process, str] = {}
	for record in records:
		if record.get("event") == "start" or "command_id" not in record:
			continue
		cid = record["command_id"]
		pairs = [(record.get("pid"), record.get("start_ticks"))]
		pairs.append((record.get("child_pid"), record.get("child_start_ticks")))
		pairs += [(o.get("pid"), o.get("start_ticks")) for o in record.get("orphans") or ()]
		for pid, ticks in pairs:
			if pid is not None and ticks is not None:
				# A shim's own processes win over another command's orphan list.
				found.setdefault((int(pid), int(ticks)), cid)
	return found


def parse(lines: Iterable[str]) -> tuple[tuple[int, int] | None, list[list[str]], int]:
	"""(wall and monotonic ns at start, event fields, lost events) of a raw file."""
	clock, events, lost = None, [], 0
	for line in lines:
		line = line.rstrip("\n")
		kind = line.split("\t", 1)[0]
		if kind in FILE_KINDS:
			# The path is last and may hold a tab, so split off five fields only.
			fields = line.split("\t", 5)
			if len(fields) == 6:
				events.append(fields)
		elif kind == "fork":
			fields = line.split("\t")
			if len(fields) == 8:
				events.append(fields)
		elif kind == "clock":
			fields = line.split("\t")
			clock = int(fields[1]), int(fields[2])
		elif line.startswith("Lost "):
			# bpftrace's own report of events the kernel buffer dropped.
			lost += int(line.split()[1])
	return clock, events, lost


def link(raw: Iterable[str], records: Iterable[Record]) -> Iterator[Record]:
	"""A header line, then one row per (command, path, event type)."""
	clock, events, lost = parse(raw)
	owner = owners(records)
	parent: dict[Process, Process] = {}
	for fields in events:
		if fields[0] == "fork":
			parent[process(fields[6], fields[7])] = process(fields[3], fields[4])

	def command_of(proc: Process) -> str | None:
		seen = set()
		while proc not in seen:
			if proc in owner:
				return owner[proc]
			seen.add(proc)
			if proc not in parent:
				return None
			proc = parent[proc]
		return None

	yield {"recorder": "bpftrace", "lost": lost, "events": len(events)}
	kept = set()
	for kind, mono, host_pid, nspid, start, path in (f for f in events if f[0] != "fork"):
		# Container pid 0: a host-side helper in the cgroup, such as runc for docker exec.
		if nspid == "0" or not path.startswith("/") or path.startswith(SKIPPED_PREFIXES):
			continue
		proc = process(nspid, start)
		cid = command_of(proc)
		key = (cid or proc, path, kind)
		if key in kept:
			continue
		kept.add(key)
		t = None if clock is None else (clock[0] + int(mono) - clock[1]) / 1e9
		yield {
			"t": t,
			"event_type": EVENT_TYPES[kind],
			"path": path,
			"command_id": cid,
			"pid": proc[0],
			"host_pid": int(host_pid),
		}
