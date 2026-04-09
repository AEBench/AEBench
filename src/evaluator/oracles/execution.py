from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Callable, Sequence, cast

from constants import ARTIFACT_SUBDIR
from models import OracleFailureMode, OracleInput, OraclePhaseResult, OracleResult, OracleStatus

from ..constants import ORACLE_DIRNAME, ORACLE_RESULT_FILENAME
from ..loader import load_case_spec
from . import utils
from .discovery import (
	REQUIRED_PHASE_KEYS,
	DiscoveredPhase,
	OracleLoadError,
	discover_oracle_phases_in_scope,
	merge_toml_and_python_phases,
	oracle_import_scope,
	oracle_root_for,
	phase_key_to_string,
)

_SKIPPED_PHASE_SUMMARY = "skipped because a previous phase failed"


def run_phases(
	context: OracleInput,
	*,
	phases: Sequence[DiscoveredPhase],
	failure_mode: OracleFailureMode | str = OracleFailureMode.FAIL_FAST,
	on_phase_start: Callable[[DiscoveredPhase], None] | None = None,
	on_phase_finish: Callable[[DiscoveredPhase, OraclePhaseResult], None] | None = None,
) -> OracleResult:
	mode = OracleFailureMode(failure_mode)

	provided_keys = {p.key for p in phases}
	missing = REQUIRED_PHASE_KEYS - provided_keys
	if missing:
		missing_names = ", ".join(sorted(phase_key_to_string(k) for k in missing))
		raise ValueError(
			f"oracle is missing required phases: {missing_names}; "
			f"all four standard evaluation phases must be implemented"
		)

	results: list[OraclePhaseResult] = []
	failed_phases: list[str] = []

	for index, phase_def in enumerate(phases):
		logger = logging.getLogger(f"oracle.{phase_def.name}")
		if on_phase_start is not None:
			on_phase_start(phase_def)

		try:
			try:
				checks = phase_def.requirements(context)
			except Exception as exc:
				msg = f"failed to enumerate requirements: {type(exc).__name__}: {exc}"
				logger.error(msg)
				report = utils.OracleReport(
					results=(
						utils.CheckEntry(
							name="<requirements>",
							outcome=utils.CheckOutcome.FAILED,
							message=msg,
						),
					)
				)
			else:
				report = utils.run_checks(checks, logger=logger)

			utils.log_oracle_report(
				logger,
				label=phase_def.name,
				report=report,
				verbose=False,
			)

			if report.ok:
				passed = report.passed_count
				total = len(report.results)
				summary = (
					f"{phase_def.name} passed ({passed}/{total} checks)"
					if total > 0
					else f"{phase_def.name} passed"
				)
				phase_result = OraclePhaseResult(
					phase=phase_def.name,
					status=OracleStatus.SUCCESS,
					summary=summary,
				)
			else:
				error_msgs = [
					result.message
					for result in report.results
					if result.outcome == utils.CheckOutcome.FAILED and result.message
				]
				phase_result = OraclePhaseResult(
					phase=phase_def.name,
					status=OracleStatus.ERROR,
					summary=f"{phase_def.name} failed",
					error="; ".join(error_msgs) if error_msgs else "one or more checks failed",
				)
				failed_phases.append(phase_def.name)

		except Exception as exc:
			phase_result = OraclePhaseResult(
				phase=phase_def.name,
				status=OracleStatus.ERROR,
				summary="phase raised an unexpected exception",
				error=f"{type(exc).__name__}: {exc}",
			)
			failed_phases.append(phase_def.name)

		results.append(phase_result)
		if on_phase_finish is not None:
			on_phase_finish(phase_def, phase_result)

		if phase_result.status != OracleStatus.SUCCESS and mode == OracleFailureMode.FAIL_FAST:
			results.extend(
				[
					OraclePhaseResult(
						phase=phase.name,
						status=OracleStatus.PENDING,
						summary=_SKIPPED_PHASE_SUMMARY,
					)
					for phase in phases[index + 1 :]
				]
			)
			break

	score = sum(1 for result in results if result.status == OracleStatus.SUCCESS)
	return OracleResult(
		status=OracleStatus.SUCCESS if not failed_phases else OracleStatus.ERROR,
		score=score,
		summary=f"Passed {score}/{len(phases)} phases.",
		phases=results,
		error=None if not failed_phases else f"oracle failed phases: {', '.join(failed_phases)}",
	)


