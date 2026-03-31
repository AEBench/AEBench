from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..case_runtime.loader import load_case_spec
from ..case_runtime.runtime import task_from_case_spec
from ..display import (
 BaseDashboardDisplay,
 CaseDisplaySession,
 DisplayEvent,
 DisplayKind,
 DisplayPanel,
)
from ..domain.models import (
 CaseRunResult,
 CaseStatus,
 OracleResult,
 OracleStatus,
 RunOptions,
 TaskStatus,
)
from ..runtime.config import AppContext
from ..runtime.events import CompositeEventSink, EventSink
from ..evaluator.oracles.execution import run_oracle
from ..project_config import ProjectConfigState
from ..reporting import task_paths_for
from ..run_control import RunControl
from ..utils import safe_name
from .task_service import TaskRunService, cleanup_workspace_for_result


class CaseRunService:
	def __init__(self, context: AppContext) -> None:
		self._context = context
		self._task_service = TaskRunService(context)

	def run(
	 self,
	 case_dir: Path,
	 *,
	 save_path: Path | None,
	 options: RunOptions,
	 sink: EventSink | None = None,
	 run_control: RunControl | None = None,
	) -> CaseRunResult:
		case_root = case_dir.resolve()
		case = load_case_spec(case_root)
		output_dir = _resolve_case_output_dir(
		 case_root, case.id, self._context.project_state, save_path
		)
		run_spec = task_from_case_spec(case_root, case, project_state=self._context.project_state)
		if (
		 options.prompt_profile is not None
		 or options.prompt_append is not None
		 or options.interactive
		):
			run_spec = run_spec.model_copy(
			 update={
			  "runtime": run_spec.runtime.model_copy(
			   update={
			    "interactive": run_spec.runtime.interactive or options.interactive,
			   }
			  ),
			  "prompt": run_spec.prompt.model_copy(
			   update={
			    "profile": options.prompt_profile or run_spec.prompt.profile,
			    "append": options.prompt_append
			    if options.prompt_append is not None
			    else run_spec.prompt.append,
			   }
			  ),
			 }
			)

		display_session = _build_display_session(
		 case_id=case.id,
		 case_root=case_root,
		 output_dir=output_dir,
		 dashboard=sink if isinstance(sink, BaseDashboardDisplay) else None,
		)
		case_sink = _case_sink(display_session, sink)
		with display_session:
			case_sink.emit(
			 DisplayEvent(
			  case_id=case.id,
			  kind=DisplayKind.LIFECYCLE.value,
			  panel=DisplayPanel.STATUS.value,
			  text=f"Case {case.id} started",
			  data={"case_dir": str(case_root), "output_dir": str(output_dir)},
			 )
			)
			runtime_result = self._task_service.run(
			 run_spec,
			 input_file=case_root / "case.toml",
			 output_dir=output_dir,
			 options=options,
			 sink=case_sink,
			 run_control=run_control,
			 defer_workspace_cleanup=True,
			)
			if runtime_result.status == TaskStatus.SUCCESS:
				case_sink.emit(
				 DisplayEvent(
				  case_id=case.id,
				  kind=DisplayKind.LIFECYCLE.value,
				  panel=DisplayPanel.STATUS.value,
				  text="Starting oracle evaluation",
				 )
				)
				oracle_result = run_oracle(
				 case_root,
				 runtime_result=runtime_result,
				 output_dir=output_dir,
				 case=case,
				)
				case_sink.emit(
				 DisplayEvent(
				  case_id=case.id,
				  kind=(
				   DisplayKind.ERROR.value
				   if oracle_result.status == OracleStatus.ERROR
				   else DisplayKind.STATUS.value
				  ),
				  panel=DisplayPanel.STATUS.value,
				  text=(
				   f"Oracle completed with status={oracle_result.status.value} "
				   f"score={oracle_result.score}/{case.oracle.expected_score}"
				  ),
				  is_error=oracle_result.status == OracleStatus.ERROR,
				 )
				)
			elif runtime_result.status == TaskStatus.INTERRUPTED:
				oracle_result = OracleResult(
				 status=OracleStatus.PENDING,
				 summary="Runtime interrupted; oracle was not executed.",
				 error=runtime_result.error,
				)
				case_sink.emit(
				 DisplayEvent(
				  case_id=case.id,
				  kind=DisplayKind.ERROR.value,
				  panel=DisplayPanel.STATUS.value,
				  text="Runtime interrupted; oracle was not executed.",
				  is_error=True,
				 )
				)
			else:
				oracle_result = OracleResult(
				 status=OracleStatus.PENDING,
				 summary="Runtime failed; oracle was not executed.",
				 error=runtime_result.error,
				)
				case_sink.emit(
				 DisplayEvent(
				  case_id=case.id,
				  kind=DisplayKind.ERROR.value,
				  panel=DisplayPanel.STATUS.value,
				  text="Runtime failed; oracle was not executed.",
				  is_error=True,
				 )
				)
				if runtime_result.error:
					case_sink.emit(
					 DisplayEvent(
					  case_id=case.id,
					  kind=DisplayKind.ERROR.value,
					  panel=DisplayPanel.STATUS.value,
					  text=f"Runtime error: {_compact_error(runtime_result.error)}",
					  is_error=True,
					 )
					)
			finished_at = datetime.now(timezone.utc)
			case_result = CaseRunResult(
			 id=case.id,
			 status=_case_status_for(runtime_result.status, oracle_result.status),
			 started_at=runtime_result.started_at,
			 finished_at=finished_at,
			 case_dir=str(case_root),
			 artifact_dir=str((case_root / "artifact").resolve()),
			 workspace_dir=runtime_result.workspace_path,
			 output_dir=str(output_dir),
			 case_card=case.case_card,
			 runtime_result=runtime_result,
			 oracle_result=oracle_result,
			)
			(output_dir / "case_result.json").write_text(
			 case_result.model_dump_json(indent=2),
			 encoding="utf-8",
			)
			case_sink.emit(
			 DisplayEvent(
			  case_id=case.id,
			  kind=(
			   DisplayKind.ERROR.value
			   if case_result.status != CaseStatus.SUCCESS
			   else DisplayKind.STATUS.value
			  ),
			  panel=DisplayPanel.STATUS.value,
			  text=f"Case {case.id} completed with status={case_result.status.value}",
			  is_error=case_result.status != CaseStatus.SUCCESS,
			 )
			)
			if case_result.status != CaseStatus.SUCCESS:
				error_text = oracle_result.error or runtime_result.error
				if error_text:
					case_sink.emit(
					 DisplayEvent(
					  case_id=case.id,
					  kind=DisplayKind.ERROR.value,
					  panel=DisplayPanel.STATUS.value,
					  text=f"Failure detail: {_compact_error(error_text)}",
					  is_error=True,
					 )
					)
				case_sink.emit(
				 DisplayEvent(
				  case_id=case.id,
				  kind=DisplayKind.ERROR.value,
				  panel=DisplayPanel.STATUS.value,
				  text=f"Outputs: {output_dir}",
				  is_error=True,
				 )
				)
			if options.cleanup_workspace:
				cleanup_workspace_for_result(
				 runtime_result.workspace_path,
				 preserve=case_result.status != CaseStatus.SUCCESS,
				 preserve_failed_workspace=self._context.settings.preserve_failed_workspace,
				)
			return case_result


