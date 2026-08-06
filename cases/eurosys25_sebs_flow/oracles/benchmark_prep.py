from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass, field

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_PLATFORMS = ("aws", "azure", "gcp")
_COLD_START_BENCHMARKS = (
	("650.vid", 2048),
	("660.map-reduce", 256),
	("670.auth", 256),
	("680.excamera", 256),
	("690.ml", 1024),
	("6100.1000-genome", 2048),
)
_STORAGE_SIZES = ("2e10", "2e20", "2e25", "2e26", "2e27", "2e28")
_INVOCATION_PLATFORMS = ("aws", "gcp")
_INVOCATION_SIZES = ("2e5", "2e8", "2e10", "2e12", "2e14", "2e16", "2e18-1000")

_TIMING_COLUMNS = ("request_id", "func", "start", "end")
_COLD_START_COLUMNS = (*_TIMING_COLUMNS, "is_cold")


@dataclass(frozen=True, slots=True, kw_only=True)
class MeasurementCorpusCheck(BaseCheck):
	files: Sequence[tuple[str, OraclePath, Sequence[str]]]
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		errors: list[str] = []
		for label, path, required_columns in self.files:
			try:
				reader = csv.DictReader(
					io.StringIO(check_read_file_text(path, executor=self.executor))
				)
				columns = reader.fieldnames or []
				missing = [column for column in required_columns if column not in columns]
				if missing:
					errors.append(f"{label}: missing columns {missing}")
					continue
				request_ids = {row["request_id"] for row in reader if row.get("request_id")}
			except (OSError, csv.Error) as exc:
				errors.append(f"{label}: {exc}")
				continue
			if len(request_ids) < 30:
				errors.append(f"{label}: only {len(request_ids)} request IDs, expected at least 30")
			if len(errors) >= 8:
				break

		if errors:
			return CheckResult.failure(
				"released measurement corpus is incomplete: " + "; ".join(errors)
			)
		return CheckResult.success(f"validated {len(self.files)} released measurement files")


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		files: list[tuple[str, OraclePath, Sequence[str]]] = []

		for platform in _PLATFORMS:
			for benchmark, memory in _COLD_START_BENCHMARKS:
				rel_path = f"perf-cost/{benchmark}/{platform}/2024/burst_{memory}_processed.csv"
				files.append(
					(
						f"cold-start {platform}/{benchmark}",
						self.runtime_path(rel_path),
						_COLD_START_COLUMNS,
					)
				)

		for platform in _PLATFORMS:
			rel_path = f"perf-cost/6200.trip-booking/{platform}/2024/burst_128_processed.csv"
			files.append(
				(f"trip-booking {platform}", self.runtime_path(rel_path), _COLD_START_COLUMNS)
			)

		for platform in _PLATFORMS:
			for size in _STORAGE_SIZES:
				rel_path = f"perf-cost/631.parallel-download/{platform}_{size}/burst_512.csv"
				files.append(
					(f"storage {platform}/{size}", self.runtime_path(rel_path), _TIMING_COLUMNS)
				)

		for platform in _INVOCATION_PLATFORMS:
			for size in _INVOCATION_SIZES:
				rel_path = f"perf-cost/620.func-invo/{platform}_{size}/warm_256.csv"
				files.append(
					(f"invocation {platform}/{size}", self.runtime_path(rel_path), _TIMING_COLUMNS)
				)

		return (
			MeasurementCorpusCheck(
				name="released_measurement_corpus",
				files=tuple(files),
				executor=self.executor,
			),
		)
