"""Public oracle check primitives."""

from __future__ import annotations

import dataclasses
import enum
import math
import os
import pathlib
import re
import shlex
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Generic, TypeVar

from constants import DEFAULT_ORACLE_CHECK_TIMEOUT

from .oracle_checks_runtime import (
	HostPath,
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_path_exists,
	check_path_is_dir,
	check_path_is_file,
	check_read_file_text,
	glob,
	path_from_user_input,
	read_check_env_var,
	resolve_check_executable,
	run_check_process_capture,
)
from .process import DEFAULT_MAX_CAPTURE_CHARS
from .reporting import BaseCheck, CheckResult

SemanticVersion = tuple[int, int, int]
_ResultT = TypeVar("_ResultT")

_EPSILON = 1e-12


class EnvMatchMode(enum.Enum):
	"""Supported environment-variable matching strategies."""

	EXACT = "exact"
	CONTAINS = "contains"
	REGEX = "regex"


class PathKind(enum.Enum):
	"""Filesystem object type required by a path check."""

	ANY = "any"
	FILE = "file"
	DIRECTORY = "directory"


class SimilarityMetric(enum.Enum):
	"""Supported aggregate similarity metrics."""

	JACCARD_SET = "jaccard_set"
	JACCARD_MULTISET = "jaccard_multiset"
	COSINE = "cosine"
	PEARSON = "pearson"
	MIN_MAX = "min_max"


