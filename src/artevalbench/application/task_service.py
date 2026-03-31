from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO, cast

import anyio

from ..constants import SUMMARY_BASENAME_TEMPLATE
from ..display import DisplayEvent, DisplayKind, DisplayPanel, activate_display_sink
from ..domain.models import (
 AgentRequest,
 AgentResult,
 PromptContext,
 RunOptions,
 RunResult,
 RunSpec,
 RuntimeMode,
 TaskStatus,
)
from ..runtime.agent_drivers import build_agent_driver
from ..runtime.config import AppContext
from ..runtime.events import CompositeEventSink, EventSink, JsonlEventSink
from ..runtime.runtimes import build_runtime_backend
from ..log import activate_infra_capture
from ..prompting import build_prompt_bundle
from ..reporting import (
 read_agent_summary,
 task_paths_for,
 write_prompt_file,
 write_task_report,
)
from ..run_control import RunControl
from ..settings import AgentKind, McpTopology
from ..sources import prepare_workspace
from ..task_loader import append_summary_instruction, prepend_case_card, read_instruction_text
from ..utils import Tee, safe_name
from .session import DriverSession, TaskArtifacts, WorkspacePaths, build_bridge_paths


class TaskRunService:
	def __init__(self, context: AppContext) -> None:
		self._context = context

	def run(
	 self,
	 spec: RunSpec,
	 *,
	 input_file: Path,
	 output_dir: Path,
	 options: RunOptions,
	 sink: EventSink | None = None,
	 run_control: RunControl | None = None,
	 defer_workspace_cleanup: bool = False,
	) -> RunResult:
		return anyio.run(
		 self._run_async,
		 spec,
		 input_file,
		 output_dir,
		 options,
		 sink,
		 run_control,
		 defer_workspace_cleanup,
		)

	async def _run_async(
	 self,
	 spec: RunSpec,
	 input_file: Path,
	 output_dir: Path,
	 options: RunOptions,
	 sink: EventSink | None,
	 run_control: RunControl | None,
	 defer_workspace_cleanup: bool,
	) -> RunResult:
		spec.require_source()
		safe_id = safe_name(spec.id)
		task_paths = task_paths_for(output_dir, safe_id)
		transcript_sink = JsonlEventSink(task_paths.transcript_path)
		output_dir.mkdir(parents=True, exist_ok=True)
		workspace_root = _create_workspace_root(
		 spec.id, self._context.settings.ephemeral_workspace_root
		)
		workspace_run_root = workspace_root.parent
		result: RunResult | None = None
		source_prepare_started_at = datetime.now(timezone.utc)
		workspace_path = prepare_workspace(spec, input_file, workspace_root)
		source_prepare_finished_at = datetime.now(timezone.utc)
		source_prepare_duration_ms = int(
		 (source_prepare_finished_at - source_prepare_started_at).total_seconds() * 1000
		)
		refs_path = _bundle_refs_path(input_file)
		summary_path = workspace_path / SUMMARY_BASENAME_TEMPLATE.format(safe_id=safe_id)
		task_text = prepend_case_card(
		 read_instruction_text(workspace_path, spec.instructions.path),
		 spec.case_card,
		)
		task_text = append_summary_instruction(task_text, summary_path.name)
		effective_sink = _task_sink(sink, transcript_sink)
		_update_workspace_context(
		 effective_sink,
		 workspace_path=workspace_path,
		 runtime_workspace_path="/repo"
		 if spec.runtime.mode == RuntimeMode.DOCKER
		 else str(workspace_path),
		)

		runtime_workspace = (
		 "/repo" if spec.runtime.mode == RuntimeMode.DOCKER else str(workspace_path)
		)
		runtime_refs = "/refs" if spec.runtime.mode == RuntimeMode.DOCKER and refs_path else None
		shell_prompt_append = _shell_prompt_append(
		 runtime_mode=spec.runtime.mode,
		 agent_config=self._context.settings.agent,
		)
		prompt = build_prompt_bundle(
		 PromptContext(
		  task_text=task_text,
		  workspace_path=runtime_workspace,
		  runtime_mode=spec.runtime.mode,
		  timeout_ms=spec.runtime.timeout_ms,
		  interactive=spec.runtime.interactive,
		  prompt_profile=spec.prompt.profile,
		  prompt_append=_merge_prompt_append(spec.prompt.append, shell_prompt_append),
		  refs_path=runtime_refs,
		  host_workspace_path=str(workspace_path),
		  container_workspace_path=runtime_workspace
		  if spec.runtime.mode == RuntimeMode.DOCKER
		  else None,
		  preferred_shell="container" if spec.runtime.mode == RuntimeMode.DOCKER else "host",
		  host_shell_policy="auxiliary"
		  if spec.runtime.mode == RuntimeMode.DOCKER
		  else "primary",
		  host_agent_controls_container_shell=_host_agent_controls_container_shell(
		   runtime_mode=spec.runtime.mode,
		   agent_config=self._context.settings.agent,
		  ),
		 )
		)
		write_prompt_file(task_paths.prompt_path, prompt)
		bridge_paths = build_bridge_paths(
		 output_dir=output_dir,
		 run_spec=spec,
		 host_workspace=workspace_path,
		)
		session = DriverSession(
		 run_spec=spec,
		 workspace=WorkspacePaths(
		  host_workspace=workspace_path,
		  runtime_workspace=runtime_workspace,
		  host_refs=refs_path,
		  runtime_refs=runtime_refs,
		 ),
		 artifacts=TaskArtifacts(
		  output_dir=output_dir,
		  task_paths=task_paths,
		  summary_path=summary_path,
		  bridge_paths=bridge_paths,
		 ),
		 prompt=prompt,
		 settings=self._context.settings,
		 run_control=run_control,
		)
		runtime_backend = build_runtime_backend(spec.runtime.mode)
		session.runtime_backend = runtime_backend
		driver = build_agent_driver(self._context.settings.agent)
		request = AgentRequest(
		 model=options.model_name,
		 system_prompt=prompt.system_prompt,
		 initial_prompt=prompt.initial_prompt,
		 interactive=spec.runtime.interactive,
		 timeout_ms=spec.runtime.timeout_ms,
		 driver_kind=self._context.settings.agent.kind.value,
		 driver_options=self._context.settings.agent.driver_options(),
		)
		_write_log_header(task_paths.log_path, spec.id, workspace_path, spec.runtime.mode)
		prepare_started_at = datetime.now(timezone.utc)
		prepare_finished_at = prepare_started_at
		started_at = prepare_started_at
		agent_result = AgentResult(model=options.model_name, exit_code=1, output="")
		error_message: str | None = None
		runtime_prepared = False
		driver_prepared = False
		try:
			display_capture = activate_display_sink(effective_sink)
			with display_capture, activate_infra_capture():
				effective_sink.emit(
				 DisplayEvent(
				  case_id=spec.id,
				  kind=DisplayKind.LIFECYCLE.value,
				  panel=DisplayPanel.STATUS.value,
				  text=f"Task {spec.id} started",
				  data={
				   "workspace_path": str(workspace_path),
				   "runtime_mode": spec.runtime.mode.value,
				  },
				 )
				)
				await runtime_backend.prepare(session, effective_sink)
				runtime_prepared = True
				await driver.prepare(session, effective_sink)
				driver_prepared = True
				prepare_finished_at = datetime.now(timezone.utc)
				started_at = prepare_finished_at
				with (
				 Tee(sys.stdout, task_paths.log_path) as tee_out,
				 Tee(sys.stderr, task_paths.log_path) as tee_err,
				):
					with (
					 redirect_stdout(cast(TextIO, tee_out)),
					 redirect_stderr(cast(TextIO, tee_err)),
					):
						agent_result = await driver.execute(request, session, effective_sink)
				effective_sink.emit(
				 DisplayEvent(
				  case_id=spec.id,
				  kind=(
				   DisplayKind.ERROR.value
				   if agent_result.exit_code != 0
				   else DisplayKind.LIFECYCLE.value
				  ),
				  panel=DisplayPanel.STATUS.value,
				  text=f"Agent exited with code={agent_result.exit_code}",
				  is_error=agent_result.exit_code != 0,
				 )
				)
				if agent_result.exit_code != 0:
					error_message = (
					 agent_result.output or f"agent exited with code {agent_result.exit_code}"
					)
		except Exception as exc:
			prepare_finished_at = datetime.now(timezone.utc)
			started_at = prepare_finished_at
			error_message = _record_exception(task_paths.log_path, "task_run", exc)
		finally:
			if runtime_prepared:
				try:
					await runtime_backend.collect_artifacts(session, effective_sink)
				except Exception as exc:
					error_message = error_message or _record_exception(
					 task_paths.log_path, "collect_artifacts", exc
					)
			if driver_prepared:
				try:
					await driver.cleanup(session, effective_sink)
				except Exception as exc:
					error_message = error_message or _record_exception(
					 task_paths.log_path, "driver_cleanup", exc
					)
			if runtime_prepared:
				try:
					await runtime_backend.cleanup(session, effective_sink)
				except Exception as exc:
					error_message = error_message or _record_exception(
					 task_paths.log_path, "runtime_cleanup", exc
					)

		finished_at = datetime.now(timezone.utc)
		prepare_duration_ms = int((prepare_finished_at - prepare_started_at).total_seconds() * 1000)
		duration_ms = int((finished_at - started_at).total_seconds() * 1000)
		interrupted = bool(run_control is not None and run_control.stop_requested)
		if interrupted and not error_message:
			error_message = "Interrupted by user"
		prepare_breakdown_ms = {
		 f"runtime_{spec.runtime.mode.value}_prepare": prepare_duration_ms,
		 f"source_{spec.require_source().type.value}_prepare": source_prepare_duration_ms,
		}
		result = RunResult(
		 id=spec.id,
		 status=(
		  TaskStatus.INTERRUPTED
		  if interrupted
		  else TaskStatus.SUCCESS
		  if error_message is None and agent_result.exit_code == 0
		  else TaskStatus.ERROR
		 ),
		 started_at=started_at,
		 finished_at=finished_at,
		 prepare_duration_ms=prepare_duration_ms + source_prepare_duration_ms,
		 prepare_breakdown_ms=prepare_breakdown_ms,
		 duration_ms=duration_ms,
		 workspace_path=str(workspace_path),
		 log_path=str(task_paths.log_path),
		 transcript_path=str(task_paths.transcript_path),
		 rendered_log_path=str(task_paths.rendered_log_path),
		 runner_log_path=str(task_paths.runner_log_path),
		 infra_log_path=str(task_paths.infra_log_path),
		 progress_log_path=str(task_paths.progress_log_path),
		 summary_path=str(summary_path),
		 prompt_profile=spec.prompt.profile,
		 runtime=runtime_backend.runtime_result(session),
		 agent_kind=self._context.settings.agent.kind.value,
		 agent=agent_result,
		 error=error_message,
		)
		effective_sink.emit(
		 DisplayEvent(
		  case_id=spec.id,
		  kind=DisplayKind.STATUS.value,
		  panel=DisplayPanel.STATUS.value,
		  text=f"Runtime completed with status={result.status.value}",
		  is_error=result.status != TaskStatus.SUCCESS,
		  data={
		   "prepare_duration_ms": result.prepare_duration_ms,
		   "duration_ms": result.duration_ms,
		   "prepare_breakdown_ms": result.prepare_breakdown_ms,
		  },
		 )
		)
		write_task_report(task_paths.report_path, result, read_agent_summary(summary_path, result))
		if options.cleanup_workspace and not defer_workspace_cleanup:
			_cleanup_workspace(
			 workspace_run_root,
			 preserve=result.status != TaskStatus.SUCCESS,
			 preserve_failed_workspace=self._context.settings.preserve_failed_workspace,
			)
		return result


