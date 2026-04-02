"""Task runner: workspace setup, agent exec, result recording."""

from __future__ import annotations

import logging
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TextIO, cast

logger = logging.getLogger(__name__)

from ..constants import SUMMARY_BASENAME_TEMPLATE
from ..display import DisplayEvent, DisplayKind, DisplayPanel, activate_display_sink
from ..log import activate_infra_capture
from ..domain.models import (
    AgentRequest,
    AgentResult,
    PromptContext as PromptArgs,
    RunOptions,
    RunResult,
    RunSpec as TaskConfig,
    RuntimeMode,
    RuntimeResult as RuntimeInfo,
    TaskStatus,
)
from ..prompting import build_prompt_bundle
from ..run_control import RunControl
from ..sources import prepare_workspace
from ..task_loader import append_summary_instruction, prepend_case_card, read_instruction_text
from ..utils import Tee, send_event, safe_name
from .backend import get_runtime
from .driver import PythonAgent, RemoteAgent, get_agent
from .workspace import bundle_refs_path, cleanup_workspace_tree, create_workspace_root
from .reporting import (
    append_run_result,
    read_agent_summary,
    task_paths_for,
    write_prompt_file,
    write_task_report,
)
from .session import RunSession


class TaskRunner:
    def __init__(self, context) -> None:
        self._context = context

    def run(
        self,
        spec: TaskConfig,
        *,
        input_file: Path,
        output_dir: Path,
        options: RunOptions,
        listener=None,
        run_control: RunControl | None = None,
        defer_workspace_cleanup: bool = False,
    ) -> RunResult:
        source = spec.require_source()
        source_type_label = source.type.value
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        task_id = safe_name(spec.id)
        task_paths = task_paths_for(output_dir, task_id)
        task_paths.tool_output_dir.mkdir(parents=True, exist_ok=True)

        requested_prompt_profile = options.prompt_profile or spec.prompt_profile
        _write_log_header(
            task_paths.log_path,
            spec.id,
            workspace_path=None,
            runtime_mode=spec.runtime.mode,
        )

        source_prepare_started_at = datetime.now(timezone.utc)
        source_prepare_finished_at = source_prepare_started_at
        runtime_prepare_started_at: datetime | None = None
        prepare_finished_at = source_prepare_started_at
        agent_started_at: datetime | None = None
        agent_finished_at: datetime | None = None
        cleanup_finished_at = source_prepare_started_at

        workspace_path: Path | None = None
        refs_path: Path | None = None
        summary_path: Path | None = None
        session: RunSession | None = None
        runtime_backend = None
        driver = None

        agent_result = AgentResult(model=options.model_name or self._context.settings.default_model, exit_code=1, output="", message_count=0)
        error_message: str | None = None

        try:
            workspace_path = create_workspace_root(
                spec.id,
                Path(self._context.settings.tmp_workspace_root).expanduser().resolve(),
            )
            _write_log_header(
                task_paths.log_path,
                spec.id,
                workspace_path=workspace_path,
                runtime_mode=spec.runtime.mode,
            )

            workspace_path = prepare_workspace(spec, input_file, workspace_path)
            source_prepare_finished_at = datetime.now(timezone.utc)

            refs_path = bundle_refs_path(input_file)
            summary_path = workspace_path / SUMMARY_BASENAME_TEMPLATE.format(safe_id=task_id)

            task_text = prepend_case_card(
                read_instruction_text(workspace_path, spec.instructions_path),
                spec.case_card,
            )
            task_text = append_summary_instruction(task_text, summary_path.name)

            runtime_workspace = "/repo" if spec.runtime.mode == RuntimeMode.DOCKER else str(workspace_path)
            runtime_refs = "/refs" if (spec.runtime.mode == RuntimeMode.DOCKER and refs_path) else (str(refs_path) if refs_path else None)

            prompt = build_prompt_bundle(
                PromptArgs(
                    task_text=task_text,
                    workspace_path=runtime_workspace,
                    runtime_mode=spec.runtime.mode,
                    timeout_ms=spec.runtime.timeout_ms,
                    interactive=spec.runtime.interactive or options.interactive,
                    prompt_profile=requested_prompt_profile,
                    prompt_append=options.prompt_append if options.prompt_append is not None else spec.prompt_append,
                    refs_path=runtime_refs,
                    host_workspace_path=str(workspace_path),
                    container_workspace_path=runtime_workspace if spec.runtime.mode == RuntimeMode.DOCKER else None,
                )
            )
            write_prompt_file(task_paths.prompt_path, prompt)

            effective_image = spec.runtime.image or getattr(self._context.settings, "default_docker_image", None)
            runtime_backend = get_runtime(
                spec.runtime.mode,
                image=effective_image,
                gpu=getattr(spec.runtime, "gpu", False),
            )

            session = RunSession(
                run_spec=spec,
                prompt=prompt,
                settings=self._context.settings,
                run_control=run_control,
                host_workspace=workspace_path,
                runtime_workspace=runtime_workspace,
                host_refs=refs_path,
                runtime_refs=runtime_refs,
                output_dir=output_dir,
                task_paths=task_paths,
                summary_path=summary_path,
                runtime_backend=runtime_backend,
            )

            driver = get_agent(self._context.settings)
            _validate_driver_runtime_compatibility(driver, spec.runtime.mode)

            request = AgentRequest(
                model=options.model_name or self._context.settings.default_model,
                system_prompt=prompt.system_prompt,
                initial_prompt=prompt.initial_prompt,
                interactive=spec.runtime.interactive or options.interactive,
                timeout_ms=spec.runtime.timeout_ms,
                agent_type=self._context.settings.agent.agent_type.value,
                agent_options=self._context.settings.agent.agent_options(),
            )

            runtime_prepare_started_at = datetime.now(timezone.utc)

            with activate_display_sink(listener), activate_infra_capture():
                send_event(
                    listener,
                    DisplayEvent(
                        case_id=spec.id,
                        kind=DisplayKind.START.value,
                        panel=DisplayPanel.STATUS.value,
                        text=f"Task {spec.id} started",
                        data={
                            "workspace_path": str(workspace_path),
                            "runtime_mode": spec.runtime.mode.value,
                        },
                    ),
                )

                runtime_backend.prepare(session, listener=listener)
                driver.prepare(session, listener=listener)

                prepare_finished_at = datetime.now(timezone.utc)
                agent_started_at = prepare_finished_at

                with (
                    Tee(sys.stdout, task_paths.log_path) as tee_out,
                    Tee(sys.stderr, task_paths.log_path) as tee_err,
                    redirect_stdout(cast(TextIO, tee_out)),
                    redirect_stderr(cast(TextIO, tee_err)),
                ):
                    if run_control is not None and run_control.stop_requested:
                        raise RuntimeError("run interrupted before agent execution")
                    agent_result = driver.execute(request, session, listener=listener)

                agent_finished_at = datetime.now(timezone.utc)
                _append_agent_output(task_paths.log_path, agent_result)

                if agent_result.exit_code != 0:
                    error_message = agent_result.output or f"agent exited with code {agent_result.exit_code}"

                send_event(
                    listener,
                    DisplayEvent(
                        case_id=spec.id,
                        kind=DisplayKind.ERROR.value if agent_result.exit_code != 0 else DisplayKind.STATUS.value,
                        panel=DisplayPanel.STATUS.value,
                        text=f"Agent exited with code={agent_result.exit_code}",
                        is_error=agent_result.exit_code != 0,
                    ),
                )

        except Exception as exc:
            now = datetime.now(timezone.utc)
            if source_prepare_finished_at == source_prepare_started_at:
                source_prepare_finished_at = now
            if runtime_prepare_started_at is not None and agent_started_at is None:
                prepare_finished_at = now
            elif agent_started_at is None:
                prepare_finished_at = max(source_prepare_finished_at, now)
            if agent_started_at is not None and agent_finished_at is None:
                agent_finished_at = now
            error_message = _record_exception(task_paths.log_path, "task_run", exc)

        finally:
            if session is not None and runtime_backend is not None:
                try:
                    runtime_backend.collect_artifacts(session, listener=listener)
                except Exception as exc:
                    error_message = error_message or _record_exception(task_paths.log_path, "collect_artifacts", exc)
                try:
                    if driver is not None:
                        driver.cleanup(session, listener=listener)
                except Exception as exc:
                    error_message = error_message or _record_exception(task_paths.log_path, "driver_cleanup", exc)
                try:
                    runtime_backend.cleanup(session, listener=listener)
                except Exception as exc:
                    error_message = error_message or _record_exception(task_paths.log_path, "runtime_cleanup", exc)
            cleanup_finished_at = datetime.now(timezone.utc)

        interrupted = bool(run_control is not None and run_control.stop_requested)
        if interrupted and not error_message:
            error_message = "Interrupted by user"

        source_prepare_duration_ms = _duration_ms(source_prepare_started_at, source_prepare_finished_at)
        runtime_prepare_duration_ms = (
            _duration_ms(runtime_prepare_started_at, prepare_finished_at)
            if runtime_prepare_started_at is not None
            else 0
        )

        if agent_started_at is None:
            agent_started_at = prepare_finished_at
        if agent_finished_at is None:
            agent_finished_at = agent_started_at

        result = RunResult(
            id=spec.id,
            status=(
                TaskStatus.INTERRUPTED
                if interrupted
                else TaskStatus.SUCCESS
                if error_message is None and agent_result.exit_code == 0
                else TaskStatus.ERROR
            ),
            started_at=agent_started_at,
            finished_at=cleanup_finished_at,
            prepare_duration_ms=source_prepare_duration_ms + runtime_prepare_duration_ms,
            prepare_breakdown_ms={
                f"source_{source_type_label}_prepare": source_prepare_duration_ms,
                f"runtime_{spec.runtime.mode.value}_prepare": runtime_prepare_duration_ms,
            },
            duration_ms=_duration_ms(agent_started_at, agent_finished_at),
            workspace_path=str(workspace_path) if workspace_path is not None else "",
            output_dir=str(output_dir),
            summary_path=str(summary_path) if summary_path is not None else "",
            prompt_profile=requested_prompt_profile,
            runtime=(
                runtime_backend.runtime_result(session)
                if session is not None and runtime_backend is not None
                else _default_runtime_result(spec.runtime.mode, spec.runtime.image or getattr(self._context.settings, "default_docker_image", None))
            ),
            agent_kind=self._context.settings.agent.agent_type.value,
            agent=agent_result,
            error=error_message,
        )

        report_summary_path = summary_path or (output_dir / SUMMARY_BASENAME_TEMPLATE.format(safe_id=task_id))
        write_task_report(task_paths.report_path, result, read_agent_summary(report_summary_path, result))
        append_run_result(output_dir, result)

        if options.cleanup_workspace and not defer_workspace_cleanup and workspace_path is not None:
            cleanup_workspace_tree(
                workspace_path,
                preserve=result.status != TaskStatus.SUCCESS,
                preserve_failed_workspace=self._context.settings.preserve_failed_workspace,
            )

        send_event(
            listener,
            DisplayEvent(
                case_id=spec.id,
                kind=DisplayKind.ERROR.value if result.status != TaskStatus.SUCCESS else DisplayKind.STATUS.value,
                panel=DisplayPanel.STATUS.value,
                text=f"Runtime completed with status={result.status.value}",
                is_error=result.status != TaskStatus.SUCCESS,
                data={
                    "prepare_duration_ms": result.prepare_duration_ms,
                    "duration_ms": result.duration_ms,
                    "workspace_path": result.workspace_path,
                },
            ),
        )
        return result


