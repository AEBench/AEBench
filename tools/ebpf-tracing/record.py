#!/usr/bin/env python3
"""Records the file, exec and fork events of one Docker container.

Usage: record.py <container> <raw.tsv>

Runs file_events.bt in the recorder image (see Dockerfile), a privileged
container next to the traced one, filtered to the traced container's cgroup,
until SIGINT or SIGTERM. The first line of the output pairs the wall clock
with the monotonic clock bpftrace stamps events with.
"""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

IMAGE = "aebench-ebpf-recorder"
CGROUP_ROOT = Path("/sys/fs/cgroup")


def container_id(name: str) -> str:
	"""The full id of a container, from its name or a short id."""
	result = subprocess.run(
		["docker", "inspect", "--format", "{{.Id}}", name],
		capture_output=True,
		text=True,
		check=True,
	)
	return result.stdout.strip()


def cgroup_id(full_id: str, root: Path = CGROUP_ROOT) -> int:
	"""The cgroup v2 id of a container: the inode of its scope directory."""
	# Docker with the systemd cgroup driver; the cgroupfs driver uses docker/<id>.
	for scope in (root / "system.slice" / f"docker-{full_id}.scope", root / "docker" / full_id):
		if scope.is_dir():
			return scope.stat().st_ino
	raise FileNotFoundError(f"no cgroup for container {full_id}")


def clock_header() -> str:
	return f"clock\t{time.time_ns()}\t{time.monotonic_ns()}\n"


def command(cgroup: int) -> list[str]:
	"""docker run of the recorder; tracefs is mounted for the tracepoints."""
	return [
		*("docker", "run", "--rm", "--name", f"aebench-ebpf-{cgroup}"),
		*("--privileged", "--pid=host"),
		*("-v", "/sys/kernel/tracing:/sys/kernel/tracing"),
		*(IMAGE, str(cgroup)),
	]


def record(name: str, out: Path) -> int:
	cgroup = cgroup_id(container_id(name))
	with out.open("w", encoding="utf-8") as handle:
		handle.write(clock_header())
		handle.flush()
		process = subprocess.Popen(
			command(cgroup), stdout=handle, stderr=subprocess.PIPE, text=True
		)

		def stop(_signum: int, _frame: object) -> None:
			# bpftrace flushes and reports lost events on SIGINT.
			process.send_signal(signal.SIGINT)

		signal.signal(signal.SIGINT, stop)
		signal.signal(signal.SIGTERM, stop)
		_, errors = process.communicate()
	if process.returncode not in (0, -signal.SIGINT):
		sys.stderr.write(errors)
	return process.returncode


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("container")
	parser.add_argument("out", type=Path)
	args = parser.parse_args()
	return record(args.container, args.out)


if __name__ == "__main__":
	sys.exit(main())
