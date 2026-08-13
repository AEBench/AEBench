#!/usr/bin/env python3
# Adapted from PostTrainBench's timestamp_lines.py:
# https://github.com/aisa-group/PostTrainBench/blob/main/src/utils/timestamp_lines.py
"""Add a UTC timestamp to each line from standard input.

Output format: [2026-04-03T14:05:32Z] <original line>

This script records the time of each output line from Codex or Claude Code.
"""

import sys
from datetime import datetime, timezone

for line in sys.stdin:
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	sys.stdout.write(f"[{ts}] {line}")
	sys.stdout.flush()