def _create_workspace_root(task_id: str, root_parent: Path) -> Path:
	root_parent.mkdir(parents=True, exist_ok=True)
	root = Path(
	 tempfile.mkdtemp(prefix=f"ae_workspace_{safe_name(task_id)}_", dir=str(root_parent))
	)
	workspace_root = root / "workspace"
	workspace_root.mkdir(parents=True, exist_ok=True)
	return workspace_root


def _task_sink(sink: EventSink | None, transcript_sink: JsonlEventSink) -> EventSink:
	if sink is None:
		return transcript_sink
	if sink.__class__.__name__ == "CaseDisplaySession":
		return sink
	return CompositeEventSink([sink, transcript_sink])


def _update_workspace_context(
 sink: EventSink,
 *,
 workspace_path: Path,
 runtime_workspace_path: str,
) -> None:
	updater = getattr(sink, "update_workspace_context", None)
	if updater is None:
		return
	updater(
	 workspace_path=workspace_path,
	 runtime_workspace_path=runtime_workspace_path,
	)


def _write_log_header(
 log_path: Path, task_id: str, workspace_path: Path, runtime_mode: RuntimeMode
) -> None:
	log_path.write_text(
	 f"Task {task_id} started at {datetime.now(timezone.utc).isoformat()}\n"
	 f"Workspace path: {workspace_path}\n"
	 f"Runtime mode: {runtime_mode.value}\n\n",
	 encoding="utf-8",
	)


