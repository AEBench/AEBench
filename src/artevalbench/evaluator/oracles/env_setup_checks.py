"""Env setup oracle checks: version, env-var, path."""

from __future__ import annotations

import abc
import dataclasses
import enum
import os
import pathlib
import re
import shutil

from ..constants import DEFAULT_ORACLE_CHECK_TIMEOUT
import subprocess

from collections.abc import Sequence

from . import utils

_MAX_REGEX_LEN = 1024


def _safe_compile(pattern: str, name: str, flags: int = 0) -> re.Pattern[str]:
    if len(pattern) > _MAX_REGEX_LEN:
        raise ValueError(f"{name}: regex pattern exceeds {_MAX_REGEX_LEN} characters")
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"{name}: invalid regex pattern: {exc}") from exc


SemanticVersion = tuple[int, int, int]


class VersionCompare(enum.Enum):

    EQ = "eq"
    GEQ = "geq"
    LEQ = "leq"


class EnvMatchMode(enum.Enum):

    EXACT = "exact"
    CONTAINS = "contains"
    REGEX = "regex"


class PathEntryMatchMode(enum.Enum):

    EXACT = "exact"
    REGEX = "regex"


class PathType(enum.Enum):

    ANY = "any"
    FILE = "file"
    DIRECTORY = "directory"


