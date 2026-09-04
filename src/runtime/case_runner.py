"""Prepare one case, run an agent harness, then execute its oracle."""

from __future__ import annotations

import shutil
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

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

from .agent_runner import (
	clear_agent_support_dir,
	prepare_agent_runtime,
	prepare_agent_support_dir,
	run_agent,
)
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


class _CaseRunner:
	def __init__(
		self,
		context: AppState,
		case_dir: Path,
		*,
		options: RunOptions,
		save_path: Path | None,
		on_output_dir: Callable[[Path], None] | None,
	) -> None:
		self.context = context
		self.case_dir = case_dir
		self.options = options
		self.save_path = save_path
		self.on_output_dir = on_output_dir

		self.workspace_root: Path | None = None
		self.workspace: Path | None = None
		self.agent_support_dir: Path | None = None
		self.runtime: BenchRuntime | None = None
		self.session: RunSession | None = None

		self.error: str | None = None
		self.interrupted: KeyboardInterrupt | SystemExit | None = None
		self._pipeline_failed = False
		self._runtime_cleanup_done = False
		self._agent_support_cleanup_done = False

	def prepare_case(self) -> None:
		self.case_root = self.case_dir.expanduser().resolve()
		self.case = load_case_spec(self.case_root)
		discover_oracle_classes(self.case_root)

		self.task = task_from_case(self.case_root, self.case)
		self.agent = _agent_name(self.options)
		self.model = _model_name(self.options)

		if self.options.interactive:
			raise ValueError("agent harnesses require non-interactive execution")
		if self.task.runtime.mode == RuntimeMode.LOCAL and not self.options.allow_unsafe_local:
			raise ValueError(
				"local agent execution has no process isolation; run it on a disposable machine "
				"and pass --allow-unsafe-local"
			)
		if self.task.artifact_requirements.docker and not self.options.allow_host_docker:
			raise ValueError(
				"this case requires access to the host Docker daemon; run it on a disposable "
				"machine and pass --allow-host-docker"
			)
		if self.task.runtime.mode == RuntimeMode.INHERIT:
			raise ValueError("case execution requires runtime.mode to be local or docker")

		self.output_dir = case_output_dir(
			self.case.id,
			root=self.context.project_state.config.resolve_case_runs_dir(
				self.context.project_state.root
			),
			explicit=self.save_path,
		)
		if self.on_output_dir is not None:
			self.on_output_dir(self.output_dir)

		self.paths = task_paths_for(self.output_dir, safe_name(self.case.id))
		self.started = datetime.now(timezone.utc)
		self.prepare_finished = self.started
		self.agent_started = self.started
		self.agent_finished = self.started
		self.agent_result = AgentResult(model=self.model, exit_code=1)
		self.oracle_result = OracleResult(status=OracleStatus.PENDING, score=0)
		self.prompt_profile = self.options.prompt_profile or self.task.prompt.profile

		try:
			self.workspace_root = create_workspace(
				self.case.id,
				self.context.settings.tmp_workspace_root,
			)
			self.workspace = prepare_workspace(
				self.task,
				self.case_root / "case.toml",
				self.workspace_root,
			)
			self.agent_support_dir = prepare_agent_support_dir(
				self.agent,
				self.workspace_root.parent,
			)

			refs = refs_dir_for_case_manifest(self.case_root / "case.toml")
			runtime_workspace = (
				str(self.workspace)
				if (
					self.task.runtime.mode == RuntimeMode.LOCAL
					or self.task.artifact_requirements.docker
				)
				else "/repo"
			)

			runtime_refs = None if refs is None else str(refs)
			if refs is not None and self.task.runtime.mode == RuntimeMode.DOCKER:
				runtime_refs = "/refs"

			runtime_agent_support_dir = (
				str(self.agent_support_dir)
				if self.task.runtime.mode == RuntimeMode.LOCAL
				else "/run/aebench-agent"
			)
			runtime_agent_home = (
				str(Path.home()) if self.task.runtime.mode == RuntimeMode.LOCAL else "/home/agent"
			)

			prompt_append = (
				self.options.prompt_append
				if self.options.prompt_append is not None
				else self.task.prompt.append
			)

			prompt = build_prompt_bundle(
				PromptArgs(
					task_text=compose_task_text(
						read_instruction_text(
							self.workspace,
							self.task.instructions.path,
						),
						self.case.case_brief,
					),
					workspace_path=runtime_workspace,
					runtime_mode=self.task.runtime.mode,
					timeout_ms=self.task.runtime.timeout_ms,
					prompt_profile=self.prompt_profile.value,
					prompt_append=prompt_append,
					required_evidence=self.task.instructions.required_evidence,
					refs_path=runtime_refs,
					host_workspace_path=str(self.workspace),
					container_workspace_path=(
						runtime_workspace if self.task.runtime.mode == RuntimeMode.DOCKER else None
					),
				)
			)
			write_prompt_file(self.paths.prompt_path, prompt)

			self.runtime = get_runtime(
				self.task.runtime.mode,
				image=(self.task.runtime.image or self.context.settings.default_docker_image),
				gpu=self.task.runtime.gpu,
				workspace=str(self.workspace),
			)

			summary_path = self.workspace / SUMMARY_BASENAME_TEMPLATE.format(
				safe_id=safe_name(self.case.id)
			)

			self.session = RunSession(
				run_spec=self.task,
				prompt=prompt,
				settings=self.context.settings,
				run_control=None,
				host_workspace=self.workspace,
				runtime_workspace=runtime_workspace,
				host_refs=refs,
				runtime_refs=runtime_refs,
				host_agent_support_dir=self.agent_support_dir,
				runtime_agent_support_dir=runtime_agent_support_dir,
				runtime_agent_user=(
					"agent" if self.task.runtime.mode == RuntimeMode.DOCKER else None
				),
				runtime_agent_home=runtime_agent_home,
				output_dir=self.output_dir,
				task_paths=self.paths,
				summary_path=summary_path,
				runtime_backend=self.runtime,
			)

			self.runtime.prepare(self.session)
			prepare_agent_runtime(self.runtime)
			self.prepare_finished = datetime.now(timezone.utc)
		except (KeyboardInterrupt, SystemExit) as exc:
			self.interrupted = exc
			self._pipeline_failed = True
		except Exception as exc:
			self.error = f"{type(exc).__name__}: {exc}"
			self.paths.infra_log_path.write_text(
				traceback.format_exc(),
				encoding="utf-8",
			)
			self.agent_finished = datetime.now(timezone.utc)
			self._pipeline_failed = True

	def execute_agent(self) -> None:
		if self._pipeline_failed:
			return

		runtime = cast(BenchRuntime, self.runtime)
		session = cast(RunSession, self.session)

		try:
			self.agent_started = datetime.now(timezone.utc)
			try:
				self.agent_result = run_agent(
					self.agent,
					model=self.model,
					prompt=(
						f"{session.prompt.system_prompt}\n\n{session.prompt.initial_prompt}"
					).strip(),
					runtime=runtime,
					cwd=session.runtime_workspace,
					runtime_home=session.runtime_agent_home,
					runtime_support_dir=session.runtime_agent_support_dir,
					timeout_seconds=self.task.runtime.timeout_ms / 1000,
					output_path=self.paths.runner_log_path,
				)
			finally:
				clear_agent_support_dir(
					runtime,
					session.runtime_agent_support_dir,
				)

			self.agent_finished = datetime.now(timezone.utc)

			if self.agent_result.exit_code != 0:
				self.error = f"agent exited with code {self.agent_result.exit_code}"

			# End the agent process namespace before preserving its runtime for scoring.
			runtime.stop(session)

			if (
				self.task.runtime.mode == RuntimeMode.DOCKER
				and self.task.runtime.commit_before_oracle
			):
				try:
					runtime.snapshot(session)
				except Exception as exc:
					self.paths.infra_log_path.write_text(
						traceback.format_exc(),
						encoding="utf-8",
					)
					raise RuntimeError(f"failed to snapshot agent runtime: {exc}") from exc
		except (KeyboardInterrupt, SystemExit) as exc:
			self.interrupted = exc
			self._pipeline_failed = True
		except Exception as exc:
			self.error = f"{type(exc).__name__}: {exc}"
			self.paths.infra_log_path.write_text(
				traceback.format_exc(),
				encoding="utf-8",
			)
			self.agent_finished = datetime.now(timezone.utc)
			self._pipeline_failed = True

	def run_oracle(self) -> None:
		if self._pipeline_failed:
			return

		runtime = cast(BenchRuntime, self.runtime)
		session = cast(RunSession, self.session)

		try:
			scored_runtime_info = runtime.runtime_result(session)
			interim_result = _run_result(
				self.case.id,
				self.output_dir,
				self.workspace,
				session.summary_path,
				session.prompt.profile,
				scored_runtime_info,
				self.agent,
				self.agent_result,
				self.started,
				self.prepare_finished,
				self.agent_started,
				self.agent_finished,
				self.error,
			)
			self.oracle_result = DirectOracleRunner().execute(
				self.case_root,
				runtime_result=interim_result,
				output_dir=self.output_dir,
				case=self.case,
				workspace_dir=self.workspace,
			)
		except (KeyboardInterrupt, SystemExit) as exc:
			self.interrupted = exc
			self._pipeline_failed = True
		except Exception as exc:
			self.error = f"{type(exc).__name__}: {exc}"
			self.paths.infra_log_path.write_text(
				traceback.format_exc(),
				encoding="utf-8",
			)
			self.agent_finished = datetime.now(timezone.utc)
			self._pipeline_failed = True

	def finalize(self) -> CaseRunResult:
		self._cleanup()

		if self.interrupted is not None:
			if self.options.cleanup_workspace and self.workspace_root is not None:
				cleanup_workspace(
					self.workspace_root,
					keep=self.context.settings.preserve_failed_workspace,
				)
			raise self.interrupted

		finished = datetime.now(timezone.utc)
		runtime_info = (
			self.runtime.runtime_result(self.session)
			if self.runtime is not None and self.session is not None
			else RuntimeInfo(
				mode=self.task.runtime.mode,
				container_stopped=True,
			)
		)
		summary_path = (
			self.session.summary_path
			if self.session is not None
			else self.output_dir / SUMMARY_BASENAME_TEMPLATE.format(safe_id=safe_name(self.case.id))
		)
		run_result = _run_result(
			self.case.id,
			self.output_dir,
			self.workspace,
			summary_path,
			self.prompt_profile,
			runtime_info,
			self.agent,
			self.agent_result,
			self.started,
			self.prepare_finished,
			self.agent_started,
			self.agent_finished,
			self.error,
		)
		case_result = CaseRunResult(
			status=_case_status(run_result, self.oracle_result),
			finished_at=finished,
			case_dir=str(self.case_root),
			artifact_dir=str(artifact_dir_for(self.case_root)),
			output_dir=str(self.output_dir),
			case_brief=self.case.case_brief,
			runtime_result=run_result,
			oracle_result=self.oracle_result,
		)
		append_run_result(self.output_dir, run_result)
		write_case_result(self.output_dir, case_result)
		write_task_report(
			self.paths.report_path,
			run_result,
			read_agent_summary(summary_path, run_result),
		)

		if self.options.cleanup_workspace and self.workspace_root is not None:
			cleanup_workspace(
				self.workspace_root,
				keep=(
					case_result.status != CaseStatus.SUCCESS
					and self.context.settings.preserve_failed_workspace
				),
			)

		return case_result

	def _cleanup(self) -> None:
		if not self._runtime_cleanup_done and self.runtime is not None and self.session is not None:
			try:
				self.runtime.cleanup(self.session)
			except Exception as exc:
				cleanup_error = f"{type(exc).__name__} during cleanup: {exc}"
				self.error = f"{self.error}; {cleanup_error}" if self.error else cleanup_error
				with self.paths.infra_log_path.open(
					"a",
					encoding="utf-8",
				) as handle:
					handle.write("\n" + traceback.format_exc())
			finally:
				self._runtime_cleanup_done = True

		if not self._agent_support_cleanup_done and self.agent_support_dir is not None:
			try:
				shutil.rmtree(self.agent_support_dir)
			except FileNotFoundError:
				pass
			except Exception as exc:
				cleanup_error = f"{type(exc).__name__} removing agent support directory: {exc}"
				self.error = f"{self.error}; {cleanup_error}" if self.error else cleanup_error
				with self.paths.infra_log_path.open(
					"a",
					encoding="utf-8",
				) as handle:
					handle.write("\n" + traceback.format_exc())
			finally:
				self._agent_support_cleanup_done = True


def run_case(
	context: AppState,
	case_dir: Path,
	*,
	options: RunOptions,
	save_path: Path | None = None,
	on_output_dir: Callable[[Path], None] | None = None,
) -> CaseRunResult:
	runner = _CaseRunner(
		context,
		case_dir,
		options=options,
		save_path=save_path,
		on_output_dir=on_output_dir,
	)
	try:
		runner.prepare_case()
		runner.execute_agent()
		runner.run_oracle()
		return runner.finalize()
	finally:
		runner._cleanup()


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


def _case_status(
	run_result: RunResult,
	oracle_result: OracleResult,
) -> CaseStatus:
	if run_result.status != TaskStatus.SUCCESS:
		return CaseStatus.ERROR
	if oracle_result.status == OracleStatus.ERROR:
		return CaseStatus.ERROR
	if oracle_result.status == OracleStatus.PENDING:
		return CaseStatus.PENDING
	return CaseStatus.SUCCESS


def _duration_ms(start: datetime, end: datetime) -> int:
	return max(0, int((end - start).total_seconds() * 1000))


__all__ = ["run_case"]
