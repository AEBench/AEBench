from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Sequence, cast

from constants import ARTIFACT_SUBDIR
from models import OracleFailureMode, OracleInput, OraclePhaseResult, OracleResult, OracleStatus

from ..constants import ORACLE_RESULT_FILENAME
from ..loader import load_case_spec
from . import utils
from .discovery import DiscoveredOracleClass, OracleLoadError, discover_oracle_classes_in_scope, oracle_import_scope, oracle_root_for

_SKIPPED_PHASE_SUMMARY = "skipped because a previous phase failed"


def _phase_result_from_report(name: str, report: utils.OracleReport) -> OraclePhaseResult:
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
    mode = OracleFailureMode(failure_mode)
    results: list[OraclePhaseResult] = []
    failed_phases: list[str] = []

    for index, definition in enumerate(classes):
        logger = logging.getLogger(f"oracle.{definition.name}")
        try:
            instance = definition.cls(context=context, logger=logger)
            report = instance.report()
            utils.log_oracle_report(logger, label=definition.name, report=report, verbose=False)
            phase_result = _phase_result_from_report(definition.name, report)
        except Exception as exc:
            phase_result = OraclePhaseResult(
                phase=definition.name,
                status=OracleStatus.ERROR,
                summary="phase raised an unexpected exception",
                error=f"{type(exc).__name__}: {exc}",
            )
            failed_phases.append(definition.name)

        results.append(phase_result)
        if phase_result.status != OracleStatus.SUCCESS and mode == OracleFailureMode.FAIL_FAST:
            results.extend(_pending_phase_result(pending.name) for pending in classes[index + 1 :])
            break

    score = sum(1 for result in results if result.status == OracleStatus.SUCCESS)
    overall_status = OracleStatus.SUCCESS if score == len(classes) else OracleStatus.ERROR
    return OracleResult(
        status=overall_status,
        score=score,
        summary=f"Passed {score}/{len(classes)} phases.",
        phases=results,
        error=None if score == len(classes) else f"oracle failed phases: {', '.join(failed_phases)}",
    )


def _pending_phase_result(name: str) -> OraclePhaseResult:
    return OraclePhaseResult(phase=name, status=OracleStatus.PENDING, summary=_SKIPPED_PHASE_SUMMARY)


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
    runtime_session: Any | None = None,
    runtime_backend: Any | None = None,
) -> OracleResult:
    case_root = case_dir.resolve()
    out_dir = output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    context: OracleInput | None = None

    try:
        spec = case or load_case_spec(case_root)
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
        context.runtime_session = runtime_session
        context.runtime_backend = runtime_backend
        context.runtime_executor = utils.build_runtime_check_executor(context)

        oracle_root = oracle_root_for(case_root)
        with oracle_import_scope(case_root, oracle_root.name):
            classes = discover_oracle_classes_in_scope(case_root, oracle_root)
            result = run_oracle_classes(context, classes=classes, failure_mode=failure_mode)
    except Exception as exc:
        traceback_text = traceback.format_exc()
        result = OracleResult(
            status=OracleStatus.ERROR,
            score=0,
            summary="Oracle evaluation failed.",
            error=f"{type(exc).__name__}: {exc}\n{traceback_text}",
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