def run_tasks_from_jsonl(
    context,
    *,
    input_file: Path,
    output_dir: Path,
    options: RunOptions,
    listener=None,
    run_control: RunControl | None = None,
) -> list[RunResult]:
    runner = TaskRunner(context)
    results: list[RunResult] = []

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for spec in _load_specs_from_jsonl(input_file):
        task_output_dir = output_dir / safe_name(spec.id)
        task_output_dir.mkdir(parents=True, exist_ok=True)

        actual_input_file = _resolve_task_input_file(context, spec, default_input_file=input_file)

        result = runner.run(
            spec,
            input_file=actual_input_file,
            output_dir=task_output_dir,
            options=options,
            listener=listener,
            run_control=run_control,
        )
        results.append(result)
        if run_control is not None and run_control.stop_requested:
            break
    return results


def _load_specs_from_jsonl(input_file: Path) -> Iterable[TaskConfig]:
    with input_file.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                yield TaskConfig.model_validate_json(text)
            except Exception as exc:
                raise RuntimeError(f"invalid task JSONL at {input_file}:{lineno}: {exc}") from exc


def _resolve_task_input_file(context, spec: TaskConfig, *, default_input_file: Path) -> Path:
    try:
        from .cases import resolve_case_dir

        case_dir = resolve_case_dir(spec.id, project_state=context.project_state)
        candidate = case_dir / "case.toml"
        if candidate.is_file():
            return candidate
    except Exception:
        logger.debug("failed to resolve case dir for %s, using default input file", spec.id, exc_info=True)
    return default_input_file