_VERSION_RE = re.compile(r"(?:^|\s)v?(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_semantic_version(text: str) -> SemanticVersion | None:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3)) if match.group(3) is not None else 0
    return (major, minor, patch)


def _normalize_path_entry(entry: str) -> str:
    return os.path.normcase(os.path.normpath(entry.strip()))


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DependencyVersionCheck(utils.BaseCheck):
    """Check executable version on PATH."""

    cmd: Sequence[str]
    required_version: SemanticVersion
    compare: VersionCompare = VersionCompare.GEQ
    version_regex: str | None = None
    timeout_seconds: float = DEFAULT_ORACLE_CHECK_TIMEOUT

    _version_pattern: re.Pattern[str] | None = dataclasses.field(
        init=False,
        repr=False,
        default=None,
    )

    def __post_init__(self) -> None:
        if not self.cmd:
            raise ValueError(f"{self.name}: command must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError(f"{self.name}: timeout_seconds must be > 0")
        object.__setattr__(self, "cmd", tuple(self.cmd))

        if self.version_regex is not None:
            pattern = _safe_compile(self.version_regex, self.name, flags=re.IGNORECASE)
            if pattern.groups < 1:
                raise ValueError(
                    f"{self.name}: version_regex must contain a capturing group"
                )
            object.__setattr__(self, "_version_pattern", pattern)

    def check(self) -> utils.CheckResult:
        executable = self.cmd[0]
        resolved = shutil.which(executable)
        if resolved is None:
            return utils.CheckResult.failure(f"not found on PATH: {executable!r}")

        try:
            proc = subprocess.run(
                (resolved, *self.cmd[1:]),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            stdout = utils.decode_text(proc.stdout)
            stderr = utils.decode_text(proc.stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = utils.decode_text(exc.stdout)
            stderr = utils.decode_text(exc.stderr)
            return utils.CheckResult.failure(
                f"version command timed out after {self.timeout_seconds}s",
                stdout=stdout,
                stderr=stderr,
                returncode=None,
                timed_out=True,
                cwd=None,
            )
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to run {executable!r}: {exc}",
                stdout="",
                stderr=str(exc),
                returncode=None,
                timed_out=False,
                cwd=None,
            )

        combined = (stdout + "\n" + stderr).strip()

        if proc.returncode != 0:
            detail = combined if combined else f"rc = {proc.returncode}"
            return utils.CheckResult.failure(
                f"version command failed: {detail}",
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
                timed_out=False,
                cwd=None,
            )

        candidate = combined
        if self._version_pattern is not None:
            re_match = self._version_pattern.search(candidate)
            if not re_match:
                return utils.CheckResult.failure(
                    "version_regex did not match output",
                    stdout=stdout,
                    stderr=stderr,
                    returncode=proc.returncode,
                )
            candidate = re_match.group(1)

        found = _parse_semantic_version(candidate)
        if found is None:
            return utils.CheckResult.failure(
                "could not parse version from output",
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
            )

        if self.compare == VersionCompare.EQ:
            ok = found == self.required_version
            op = "=="
        elif self.compare == VersionCompare.GEQ:
            ok = found >= self.required_version
            op = ">="
        else:
            ok = found <= self.required_version
            op = "<="

        if not ok:
            return utils.CheckResult.failure(
                f"version {'.'.join(map(str, found))} does not satisfy "
                f"{op} {'.'.join(map(str, self.required_version))}",
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode,
            )
        return utils.CheckResult.success(
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            cwd=None,
        )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentVariableCheck(utils.BaseCheck):

    env_var: str
    expected: str
    match_mode: EnvMatchMode = EnvMatchMode.EXACT

    _expected_pattern: re.Pattern[str] | None = dataclasses.field(
        init=False,
        repr=False,
        default=None,
    )

    def __post_init__(self) -> None:
        if not self.env_var:
            raise ValueError(f"{self.name}: env_var must be non-empty")

        if self.match_mode in (EnvMatchMode.CONTAINS, EnvMatchMode.REGEX) and not self.expected:
            raise ValueError(f"{self.name}: expected must be non-empty")

        if self.match_mode == EnvMatchMode.REGEX:
            object.__setattr__(self, "_expected_pattern", _safe_compile(self.expected, self.name))

    def check(self) -> utils.CheckResult:
        actual = os.environ.get(self.env_var)
        if actual is None:
            return utils.CheckResult.failure("not set")

        if self.match_mode == EnvMatchMode.EXACT:
            if actual == self.expected:
                return utils.CheckResult.success()
            return utils.CheckResult.failure(
                f"expected {self.expected!r}, got {actual!r}"
            )

        if self.match_mode == EnvMatchMode.CONTAINS:
            if self.expected in actual:
                return utils.CheckResult.success()
            return utils.CheckResult.failure(
                f"expected substring {self.expected!r} was not found"
            )

        assert self._expected_pattern is not None
        if self._expected_pattern.search(actual):
            return utils.CheckResult.success()
        return utils.CheckResult.failure(
            f"value does not match regex {self.expected!r}"
        )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentPathEntryCheck(utils.BaseCheck):

    env_var: str
    expected: str
    match_mode: PathEntryMatchMode = PathEntryMatchMode.EXACT

    _expected_pattern: re.Pattern[str] | None = dataclasses.field(
        init=False,
        repr=False,
        default=None,
    )

    def __post_init__(self) -> None:
        if not self.env_var:
            raise ValueError(f"{self.name}: env_var must be non-empty")
        if not self.expected:
            raise ValueError(f"{self.name}: expected must be non-empty")

        if self.match_mode == PathEntryMatchMode.REGEX:
            object.__setattr__(self, "_expected_pattern", _safe_compile(self.expected, self.name))

    def check(self) -> utils.CheckResult:
        actual = os.environ.get(self.env_var)
        if actual is None:
            return utils.CheckResult.failure("not set")

        entries = [entry.strip() for entry in actual.split(os.pathsep) if entry.strip()]
        if self.match_mode == PathEntryMatchMode.EXACT:
            want = _normalize_path_entry(self.expected)
            normalized = [_normalize_path_entry(entry) for entry in entries]
            if want in normalized:
                return utils.CheckResult.success()
            return utils.CheckResult.failure(f"missing entry {self.expected!r}")

        assert self._expected_pattern is not None
        if any(self._expected_pattern.search(entry) for entry in entries):
            return utils.CheckResult.success()
        return utils.CheckResult.failure(
            f"no entry matches regex {self.expected!r}"
        )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class FilesystemPathCheck(utils.BaseCheck):

    path: pathlib.Path | str | os.PathLike[str]
    path_type: PathType = PathType.ANY

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", pathlib.Path(self.path))
        if str(self.path).strip() == "":
            raise ValueError(f"{self.name}: path must be non-empty")

    def check(self) -> utils.CheckResult:
        if not self.path.exists():
            if self.path_type == PathType.FILE:
                return utils.CheckResult.failure(f"file missing: {self.path}")
            if self.path_type == PathType.DIRECTORY:
                return utils.CheckResult.failure(f"directory missing: {self.path}")
            return utils.CheckResult.failure(f"path missing: {self.path}")

        if self.path_type == PathType.ANY:
            return utils.CheckResult.success()

        if self.path_type == PathType.FILE:
            if self.path.is_file():
                return utils.CheckResult.success()
            return utils.CheckResult.failure(f"expected file: {self.path}")

        if self.path.is_dir():
            return utils.CheckResult.success()
        return utils.CheckResult.failure(f"expected directory: {self.path}")


class OracleEnvSetupBase(utils._OraclePhaseBase):
    """Base for env setup oracle phases."""

    phase_label = "EnvironmentSetup"

    @abc.abstractmethod
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError