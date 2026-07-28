from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult


def _parse_csv(text: str) -> tuple[list[str], dict[str, list[str]]]:
	"""Parse a decarb output CSV into (header, rows-keyed-by-first-column).

	The first column is a row key (zone code / latency / migration label); the
	remaining columns are numeric (possibly empty). Raises ValueError on an empty
	table or duplicate row keys.
	"""
	rows = list(csv.reader(text.splitlines()))
	rows = [r for r in rows if r]
	if len(rows) < 2:
		raise ValueError(f"expected a header + >=1 data row, got {len(rows)} row(s)")
	header = rows[0]
	body: dict[str, list[str]] = {}
	for r in rows[1:]:
		key = r[0]
		if key in body:
			raise ValueError(f"duplicate row key {key!r}")
		body[key] = r[1:]
	return header, body


def _read_csv(
	path: OraclePath, executor: RuntimeCheckExecutor | None
) -> tuple[list[str], dict[str, list[str]]]:
	return _parse_csv(check_read_file_text(path, executor=executor))


def _cells_match(observed: str, reference: str, rel_tol: float) -> bool:
	"""Numeric cells match within relative tolerance; otherwise exact string match."""
	obs, ref = observed.strip(), reference.strip()
	if obs == ref:
		return True
	try:
		o, r = float(obs), float(ref)
	except ValueError:
		return False
	return abs(o - r) <= max(abs(r) * rel_tol, 1e-9)


@dataclass(frozen=True, slots=True, kw_only=True)
class CsvNumericMatchCheck(BaseCheck):
	"""Compare a produced CSV against a committed reference: same header, same row
	keys, and every cell equal (numeric within relative tolerance).
	"""

	label: str
	observed_path: OraclePath
	reference_path: Path
	rel_tol: float
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			ref_header, ref_rows = _parse_csv(self.reference_path.read_text(encoding="utf-8"))
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"{self.label}: reference unreadable: {exc}")
		try:
			obs_header, obs_rows = _read_csv(self.observed_path, self.executor)
		except OSError as exc:
			return CheckResult.failure(f"{self.label}: could not read output: {exc}")
		except ValueError as exc:
			return CheckResult.failure(f"{self.label}: malformed output CSV: {exc}")

		if obs_header != ref_header:
			return CheckResult.failure(
				f"{self.label}: header mismatch: got {obs_header}, expected {ref_header}"
			)

		missing = [k for k in ref_rows if k not in obs_rows]
		extra = [k for k in obs_rows if k not in ref_rows]
		if missing or extra:
			return CheckResult.failure(
				f"{self.label}: row-key mismatch (missing {missing[:5]}, extra {extra[:5]})"
			)

		errors: list[str] = []
		for key, ref_cells in ref_rows.items():
			obs_cells = obs_rows[key]
			if len(obs_cells) != len(ref_cells):
				errors.append(f"row {key!r}: {len(obs_cells)} cols != expected {len(ref_cells)}")
				continue
			for col, (o, r) in zip(ref_header[1:], zip(obs_cells, ref_cells)):
				if not _cells_match(o, r, self.rel_tol):
					errors.append(f"row {key!r} col {col!r}: got {o!r}, expected {r!r}")
			if len(errors) >= 8:
				break

		if errors:
			return CheckResult.failure(f"{self.label}: " + "; ".join(errors[:8]))

		return CheckResult.success(
			message=f"{self.label}: {len(ref_rows)} rows match reference within rel_tol {self.rel_tol}"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class OneVsInfSavingsCheck(BaseCheck):
	"""Paper claim (spatial): unlimited migration ("inf") yields at least as much
	carbon savings as a single migration ("one") in every region.
	"""

	observed_path: OraclePath
	one_row: str
	inf_row: str
	tol: float
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			header, rows = _read_csv(self.observed_path, self.executor)
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"savings_mean.csv: {exc}")
		if self.one_row not in rows or self.inf_row not in rows:
			return CheckResult.failure(
				f"savings_mean.csv: missing {self.one_row!r}/{self.inf_row!r} rows"
			)

		regions = header[1:]
		errors: list[str] = []
		for region, one_s, inf_s in zip(regions, rows[self.one_row], rows[self.inf_row]):
			try:
				one_v, inf_v = float(one_s), float(inf_s)
			except ValueError:
				errors.append(f"{region}: non-numeric ({one_s!r}/{inf_s!r})")
				continue
			if inf_v < one_v - self.tol:
				errors.append(f"{region}: inf {inf_v} < one {one_v}")

		if errors:
			return CheckResult.failure("inf>=one savings violated: " + "; ".join(errors))
		return CheckResult.success(
			message=f"unlimited migration >= single migration in all {len(regions)} regions"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class CapacityMonotonicCheck(BaseCheck):
	"""Paper claim (spatial): within each latency budget (row), emissions are
	non-increasing as idle capacity grows across the columns (more spare capacity
	enables more shifting to cleaner regions). Empty cells are skipped.
	"""

	observed_path: OraclePath
	tol: float
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			_, rows = _read_csv(self.observed_path, self.executor)
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"emissions.csv: {exc}")

		errors: list[str] = []
		for key, cells in rows.items():
			prev: float | None = None
			for cell in cells:
				s = cell.strip()
				if not s:
					continue
				try:
					v = float(s)
				except ValueError:
					errors.append(f"row {key!r}: non-numeric {s!r}")
					break
				if prev is not None and v > prev * (1 + self.tol) + self.tol:
					errors.append(f"row {key!r}: emissions rose {prev} -> {v} with more capacity")
					break
				prev = v

		if errors:
			return CheckResult.failure("capacity monotonicity violated: " + "; ".join(errors))
		return CheckResult.success(
			message=f"emissions non-increasing with capacity across all {len(rows)} latency rows"
		)