def _validate_numeric_sequence_pair(
	observed: Sequence[float], reference: Sequence[float], *, label: str
) -> None:
	"""Validates a non-empty pair of equal-length numeric sequences."""
	if len(observed) != len(reference):
		raise ValueError(f"{label}: observed and reference must have the same length")
	if not observed:
		raise ValueError(f"{label}: observed and reference must be non-empty")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Comparison(Generic[_ResultT]):
	"""Stores one observed/reference comparison and its derived result."""

	observed: float
	reference: float
	result: _ResultT


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class VersionCheck(BaseCheck):
	"""Checks that an executable reports a version within allowed bounds.

	The command may print the version on either stdout or stderr. An optional
	regular expression can isolate the version portion of the output.
	"""

	cmd: Sequence[str]
	min_version: SemanticVersion | None = None
	max_version: SemanticVersion | None = None
	version_regex: str | None = None
	timeout_seconds: float = DEFAULT_ORACLE_CHECK_TIMEOUT
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	_compiled_version_regex: re.Pattern[str] | None = dataclasses.field(
		init=False, repr=False, default=None
	)

	def __post_init__(self) -> None:
		if not self.cmd:
			raise ValueError(f"{self.name}: cmd must be non-empty")
		if self.timeout_seconds <= 0:
			raise ValueError(f"{self.name}: timeout_seconds must be > 0")

		# Normalize caller-provided sequences
		object.__setattr__(self, "cmd", tuple(self.cmd))
		object.__setattr__(
			self, "min_version", self._validate_version(self.min_version, "min_version")
		)
		object.__setattr__(
			self, "max_version", self._validate_version(self.max_version, "max_version")
		)

		if self.min_version is None and self.max_version is None:
			raise ValueError(f"{self.name}: provide at least one of min_version or max_version")
		if (
			self.min_version is not None
			and self.max_version is not None
			and self.min_version > self.max_version
		):
			raise ValueError(f"{self.name}: min_version must be <= max_version")
		if self.version_regex is not None:
			object.__setattr__(
				self, "_compiled_version_regex", self._compile_regex(self.version_regex)
			)

	def _validate_version(
		self, value: SemanticVersion | None, field_name: str
	) -> SemanticVersion | None:
		"""Validates and normalizes a three-part semantic version."""
		if value is None:
			return None
		try:
			major, minor, patch = value
		except (TypeError, ValueError):
			raise ValueError(f"{self.name}: {field_name} must be a 3-part version tuple") from None
		if not all(isinstance(part, int) for part in (major, minor, patch)):
			raise ValueError(f"{self.name}: {field_name} must be a 3-part version tuple")
		return major, minor, patch

	def _compile_regex(self, pattern: str) -> re.Pattern[str]:
		"""Compiles a case-insensitive version extraction pattern."""
		try:
			return re.compile(pattern, flags=re.IGNORECASE)
		except re.error as exc:
			raise ValueError(f"{self.name}: invalid version_regex: {exc}") from exc

	def _parse_version(self, text: str) -> SemanticVersion | None:
		"""Parses a <major.minor> or <major.minor.patch> version."""
		match = re.search(r"(?:^|\s)v?(\d+)\.(\d+)(?:\.(\d+))?", text)
		if match is None:
			return None
		patch = 0 if match.group(3) is None else int(match.group(3))
		return int(match.group(1)), int(match.group(2)), patch

	def _format_version(self, version: SemanticVersion) -> str:
		"""Formats a semantic version for diagnostics."""
		return ".".join(str(part) for part in version)

	def _format_requirement(self) -> str:
		"""Returns the configured version range for diagnostics."""
		if self.min_version is not None and self.max_version is not None:
			if self.min_version == self.max_version:
				return f"== {self._format_version(self.min_version)}"
			return f">= {self._format_version(self.min_version)} and <= {self._format_version(self.max_version)}"
		if self.min_version is not None:
			return f">= {self._format_version(self.min_version)}"
		assert self.max_version is not None
		return f"<= {self._format_version(self.max_version)}"

	def check(self) -> CheckResult:
		executable = self.cmd[0]
		try:
			resolved = resolve_check_executable(executable, executor=self.executor)
		except (RuntimeError, ValueError) as exc:
			return CheckResult.failure(str(exc))
		if resolved is None:
			return CheckResult.failure(f"{executable!r} was not found on PATH")

		try:
			proc = run_check_process_capture(
				cmd=(resolved, *self.cmd[1:]),
				cwd=None,
				env=None,
				timeout_seconds=self.timeout_seconds,
				capture_limit_chars=DEFAULT_MAX_CAPTURE_CHARS,
				executor=self.executor,
			)
		except (OSError, RuntimeError) as exc:
			return CheckResult.failure(f"failed to run {executable!r}: {exc}", stderr=str(exc))

		if proc.timed_out:
			return CheckResult.failure(
				f"{executable!r} timed out after {self.timeout_seconds}s",
				stdout=proc.stdout,
				stderr=proc.stderr,
				timed_out=True,
			)
		if proc.returncode != 0:
			detail = (
				"\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
				or f"rc={proc.returncode}"
			)
			return CheckResult.failure(
				f"{executable!r} version check failed: {detail}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
			)

		version_text = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()

		# Isolate the version using a regex
		if self._compiled_version_regex is not None:
			match = self._compiled_version_regex.search(version_text)
			if match is None:
				return CheckResult.failure(
					"version_regex did not match command output",
					stdout=proc.stdout,
					stderr=proc.stderr,
					returncode=proc.returncode,
				)
			if match.lastindex not in (None, 1):
				return CheckResult.failure(
					"version_regex must contain at most one capture group",
					stdout=proc.stdout,
					stderr=proc.stderr,
					returncode=proc.returncode,
				)
			version_text = match.group(1) if match.lastindex == 1 else match.group(0)

		found_version = self._parse_version(version_text)
		if found_version is None:
			return CheckResult.failure(
				f"could not parse a version from {executable!r} output",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
			)
		if self.min_version is not None and found_version < self.min_version:
			return CheckResult.failure(
				f"{executable!r} version {self._format_version(found_version)} does not satisfy {self._format_requirement()}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
			)
		if self.max_version is not None and found_version > self.max_version:
			return CheckResult.failure(
				f"{executable!r} version {self._format_version(found_version)} does not satisfy {self._format_requirement()}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
			)
		return CheckResult.success(
			stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode
		)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EnvVarCheck(BaseCheck):
	"""Checks an environment variable through the configured runtime."""

	env_var: str
	expected: str
	match_mode: EnvMatchMode = EnvMatchMode.EXACT
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	_pattern: re.Pattern[str] | None = dataclasses.field(init=False, repr=False, default=None)

	def __post_init__(self) -> None:
		if not self.env_var:
			raise ValueError(f"{self.name}: env_var cannot be empty")
		if self.match_mode == EnvMatchMode.CONTAINS and not self.expected:
			raise ValueError(f"{self.name}: expected cannot be empty for contains mode")
		if self.match_mode == EnvMatchMode.REGEX:
			try:
				pattern = re.compile(self.expected)
			except re.error as exc:
				raise ValueError(f"{self.name}: invalid regex: {exc}") from exc
			object.__setattr__(self, "_pattern", pattern)

	def check(self) -> CheckResult:
		try:
			actual = read_check_env_var(self.env_var, executor=self.executor)
		except (RuntimeError, ValueError) as exc:
			return CheckResult.failure(str(exc))
		if actual is None:
			return CheckResult.failure(f"{self.env_var} is not set")
		if self.match_mode == EnvMatchMode.EXACT:
			if actual == self.expected:
				return CheckResult.success()
			return CheckResult.failure(f"{self.env_var} expected {self.expected!r}, got {actual!r}")
		if self.match_mode == EnvMatchMode.CONTAINS:
			if self.expected in actual:
				return CheckResult.success()
			return CheckResult.failure(f"{self.env_var} does not contain {self.expected!r}")
		assert self._pattern is not None
		if self._pattern.search(actual):
			return CheckResult.success()
		return CheckResult.failure(f"{self.env_var} does not match regex {self.expected!r}")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PathCheck(BaseCheck):
	"""Checks that a runtime-visible path exists with the required type."""

	path: OraclePath
	kind: PathKind = PathKind.ANY
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None,
		repr=False,
		compare=False,
	)

	def __post_init__(self) -> None:
		if isinstance(self.path, RuntimePath):
			return

		path_text = os.fspath(self.path).strip()
		if not path_text:
			raise ValueError(f"{self.name}: path cannot be empty")

		object.__setattr__(
			self,
			"path",
			pathlib.Path(path_text),
		)

	def check(self) -> CheckResult:
		path = self.path
		path_text = str(path)

		if not check_path_exists(path, executor=self.executor):
			label = "path"
			if self.kind == PathKind.FILE:
				label = "file"
			elif self.kind == PathKind.DIRECTORY:
				label = "directory"
			return CheckResult.failure(f"{label} not found: {path_text}")

		if self.kind == PathKind.ANY:
			return CheckResult.success()

		if self.kind == PathKind.FILE:
			if check_path_is_file(path, executor=self.executor):
				return CheckResult.success()
			return CheckResult.failure(f"expected a file: {path_text}")

		if check_path_is_dir(path, executor=self.executor):
			return CheckResult.success()

		return CheckResult.failure(f"expected a directory: {path_text}")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionEvidenceFileCheck(BaseCheck):
	"""Checks that a text file records evidence from artifact execution."""

	path: OraclePath
	min_size_bytes: int = 1
	required_text: str | None = None
	required_regex: str | None = None
	modified_after: datetime | None = None
	modified_after_required: bool = False
	encoding: str = "utf-8"
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None,
		repr=False,
		compare=False,
	)

	_pattern: re.Pattern[str] | None = dataclasses.field(init=False, repr=False, default=None)

	def __post_init__(self) -> None:
		if isinstance(self.path, RuntimePath):
			pass
		else:
			path_text = os.fspath(self.path).strip()
			if not path_text:
				raise ValueError(f"{self.name}: path cannot be empty")
			object.__setattr__(self, "path", pathlib.Path(path_text))

		if self.min_size_bytes < 0:
			raise ValueError(f"{self.name}: min_size_bytes must be >= 0")
		if self.required_text is not None and not self.required_text:
			raise ValueError(f"{self.name}: required_text cannot be empty")
		if self.required_regex is not None:
			if not self.required_regex:
				raise ValueError(f"{self.name}: required_regex cannot be empty")
			try:
				pattern = re.compile(self.required_regex)
			except re.error as exc:
				raise ValueError(f"{self.name}: invalid required_regex: {exc}") from exc
			object.__setattr__(self, "_pattern", pattern)
		if not self.encoding:
			raise ValueError(f"{self.name}: encoding cannot be empty")

	def _host_path_for_mtime(self) -> pathlib.Path | None:
		"""Returns a host-visible path for mtime checks, when available."""
		if isinstance(self.path, RuntimePath):
			return None

		host_path = pathlib.Path(self.path).expanduser()
		if host_path.is_absolute():
			return host_path.resolve(strict=False)

		if self.executor is None:
			return host_path.resolve(strict=False)

		resolved = self.executor.resolve_path(self.path)
		if isinstance(resolved, pathlib.Path):
			return resolved

		return None

	def _check_mtime(self) -> CheckResult | None:
		if self.modified_after is None:
			if self.modified_after_required:
				return CheckResult.failure(
					"mtime threshold unavailable for evidence freshness check"
				)
			return None

		host_path = self._host_path_for_mtime()
		if host_path is None:
			return CheckResult.failure(
				f"mtime check requires a host-visible evidence path: {self.path}"
			)

		try:
			modified_at = datetime.fromtimestamp(
				host_path.stat().st_mtime,
				tz=self.modified_after.tzinfo,
			)
		except OSError as exc:
			return CheckResult.failure(f"failed to stat evidence file: {exc}")

		if modified_at <= self.modified_after:
			return CheckResult.failure(
				f"evidence file is older than required threshold: {self.path}"
			)

		return None

	def check(self) -> CheckResult:
		path = self.path
		path_text = str(path)

		if not check_path_exists(path, executor=self.executor):
			return CheckResult.failure(f"evidence file not found: {path_text}")
		if not check_path_is_file(path, executor=self.executor):
			return CheckResult.failure(f"evidence path is not a file: {path_text}")

		try:
			text = check_read_file_text(path, encoding=self.encoding, executor=self.executor)
			size_bytes = len(text.encode(self.encoding))
		except (OSError, UnicodeError, LookupError) as exc:
			return CheckResult.failure(f"failed to read evidence file: {exc}")

		if size_bytes < self.min_size_bytes:
			return CheckResult.failure(
				f"evidence file too small: {path_text} has {size_bytes} byte(s), "
				f"expected at least {self.min_size_bytes}"
			)
		if self.required_text is not None and self.required_text not in text:
			return CheckResult.failure(
				f"required evidence text not found in {path_text}: {self.required_text!r}"
			)
		if self._pattern is not None and self._pattern.search(text) is None:
			return CheckResult.failure(
				f"required evidence regex did not match {path_text}: {self.required_regex!r}"
			)

		mtime_result = self._check_mtime()
		if mtime_result is not None:
			return mtime_result

		return CheckResult.success(f"evidence file satisfied requirements: {path_text}")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CommandCheck(BaseCheck):
	"""Runs a command and optionally requires an output signature.

	Commands execute through the configured runtime executor. Output is
	streamed while the process runs so signatures can be detected even when
	the saved output is truncated.
	"""

	cmd: str | Sequence[str]
	cwd: HostPath | None = None
	timeout_seconds: float = DEFAULT_ORACLE_CHECK_TIMEOUT
	env: Mapping[str, str] = dataclasses.field(default_factory=dict)
	use_shell: bool = False
	signature: str | None = None
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	def __post_init__(self) -> None:
		if self.timeout_seconds <= 0:
			raise ValueError(f"{self.name}: timeout_seconds must be > 0")
		if isinstance(self.cmd, (list, tuple)):
			if not self.cmd:
				raise ValueError(f"{self.name}: cmd must be non-empty")
			bad_args = [arg for arg in self.cmd if not isinstance(arg, str) or not arg]
			if bad_args:
				raise TypeError(f"{self.name}: all argv entries must be non-empty strings")
			object.__setattr__(self, "cmd", tuple(self.cmd))
		elif isinstance(self.cmd, str):
			if not self.cmd.strip():
				raise ValueError(f"{self.name}: cmd must be non-empty")
			if not self.use_shell:
				raise ValueError(f"{self.name}: string cmd requires use_shell=True")
		else:
			raise TypeError(f"{self.name}: cmd must be a string or sequence of strings")

		clean_env: dict[str, str] = {}
		for key, value in dict(self.env).items():
			if not isinstance(key, str) or not key:
				raise TypeError(f"{self.name}: env contains an empty variable name")
			clean_env[key] = str(value)
		object.__setattr__(self, "env", clean_env)
		if self.cwd is not None:
			object.__setattr__(self, "cwd", path_from_user_input(self.cwd))
		if self.signature is not None and not self.signature.strip():
			object.__setattr__(self, "signature", None)

	def _cwd(self) -> pathlib.Path | None:
		return None if self.cwd is None else pathlib.Path(self.cwd)

	def _display_cmd(self) -> str:
		if isinstance(self.cmd, str):
			return self.cmd
		return " ".join(shlex.quote(arg) for arg in self.cmd)

	def check(self) -> CheckResult:
		cwd = self._cwd()
		if cwd is not None:
			if not check_path_exists(cwd, executor=self.executor):
				return CheckResult.failure(f"working directory not found: {cwd}", cwd=cwd)
			if not check_path_is_dir(cwd, executor=self.executor):
				return CheckResult.failure(f"working directory is not a directory: {cwd}", cwd=cwd)

		signature = self.signature
		stdout_seen = signature is None
		stderr_seen = signature is None

		# Retain enough trailing text to detect a given pattern/signature
		carry_len = 0 if signature is None else max(len(signature) - 1, 0)
		stdout_tail = ""
		stderr_tail = ""

		def on_chunk(stream_name: str, text: str) -> None:
			"""Searches streamed output for the configured signature."""
			nonlocal stdout_seen, stderr_seen, stdout_tail, stderr_tail
			if signature is None:
				return
			if stream_name == "stdout" and not stdout_seen:
				haystack = stdout_tail + text
				stdout_seen = signature in haystack
				stdout_tail = haystack[-carry_len:] if carry_len else ""
			elif stream_name == "stderr" and not stderr_seen:
				haystack = stderr_tail + text
				stderr_seen = signature in haystack
				stderr_tail = haystack[-carry_len:] if carry_len else ""

		try:
			proc = run_check_process_capture(
				cmd=self.cmd,
				cwd=cwd,
				env=self.env or None,
				timeout_seconds=float(self.timeout_seconds),
				use_shell=self.use_shell,
				capture_limit_chars=DEFAULT_MAX_CAPTURE_CHARS,
				drain_after_kill=False,
				on_chunk=on_chunk,
				executor=self.executor,
			)
		except (OSError, RuntimeError) as exc:
			return CheckResult.failure(
				f"failed to run command: {self._display_cmd()}: {exc}",
				stderr=str(exc),
				cwd=cwd,
			)

		if proc.timed_out:
			return CheckResult.failure(
				f"command timed out after {self.timeout_seconds}s: {self._display_cmd()}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				timed_out=True,
				cwd=cwd,
			)
		if proc.returncode != 0:
			return CheckResult.failure(
				f"command failed (rc={proc.returncode}): {self._display_cmd()}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
				cwd=cwd,
			)
		if signature is not None and not (stdout_seen or stderr_seen):
			return CheckResult.failure(
				f"signature not found: {signature!r}: {self._display_cmd()}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
				cwd=cwd,
			)
		return CheckResult.success(
			stdout=proc.stdout,
			stderr=proc.stderr,
			returncode=proc.returncode,
			cwd=cwd,
		)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TextFileEqualityCheck(BaseCheck):
	"""Compares text files for exact equality."""

	observed_path: OraclePath
	reference_path: OraclePath
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	def check(self) -> CheckResult:
		observed_path = self.observed_path
		reference_path = self.reference_path
		if not check_path_is_file(observed_path, executor=self.executor):
			return CheckResult.failure(f"observed file missing: {observed_path}")
		if not check_path_is_file(reference_path, executor=self.executor):
			return CheckResult.failure(f"reference file missing: {reference_path}")
		try:
			observed = check_read_file_text(observed_path, executor=self.executor)
			reference = check_read_file_text(reference_path, executor=self.executor)
		except OSError as exc:
			return CheckResult.failure(f"failed to read file: {exc}")
		if observed == reference:
			return CheckResult.success("file contents match reference")

		# Add short mismatch diagnostics message
		preview_limit = 200
		observed_preview = observed[:preview_limit] + (
			"..." if len(observed) > preview_limit else ""
		)
		reference_preview = reference[:preview_limit] + (
			"..." if len(reference) > preview_limit else ""
		)
		return CheckResult.failure(
			f"content mismatch ({len(reference)} vs {len(observed)} chars): expected={reference_preview!r} observed={observed_preview!r}"
		)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MinMatchingEntryCountCheck(BaseCheck):
	"""Fail if fewer than min_count entries match the glob pattern."""

	directory: pathlib.Path
	pattern: str
	min_count: int = 1
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	def check(self) -> CheckResult:
		if not check_path_is_dir(self.directory, executor=self.executor):
			return CheckResult.failure(f"directory missing: {self.directory}")
		try:
			matches = glob(self.directory, self.pattern, executor=self.executor)
		except OSError as exc:
			return CheckResult.failure(f"cannot scan {self.directory}: {exc}")
		if len(matches) < self.min_count:
			return CheckResult.failure(
				f"found {len(matches)} entr(y/ies) matching {self.pattern!r} in "
				f"{self.directory}, expected at least {self.min_count}"
			)
		return CheckResult.success(
			message=f"{len(matches)} entr(y/ies) matching {self.pattern!r} in {self.directory}"
		)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ListSimilarityCheck(BaseCheck):
	"""Checks aggregate similarity between two numeric sequences."""

	observed: Sequence[float]
	reference: Sequence[float]
	metric: SimilarityMetric = SimilarityMetric.PEARSON
	min_similarity: float = 1.0

	def __post_init__(self) -> None:
		if not math.isfinite(self.min_similarity):
			raise ValueError(f"{self.name}: min_similarity must be finite")
		if self.metric in {
			SimilarityMetric.JACCARD_SET,
			SimilarityMetric.JACCARD_MULTISET,
			SimilarityMetric.MIN_MAX,
		}:
			if not 0.0 <= self.min_similarity <= 1.0:
				raise ValueError(
					f"{self.name}: {self.metric.value} min_similarity must be in [0, 1]"
				)
		if self.metric in {SimilarityMetric.COSINE, SimilarityMetric.PEARSON}:
			if not -1.0 <= self.min_similarity <= 1.0:
				raise ValueError(
					f"{self.name}: {self.metric.value} min_similarity must be in [-1, 1]"
				)
		object.__setattr__(self, "observed", tuple(self.observed))
		object.__setattr__(self, "reference", tuple(self.reference))

	def check(self) -> CheckResult:
		try:
			score = compute_similarity(self.metric, self.observed, self.reference)
		except ValueError as exc:
			return CheckResult.failure(f"{self.name}: {exc}")
		if score < self.min_similarity:
			return CheckResult.failure(
				f"{self.metric.value} similarity {score:.6f} < min_similarity {self.min_similarity:.6f}"
			)
		return CheckResult.success()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ElementwiseEqualityCheck(BaseCheck):
	"""Checks exact equality at each position in two numeric sequences."""

	observed: Sequence[float]
	reference: Sequence[float]
	max_mismatches_to_report: int = 10

	def __post_init__(self) -> None:
		if self.max_mismatches_to_report <= 0:
			raise ValueError(f"{self.name}: max_mismatches_to_report must be > 0")
		object.__setattr__(self, "observed", tuple(self.observed))
		object.__setattr__(self, "reference", tuple(self.reference))

	def check(self) -> CheckResult:
		try:
			comparisons = elementwise_equal(self.observed, self.reference)
		except ValueError as exc:
			return CheckResult.failure(f"{self.name}: {exc}")
		if all(comparison.result for comparison in comparisons):
			return CheckResult.success()
		detail = _summarize_boolean_mismatches(comparisons, max_items=self.max_mismatches_to_report)
		message = "elementwise equality check failed"
		if detail:
			message = f"{message}\n{detail}"
		return CheckResult.failure(message)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ElementwiseSimilarityThresholdCheck(BaseCheck):
	"""Checks that every element pair meets a similarity threshold."""

	observed: Sequence[float]
	reference: Sequence[float]
	threshold: float
	abs_epsilon: float = 1e-12
	max_mismatches_to_report: int = 10

	def __post_init__(self) -> None:
		if not math.isfinite(self.threshold):
			raise ValueError(f"{self.name}: threshold must be finite")
		if not 0.0 <= self.threshold <= 1.0:
			raise ValueError(f"{self.name}: threshold must be in [0, 1]")
		if self.abs_epsilon <= 0:
			raise ValueError(f"{self.name}: abs_epsilon must be > 0")
		if self.max_mismatches_to_report <= 0:
			raise ValueError(f"{self.name}: max_mismatches_to_report must be > 0")
		object.__setattr__(self, "observed", tuple(self.observed))
		object.__setattr__(self, "reference", tuple(self.reference))

	def check(self) -> CheckResult:
		try:
			scores = elementwise_similarity_scores(
				self.observed,
				self.reference,
				abs_epsilon=self.abs_epsilon,
			)
		except ValueError as exc:
			return CheckResult.failure(f"{self.name}: {exc}")
		if all(score.result >= self.threshold for score in scores):
			return CheckResult.success()
		detail = _summarize_threshold_mismatches(
			scores,
			threshold=self.threshold,
			max_items=self.max_mismatches_to_report,
		)
		message = f"elementwise similarity below threshold {self.threshold:.6f}"
		if detail:
			message = f"{message}\n{detail}"
		return CheckResult.failure(message)


