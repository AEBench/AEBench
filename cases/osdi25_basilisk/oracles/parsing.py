from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import PROTOCOLS

_BEGIN_RE = re.compile(r"^===== BEGIN ([A-Za-z0-9-]+) =====$")
_VERIFY_RE = re.compile(r"^Verifying\s+([A-Za-z0-9-]+)$", re.IGNORECASE)
_END_RE = re.compile(r"^===== END ([A-Za-z0-9-]+) status=(\d+) =====$")
_SUMMARY_RE = re.compile(
	r"Dafny program verifier finished with\s+(\d+)\s+verified,\s+"
	r"(\d+)\s+errors?(?:,\s+(\d+)\s+time\s+outs?)?",
	re.IGNORECASE,
)
_EXPLICIT_ERROR_RE = re.compile(r"(?:^|:\s)Error:", re.MULTILINE)
_TIMEOUT_RE = re.compile(r"\b(?:timeouts?|timed?\s+out)\b", re.IGNORECASE)

_HINTS_HEADER = (
	"protocol",
	"monotonicity_annotations",
	"provenance_hints",
)
_SLOC_HEADER = (
	"protocol",
	"basilisk_safety",
	"basilisk_total",
	"kondo_safety",
	"kondo_total",
)


def _read_runtime_text(
	path: OraclePath,
	*,
	label: str,
	executor: RuntimeCheckExecutor | None,
) -> str:
	try:
		return check_read_file_text(path, executor=executor)
	except OSError as exc:
		raise ValueError(f"{label}: failed to read {path}: {exc}") from exc
	except ValueError as exc:
		raise ValueError(f"{label}: failed to resolve or decode {path}: {exc}") from exc
	except RuntimeError as exc:
		raise ValueError(f"{label}: runtime failed to read {path}: {exc}") from exc


def _parse_integer_csv(
	text: str,
	*,
	header: tuple[str, ...],
	label: str,
) -> dict[str, dict[str, int]]:
	reader = csv.DictReader(io.StringIO(text))
	if tuple(reader.fieldnames or ()) != header:
		raise ValueError(
			f"{label}: unexpected header {reader.fieldnames!r}; expected {list(header)!r}"
		)

	rows: dict[str, dict[str, int]] = {}
	for line_number, row in enumerate(reader, start=2):
		if None in row or any(value is None for value in row.values()):
			raise ValueError(f"{label}: malformed row at line {line_number}")
		protocol = row["protocol"].strip()
		if not protocol:
			raise ValueError(f"{label}: empty protocol at line {line_number}")
		if protocol in rows:
			raise ValueError(f"{label}: duplicate protocol {protocol!r}")

		values: dict[str, int] = {}
		for field_name in header[1:]:
			raw = row[field_name].strip()
			try:
				values[field_name] = int(raw)
			except ValueError as exc:
				raise ValueError(
					f"{label}: {protocol}.{field_name} is not an integer: {raw!r}"
				) from exc
		rows[protocol] = values

	expected = set(PROTOCOLS)
	observed = set(rows)
	if observed != expected:
		missing = sorted(expected - observed)
		extra = sorted(observed - expected)
		details = []
		if missing:
			details.append("missing " + ", ".join(missing))
		if extra:
			details.append("unexpected " + ", ".join(extra))
		raise ValueError(f"{label}: protocol set mismatch ({'; '.join(details)})")

	return rows


def _load_reference(path: Path) -> dict[str, object]:
	try:
		value = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as exc:
		raise ValueError(f"could not load reference {path}: {exc}") from exc
	if not isinstance(value, dict):
		raise ValueError("reference root must be a JSON object")
	return value


