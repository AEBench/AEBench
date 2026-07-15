from __future__ import annotations

import csv
import dataclasses
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind
from evaluator.oracles.oracle_checks_runtime import (
	RuntimeCheckExecutor,
	check_path_is_file,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import PROTOCOLS

_SLOC_TOLERANCE = 5
_SLOC_FIELDS = ("sync_spec", "manual_proof", "sync_proof")


def _load_json_file(
	path: Path,
	*,
	label: str,
	executor: RuntimeCheckExecutor | None = None,
) -> object:
	try:
		text = check_read_file_text(path, encoding="utf-8", executor=executor)
	except (OSError, RuntimeError) as exc:
		raise ValueError(f"{label}: failed to read {path}: {exc}") from exc

	text = text.strip()
	if not text:
		raise ValueError(f"{label}: empty JSON content at {path}")

	try:
		return json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError(f"{label}: invalid JSON in {path}: {exc}") from exc


def _load_sloc_csv(
	path: Path,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> dict[str, dict[str, int]]:
	try:
		text = check_read_file_text(path, encoding="utf-8", executor=executor)
	except (OSError, RuntimeError) as exc:
		raise ValueError(f"failed to read sloc.csv: {exc}") from exc

	rows: dict[str, dict[str, int]] = {}
	reader = csv.DictReader(text.strip().splitlines())
	for row in reader:
		protocol = row.get("protocol", "").strip()
		if not protocol:
			continue
		rows[protocol] = {
			"sync_spec": int(row.get("sync_spec", 0)),
			"manual_proof": int(row.get("manual_proof", 0)),
			"sync_proof": int(row.get("sync_proof", 0)),
		}
	return rows


@dataclass(frozen=True, slots=True, kw_only=True)
class SlocExactMatchCheck(BaseCheck):
	"""Check observed SLOC values against the checked-in reference."""

	sloc_csv_path: Path
	reference_path: Path
	tolerance: int = _SLOC_TOLERANCE
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	def check(self) -> CheckResult:
		if self.tolerance < 0:
			return CheckResult.failure(f"invalid tolerance: {self.tolerance}; expected >= 0")

		if not check_path_is_file(self.sloc_csv_path, executor=self.executor):
			return CheckResult.failure(f"sloc.csv not found: {self.sloc_csv_path}")

		if not check_path_is_file(self.reference_path, executor=self.executor):
			return CheckResult.failure(f"SLOC reference not found: {self.reference_path}")

		try:
			observed_data = _load_sloc_csv(self.sloc_csv_path, executor=self.executor)
		except (ValueError, KeyError) as exc:
			return CheckResult.failure(f"failed to parse sloc.csv: {exc}")

		try:
			ref_obj = _load_json_file(
				self.reference_path,
				label="sloc reference",
				executor=self.executor,
			)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		if not isinstance(ref_obj, dict):
			return CheckResult.failure("sloc reference: expected a JSON object")

		missing_protocols: list[str] = []
		malformed_reference_protocols: list[str] = []
		mismatches: list[str] = []
		matched = 0

		for protocol in PROTOCOLS:
			ref_entry = ref_obj.get(protocol)
			if not isinstance(ref_entry, dict):
				malformed_reference_protocols.append(protocol)
				continue

			obs_entry = observed_data.get(protocol)
			if obs_entry is None:
				missing_protocols.append(protocol)
				continue

			for field in _SLOC_FIELDS:
				ref_val = ref_entry.get(field)
				obs_val = obs_entry.get(field)

				if ref_val is None:
					continue

				if obs_val is None:
					mismatches.append(f"{protocol}.{field}: missing in sloc.csv")
					continue

				try:
					ref_int = int(ref_val)
					obs_int = int(obs_val)
				except (TypeError, ValueError):
					mismatches.append(
						f"{protocol}.{field}: non-integer value "
						f"observed={obs_val!r}, reference={ref_val!r}"
					)
					continue

				if abs(obs_int - ref_int) > self.tolerance:
					mismatches.append(
						f"{protocol}.{field}: got {obs_int}, expected {ref_int} "
						f"(+/- {self.tolerance})"
					)
				else:
					matched += 1

		if malformed_reference_protocols:
			return CheckResult.failure(
				"sloc reference missing or malformed protocol entries: "
				+ ", ".join(malformed_reference_protocols)
			)

		if missing_protocols:
			return CheckResult.failure(
				"sloc.csv missing protocols: " + ", ".join(missing_protocols)
			)

		if mismatches:
			shown = mismatches[:10]
			more = f"\n... ({len(mismatches) - 10} more)" if len(mismatches) > 10 else ""
			return CheckResult.failure(
				f"{len(mismatches)} SLOC mismatch(es):\n"
				+ "\n".join(f"- {m}" for m in shown)
				+ more
			)

		return CheckResult.success(
			message=(f"all {matched} SLOC values match reference (tolerance +/- {self.tolerance})")
		)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		kondo_protos = self.workspace_path("kondoPrototypes")
		sloc_csv = kondo_protos / "evaluation" / "sloc.csv"
		sloc_ref = self.ref_path("sloc.ref.json")

		reqs: list[BaseCheck] = [
			self.path_check(
				name="sloc_csv_exists",
				path=sloc_csv,
				kind=PathKind.FILE,
			),
			self.path_check(
				name="sloc_reference_exists",
				path=sloc_ref,
				kind=PathKind.FILE,
			),
			SlocExactMatchCheck(
				name="sloc_values_match",
				sloc_csv_path=sloc_csv,
				reference_path=sloc_ref,
				executor=self.executor,
			),
		]

		for protocol in PROTOCOLS:
			safe_protocol = protocol.replace("/", "_").replace("-", "_").replace(".", "_")

			reqs.append(
				self.path_check(
					name=f"sync_proof_{safe_protocol}",
					path=kondo_protos / protocol / "sync" / "applicationProof.dfy",
					kind=PathKind.FILE,
				)
			)
			reqs.append(
				self.path_check(
					name=f"manual_proof_{safe_protocol}",
					path=kondo_protos / protocol / "manual" / "applicationProof.dfy",
					kind=PathKind.FILE,
				)
			)

		return tuple(reqs)
