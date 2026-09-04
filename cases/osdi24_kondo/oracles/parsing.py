from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import PROTOCOLS

_SUMMARY_RE = re.compile(
	r"Dafny program verifier finished with\s+(\d+)\s+verified,\s+(\d+)\s+errors?"
	r"(?:,\s+(\d+)\s+time outs?)?"
)
_EXPLICIT_ERROR_RE = re.compile(r"(?:^|:\s)Error:", re.MULTILINE)
_TIMEOUT_RE = re.compile(r"\b(?:timeouts?|timed?\s+out)\b", re.IGNORECASE)
_SECTION_RE = re.compile(r"^Verifying .+ \((?:manual|sync|kondo)\)$")

_VERIFY_SECTIONS = (
	"Verifying Client-Server (manual)",
	"Verifying Client-Server (sync)",
	"Verifying Client-Server (kondo)",
	"Verifying Ring Leader Election (manual)",
	"Verifying Ring Leader Election (sync)",
	"Verifying Ring Leader Election (kondo)",
	"Verifying Simplified Leader Election (manual)",
	"Verifying Simplified Leader Election (sync)",
	"Verifying Simplified Leader Election (kondo)",
	"Verifying Two-Phase Commit (manual)",
	"Verifying Two-Phase Commit (sync)",
	"Verifying Two-Phase Commit (kondo)",
	"Verifying Paxos (manual)",
	"Verifying Paxos (sync)",
	"Verifying Paxos (kondo)",
	"Verifying Flexible Paxos (sync)",
	"Verifying Flexible Paxos (kondo)",
	"Verifying DistributedLock (manual)",
	"Verifying DistributedLock (sync)",
	"Verifying DistributedLock (kondo)",
	"Verifying ShardedKV (manual)",
	"Verifying ShardedKV (sync)",
	"Verifying ShardedKV (kondo)",
	"Verifying ShardedKV-Batched (manual)",
	"Verifying ShardedKV-Batched (sync)",
	"Verifying ShardedKV-Batched (kondo)",
	"Verifying Lock Server (manual)",
	"Verifying Lock Server (sync)",
	"Verifying Lock Server (kondo)",
)

_SLOC_TOLERANCE = 5
_SLOC_FIELDS = ("sync_spec", "manual_proof", "sync_proof")
_SLOC_HEADER = ("protocol", *_SLOC_FIELDS)


def _read_runtime_text(
	path: OraclePath,
	*,
	label: str,
	executor: RuntimeCheckExecutor,
) -> str:
	try:
		return check_read_file_text(path, encoding="utf-8", executor=executor)
	except OSError as exc:
		raise ValueError(f"{label}: failed to read {path}: {exc}") from exc
	except ValueError as exc:
		raise ValueError(f"{label}: failed to resolve or decode {path}: {exc}") from exc
	except RuntimeError as exc:
		raise ValueError(f"{label}: runtime failed to read {path}: {exc}") from exc


def _load_sloc_csv(
	path: OraclePath,
	*,
	executor: RuntimeCheckExecutor,
) -> dict[str, dict[str, int]]:
	text = _read_runtime_text(path, label="sloc.csv", executor=executor)
	rows: dict[str, dict[str, int]] = {}
	reader = csv.DictReader(text.strip().splitlines())
	if tuple(reader.fieldnames or ()) != _SLOC_HEADER:
		raise ValueError(
			f"sloc.csv has unexpected header {reader.fieldnames!r}; expected {list(_SLOC_HEADER)!r}"
		)

	for line_number, row in enumerate(reader, start=2):
		if None in row or any(value is None for value in row.values()):
			raise ValueError(f"sloc.csv line {line_number}: malformed row")
		protocol = row["protocol"].strip()
		if not protocol:
			raise ValueError(f"sloc.csv line {line_number}: empty protocol")
		if protocol in rows:
			raise ValueError(f"sloc.csv line {line_number}: duplicate protocol {protocol!r}")
		try:
			rows[protocol] = {field_name: int(row[field_name]) for field_name in _SLOC_FIELDS}
		except (KeyError, TypeError, ValueError) as exc:
			raise ValueError(
				f"sloc.csv line {line_number}: invalid SLOC value for {protocol!r}: {exc}"
			) from exc

	expected = set(PROTOCOLS)
	observed = set(rows)
	if observed != expected:
		missing = sorted(expected - observed)
		extra = sorted(observed - expected)
		raise ValueError(f"sloc.csv protocol mismatch; missing={missing}, unexpected={extra}")
	return rows


