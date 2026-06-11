from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathCheck, PathKind

_LFS_POINTER_MAX_BYTES = 200

@dataclass(frozen=True, slots=True, kw_only=True)
class LFSFileResolvedCheck(utils.BaseCheck):
    """Fail if the file is a Git LFS pointer instead of real data."""
    path: Path
    min_size: int = _LFS_POINTER_MAX_BYTES

    def check(self) -> utils.CheckResult:
        if not self.path.is_file():
            return utils.CheckResult.failure(f"file missing: {self.path}")
        try:
            size = self.path.stat().st_size
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot stat {self.path}: {exc}")

        if size <= self.min_size:
            try:
                head = self.path.read_bytes()[:64]
            except OSError as exc:
                return utils.CheckResult.failure(f"Could not read {self.path.name}: {exc}")

            if head.startswith(b"version https://git-lfs.github.com"):
                return utils.CheckResult.failure(
                    f"{self.path.name} is a Git LFS pointer ({size} bytes). "
                    f"Run 'git lfs pull' to download the actual data."
                )
            return utils.CheckResult.failure(f"{self.path.name} is unexpectedly small ({size} bytes)")

        return utils.CheckResult.success(message=f"{self.path.name}: {size} bytes")

@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryGlobCountCheck(utils.BaseCheck):
    """Fail if fewer than min_count entries match the glob pattern."""
    directory: Path
    pattern: str
    min_count: int

    def check(self) -> utils.CheckResult:
        if not self.directory.is_dir():
            return utils.CheckResult.failure(f"directory missing: {self.directory}")
        try:
            matches = list(self.directory.glob(self.pattern))
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot scan {self.directory}: {exc}")

        if len(matches) < self.min_count:
            return utils.CheckResult.failure(
                f"found {len(matches)} entr(y/ies) matching {self.pattern!r} in "
                f"{self.directory}, expected at least {self.min_count}"
            )

        return utils.CheckResult.success(
            message=f"{len(matches)} entr(y/ies) matching {self.pattern!r} in {self.directory}"
        )

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.workspace_path()
        traces_dir = repo_root / "traces"

        checks: list[utils.BaseCheck] = [
            PathCheck(
                name="traces_root_dir",
                path=traces_dir,
                kind=PathKind.DIRECTORY,
            ),
        ]

        required_trace_subdirs = ["bert", "helr", "resnet"]
        for subdir in required_trace_subdirs:
            checks.append(
                DirectoryGlobCountCheck(
                    name=f"traces_{subdir}_populated",
                    directory=traces_dir / subdir,
                    pattern="*",
                    min_count=1,
                )
            )

        checks.append(
            LFSFileResolvedCheck(
                name="bert_trace_not_lfs_pointer",
                path=(traces_dir / "bert" / "bert-250reg-4ch")
            )
        )

        return tuple(checks)