def _build_display_session(
 *,
 case_id: str,
 case_root: Path,
 output_dir: Path,
 dashboard: BaseDashboardDisplay | None,
) -> CaseDisplaySession:
	task_paths = task_paths_for(output_dir, safe_name(case_id))
	return CaseDisplaySession(
	 case_id=case_id,
	 output_dir=output_dir,
	 log_path=task_paths.log_path,
	 transcript_path=task_paths.transcript_path,
	 rendered_log_path=task_paths.rendered_log_path,
	 runner_log_path=task_paths.runner_log_path,
	 infra_log_path=task_paths.infra_log_path,
	 progress_log_path=task_paths.progress_log_path,
	 tool_output_dir=task_paths.tool_output_dir,
	 workspace_path=case_root,
	 runtime_workspace_path=str(case_root),
	 dashboard=dashboard,
	 fallback_to_console=dashboard is None,
	)


def _compact_error(error: str, *, max_length: int = 240) -> str:
	first_line = error.strip().splitlines()[0] if error.strip() else "unknown error"
	if len(first_line) <= max_length:
		return first_line
	return first_line[: max_length - 3] + "..."


def _case_sink(display_session: CaseDisplaySession, sink: EventSink | None) -> EventSink:
	if sink is None or sink is display_session or isinstance(sink, BaseDashboardDisplay):
		return display_session
	return CompositeEventSink([display_session, sink])


def _resolve_case_output_dir(
 case_dir: Path,
 case_id: str,
 project_state: ProjectConfigState,
 save_path: Path | None,
) -> Path:
	if save_path is not None:
		target = save_path.expanduser().resolve()
		target.mkdir(parents=True, exist_ok=True)
		return target
	runs_root = project_state.config.resolve_case_runs_dir(project_state.root)
	case_runs_root = (runs_root / safe_name(case_id)).resolve()
	case_runs_root.mkdir(parents=True, exist_ok=True)
	_ensure_runs_link(case_dir, case_runs_root)
	timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%f")
	target = (case_runs_root / timestamp).resolve()
	target.mkdir(parents=True, exist_ok=True)
	return target


def _ensure_runs_link(case_dir: Path, case_runs_root: Path) -> None:
	link_path = case_dir / ".runs"
	if link_path.is_symlink():
		if link_path.resolve(strict=False) == case_runs_root:
			return
		link_path.unlink()
	elif link_path.exists():
		if link_path.is_dir() and not any(link_path.iterdir()):
			link_path.rmdir()
		else:
			raise RuntimeError(
			 f"bundle .runs path already exists and is not a symlink: {link_path}"
			)
	relative_target = Path(os.path.relpath(case_runs_root, start=case_dir))
	link_path.symlink_to(relative_target, target_is_directory=True)


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