def _validate_driver_runtime_compatibility(driver, runtime_mode: RuntimeMode) -> None:
    if runtime_mode != RuntimeMode.DOCKER:
        return
    if isinstance(driver, PythonAgent):
        raise RuntimeError(
            "python driver is not supported with runtime.mode='docker'; it executes on the host."
        )
    if isinstance(driver, RemoteAgent):
        raise RuntimeError(
            "remote driver is not supported with runtime.mode='docker' unless the remote service "
            "is explicitly container-aware."
        )


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int:
    if started_at is None or finished_at is None:
        return 0
    return int((finished_at - started_at).total_seconds() * 1000)


def _record_exception(log_path: Path, phase: str, exc: Exception) -> str:
    message = f"{type(exc).__name__} during {phase}: {exc}"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{phase} error]\n{message}\n")
        handle.write(traceback.format_exc())
        handle.write("\n")
    return message


def _append_agent_output(log_path: Path, agent_result: AgentResult) -> None:
    if not agent_result.output:
        return
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n[agent output]\n")
        handle.write(agent_result.output)
        if not agent_result.output.endswith("\n"):
            handle.write("\n")


def _write_log_header(
    log_path: Path,
    task_id: str,
    workspace_path: Path | None,
    runtime_mode: RuntimeMode,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"Task {task_id} started at {datetime.now(timezone.utc).isoformat()}\n"
        f"Workspace path: {workspace_path if workspace_path is not None else 'pending'}\n"
        f"Runtime mode: {runtime_mode.value}\n\n",
        encoding="utf-8",
    )


def _default_runtime_result(mode: RuntimeMode, image: str | None) -> RuntimeInfo:
    return RuntimeInfo(
        mode=mode,
        image=image if mode == RuntimeMode.DOCKER else None,
        container_id=None,
        saved_image=None,
        container_stopped=(mode != RuntimeMode.DOCKER),
    )