def compute_similarity(
	metric: SimilarityMetric, left: Sequence[float], right: Sequence[float]
) -> float:
	"""Computes aggregate similarity using a given metric.

	Args:
		metric: Similarity metric to evaluate.
		left: First numeric sequence.
		right: Second numeric sequence.

	Returns:
		The computed similarity score.

	Raises:
		ValueError: If the metric is unsupported or the inputs do not satisfy
			the selected metric's requirements.
	"""
	if metric == SimilarityMetric.JACCARD_SET:
		return _jaccard_set_similarity(left, right)
	if metric == SimilarityMetric.JACCARD_MULTISET:
		return _jaccard_multiset_similarity(left, right)
	if metric == SimilarityMetric.COSINE:
		return _cosine_similarity(left, right)
	if metric == SimilarityMetric.PEARSON:
		return _pearson_similarity(left, right)
	if metric == SimilarityMetric.MIN_MAX:
		return _min_max_similarity(left, right)
	raise ValueError(f"unsupported similarity metric: {metric!r}")


def elementwise_equal(
	observed: Sequence[float], reference: Sequence[float]
) -> list[Comparison[bool]]:
	"""Compares two numeric sequences and checks if they are elementwise equal.

	Args:
		observed: Observed numeric values.
		reference: Expected numeric values.

	Returns:
		One comparison result per input position.

	Raises:
		ValueError: If the sequences are empty or have different lengths.
	"""
	_validate_numeric_sequence_pair(observed, reference, label="elementwise_equal")
	return [
		Comparison(observed=a, reference=b, result=a == b)
		for a, b in zip(observed, reference, strict=True)
	]