def _record_exception(log_path: Path, phase: str, exc: Exception) -> str:
	message = f"{type(exc).__name__} during {phase}: {exc}"
	with log_path.open("a", encoding="utf-8") as handle:
		handle.write(f"\n[{phase} error]\n{message}\n")
		handle.write(traceback.format_exc())
		handle.write("\n")
	return message


def _bundle_refs_path(input_file: Path) -> Path | None:
	case_dir = input_file.resolve().parent
	refs_dir = case_dir / "refs"
	if refs_dir.is_dir():
		return refs_dir
	return None


def _cleanup_workspace(
 workspace_run_root: Path,
 *,
 preserve: bool,
 preserve_failed_workspace: bool,
) -> None:
	if preserve and preserve_failed_workspace:
		return
	shutil.rmtree(workspace_run_root, ignore_errors=True)


def cleanup_workspace_for_result(
 workspace_path: Path | str,
 *,
 preserve: bool,
 preserve_failed_workspace: bool,
) -> None:
	workspace_run_root = _workspace_run_root(Path(workspace_path).resolve())
	_cleanup_workspace(
	 workspace_run_root,
	 preserve=preserve,
	 preserve_failed_workspace=preserve_failed_workspace,
	)


def _workspace_run_root(workspace_path: Path) -> Path:
	if len(workspace_path.parents) < 2:
		raise RuntimeError(f"cannot determine workspace run root from: {workspace_path}")
	return workspace_path.parents[1]