@dataclass(frozen=True, slots=True, kw_only=True)
class VerificationLogCheck(BaseCheck):
	path: OraclePath
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			text = _read_runtime_text(self.path, label="verification log", executor=self.executor)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		sections: dict[str, list[str]] = {}
		current: str | None = None
		current_lines: list[str] = []
		current_requires_end = False
		for line_number, raw_line in enumerate(text.splitlines(), start=1):
			line = raw_line.strip()
			begin_marker = _BEGIN_RE.fullmatch(line)
			begin = begin_marker or _VERIFY_RE.fullmatch(line)
			if begin:
				protocol = begin.group(1)
				if current is not None:
					if current_requires_end:
						return CheckResult.failure(
							f"unterminated verification block for {current} before line {line_number}"
						)
					sections[current] = current_lines
				if protocol in sections:
					return CheckResult.failure(f"duplicate verification block for {protocol}")
				current = protocol
				current_lines = []
				current_requires_end = begin_marker is not None
				continue

			end = _END_RE.fullmatch(line)
			if end:
				protocol, status_raw = end.groups()
				if current != protocol:
					return CheckResult.failure(
						f"END for {protocol} at line {line_number} does not match {current!r}"
					)
				if int(status_raw) != 0:
					return CheckResult.failure(
						f"verification block for {protocol} ended with status {status_raw}"
					)
				sections[protocol] = current_lines
				current = None
				current_lines = []
				current_requires_end = False
				continue

			if current is not None:
				current_lines.append(raw_line)

		if current is not None:
			if current_requires_end:
				return CheckResult.failure(f"unterminated verification block for {current}")
			sections[current] = current_lines

		expected = set(PROTOCOLS)
		observed = set(sections)
		if observed != expected:
			missing = sorted(expected - observed)
			extra = sorted(observed - expected)
			return CheckResult.failure(
				f"verification log protocol mismatch; missing={missing}, unexpected={extra}"
			)

		total_summaries = 0
		for protocol in PROTOCOLS:
			body = "\n".join(sections[protocol])
			summaries = _SUMMARY_RE.findall(body)
			if not summaries:
				return CheckResult.failure(f"{protocol}: no Dafny verifier summary found")
			if _EXPLICIT_ERROR_RE.search(body):
				return CheckResult.failure(f"{protocol}: explicit Dafny Error found in log")
			if _TIMEOUT_RE.search(body):
				return CheckResult.failure(f"{protocol}: timeout found in log")

			failed = [
				(verified, errors, timeouts or "0")
				for verified, errors, timeouts in summaries
				if int(errors) != 0 or int(timeouts or 0) != 0
			]
			if failed:
				return CheckResult.failure(
					f"{protocol}: verifier reported errors or timeouts: {failed}"
				)
			if sum(int(verified) for verified, _errors, _timeouts in summaries) == 0:
				return CheckResult.failure(f"{protocol}: verifier completed no proof obligations")
			total_summaries += len(summaries)

		return CheckResult.success(
			message=(
				f"all {len(PROTOCOLS)} protocols completed with "
				f"{total_summaries} zero-error, zero-timeout verifier summaries"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class HintsCsvCheck(BaseCheck):
	path: OraclePath
	reference_path: Path
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			text = _read_runtime_text(self.path, label="hints.csv", executor=self.executor)
			observed = _parse_integer_csv(text, header=_HINTS_HEADER, label="hints.csv")
			ref = _load_reference(self.reference_path)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		protocol_refs = ref.get("protocols")
		if not isinstance(protocol_refs, dict):
			return CheckResult.failure("reference protocols must be a JSON object")

		mismatches: list[str] = []
		for protocol in PROTOCOLS:
			expected = protocol_refs.get(protocol)
			if not isinstance(expected, dict):
				return CheckResult.failure(f"missing reference entry for {protocol}")
			for field_name in _HINTS_HEADER[1:]:
				expected_value = expected.get(field_name)
				if not isinstance(expected_value, int):
					return CheckResult.failure(
						f"reference {protocol}.{field_name} must be an integer"
					)
				actual = observed[protocol][field_name]
				if actual != expected_value:
					mismatches.append(
						f"{protocol}.{field_name}: got {actual}, expected {expected_value}"
					)

		if mismatches:
			return CheckResult.failure("hint-count mismatches: " + "; ".join(mismatches))
		return CheckResult.success(
			message=f"all {len(PROTOCOLS)} protocols match the exact hint-count reference"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class SlocCsvCheck(BaseCheck):
	path: OraclePath
	reference_path: Path
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			text = _read_runtime_text(self.path, label="sloc.csv", executor=self.executor)
			observed = _parse_integer_csv(text, header=_SLOC_HEADER, label="sloc.csv")
			ref = _load_reference(self.reference_path)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		protocol_refs = ref.get("protocols")
		tolerance = ref.get("basilisk_sloc_tolerance")
		if not isinstance(protocol_refs, dict):
			return CheckResult.failure("reference protocols must be a JSON object")
		if not isinstance(tolerance, int) or tolerance < 0:
			return CheckResult.failure("reference basilisk_sloc_tolerance must be >= 0")

		mismatches: list[str] = []
		for protocol in PROTOCOLS:
			expected = protocol_refs.get(protocol)
			if not isinstance(expected, dict):
				return CheckResult.failure(f"missing reference entry for {protocol}")
			for field_name in _SLOC_HEADER[1:]:
				expected_value = expected.get(field_name)
				if not isinstance(expected_value, int):
					return CheckResult.failure(
						f"reference {protocol}.{field_name} must be an integer"
					)
				actual = observed[protocol][field_name]
				allowed = tolerance if field_name.startswith("basilisk_") else 0
				if abs(actual - expected_value) > allowed:
					mismatches.append(
						f"{protocol}.{field_name}: got {actual}, expected {expected_value} "
						f"(+/- {allowed})"
					)

		if mismatches:
			return CheckResult.failure("SLOC mismatches: " + "; ".join(mismatches))

		comparable = [protocol for protocol in PROTOCOLS if observed[protocol]["kondo_total"] >= 0]
		basilisk_total = sum(observed[p]["basilisk_total"] for p in comparable)
		kondo_total = sum(observed[p]["kondo_total"] for p in comparable)
		if basilisk_total >= kondo_total:
			return CheckResult.failure(
				f"aggregate proof-SLOC claim failed: Basilisk {basilisk_total} "
				f">= Kondo {kondo_total} across {len(comparable)} shared protocols"
			)

		return CheckResult.success(
			message=(
				f"all SLOC values match reference; aggregate Basilisk total "
				f"{basilisk_total} < Kondo {kondo_total} across {len(comparable)} protocols"
			)
		)
