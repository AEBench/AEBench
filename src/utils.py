from __future__ import annotations

import re


def safe_name(value: str | None, fallback: str = "unknown") -> str:
	raw = (value or fallback).strip() or fallback
	return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._").lower() or fallback
