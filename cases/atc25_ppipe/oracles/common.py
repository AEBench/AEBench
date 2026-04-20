from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils


def load_json_file(path: Path, *, label: str) -> object:
    """Read and parse a JSON file, raising ValueError on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{label}: failed to read {path}: {exc}") from exc

    text = text.strip()
    if not text:
        raise ValueError(f"{label}: empty JSON content at {path}")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: invalid JSON in {path}: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class NonEmptyDirectoryCheck(utils.BaseCheck):
    """Fail if the directory is missing or empty."""

    path: Path

    def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
        if not self.path.exists():
            return utils.CheckResult.failure(f"directory missing: {self.path}")
        if not self.path.is_dir():
            return utils.CheckResult.failure(f"not a directory: {self.path}")
        try:
            entries = list(self.path.iterdir())
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot list directory {self.path}: {exc}")
        if not entries:
            return utils.CheckResult.failure(f"directory is empty: {self.path}")
        return utils.CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class GlobFileExistsCheck(utils.BaseCheck):
    """Fail if no file matches the glob pattern."""

    directory: Path
    pattern: str

    def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
        if not self.directory.exists():
            return utils.CheckResult.failure(f"directory missing: {self.directory}")
        if not self.directory.is_dir():
            return utils.CheckResult.failure(f"not a directory: {self.directory}")
        matches = list(self.directory.glob(self.pattern))
        if not matches:
            return utils.CheckResult.failure(
                f"no files matching '{self.pattern}' in {self.directory}"
            )
        return utils.CheckResult.success()
