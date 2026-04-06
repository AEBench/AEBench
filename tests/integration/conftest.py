"""Sets PYTHONPATH so subprocess oracle runners can import src/."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
_SRC_STR = str(_SRC)

existing = os.environ.get("PYTHONPATH", "")
entries = [e for e in existing.split(os.pathsep) if e]
if _SRC_STR not in entries:
	entries.insert(0, _SRC_STR)
os.environ["PYTHONPATH"] = os.pathsep.join(entries)
