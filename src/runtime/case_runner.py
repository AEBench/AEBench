"""Case runner: task execution then oracle evaluation."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from console.dashboard import BaseDashboardDisplay, CaseDisplaySession, DisplayEvent, DisplayKind, DisplayPanel
from evaluator import artifact_dir_for
from evaluator.loader import load_case_spec
from models import CaseRunResult, CaseStatus, OracleResult, OracleStatus, RunOptions, TaskStatus
from run_control import RunControl
from utils import send_event, safe_name
from .cases import task_from_case_spec
from .workspace import cleanup_workspace_tree
from .oracle_runner import DirectOracleRunner, OracleRunner
from .reporting import task_paths_for
from .task_runner import TaskRunner


class CaseRunner:
    def __init__(self, context, *, oracle_runner: OracleRunner | None = None) -> None:
        self._context = context
        self._task_runner = TaskRunner(context)
        self._oracle_runner = oracle_runner or DirectOracleRunner()

    def run(
        self,
        case_dir: Path,
        *,
        save_path: Path | None,
        options: RunOptions,
        listener=None,
        run_control: RunControl | None = None,
    ) -> CaseRunResult:
        case_root = case_dir.resolve()
        case = load_case_spec(case_root)
        output_dir = _resolve_case_output_dir(case_root, case.id, self._context.project_state, save_path)
        run_spec = task_from_case_spec(case_root, case, project_state=self._context.project_state)

        _dashboard = listener if isinstance(listener, BaseDashboardDisplay) else None
        tmp_root = Path(self._context.settings.tmp_workspace_root).expanduser().resolve()

        display_session = CaseDisplaySession(
            case_id=case.id,
            output_dir=output_dir,
            task_paths=task_paths_for(output_dir, safe_name(case.id)),
            workspace_path=tmp_root,
            runtime_workspace_path="/repo" if run_spec.runtime.mode.value == "docker" else str(tmp_root),
            dashboard=_dashboard,
            fallback_to_console=_dashboard is None,
        )
        case_sink = display_session if listener is None else listener

        with display_session:
            send_event(
                case_sink,
                DisplayEvent(
                    case_id=case.id,
                    kind=DisplayKind.START.value,
                    panel=DisplayPanel.STATUS.value,
                    text=f"Case {case.id} started",
                    data={"case_dir": str(case_root), "output_dir": str(output_dir)},
                ),
            )

            runtime_result = self._task_runner.run(
                run_spec,
                input_file=case_root / "case.toml",
                output_dir=output_dir,
                options=options,
                listener=case_sink,
                run_control=run_control,
                defer_workspace_cleanup=True,
            )

            # Workspace cleanup is deferred from task_runner, so we must
            # guarantee it runs even if oracle/reporting code throws.
            case_result: CaseRunResult | None = None
            try:
                if runtime_result.status == TaskStatus.SUCCESS:
                    send_event(
                        case_sink,
                        DisplayEvent(
                            case_id=case.id,
                            kind=DisplayKind.START.value,
                            panel=DisplayPanel.STATUS.value,
                            text="Starting oracle evaluation",
                        ),
                    )
                    try:
                        oracle_result = self._oracle_runner.execute(
                            case_root,
                            runtime_result=runtime_result,
                            output_dir=output_dir,
                            case=case,
                        )
                    except Exception as exc:
                        logging.getLogger(__name__).error(
                            "oracle execution raised for %s: %s",
                            case.id,
                            exc,
                            exc_info=True,
                        )
                        oracle_result = OracleResult(
                            status=OracleStatus.ERROR,
                            score=0,
                            summary="Oracle execution raised an unexpected exception.",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                elif runtime_result.status == TaskStatus.INTERRUPTED:
                    oracle_result = OracleResult(
                        status=OracleStatus.PENDING,
                        summary="Runtime interrupted; oracle was not executed.",
                        error=runtime_result.error,
                    )
                else:
                    oracle_result = OracleResult(
                        status=OracleStatus.PENDING,
                        summary="Runtime failed; oracle was not executed.",
                        error=runtime_result.error,
                    )

                finished_at = datetime.now(timezone.utc)
                case_result = CaseRunResult(
                    status=_case_status_for(runtime_result.status, oracle_result.status),
                    finished_at=finished_at,
                    case_dir=str(case_root),
                    artifact_dir=str(artifact_dir_for(case_root)),
                    output_dir=str(output_dir),
                    case_brief=case.case_brief,
                    runtime_result=runtime_result,
                    oracle_result=oracle_result,
                )

                (output_dir / "case_result.json").write_text(
                    case_result.model_dump_json(indent=2),
                    encoding="utf-8",
                )

                send_event(
                    case_sink,
                    DisplayEvent(
                        case_id=case.id,
                        kind=DisplayKind.ERROR.value if case_result.status != CaseStatus.SUCCESS else DisplayKind.STATUS.value,
                        panel=DisplayPanel.STATUS.value,
                        text=f"Case {case.id} completed with status={case_result.status.value}",
                        is_error=case_result.status != CaseStatus.SUCCESS,
                        data={"workspace_path": runtime_result.workspace_path or ""},
                    ),
                )
            finally:
                if options.cleanup_workspace and runtime_result.workspace_path:
                    cleanup_workspace_tree(
                        runtime_result.workspace_path,
                        preserve=case_result is None or case_result.status != CaseStatus.SUCCESS,
                        preserve_failed_workspace=self._context.settings.preserve_failed_workspace,
                    )
            return case_result


def _resolve_case_output_dir(case_dir: Path, case_id: str, project_state, save_path: Path | None) -> Path:
    if save_path is not None:
        output_dir = save_path.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    case_runs_root = (project_state.config.resolve_case_runs_dir(project_state.root) / safe_name(case_id)).resolve()
    case_runs_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%f")
    output_dir = (case_runs_root / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _case_status_for(runtime_status: TaskStatus, oracle_status: OracleStatus) -> CaseStatus:
    if runtime_status == TaskStatus.INTERRUPTED:
        return CaseStatus.INTERRUPTED
    if runtime_status != TaskStatus.SUCCESS:
        return CaseStatus.ERROR
    if oracle_status == OracleStatus.ERROR:
        return CaseStatus.ERROR
    if oracle_status == OracleStatus.PENDING:
        return CaseStatus.PENDING
    return CaseStatus.SUCCESS