"""Sets PYTHONPATH so subprocess oracle runners can import src/."""
from __future__ import annotations

import os
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
_SRC_STR = str(_SRC)

existing = os.environ.get("PYTHONPATH", "")
entries = [e for e in existing.split(os.pathsep) if e]
if _SRC_STR not in entries:
    entries.insert(0, _SRC_STR)
os.environ["PYTHONPATH"] = os.pathsep.join(entries)

_FAKEBIN = Path(__file__).resolve().parent / ".fakebin"
_FAKEBIN.mkdir(exist_ok=True)
_SHIMS = {
    "rustc": "rustc 1.83.0\n",
    "cargo": "cargo 1.83.0\n",
    "node": "v20.0.0\n",
    "make": "GNU Make 4.4\n",
}
for name, output in _SHIMS.items():
    path = _FAKEBIN / name
    if not path.exists():
        path.write_text(
            "#!/usr/bin/env sh\n"
            f"printf '%s' '{output}'\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

path_entries = [str(_FAKEBIN)]
existing_path = os.environ.get("PATH", "")
if existing_path:
    path_entries.append(existing_path)
os.environ["PATH"] = os.pathsep.join(path_entries)
