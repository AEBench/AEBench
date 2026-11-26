#!/usr/bin/env python3
"""Links recorded file events to the commands of a monitored run.

Usage: link.py <raw.tsv> <commands.jsonl> <file_events.jsonl>

`aebench case monitor` does this itself; this re-links a kept raw file.
The logic lives in src/runtime/file_events.py.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from runtime.file_events import link_files  # noqa: E402


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("raw", type=Path)
	parser.add_argument("commands", type=Path)
	parser.add_argument("out", type=Path)
	args = parser.parse_args()
	log = args.raw.with_name("file_events.recorder.log")
	info = link_files(args.raw, args.commands, args.out, extra=[log])
	print(f"{info.events} events ({info.lost} lost) in {info.path}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
