#!/usr/bin/env python3
"""Records the file, exec and fork events of one Docker container until Ctrl-C.

Usage: record.py <container> <raw.tsv>

`aebench case monitor` does this itself; this records any other container.
The recorder image is built on first use; see src/runtime/file_events.py.
"""

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from runtime.file_events import FileEventsRecorder  # noqa: E402


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("container")
	parser.add_argument("out", type=Path)
	args = parser.parse_args()
	recorder = FileEventsRecorder(args.out)
	# SIGINT ends the wait below; the recorder stops itself in the finally.
	signal.signal(signal.SIGTERM, signal.default_int_handler)
	try:
		recorder.start(args.container)
		print(f"recording {args.container} into {args.out}; Ctrl-C to stop", file=sys.stderr)
		signal.pause()
	except KeyboardInterrupt:
		pass
	finally:
		recorder.stop()
	return 0


if __name__ == "__main__":
	sys.exit(main())
