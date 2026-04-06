from __future__ import annotations

import json
import logging
import traceback

from pathlib import Path
from typing import Any, Callable, Sequence

from constants import ARTIFACT_SUBDIR
from models import (
    OracleInput,
    OracleFailureMode,
    OraclePhaseResult,
    OracleResult,
    OracleStatus,
)

from ..constants import ORACLE_RESULT_FILENAME
from ..loader import load_case_spec
from . import utils
from .discovery import (
    DiscoveredPhase,
    OracleLoadError,
    discover_oracle_phases_in_scope,
    oracle_import_scope,
    oracle_root_for,
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
    if not phases:
        return OracleResult(
            status=OracleStatus.SUCCESS,
            score=0,
            summary="No oracle phases configured.",
        )

    results: list[OraclePhaseResult] = []
    failed_phases: list[str] = []

    for index, phase_def in enumerate(phases):
        logger = logging.getLogger(f"oracle.{phase_def.name}")
        if on_phase_start is not None:
            on_phase_start(phase_def)

        try:
            instance = phase_def.cls(context=context, logger=logger)
            report = instance.report()

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

    runtime_workspace = None if runtime_result is None else getattr(runtime_result, "workspace_path", None)
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
) -> OracleResult:
    """Discover and run oracle phases for a case dir."""
    case_root = case_dir.resolve()
    out_dir = output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        spec = case or load_case_spec(case_root)
        oracle_root = oracle_root_for(case_root)
        failure_mode = spec.oracle.failure_mode if spec.oracle else OracleFailureMode.FAIL_FAST
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

        # Keep the import scope alive for the entire discovery + execution so
        # that oracle classes can use relative imports in their methods.
        with oracle_import_scope(case_root, oracle_root.name):
            phases = discover_oracle_phases_in_scope(case_root, oracle_root)
            result = run_phases(
                context,
                phases=phases,
                failure_mode=failure_mode,
            )
    except Exception as exc:
        tb = traceback.format_exc()
        result = OracleResult(
            status=OracleStatus.ERROR,
            score=0,
            summary="Oracle evaluation failed.",
            error=f"{type(exc).__name__}: {exc}\n{tb}",
        )

    try:
        (out_dir / ORACLE_RESULT_FILENAME).write_text(
            json.dumps(result.to_json_dict(), indent=2),
            encoding="utf-8",
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to write oracle result file to %s", out_dir
        )
    return result
