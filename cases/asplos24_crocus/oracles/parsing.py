from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.utils import BaseCheck, CheckResult

# Table 1 row regexes. Counterexample/runtime detail is ignored; only counts matter.
# Example rows:
#   Rules             98  86 (all types) / 93 (any type)  ...  2 (0)
#   Type Insts.      377  245  28  104  4 (0)
_RULES_ROW = re.compile(
	r"^Rules\b\s+(\d+)\s+(\d+)\s*\(all types\)\s*/\s*(\d+)\s*\(any type\).*?(\d+)\s*\(\d+\)\s*$",
	re.MULTILINE,
)
_TYPE_INSTS_ROW = re.compile(
	r"^Type Insts\.\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\(\d+\)\s*$",
	re.MULTILINE,
)

_NAMED_USES = re.compile(r"Named uses:.*?=\s*([\d.]+)\s*%")
_NAMED_COVERED = re.compile(r"Named covered:.*?=\s*([\d.]+)\s*%")


@dataclass(frozen=True, slots=True, kw_only=True)
class FileContainsCheck(BaseCheck):
	"""Pass iff the file exists and contains every required substring."""

	path: Path
	required: tuple[str, ...]

	def check(self) -> CheckResult:
		try:
			text = self.path.read_text(encoding="utf-8", errors="replace")
		except OSError as exc:
			return CheckResult.failure(f"failed to read {self.path}: {exc}")

		missing = [needle for needle in self.required if needle not in text]
		if missing:
			return CheckResult.failure(f"{self.path.name} missing expected signature(s): {missing}")

		return CheckResult.success(message=f"{self.path.name} contains all expected signatures")


@dataclass(frozen=True, slots=True, kw_only=True)
class Table1CountsCheck(BaseCheck):
	"""Validate the reproduced Table 1 counts against the reference.

	Totals and failure counts must match exactly; success counts must be within a
	tolerance below the reference (Z3-version-dependent timeouts can reduce them).
	"""

	path: Path
	rules_total: int
	rules_success_all: int
	rules_success_any: int
	rules_failure: int
	type_insts_total: int
	type_insts_success: int
	type_insts_failure: int
	success_tolerance: int

	def check(self) -> CheckResult:
		try:
			text = self.path.read_text(encoding="utf-8", errors="replace")
		except OSError as exc:
			return CheckResult.failure(f"failed to read {self.path}: {exc}")

		rules = _RULES_ROW.search(text)
		insts = _TYPE_INSTS_ROW.search(text)
		if rules is None or insts is None:
			return CheckResult.failure(
				"could not parse the Table 1 'Rules'/'Type Insts.' rows from the output"
			)

		r_total, r_succ_all, r_succ_any, r_fail = (int(g) for g in rules.groups())
		ti_total, ti_succ, _ti_timeout, _ti_inapp, ti_fail = (int(g) for g in insts.groups())

		errors: list[str] = []

		def expect_exact(label: str, got: int, want: int) -> None:
			if got != want:
				errors.append(f"{label}: got {got}, expected {want}")

		def expect_floor(label: str, got: int, want: int) -> None:
			if got < want - self.success_tolerance:
				errors.append(
					f"{label}: got {got}, expected >= {want - self.success_tolerance} "
					f"(reference {want}, tolerance {self.success_tolerance})"
				)

		expect_exact("rules_total", r_total, self.rules_total)
		expect_exact("rules_failure", r_fail, self.rules_failure)
		expect_exact("type_insts_total", ti_total, self.type_insts_total)
		expect_exact("type_insts_failure", ti_fail, self.type_insts_failure)
		expect_floor("rules_success_all", r_succ_all, self.rules_success_all)
		expect_floor("rules_success_any", r_succ_any, self.rules_success_any)
		expect_floor("type_insts_success", ti_succ, self.type_insts_success)

		if errors:
			return CheckResult.failure("Table 1 mismatch: " + "; ".join(errors))

		return CheckResult.success(
			message=(
				f"Table 1 reproduced: rules {r_total} (success {r_succ_all}/{r_succ_any}, "
				f"fail {r_fail}); type insts {ti_total} (success {ti_succ}, fail {ti_fail})"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class CoveragePercentCheck(BaseCheck):
	"""Validate 'Named uses'/'Named covered' percentages (deterministic on saved CSVs)."""

	path: Path
	expected_uses_pct: float
	expected_covered_pct: float
	epsilon: float

	def check(self) -> CheckResult:
		try:
			text = self.path.read_text(encoding="utf-8", errors="replace")
		except OSError as exc:
			return CheckResult.failure(f"failed to read {self.path}: {exc}")

		uses = _last_pct(_NAMED_USES.findall(text))
		covered = _last_pct(_NAMED_COVERED.findall(text))
		if uses is None or covered is None:
			return CheckResult.failure(
				f"could not parse 'Named uses'/'Named covered' percentages from {self.path.name}"
			)

		errors: list[str] = []
		if abs(uses - self.expected_uses_pct) > self.epsilon:
			errors.append(f"named uses {uses}% != expected {self.expected_uses_pct}%")
		if abs(covered - self.expected_covered_pct) > self.epsilon:
			errors.append(f"named covered {covered}% != expected {self.expected_covered_pct}%")

		if errors:
			return CheckResult.failure("coverage mismatch: " + "; ".join(errors))

		return CheckResult.success(message=f"coverage reproduced: uses {uses}%, covered {covered}%")


def _last_pct(matches: Sequence[str]) -> float | None:
	if not matches:
		return None
	try:
		return float(matches[-1])
	except ValueError:
		return None
