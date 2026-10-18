from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.reporting import BaseCheck, CheckResult


def _status_lines(text: str) -> list[str]:
	"""Non-empty, stripped lines of a validate.sh output (<bench>.hash) file."""
	return [line.strip() for line in text.splitlines() if line.strip()]


@dataclass(frozen=True, slots=True, kw_only=True)
class KoalaCorrectnessCheck(BaseCheck):
	"""Validate a benchmark's saved <bench>.hash (the validate.sh output).

	Implements Koala main.sh's ``correct()`` rule: the run is correct iff every
	status line's final whitespace token is ``0``. Also requires the expected
	number of status lines (no scripts silently skipped on --min).
	"""

	path: Path
	expected_lines: int

	def check(self) -> CheckResult:
		try:
			text = self.path.read_text(encoding="utf-8", errors="replace")
		except OSError as exc:
			return CheckResult.failure(f"failed to read {self.path}: {exc}")

		lines = _status_lines(text)
		if not lines:
			return CheckResult.failure(f"{self.path.name} is empty (no validation status lines)")

		bad: list[str] = []
		for line in lines:
			status = line.split()[-1]
			if status != "0":
				bad.append(line)

		if bad:
			return CheckResult.failure(
				f"{self.path.name}: {len(bad)} of {len(lines)} status line(s) not 0 "
				f"(e.g. {bad[0]!r}) — benchmark did not pass validation"
			)

		if len(lines) != self.expected_lines:
			return CheckResult.failure(
				f"{self.path.name}: got {len(lines)} status line(s), expected "
				f"{self.expected_lines} — wrong number of scripts validated"
			)

		return CheckResult.success(
			message=f"{self.path.name}: {len(lines)}/{self.expected_lines} scripts validated (all status 0)"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class KoalaPassLogCheck(BaseCheck):
	"""Pass iff the harness log shows ``<bench> [pass]`` and never ``<bench> [fail]``."""

	path: Path
	bench: str

	def check(self) -> CheckResult:
		try:
			text = self.path.read_text(encoding="utf-8", errors="replace")
		except OSError as exc:
			return CheckResult.failure(f"failed to read {self.path}: {exc}")

		fail_marker = f"{self.bench} [fail]"
		pass_marker = f"{self.bench} [pass]"
		if fail_marker in text:
			return CheckResult.failure(f"{self.path.name} reports {fail_marker!r}")
		if pass_marker not in text:
			return CheckResult.failure(
				f"{self.path.name} does not contain {pass_marker!r} (harness verdict missing)"
			)
		return CheckResult.success(message=f"{self.path.name} reports {pass_marker!r}")
