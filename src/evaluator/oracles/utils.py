"""Shared oracle infrastructure: checks, executors, subprocess helpers, and reporting."""

from __future__ import annotations

import abc
import codecs
import dataclasses
import enum
import locale
import logging
import os
import pathlib
import selectors
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any, Protocol, cast, runtime_checkable

from models import OracleInput, RuntimeMode
from runtime.backend import BenchRuntime, validate_env_var_name


from .process import (
	DEFAULT_MAX_CAPTURE_CHARS,
	ProcResult,
	decode_text,
	run_subprocess_capture,
	truncate_text,
)

from .executors import (
	DockerRuntimeCheckExecutor,
	LocalRuntimeCheckExecutor,
	PathLike,
	RuntimeCheckExecutor,
	SessionRuntimeCheckExecutor,
	UnavailableRuntimeCheckExecutor,
	build_path_mounts,
	build_runtime_check_executor,
	check_path_exists,
	check_path_is_dir,
	check_path_is_file,
	check_read_file_text,
	get_check_path_separator,
	path_from_user_input,
	read_check_env_var,
	resolve_check_executable,
	run_check_process_capture,
)

DEFAULT_MAX_TRUNCATED_MESSAGE_CHARS = 2_048


class CheckOutcome(enum.Enum):
	PASSED = "passed"
	FAILED = "failed"
	WARNING = "warning"


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:
	ok: bool
	message: str
	stdout: str = ""
	stderr: str = ""
	returncode: int | None = None
	timed_out: bool = False
	cwd: pathlib.Path | None = None

	@classmethod
	def success(
		cls,
		message: str = "",
		*,
		stdout: str = "",
		stderr: str = "",
		returncode: int | None = None,
		timed_out: bool = False,
		cwd: pathlib.Path | None = None,
	) -> CheckResult:
		return cls(
			ok=True,
			message=message,
			stdout=stdout,
			stderr=stderr,
			returncode=returncode,
			timed_out=timed_out,
			cwd=cwd,
		)

	@classmethod
	def failure(
		cls,
		message: str,
		*,
		stdout: str = "",
		stderr: str = "",
		returncode: int | None = None,
		timed_out: bool = False,
		cwd: pathlib.Path | None = None,
	) -> CheckResult:
		return cls(
			ok=False,
			message=message,
			stdout=stdout,
			stderr=stderr,
			returncode=returncode,
			timed_out=timed_out,
			cwd=cwd,
		)


@runtime_checkable
class Checkable(Protocol):
	@property
	def name(self) -> str:
		raise NotImplementedError

	@property
	def optional(self) -> bool:
		raise NotImplementedError

	def check(self) -> CheckResult:
		raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True)
class CheckEntry:
	name: str
	outcome: CheckOutcome
	message: str
	optional: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class OracleReport:
	results: tuple[CheckEntry, ...]

	@property
	def ok(self) -> bool:
		return all(
			entry.outcome != CheckOutcome.FAILED for entry in self.results if not entry.optional
		)

	@property
	def passed_count(self) -> int:
		return sum(1 for entry in self.results if entry.outcome == CheckOutcome.PASSED)

	@property
	def failed_count(self) -> int:
		return sum(1 for entry in self.results if entry.outcome == CheckOutcome.FAILED)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseCheck(Checkable, abc.ABC):
	name: str
	optional: bool = False

	@abc.abstractmethod
	def check(self) -> CheckResult:
		raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Check(BaseCheck):
	fn: Callable[[], CheckResult]

	def check(self) -> CheckResult:
		return self.fn()


def run_checks(checks: Sequence[Checkable], *, logger: logging.Logger) -> OracleReport:
	results: list[CheckEntry] = []
	for check in checks:
		try:
			result = check.check()
			if result.ok:
				outcome = CheckOutcome.PASSED
				message = result.message or "ok"
			else:
				outcome = CheckOutcome.WARNING if check.optional else CheckOutcome.FAILED
				message = result.message or "failed"
		except Exception as exc:
			outcome = CheckOutcome.WARNING if check.optional else CheckOutcome.FAILED
			message = f"unexpected error: {type(exc).__name__}: {exc}"
			logger.exception("check %r raised an unexpected exception", check.name)

		results.append(
			CheckEntry(
				name=check.name,
				outcome=outcome,
				message=message,
				optional=check.optional,
			)
		)
	return OracleReport(results=tuple(results))


def build_oracle_report(
	*,
	logger: logging.Logger,
	requirements: Callable[[], Sequence[BaseCheck]],
) -> OracleReport:
	try:
		checks = requirements()
	except Exception as exc:
		message = f"failed to enumerate requirements: {type(exc).__name__}: {exc}"
		logger.error(message)
		return OracleReport(
			results=(
				CheckEntry(
					name="<requirements>",
					outcome=CheckOutcome.FAILED,
					message=message,
					optional=False,
				),
			)
		)
	return run_checks(checks, logger=logger)


def log_oracle_report(
	logger: logging.Logger,
	*,
	label: str,
	report: OracleReport,
	verbose: bool = False,
) -> bool:
	for result in report.results:
		if result.outcome == CheckOutcome.PASSED:
			if verbose:
				logger.info("[%s] %s: PASSED: %s", label, result.name, result.message)
		elif result.outcome == CheckOutcome.WARNING:
			logger.warning("[%s] %s: WARNING: %s", label, result.name, result.message)
		else:
			logger.error("[%s] %s: FAILED: %s", label, result.name, result.message)
	return report.ok