def elementwise_similarity_scores(
	observed: Sequence[float],
	reference: Sequence[float],
	*,
	similarity_fn: Callable[[float, float], float] | None = None,
	abs_epsilon: float = 1e-12,
) -> list[Comparison[float]]:
	"""Computes a similarity score for each pair of numeric values.

	Args:
		observed: Observed numeric values.
		reference: Expected numeric values.
		similarity_fn: Optional pairwise similarity function.
		abs_epsilon: Minimum denominator used by the default function.

	Returns:
		One similarity score per input position.

	Raises:
		ValueError: If the inputs are empty, differ in length, or use an
			invalid epsilon.
	"""
	_validate_numeric_sequence_pair(observed, reference, label="elementwise_similarity_scores")
	if abs_epsilon <= 0:
		raise ValueError("elementwise_similarity_scores: abs_epsilon must be > 0")
	if similarity_fn is None:

		def _default_similarity(a: float, b: float) -> float:
			return _default_numeric_similarity(a, b, abs_epsilon=abs_epsilon)

		similarity_fn = _default_similarity

	return [
		Comparison(observed=a, reference=b, result=similarity_fn(a, b))
		for a, b in zip(observed, reference, strict=True)
	]


def elementwise_similarity_threshold(
	observed: Sequence[float],
	reference: Sequence[float],
	*,
	threshold: float,
	similarity_fn: Callable[[float, float], float] | None = None,
	abs_epsilon: float = 1e-12,
) -> list[Comparison[bool]]:
	"""Checks pairwise similarity scores against a minimum threshold.

	Args:
		observed: Observed numeric values.
		reference: Expected numeric values.
		threshold: Inclusive minimum score in the range [0, 1].
		similarity_fn: Optional pairwise similarity function.
		abs_epsilon: Minimum denominator used by the default function.

	Returns:
		One threshold result per input position.

	Raises:
		ValueError: If the threshold, inputs, or epsilon are invalid.
	"""
	if not math.isfinite(threshold):
		raise ValueError("elementwise_similarity_threshold: threshold must be finite")
	if not 0.0 <= threshold <= 1.0:
		raise ValueError("elementwise_similarity_threshold: threshold must be in [0, 1]")
	return [
		Comparison(
			observed=score.observed, reference=score.reference, result=score.result >= threshold
		)
		for score in elementwise_similarity_scores(
			observed,
			reference,
			similarity_fn=similarity_fn,
			abs_epsilon=abs_epsilon,
		)
	]


