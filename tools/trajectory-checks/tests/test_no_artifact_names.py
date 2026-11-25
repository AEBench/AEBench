"""Guard: no artifact (case) name appears in the tool's code or tests.

The matching layer must be artifact-agnostic (spec §1). Case ids are read
from the repository's ``cases/`` directory at test time, so this file holds
no names itself.
"""

import pathlib
import re

import pytest

TOOL_DIR = pathlib.Path(__file__).resolve().parents[1]
CASES_DIR = TOOL_DIR.parents[1] / "cases"
SCANNED_DIRS = (TOOL_DIR / "src", TOOL_DIR / "tests")
TEXT_SUFFIXES = frozenset(
    {".py", ".yaml", ".yml", ".json", ".jsonl", ".log", ".md", ".toml", ".txt"}
)
# A case id is <venue><year>_<artifact>, e.g. "abc24_name".
VENUE_PREFIX = re.compile(r"^[a-z]+[0-9]{2}_")


def _names() -> list[str]:
    names = set()
    for entry in CASES_DIR.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        names.add(entry.name.lower())
        artifact = VENUE_PREFIX.sub("", entry.name.lower())
        if artifact:
            names.add(artifact)
    return sorted(names)


def _files() -> list[pathlib.Path]:
    found = []
    for directory in SCANNED_DIRS:
        if directory.is_dir():
            found += [
                p
                for p in directory.rglob("*")
                if p.is_file()
                and p.suffix in TEXT_SUFFIXES
                and "__pycache__" not in p.parts
            ]
    return sorted(found)


def test_no_artifact_names() -> None:
    """No case id or artifact name occurs, word-bounded, case-insensitive."""
    if not CASES_DIR.is_dir():
        pytest.skip(f"no cases directory at {CASES_DIR}")
    names = _names()
    assert names, "cases directory holds no case"
    pattern = re.compile(
        r"(?<![a-z0-9])(" + "|".join(map(re.escape, names)) + r")(?![a-z0-9])",
        re.IGNORECASE,
    )
    hits = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for match in pattern.finditer(line):
                hits.append(
                    f"{path.relative_to(TOOL_DIR)}:{number}: {match.group(0)!r}"
                )
    assert hits == [], "\n".join(hits[:50])
