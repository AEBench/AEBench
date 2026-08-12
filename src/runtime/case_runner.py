"""Prepare one case, run an agent harness, then execute its oracle."""

from __future__ import annotations

import shutil
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from config import AppState
from constants import SUMMARY_BASENAME_TEMPLATE
from evaluator import artifact_dir_for
from evaluator.loader import load_case_spec
from evaluator.oracles.discovery import discover_oracle_classes
from models import (
	AgentName,
	AgentResult,
	CaseRunResult,
	CaseStatus,
	OracleResult,
	OracleStatus,
	PromptArgs,
	PromptProfile,
	RunOptions,
	RunResult,
	RuntimeInfo,
	RuntimeMode,
	TaskStatus,
)
from prompting import build_prompt_bundle
from sources import prepare_workspace
from task_loader import compose_task_text, read_instruction_text
from utils import safe_name

from .agent_runner import clear_agent_home, prepare_agent_home, run_agent
from .backend import BenchRuntime, get_runtime
from .cases import task_from_case
from .oracle_runner import DirectOracleRunner
from .reporting import (
	append_run_result,
	case_output_dir,
	read_agent_summary,
	task_paths_for,
	write_case_result,
	write_prompt_file,
	write_task_report,
)
from .session import RunSession
from .workspace import cleanup_workspace, create_workspace, refs_dir_for_case_manifest