def _require_equal_lengths(left: Sequence[float], right: Sequence[float], *, label: str) -> None:
	"""Requires two numeric sequences to have equal lengths."""
	if len(left) != len(right):
		raise ValueError(f"{label}: length mismatch: left has {len(left)}, right has {len(right)}")


def _require_all_finite(values: Sequence[float], *, label: str) -> None:
	"""Requires every numeric value to be finite."""
	for index, value in enumerate(values):
		if not math.isfinite(value):
			raise ValueError(f"{label}: non-finite value at index {index}: {value!r}")


def _jaccard_set_similarity(left: Sequence[float], right: Sequence[float]) -> float:
	"""Computes Jaccard similarity for duplicate-free sets of values."""
	_require_all_finite(left, label="jaccard_set_similarity.left")
	_require_all_finite(right, label="jaccard_set_similarity.right")

	# Reject duplicates rather than silently discarding multiplicity
	left_set = set(left)
	right_set = set(right)
	if len(left_set) != len(left):
		raise ValueError(
			"jaccard_set_similarity: left input contains duplicates; use jaccard_multiset"
		)
	if len(right_set) != len(right):
		raise ValueError(
			"jaccard_set_similarity: right input contains duplicates; use jaccard_multiset"
		)
	union = left_set | right_set
	if not union:
		# Two empty sets are identical
		return 1.0
	return len(left_set & right_set) / len(union)


