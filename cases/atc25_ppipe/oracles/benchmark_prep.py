from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase

from .common import NonEmptyDirectoryCheck

_LFS_POINTER_MAX_BYTES = 200

_REQUIRED_MODEL_SUBDIRS = (
    "block-timing-tf32",
    "cuts-no-const",
    "model-profile-tf32",
    "node-profile-no-const",
    "shapes",
)

_REQUIRED_PLAN_DIRS = ("maf19", "maf21", "ablation")

_EXPECTED_MODELS = 18
_MIN_PREPARTITION_CSVS = 18
_MIN_PLAN_JSONS_PER_WORKLOAD = 20


@dataclass(frozen=True, slots=True, kw_only=True)
class LFSFileResolvedCheck(utils.BaseCheck):
    """Fail if the file is a Git LFS pointer instead of real data."""

    path: Path
    min_size: int = _LFS_POINTER_MAX_BYTES

    def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
        if not self.path.is_file():
            return utils.CheckResult.failure(f"file missing: {self.path}")

        try:
            size = self.path.stat().st_size
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot stat {self.path}: {exc}")

        if size <= self.min_size:
            try:
                head = self.path.read_bytes()[:64]
                if head.startswith(b"version https://git-lfs.github.com"):
                    return utils.CheckResult.failure(
                        f"{self.path.name} is a Git LFS pointer ({size} bytes). "
                        f"Run 'git lfs pull' to download the actual data."
                    )
            except OSError as exc:
                return utils.CheckResult.failure(
                    f"{self.path.name} is unexpectedly small ({size} bytes) "
                    f"and could not be read to check for a Git LFS pointer: {exc}"
                )
            return utils.CheckResult.failure(
                f"{self.path.name} is unexpectedly small ({size} bytes)"
            )

        return utils.CheckResult.success(
            message=f"{self.path.name}: {size} bytes"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelListCountCheck(utils.BaseCheck):
    """Fail if model_list.txt has fewer than expected entries."""

    path: Path
    expected_count: int

    def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
        if not self.path.is_file():
            return utils.CheckResult.failure(f"file missing: {self.path}")

        try:
            lines = [
                line.strip()
                for line in self.path.read_text("utf-8").splitlines()
                if line.strip()
            ]
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot read {self.path}: {exc}")

        if len(lines) < self.expected_count:
            return utils.CheckResult.failure(
                f"model_list.txt has {len(lines)} entries, expected {self.expected_count}"
            )

        return utils.CheckResult.success(
            message=f"model_list.txt has {len(lines)} model(s)"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryFileCountCheck(utils.BaseCheck):
    """Fail if fewer than min_count files match the glob pattern."""

    directory: Path
    pattern: str
    min_count: int

    def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
        if not self.directory.is_dir():
            return utils.CheckResult.failure(f"directory missing: {self.directory}")

        try:
            matches = list(self.directory.glob(self.pattern))
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot scan {self.directory}: {exc}")

        if len(matches) < self.min_count:
            return utils.CheckResult.failure(
                f"found {len(matches)} {self.pattern} file(s) in {self.directory.name}, "
                f"expected at least {self.min_count}"
            )

        return utils.CheckResult.success(
            message=f"{len(matches)} {self.pattern} file(s) in {self.directory.name}"
        )


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):

    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.paths.workspace_dir

        data_dir = repo_root / "data"
        checks: list[utils.BaseCheck] = []

        checks.append(
            ModelListCountCheck(
                name="model_list_count",
                path=data_dir / "model_list.txt",
                expected_count=_EXPECTED_MODELS,
            )
        )

        for subdir in _REQUIRED_MODEL_SUBDIRS:
            checks.append(
                NonEmptyDirectoryCheck(
                    name=f"models_{subdir}_populated",
                    path=data_dir / "models" / subdir,
                )
            )

        checks.append(
            LFSFileResolvedCheck(
                name="maf_trace_not_lfs_pointer",
                path=(
                    data_dir / "maf_traces" / "azure_functions_trace_2021"
                    / "AzureFunctionsInvocationTraceForTwoWeeksJan2021.txt"
                ),
            )
        )

        checks.append(
            DirectoryFileCountCheck(
                name="prepartition_mappings",
                directory=data_dir / "prepartition_mappings",
                pattern="*/*.csv",
                min_count=_MIN_PREPARTITION_CSVS,
            )
        )

        for plan_dir in _REQUIRED_PLAN_DIRS:
            checks.append(
                DirectoryFileCountCheck(
                    name=f"reference_plans_{plan_dir}",
                    directory=data_dir / "plans" / plan_dir,
                    pattern="*.json",
                    min_count=_MIN_PLAN_JSONS_PER_WORKLOAD,
                )
            )

        return tuple(checks)
