"""Oracle check results, report generation, and report logging."""

from __future__ import annotations

import abc
import dataclasses
import enum
import logging
import pathlib
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable


class CheckOutcome(enum.Enum):
	"""Final reporting status for an individual oracle check."""

	PASSED = "passed"
	FAILED = "failed"
	WARNING = "warning"


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:
	"""Detailed result returned by a check implementation.

	Attributes:
		ok: Whether the check satisfied its requirement.
		message: Human-readable result summary.
		stdout: Captured standard output, when applicable.
		stderr: Captured standard error, when applicable.
		returncode: Process exit code, when applicable.
		timed_out: Whether an associated process exceeded its timeout.
		cwd: Working directory used by an associated process.
	"""

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
		"""Creates a successful check result."""
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
		"""Creates a failed check result."""
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
	"""Structural interface implemented by oracle checks."""

	@property
	def name(self) -> str:
		"""Returns the check name used in reports."""
		raise NotImplementedError

	@property
	def optional(self) -> bool:
		"""Returns whether failure should be reported as a warning."""
		raise NotImplementedError

	def check(self) -> CheckResult:
		"""Evaluates the check and returns its detailed result."""
		raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True)
class CheckEntry:
	"""Compact report entry for one evaluated check.

	Attributes:
		name: Check name shown in logs and reports.
		outcome: Final status after applying optional-check semantics.
		message: Human-readable result summary.
		optional: Whether the check is informational rather than required.
	"""

	name: str
	outcome: CheckOutcome
	message: str
	optional: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class OracleReport:
	"""Immutable collection of evaluated oracle checks."""

	results: tuple[CheckEntry, ...]

	@property
	def ok(self) -> bool:
		"""Returns whether every required check avoided failure."""
		return all(
			entry.outcome != CheckOutcome.FAILED
			for entry in self.results
			if not entry.optional
		)

	@property
	def passed_count(self) -> int:
		"""Returns the number of successful checks."""
		return sum(
			1
			for entry in self.results
			if entry.outcome == CheckOutcome.PASSED
		)

	@property
	def failed_count(self) -> int:
		"""Returns the number of required checks that failed."""
		return sum(
			1
			for entry in self.results
			if entry.outcome == CheckOutcome.FAILED
		)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseCheck(Checkable, abc.ABC):
	"""Base class for named, optionally required oracle checks."""

	name: str
	optional: bool = False

	@abc.abstractmethod
	def check(self) -> CheckResult:
		"""Evaluates the check."""
		raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Check(BaseCheck):
	"""Adapts a callable into a check object."""

	fn: Callable[[], CheckResult]

	def check(self) -> CheckResult:
		"""Evaluates the wrapped callable."""
		return self.fn()


def run_checks(
	checks: Sequence[Checkable],
	*,
	logger: logging.Logger,
) -> OracleReport:
	"""Evaluates checks and converts their results into a report.

	Checks are isolated from one another. An unexpected exception fails the
	current required check, or warns for an optional check, without preventing
	remaining checks from running.

	Args:
		checks: Checks to evaluate in order.
		logger: Logger used for unexpected check exceptions.

	Returns:
		A report containing one entry per check.
	"""
	results: list[CheckEntry] = []

	for check in checks:
		try:
			result = check.check()
			if result.ok:
				outcome = CheckOutcome.PASSED
				message = result.message or "ok"
			else:
				outcome = (
					CheckOutcome.WARNING
					if check.optional
					else CheckOutcome.FAILED
				)
				message = result.message or "failed"
		except Exception as exc:
			# Preserve phase progress while recording implementation errors as
			# failures of the affected check
			outcome = (
				CheckOutcome.WARNING
				if check.optional
				else CheckOutcome.FAILED
			)
			message = (
				f"unexpected error: {type(exc).__name__}: {exc}"
			)
			logger.exception(
				"check %r raised an unexpected exception",
				check.name,
			)

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
	"""Enumerates and evaluates the checks for an oracle phase.

	Failure to generate the requirement list is represented as a synthetic
	failed entry so callers always receive a structured report.

	Args:
		logger: Logger used for requirement and check failures.
		requirements: Callable that generates the phase checks.

	Returns:
		The completed oracle report.
	"""
	try:
		checks = requirements()
	except Exception as exc:
		message = (
			"failed to enumerate requirements: "
			f"{type(exc).__name__}: {exc}"
		)
		logger.error(message)

		# Report setup failures through the same I/O stream as check failures
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
	"""Logs an oracle report using severity appropriate to each outcome.

	Successful checks are omitted by default to keep normal output concise.
	Warnings and failures are always logged.

	Args:
		logger: Destination for report messages.
		label: Oracle phase label included in each message.
		report: Report to log.
		verbose: Whether to log successful checks.

	Returns:
		True when all required checks passed.
	"""
	for result in report.results:
		if result.outcome == CheckOutcome.PASSED:
			if verbose:
				logger.info(
					"[%s] %s: PASSED: %s",
					label,
					result.name,
					result.message,
				)
		elif result.outcome == CheckOutcome.WARNING:
			logger.warning(
				"[%s] %s: WARNING: %s",
				label,
				result.name,
				result.message,
			)
		else:
			logger.error(
				"[%s] %s: FAILED: %s",
				label,
				result.name,
				result.message,
			)

	return report.ok