def _jaccard_multiset_similarity(left: Sequence[float], right: Sequence[float]) -> float:
	"""Computes Jaccard similarity while preserving value multiplicity."""
	_require_all_finite(left, label="jaccard_multiset_similarity.left")
	_require_all_finite(right, label="jaccard_multiset_similarity.right")
	left_counter = Counter(left)
	right_counter = Counter(right)
	keys = set(left_counter) | set(right_counter)
	denominator = sum(max(left_counter[key], right_counter[key]) for key in keys)
	if denominator == 0:
		return 1.0
	numerator = sum(min(left_counter[key], right_counter[key]) for key in keys)
	return numerator / denominator


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
	"""Computes cosine similarity for equal-length numeric vectors."""
	_require_equal_lengths(left, right, label="cosine_similarity")
	_require_all_finite(left, label="cosine_similarity.left")
	_require_all_finite(right, label="cosine_similarity.right")
	dot = 0.0
	left_norm = 0.0
	right_norm = 0.0
	for a, b in zip(left, right, strict=True):
		dot += a * b
		left_norm += a * a
		right_norm += b * b

	# Two zero vectors as identical; a zero and nonzero vector are orthogonal
	if left_norm <= _EPSILON and right_norm <= _EPSILON:
		return 1.0
	if left_norm <= _EPSILON or right_norm <= _EPSILON:
		return 0.0
	return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def _pearson_similarity(left: Sequence[float], right: Sequence[float]) -> float:
	"""Computes Pearson correlation for equal-length numeric sequences."""
	_require_equal_lengths(left, right, label="pearson_similarity")
	if len(left) < 2:
		raise ValueError(f"pearson_similarity: need at least 2 samples, got {len(left)}")
	_require_all_finite(left, label="pearson_similarity.left")
	_require_all_finite(right, label="pearson_similarity.right")
	mean_left = sum(left) / len(left)
	mean_right = sum(right) / len(right)
	covariance = 0.0
	left_var = 0.0
	right_var = 0.0
	for a, b in zip(left, right, strict=True):
		left_delta = a - mean_left
		right_delta = b - mean_right
		covariance += left_delta * right_delta
		left_var += left_delta * left_delta
		right_var += right_delta * right_delta

	# Correlation is undefined for constant sequences
	if left_var <= _EPSILON and right_var <= _EPSILON:
		return 1.0 if tuple(left) == tuple(right) else 0.0
	if left_var <= _EPSILON or right_var <= _EPSILON:
		return 0.0
	return covariance / (math.sqrt(left_var) * math.sqrt(right_var))


