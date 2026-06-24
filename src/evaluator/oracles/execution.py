"""Oracle phase execution and persistent results generation."""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Sequence, cast

from constants import ARTIFACT_DIRNAME
from models import (
	OracleFailureMode,
	OracleInput,
	OraclePhaseResult,
	OracleResult,
	OracleStatus,
)

from constants import ORACLE_RESULT_FILENAME
from ..loader import load_case_spec
from .discovery import (
	DiscoveredOracleClass,
	discover_oracle_classes_in_scope,
	oracle_import_scope,
	oracle_root_for,
)
from .oracle_checks_runtime import (
	OracleRuntimeRegistry,
	build_oracle_runtime_registry,
)
from .reporting import OracleReport, log_oracle_report

_SKIPPED_PHASE_SUMMARY = "skipped because a previous phase failed"


def _phase_result_from_report(
	name: str,
	report: OracleReport,
) -> OraclePhaseResult:
	"""Converts a check report into a phase-level result."""
	status = OracleStatus.SUCCESS if report.ok else OracleStatus.ERROR
	summary = "all checks passed" if report.ok else "one or more checks failed"
	return OraclePhaseResult(
		phase=name,
		status=status,
		summary=summary,
	)


def run_oracle_classes(
	context: OracleInput,
	*,
	classes: Sequence[DiscoveredOracleClass],
	failure_mode: OracleFailureMode | str = OracleFailureMode.FAIL_FAST,
) -> OracleResult:
	"""Runs discovered oracle phases in their configured order.

	Each phase is isolated so an unexpected exception becomes a phase result
	rather than escaping the orchestration loop. In fail-fast mode, remaining
	phases are recorded as pending after the first failure.

	Args:
		context: Paths and runtime state shared by all oracle phases.
		classes: Discovered phase implementations in execution order.
		failure_mode: Whether evaluation stops after the first failed phase.

	Returns:
		The aggregate oracle result.
	"""
	mode = OracleFailureMode(failure_mode)
	results: list[OraclePhaseResult] = []
	failed_phases: list[str] = []

	for index, definition in enumerate(classes):
		logger = logging.getLogger(f"oracle.{definition.name}")

		try:
			instance = definition.cls(
				context=context,
				logger=logger,
			)
			report = instance.report()
			log_oracle_report(
				logger,
				label=definition.name,
				report=report,
				verbose=False,
			)
			phase_result = _phase_result_from_report(
				definition.name,
				report,
			)
		except Exception as exc:
			# Preserve an aggregate result even when a phase fails
			phase_result = OraclePhaseResult(
				phase=definition.name,
				status=OracleStatus.ERROR,
				summary="phase raised an unexpected exception",
				error=f"{type(exc).__name__}: {exc}",
			)
			failed_phases.append(definition.name)

		results.append(phase_result)

		if phase_result.status != OracleStatus.SUCCESS and mode == OracleFailureMode.FAIL_FAST:
			# Pending entries preserve the complete phase plan in the result
			results.extend(_pending_phase_result(pending.name) for pending in classes[index + 1 :])
			break

	score = sum(1 for result in results if result.status == OracleStatus.SUCCESS)
	overall_status = OracleStatus.SUCCESS if score == len(classes) else OracleStatus.ERROR

	return OracleResult(
		status=overall_status,
		score=score,
		summary=f"Passed {score}/{len(classes)} phases.",
		phases=results,
		error=(
			None if score == len(classes) else f"oracle failed phases: {', '.join(failed_phases)}"
		),
	)


def _pending_phase_result(name: str) -> OraclePhaseResult:
	"""Creates the result for a phase skipped by fail-fast execution."""
	return OraclePhaseResult(
		phase=name,
		status=OracleStatus.PENDING,
		summary=_SKIPPED_PHASE_SUMMARY,
	)


def _resolve_workspace_dir(
	*,
	case_root: Path,
	artifact_dir: Path,
	runtime_result: Any,
	explicit_workspace_dir: Path | None,
) -> Path:
	"""Resolves the workspace used by oracle phases.

	Workspace selection follows this priority sequence:

	1. An explicitly supplied workspace.
	2. The workspace recorded by the task runtime.
	3. The case artifact directory, when present.
	4. The case root.

	Args:
		case_root: Root directory of the case.
		artifact_dir: Expected artifact directory for the case.
		runtime_result: Task result that may record a workspace path.
		explicit_workspace_dir: Caller-provided workspace override.

	Returns:
		The resolved workspace path.
	"""
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
	"""Loads and runs all oracle phases for a case.

	The runtime registry is closed on a best-effort basis. Closing the
	registry closes every target executor created during the invocation.
	The final result is also written on a best-effort basis so persistence
	failures do not replace the evaluation result returned to the caller.

	Args:
		case_dir: Root directory of the case.
		runtime_result: Result of the task runtime, when available.
		output_dir: Directory for oracle-generated output.
		case: Preloaded case specification, or None to load it from disk.
		workspace_dir: Explicit workspace override.
		runtime_session: Active runtime session available to oracle checks.
		runtime_backend: Backend associated with the active session.

	Returns:
		The aggregate oracle result.
	"""
	case_root = case_dir.resolve()
	out_dir = output_dir.resolve()
	out_dir.mkdir(parents=True, exist_ok=True)

	runtime_registry: OracleRuntimeRegistry | None = None

	try:
		spec = case or load_case_spec(case_root)
		if spec.oracle is None:
			raise RuntimeError(
				f"case {spec.id!r} does not define "
				"an oracle configuration"
			)

		failure_mode = spec.oracle.failure_mode
		artifact_dir = (
			case_root / ARTIFACT_DIRNAME
		).resolve(strict=False)
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
			oracle_targets=spec.oracle.targets,
			oracle_phase_targets=spec.oracle.phase_targets,
			runtime_result=runtime_result,
			runtime_session=runtime_session,
			runtime_backend=runtime_backend,
		)

		# Build one lazy executor registry for this oracle invocation.
		runtime_registry = build_oracle_runtime_registry(
			context
		)
		context.runtime_registry = runtime_registry

		oracle_root = oracle_root_for(case_root)
		with oracle_import_scope(
			case_root,
			oracle_root.name,
		):
			classes = discover_oracle_classes_in_scope(
				case_root,
				oracle_root,
			)
			result = run_oracle_classes(
				context,
				classes=classes,
				failure_mode=failure_mode,
			)
	except Exception as exc:
		# Represent setup, discovery, and orchestration failures through the
		# OracleResult API instead of propagating them to callers.
		traceback_text = traceback.format_exc()
		result = OracleResult(
			status=OracleStatus.ERROR,
			score=0,
			summary="Oracle evaluation failed.",
			error=(
				f"{type(exc).__name__}: {exc}\n"
				f"{traceback_text}"
			),
		)
	finally:
		if runtime_registry is not None:
			try:
				runtime_registry.close()
			except Exception:
				# Cleanup failures must not replace the evaluation result.
				logging.getLogger(__name__).exception(
					"failed to close oracle runtime registry"
				)

	try:
		(out_dir / ORACLE_RESULT_FILENAME).write_text(
			json.dumps(
				result.model_dump(mode="json"),
				indent=2,
			),
			encoding="utf-8",
		)
	except Exception:
		# The caller still receives the in-memory result when persistence
		# fails.
		logging.getLogger(__name__).exception(
			"failed to write oracle result file to %s",
			out_dir,
		)

	return result
