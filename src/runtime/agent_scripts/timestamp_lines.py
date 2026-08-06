#!/usr/bin/env python3
# Adapted from PostTrainBench's timestamp_lines.py:
# https://github.com/aisa-group/PostTrainBench/blob/main/src/utils/timestamp_lines.py
"""Prepend UTC timestamps to each line of stdin, flushing immediately.

Output format: [2026-04-03T14:05:32Z] <original line>

Designed to sit between an agent process and its log file so that
scaffolds which don't emit their own timestamps (Claude Code, Codex CLI)
still get wall-clock times in the raw trace.
"""

import sys
from datetime import datetime, timezone

for line in sys.stdin:
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	sys.stdout.write(f"[{ts}] {line}")
	sys.stdout.flush()