def _min_max_similarity(left: Sequence[float], right: Sequence[float]) -> float:
	"""Computes min-max similarity for nonnegative numeric vectors."""
	_require_equal_lengths(left, right, label="min_max_similarity")
	_require_all_finite(left, label="min_max_similarity.left")
	_require_all_finite(right, label="min_max_similarity.right")
	numerator = 0.0
	denominator = 0.0
	for index, (a, b) in enumerate(zip(left, right, strict=True)):
		if a < 0.0 or b < 0.0:
			raise ValueError(
				f"min_max_similarity: negative value at index {index}: left={a!r}, right={b!r}"
			)
		numerator += min(a, b)
		denominator += max(a, b)
	if denominator == 0.0:
		return 1.0
	return numerator / denominator


def _default_numeric_similarity(a: float, b: float, *, abs_epsilon: float) -> float:
	"""Returns bounded similarity based on relative absolute difference."""
	if not math.isfinite(a) or not math.isfinite(b):
		raise ValueError(f"default_numeric_similarity: non-finite input: a={a!r}, b={b!r}")
	denominator = max(abs(a), abs(b), abs_epsilon)
	return max(0.0, min(1.0, 1.0 - (abs(a - b) / denominator)))


def _summarize_boolean_mismatches(
	comparisons: Sequence[Comparison[bool]], *, max_items: int
) -> str:
	"""Formats a bounded summary of failed equality comparisons."""
	lines: list[str] = []
	total_bad = 0
	for index, comparison in enumerate(comparisons):
		if comparison.result:
			continue
		total_bad += 1
		if len(lines) < max_items:
			lines.append(
				f"[{index}] observed={comparison.observed!r}, reference={comparison.reference!r}"
			)
	if not lines:
		return ""
	suffix = f"\n... ({total_bad - len(lines)} more)" if total_bad > len(lines) else ""
	return "mismatches:\n" + "\n".join(lines) + suffix


def _summarize_threshold_mismatches(
	comparisons: Sequence[Comparison[float]],
	*,
	threshold: float,
	max_items: int,
) -> str:
	"""Formats a bounded summary of scores below a threshold."""
	lines: list[str] = []
	total_bad = 0
	for index, comparison in enumerate(comparisons):
		if comparison.result >= threshold:
			continue
		total_bad += 1
		if len(lines) < max_items:
			lines.append(
				f"[{index}] score={comparison.result:.6f} observed={comparison.observed!r}, reference={comparison.reference!r}"
			)
	if not lines:
		return ""
	suffix = f"\n... ({total_bad - len(lines)} more)" if total_bad > len(lines) else ""
	return "mismatches:\n" + "\n".join(lines) + suffix