def _host_agent_controls_container_shell(
 *,
 runtime_mode: RuntimeMode,
 agent_config: object,
) -> bool:
	if runtime_mode != RuntimeMode.DOCKER:
		return False
	kind = getattr(agent_config, "kind", None)
	if kind == AgentKind.CLI:
		return bool(
		 getattr(agent_config, "cli_shim_shells", False)
		 or getattr(agent_config, "cli_expose_container_shell", False)
		)
	return (
	 kind == AgentKind.MCP_CLIENT
	 and getattr(agent_config, "mcp_topology", None) == McpTopology.MCP_HOST_BRIDGE
	)


def _merge_prompt_append(base: str | None, extra: str | None) -> str | None:
	parts = [part.strip() for part in (base, extra) if part and part.strip()]
	if not parts:
		return None
	return "\n\n".join(parts)


def _shell_prompt_append(
 *,
 runtime_mode: RuntimeMode,
 agent_config: object,
) -> str | None:
	if runtime_mode != RuntimeMode.DOCKER:
		return None
	kind = getattr(agent_config, "kind", None)
	if kind == AgentKind.CLI:
		shim_shells = bool(getattr(agent_config, "cli_shim_shells", False))
		expose_container_shell = bool(getattr(agent_config, "cli_expose_container_shell", False))
		expose_host_shell = bool(getattr(agent_config, "cli_expose_host_shell", False))
		if shim_shells:
			lines = [
			 "DOCKER SHELL CONTRACT:",
			 "- Use ordinary bash/sh commands for task execution; in this benchmark run they are routed to the container shell.",
			 "- Treat the host shell as auxiliary only.",
			]
			if expose_host_shell:
				lines.append(
				 "- Use `artevalbench-host-bash` only for host-only observation or debugging."
				)
			return "\n".join(lines)
		if expose_container_shell:
			lines = [
			 "DOCKER SHELL CONTRACT:",
			 "- Use `artevalbench-container-bash` for task-execution commands in the benchmark container.",
			 "- Treat the host shell as auxiliary only.",
			]
			if expose_host_shell:
				lines.append(
				 "- Use `artevalbench-host-bash` only for host-only observation or debugging."
				)
			return "\n".join(lines)
		return None
	if (
	 kind == AgentKind.MCP_CLIENT
	 and getattr(agent_config, "mcp_topology", None) == McpTopology.MCP_HOST_BRIDGE
	):
		return (
		 "DOCKER SHELL CONTRACT:\n"
		 "- Use the `artevalbench_container_bash` tool for task-execution commands in the benchmark container.\n"
		 "- Treat the host shell as auxiliary only; do not use it as the primary execution path."
		)
	return None