def run_case(
	context: AppState,
	case_dir: Path,
	*,
	options: RunOptions,
	save_path: Path | None = None,
	on_output_dir: Callable[[Path], None] | None = None,
) -> CaseRunResult:
	case_root = case_dir.expanduser().resolve()
	case = load_case_spec(case_root)
	discover_oracle_classes(case_root)
	task = task_from_case(case_root, case)
	agent = _agent_name(options)
	model = _model_name(options)

	if options.interactive:
		raise ValueError("agent harnesses require non-interactive execution")
	if task.runtime.mode == RuntimeMode.LOCAL and not options.allow_unsafe_local:
		raise ValueError(
			"local agent execution is not isolated; use --allow-unsafe-local only on a disposable host"
		)
	if task.artifact_requirements.docker and not options.allow_host_docker:
		raise ValueError(
			"this case exposes the host Docker daemon; use --allow-host-docker only on a disposable host"
		)
	if task.runtime.mode == RuntimeMode.INHERIT:
		raise ValueError("case execution requires runtime.mode to be local or docker")

	output_dir = case_output_dir(
		case.id,
		root=context.project_state.config.resolve_case_runs_dir(context.project_state.root),
		explicit=save_path,
	)
	if on_output_dir is not None:
		on_output_dir(output_dir)
	paths = task_paths_for(output_dir, safe_name(case.id))
	started = datetime.now(timezone.utc)
	prepare_finished = started
	agent_started = started
	agent_finished = started
	workspace_root: Path | None = None
	workspace: Path | None = None
	agent_home: Path | None = None
	runtime: BenchRuntime | None = None
	session: RunSession | None = None
	agent_result = AgentResult(model=model, exit_code=1)
	oracle_result = OracleResult(status=OracleStatus.PENDING, score=0)
	error: str | None = None
	interrupted: KeyboardInterrupt | SystemExit | None = None
	prompt_profile = options.prompt_profile or task.prompt.profile

	try:
		workspace_root = create_workspace(case.id, context.settings.tmp_workspace_root)
		workspace = prepare_workspace(task, case_root / "case.toml", workspace_root)
		agent_home = prepare_agent_home(agent, workspace_root.parent)
		refs = refs_dir_for_case_manifest(case_root / "case.toml")
		runtime_workspace = (
			str(workspace)
			if task.runtime.mode == RuntimeMode.LOCAL or task.artifact_requirements.docker
			else "/repo"
		)
		runtime_refs = None if refs is None else str(refs)
		if refs is not None and task.runtime.mode == RuntimeMode.DOCKER:
			runtime_refs = "/refs"
		runtime_agent_home = (
			str(agent_home) if task.runtime.mode == RuntimeMode.LOCAL else "/home/agent"
		)
		prompt_append = (
			options.prompt_append if options.prompt_append is not None else task.prompt.append
		)
		prompt = build_prompt_bundle(
			PromptArgs(
				task_text=compose_task_text(
					read_instruction_text(workspace, task.instructions.path), case.case_brief
				),
				workspace_path=runtime_workspace,
				runtime_mode=task.runtime.mode,
				timeout_ms=task.runtime.timeout_ms,
				prompt_profile=prompt_profile.value,
				prompt_append=prompt_append,
				refs_path=runtime_refs,
				host_workspace_path=str(workspace),
				container_workspace_path=(
					runtime_workspace if task.runtime.mode == RuntimeMode.DOCKER else None
				),
			)
		)
		write_prompt_file(paths.prompt_path, prompt)

		runtime = get_runtime(
			task.runtime.mode,
			image=task.runtime.image or context.settings.default_docker_image,
			gpu=task.runtime.gpu,
			workspace=str(workspace),
		)
		summary_path = workspace / SUMMARY_BASENAME_TEMPLATE.format(safe_id=safe_name(case.id))
		session = RunSession(
			run_spec=task,
			prompt=prompt,
			settings=context.settings,
			run_control=None,
			host_workspace=workspace,
			runtime_workspace=runtime_workspace,
			host_refs=refs,
			runtime_refs=runtime_refs,
			host_agent_home=agent_home,
			runtime_agent_home=runtime_agent_home,
			output_dir=output_dir,
			task_paths=paths,
			summary_path=summary_path,
			runtime_backend=runtime,
		)
		runtime.prepare(session)
		prepare_finished = datetime.now(timezone.utc)

		agent_started = datetime.now(timezone.utc)
		try:
			agent_result = run_agent(
				agent,
				model=model,
				prompt=f"{prompt.system_prompt}\n\n{prompt.initial_prompt}".strip(),
				runtime=runtime,
				cwd=runtime_workspace,
				runtime_home=runtime_agent_home,
				timeout_seconds=task.runtime.timeout_ms / 1000,
				output_path=paths.runner_log_path,
			)
		finally:
			clear_agent_home(runtime, runtime_agent_home)
		agent_finished = datetime.now(timezone.utc)
		if agent_result.exit_code != 0:
			error = f"agent exited with code {agent_result.exit_code}"

		# End the agent process namespace before preserving its runtime for scoring.
		runtime.stop(session)
		if task.runtime.mode == RuntimeMode.DOCKER and task.runtime.commit_before_oracle:
			try:
				runtime.snapshot(session)
			except Exception as exc:
				paths.infra_log_path.write_text(traceback.format_exc(), encoding="utf-8")
				raise RuntimeError(f"failed to snapshot agent runtime: {exc}") from exc

		scored_runtime_info = runtime.runtime_result(session)
		interim_result = _run_result(
			case.id,
			output_dir,
			workspace,
			session.summary_path,
			prompt.profile,
			scored_runtime_info,
			agent,
			agent_result,
			started,
			prepare_finished,
			agent_started,
			agent_finished,
			error,
		)
		oracle_result = DirectOracleRunner().execute(
			case_root,
			runtime_result=interim_result,
			output_dir=output_dir,
			case=case,
			workspace_dir=workspace,
		)
	except (KeyboardInterrupt, SystemExit) as exc:
		interrupted = exc
	except Exception as exc:
		error = f"{type(exc).__name__}: {exc}"
		paths.infra_log_path.write_text(traceback.format_exc(), encoding="utf-8")
		agent_finished = datetime.now(timezone.utc)
	finally:
		if runtime is not None and session is not None:
			try:
				runtime.cleanup(session)
			except Exception as exc:
				cleanup_error = f"{type(exc).__name__} during cleanup: {exc}"
				error = f"{error}; {cleanup_error}" if error else cleanup_error
				with paths.infra_log_path.open("a", encoding="utf-8") as handle:
					handle.write("\n" + traceback.format_exc())
		if agent_home is not None:
			try:
				shutil.rmtree(agent_home)
			except FileNotFoundError:
				pass
			except Exception as exc:
				cleanup_error = f"{type(exc).__name__} removing agent home: {exc}"
				error = f"{error}; {cleanup_error}" if error else cleanup_error
				with paths.infra_log_path.open("a", encoding="utf-8") as handle:
					handle.write("\n" + traceback.format_exc())

	if interrupted is not None:
		if options.cleanup_workspace and workspace_root is not None:
			cleanup_workspace(workspace_root, keep=context.settings.preserve_failed_workspace)
		raise interrupted

	finished = datetime.now(timezone.utc)
	runtime_info = (
		runtime.runtime_result(session)
		if runtime is not None and session is not None
		else RuntimeInfo(mode=task.runtime.mode, container_stopped=True)
	)
	summary_path = (
		session.summary_path
		if session is not None
		else output_dir / SUMMARY_BASENAME_TEMPLATE.format(safe_id=safe_name(case.id))
	)
	run_result = _run_result(
		case.id,
		output_dir,
		workspace,
		summary_path,
		prompt_profile,
		runtime_info,
		agent,
		agent_result,
		started,
		prepare_finished,
		agent_started,
		agent_finished,
		error,
	)
	case_result = CaseRunResult(
		status=_case_status(run_result, oracle_result),
		finished_at=finished,
		case_dir=str(case_root),
		artifact_dir=str(artifact_dir_for(case_root)),
		output_dir=str(output_dir),
		case_brief=case.case_brief,
		runtime_result=run_result,
		oracle_result=oracle_result,
	)
	append_run_result(output_dir, run_result)
	write_case_result(output_dir, case_result)
	write_task_report(paths.report_path, run_result, read_agent_summary(summary_path, run_result))

	if options.cleanup_workspace and workspace_root is not None:
		cleanup_workspace(
			workspace_root,
			keep=(
				case_result.status != CaseStatus.SUCCESS
				and context.settings.preserve_failed_workspace
			),
		)
	return case_result