def _resolve_workspace_dir(
	*,
	case_root: Path,
	artifact_dir: Path,
	runtime_result: Any,
	explicit_workspace_dir: Path | None,
) -> Path:
	if explicit_workspace_dir is not None:
		return explicit_workspace_dir.expanduser().resolve(strict=False)

	runtime_workspace = (
		None if runtime_result is None else getattr(runtime_result, "workspace_path", None)
	)
	if runtime_workspace:
		return Path(str(runtime_workspace)).expanduser().resolve(strict=False)

	if artifact_dir.exists():
		return artifact_dir

	return case_root


def run_oracle(
	case_dir: Path,
	*,
	runtime_result: Any,
	output_dir: Path,
	case: Any = None,
	workspace_dir: Path | None = None,
	runtime_session: Any | None = None,
	runtime_backend: Any | None = None,
) -> OracleResult:
	"""Discover and run oracle phases for a case dir."""
	case_root = case_dir.resolve()
	out_dir = output_dir.resolve()
	out_dir.mkdir(parents=True, exist_ok=True)
	context: OracleInput | None = None

	try:
		spec = case or load_case_spec(case_root)
		failure_mode = spec.oracle.failure_mode if spec.oracle else OracleFailureMode.FAIL_FAST
		has_toml = spec.oracle.has_toml_checks
		artifact_dir = (case_root / ARTIFACT_SUBDIR).resolve(strict=False)
		resolved_workspace_dir = _resolve_workspace_dir(
			case_root=case_root,
			artifact_dir=artifact_dir,
			runtime_result=runtime_result,
			explicit_workspace_dir=workspace_dir,
		)
		context = OracleInput(
			case_dir=case_root,
			artifact_dir=artifact_dir,
			workspace_dir=resolved_workspace_dir,
			output_dir=out_dir,
			runtime_result=runtime_result,
		)
		context.runtime_session = runtime_session
		context.runtime_backend = runtime_backend
		context.runtime_executor = utils.build_runtime_check_executor(context)

		toml_phases: list[DiscoveredPhase] = []
		if has_toml:
			from .toml_checks import discover_toml_phases

			toml_phases = discover_toml_phases(spec.oracle, context)

		has_oracle_dir = (case_root / ORACLE_DIRNAME).is_dir()

		if has_oracle_dir:
			# Keep the import scope alive for the entire discovery + execution
			# so that oracle classes can use relative imports in their methods.
			oracle_root = oracle_root_for(case_root)
			has_python_oracle_modules = any(
				path.is_file() and path.suffix == ".py" and path.name != "__init__.py"
				for path in oracle_root.rglob("*.py")
			)
			with oracle_import_scope(case_root, oracle_root.name):
				try:
					python_phases = discover_oracle_phases_in_scope(case_root, oracle_root)
				except OracleLoadError:
					if not has_toml or has_python_oracle_modules:
						logging.getLogger(__name__).exception(
							"failed to discover Python oracle phases in %s",
							oracle_root,
						)
						raise
					python_phases = []

				phases = merge_toml_and_python_phases(python_phases, toml_phases)
				result = run_phases(
					context,
					phases=phases,
					failure_mode=failure_mode,
				)
		elif has_toml:
			result = run_phases(
				context,
				phases=toml_phases,
				failure_mode=failure_mode,
			)
		else:
			raise OracleLoadError(
				f"oracle directory is missing under {case_root} "
				f"(expected {ORACLE_DIRNAME}/) and no TOML checks declared"
			)
	except Exception as exc:
		tb = traceback.format_exc()
		result = OracleResult(
			status=OracleStatus.ERROR,
			score=0,
			summary="Oracle evaluation failed.",
			error=f"{type(exc).__name__}: {exc}\n{tb}",
		)
	finally:
		try:
			if context is not None and context.runtime_executor is not None:
				executor = cast(utils.RuntimeCheckExecutor, context.runtime_executor)
				executor.close()
		except Exception:
			logging.getLogger(__name__).exception("failed to clean up oracle runtime executor")

	try:
		(out_dir / ORACLE_RESULT_FILENAME).write_text(
			json.dumps(result.model_dump(mode="json"), indent=2),
			encoding="utf-8",
		)
	except Exception:
		logging.getLogger(__name__).exception("failed to write oracle result file to %s", out_dir)
	return result
