from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	run_check_process_capture,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .common import TARGETS


def _point(core: dict) -> tuple[float, float]:
	"""Read measured time and error, rejecting incomplete or invalid measurements."""
	time, error = core["time"], core["err"]
	if any(type(value) not in (int, float) or not math.isfinite(value) for value in (time, error)):
		raise ValueError("time and error must be finite numbers")
	# FPCore defaults to binary64 when the precision annotation is omitted.
	bits = {"binary32": 32, "binary64": 64, None: 64}[core["prec"]]
	if time <= 0 or not 0 <= error <= bits:
		raise ValueError("time must be positive and error must fit the output precision")
	return time, bits - error


def _beats(candidate: tuple[float, float], baseline: tuple[float, float]) -> bool:
	return candidate[0] < baseline[0] and candidate[1] >= baseline[1]


@dataclass(frozen=True, slots=True, kw_only=True)
class ChassisResultsCheck(BaseCheck):
	reference_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			architecture = run_check_process_capture(
				cmd=("uname", "-m"), cwd=None, env=None, timeout_seconds=30, executor=executor
			)
			if architecture.returncode != 0 or architecture.timed_out:
				raise ValueError("cannot determine the evaluation machine architecture")
			targets = set(TARGETS)
			if architecture.stdout.strip() in ("aarch64", "arm64"):
				targets.remove("avx")
			reference = json.loads(executor.read_file_text(self.reference_path))
			wins = dict.fromkeys(sorted(targets), 0)
			clang_wins = 0
			for suite, expected_names in reference.items():
				path = RuntimePath.from_parts(
					"reports", "platforms", "output", f"{suite}-1", "results.json"
				)
				report = json.loads(executor.read_file_text(path))
				if not isinstance(report, dict) or not targets <= report.keys():
					raise ValueError(f"{suite}: missing target reports")
				for target in sorted(targets):
					improve = report[target]["improve"]
					compare = report[target]["compare"]["baseline"]
					if (
						improve["platform"] != target
						or compare["platform"] != target
						or improve["seed"] != 1
						or compare["seed"] != 1
						or improve["time_unit"] != "ms"
					):
						raise ValueError(f"{suite}/{target}: wrong target, seed, or time unit")
					for data in (improve, compare):
						rows = data["cores"]
						if not isinstance(rows, list) or len(rows) != len(expected_names):
							raise ValueError(f"{suite}/{target}: incomplete benchmark coverage")
						if {row["input_core"]["name"] for row in rows} != set(expected_names):
							raise ValueError(f"{suite}/{target}: unexpected benchmark names")
					for row in improve["cores"]:
						if row["input_core"]["time"] is not None:
							_point(row["input_core"])
							if not row["platform_cores"]:
								raise ValueError(
									f"{suite}/{target}: measured input has no output candidates"
								)
							for core in row["platform_cores"]:
								_point(core["platform_core"])
					for row in compare["cores"]:
						# The artifact omits measurements for unsupported or unsamplable inputs.
						candidates = [_point(core) for core in row["platform_cores"]]
						for core in row["supported_cores"]:
							if core["time"] is not None and core["err"] is not None:
								_point(core)
						baselines = [
							_point(core)
							for core in row["desugared_cores"]
							if core["time"] is not None and core["err"] is not None
						]
						wins[target] += any(_beats(c, b) for c in candidates for b in baselines)
					if target == "c":
						configs = improve["extra"]
						expected_flags = {
							(opt,) + fast
							for opt in ("-O0", "-O1", "-O2", "-O3", "-Os", "-Oz")
							for fast in ((), ("-ffast-math",))
						}
						if (
							len(configs) != 12
							or {tuple(flags) for flags, _, _ in configs} != expected_flags
						):
							raise ValueError(f"{suite}: expected all 12 Clang configurations")
						measured = [
							row for row in improve["cores"] if row["input_core"]["time"] is not None
						]
						for _, times, errors in configs:
							if len(times) != len(measured) or len(errors) != len(measured):
								raise ValueError(f"{suite}: incomplete Clang measurements")
							for row, time, error in zip(measured, times, errors):
								baseline = _point(dict(row["input_core"], time=time, err=error))
								clang_wins += any(
									_beats(_point(core["platform_core"]), baseline)
									for core in row["platform_cores"]
								)
			# shortcut: check per-benchmark wins, add aggregate Pareto scoring after hardware calibration.
			if not all(wins.values()) or not clang_wins:
				raise ValueError(
					f"no measured speedup at equal or better accuracy: Herbie={wins}, Clang={clang_wins}"
				)
		except (OSError, RuntimeError, ValueError, KeyError, TypeError, OverflowError) as exc:
			return CheckResult.failure(f"invalid Chassis evaluation: {exc}")
		return CheckResult.success(
			f"81 benchmarks on {len(targets)} targets; Herbie wins={wins}; Clang wins={clang_wins}"
		)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			ChassisResultsCheck(
				name="chassis_target_comparisons",
				reference_path=self.ref_path("benchmarks.ref.json"),
			),
			*(
				self.path_check(
					name=figure,
					path=self.runtime_path("reports", "platforms", "output", figure),
					kind=PathKind.FILE,
				)
				for figure in (
					"c-pareto.png",
					"baseline-pareto.png",
					"baseline-pareto2.png",
					"cost-vs-time.png",
				)
			),
		)