def _agent_name(options: RunOptions) -> AgentName:
	if options.agent_type is None:
		raise ValueError("choose an agent harness with --agent")
	return options.agent_type


def _model_name(options: RunOptions) -> str:
	if options.model_name is None:
		raise ValueError("choose an agent model with --model")
	return options.model_name


def _run_result(
	case_id: str,
	output_dir: Path,
	workspace: Path | None,
	summary_path: Path,
	prompt_profile: PromptProfile,
	runtime_info: RuntimeInfo,
	agent: AgentName,
	agent_result: AgentResult,
	started: datetime,
	prepare_finished: datetime,
	agent_started: datetime,
	agent_finished: datetime,
	error: str | None,
) -> RunResult:
	return RunResult(
		id=case_id,
		status=(
			TaskStatus.SUCCESS
			if error is None and agent_result.exit_code == 0
			else TaskStatus.ERROR
		),
		started_at=agent_started,
		finished_at=agent_finished,
		prepare_duration_ms=_duration_ms(started, prepare_finished),
		duration_ms=_duration_ms(agent_started, agent_finished),
		workspace_path=str(workspace or ""),
		output_dir=str(output_dir),
		summary_path=str(summary_path),
		prompt_profile=prompt_profile,
		runtime=runtime_info,
		agent_kind=agent,
		agent=agent_result,
		error=error,
	)


def _case_status(run_result: RunResult, oracle_result: OracleResult) -> CaseStatus:
	if oracle_result.status == OracleStatus.ERROR:
		return CaseStatus.ERROR
	if oracle_result.status == OracleStatus.PENDING:
		return CaseStatus.ERROR if run_result.error else CaseStatus.PENDING
	return CaseStatus.SUCCESS


def _duration_ms(start: datetime, end: datetime) -> int:
	return max(0, int((end - start).total_seconds() * 1000))


__all__ = ["run_case"]