def _load_reference(path: Path) -> object:
	try:
		text = path.read_text(encoding="utf-8").strip()
	except OSError as exc:
		raise ValueError(f"sloc reference: failed to read {path}: {exc}") from exc
	if not text:
		raise ValueError(f"sloc reference: empty JSON content at {path}")
	try:
		return json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError(f"sloc reference: invalid JSON in {path}: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolManifestCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			text = _read_runtime_text(self.path, label="protocols.csv", executor=executor)
			reader = csv.reader(text.splitlines())
			rows = [row for row in reader if any(field.strip() for field in row)]
		except (csv.Error, ValueError) as exc:
			return CheckResult.failure(str(exc))

		protocols: list[str] = []
		for line_number, row in enumerate(rows, start=1):
			if not row or not row[0].strip() or any(field.strip() for field in row[1:]):
				return CheckResult.failure(f"protocols.csv line {line_number}: malformed row")
			protocols.append(row[0].strip())
		if len(protocols) != len(set(protocols)):
			return CheckResult.failure("protocols.csv contains duplicate protocols")
		observed = set(protocols)
		expected = set(PROTOCOLS)
		if observed != expected:
			return CheckResult.failure(
				"protocols.csv protocol mismatch; "
				f"missing={sorted(expected - observed)}, unexpected={sorted(observed - expected)}"
			)
		return CheckResult.success(f"protocols.csv contains exactly {len(PROTOCOLS)} protocols")


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifyAllLogCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			text = _read_runtime_text(
				self.path,
				label="verify-all log",
				executor=executor,
			)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		lines = text.splitlines()
		positions: dict[str, int] = {}
		for index, line in enumerate(lines):
			section = line.strip()
			if not _SECTION_RE.fullmatch(section):
				continue
			if section in positions:
				return CheckResult.failure(f"duplicate {section!r} section in verify-all log")
			positions[section] = index

		expected = set(_VERIFY_SECTIONS)
		observed = set(positions)
		if observed != expected:
			missing = sorted(expected - observed)
			extra = sorted(observed - expected)
			return CheckResult.failure(
				f"verify-all section mismatch; missing={missing}, unexpected={extra}"
			)

		ordered_sections = sorted(positions.items(), key=lambda item: item[1])
		summary_count = 0
		for index, (section, position) in enumerate(ordered_sections):
			start = position + 1
			end = ordered_sections[index + 1][1] if index + 1 < len(positions) else len(lines)
			section_text = "\n".join(lines[start:end])
			summaries = _SUMMARY_RE.findall(section_text)
			if not summaries:
				return CheckResult.failure(f"no Dafny verification summary found for {section}")
			if _EXPLICIT_ERROR_RE.search(section_text):
				return CheckResult.failure(f"explicit Dafny Error found for {section}")
			if _TIMEOUT_RE.search(section_text):
				return CheckResult.failure(f"Dafny verification timed out for {section}")

			failed = [
				(verified, errors, timeouts or "0")
				for verified, errors, timeouts in summaries
				if int(errors) != 0 or int(timeouts or 0) != 0
			]
			if failed:
				return CheckResult.failure(
					f"Dafny verification reported errors or timeouts for {section}: {failed}"
				)
			if sum(int(verified) for verified, _errors, _timeouts in summaries) == 0:
				return CheckResult.failure(f"Dafny verified no obligations for {section}")
			summary_count += len(summaries)

		return CheckResult.success(
			f"all {len(_VERIFY_SECTIONS)} verify-all sections passed "
			f"({summary_count} Dafny summaries, 0 errors, no timeouts)"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class SlocReferenceCheck(BaseCheck):
	sloc_csv_path: OraclePath
	reference_path: Path
	tolerance: int = _SLOC_TOLERANCE

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		if self.tolerance < 0:
			return CheckResult.failure(f"invalid tolerance: {self.tolerance}; expected >= 0")

		try:
			observed_data = _load_sloc_csv(self.sloc_csv_path, executor=executor)
			ref_obj = _load_reference(self.reference_path)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		if not isinstance(ref_obj, dict):
			return CheckResult.failure("sloc reference: expected a JSON object")

		expected_protocols = set(PROTOCOLS)
		reference_protocols = set(ref_obj)
		if reference_protocols != expected_protocols:
			missing = sorted(expected_protocols - reference_protocols)
			extra = sorted(reference_protocols - expected_protocols)
			return CheckResult.failure(
				f"sloc reference protocol mismatch; missing={missing}, unexpected={extra}"
			)

		mismatches: list[str] = []
		matched = 0
		for protocol in PROTOCOLS:
			ref_entry = ref_obj.get(protocol)
			if not isinstance(ref_entry, dict):
				return CheckResult.failure(f"sloc reference entry for {protocol} must be an object")
			obs_entry = observed_data[protocol]

			for field_name in _SLOC_FIELDS:
				ref_val = ref_entry.get(field_name)
				if not isinstance(ref_val, int):
					return CheckResult.failure(
						f"sloc reference {protocol}.{field_name} must be an integer"
					)
				obs_int = obs_entry[field_name]
				if abs(obs_int - ref_val) > self.tolerance:
					mismatches.append(
						f"{protocol}.{field_name}: got {obs_int}, expected {ref_val} "
						f"(+/- {self.tolerance})"
					)
				else:
					matched += 1

		if mismatches:
			shown = mismatches[:10]
			more = f"\n... ({len(mismatches) - 10} more)" if len(mismatches) > 10 else ""
			return CheckResult.failure(
				f"{len(mismatches)} SLOC mismatch(es):\n"
				+ "\n".join(f"- {mismatch}" for mismatch in shown)
				+ more
			)

		return CheckResult.success(
			message=f"all {matched} SLOC values match reference (tolerance +/- {self.tolerance})"
		)
