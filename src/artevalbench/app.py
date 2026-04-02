from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from structlog.typing import FilteringBoundLogger

from .runtime.task_runner import TaskRunner as TaskRunService
from .domain.models import (
 AgentResult,
 PromptProfile,
 PromptSpec,
 RunOptions,
 RunResult,
 RunSpec,
 RuntimeMode,
 RuntimeResult,
 TaskStatus,
)
from .config import AppContext
from .log import get_logger, print_console
from .reporting import persist_result, task_paths_for, write_summary, write_task_report
from .utils import safe_name

logger: FilteringBoundLogger = get_logger(__name__)


class TaskParseError(ValueError):
	pass


def run_app(
 *,
 context: AppContext,
 input_file: Path,
 save_path: Path,
 options: RunOptions,
) -> int:
	if not input_file.is_file():
		logger.error("input file not found", input_file=str(input_file))
		return 1

	print_console(f"[bold]Input file:[/bold] {input_file}")
	print_console(f"[bold]Save path:[/bold] {save_path}")
	print_console(f"[bold]Using model:[/bold] {options.model_name}")

	service = TaskRunService(context)
	results: list[RunResult] = []
	for line_no, raw_line in _iter_task_lines(input_file):
		try:
			spec = _parse_task_line(raw_line, line_no=line_no, options=options)
		except TaskParseError as exc:
			result = _build_skipped_result(
			 task_id=f"line_{line_no}",
			 model_name=options.model_name,
			 message=str(exc),
			 save_path=save_path,
			)
			persist_result(save_path, result)
			results.append(result)
			print_console(f"[yellow]{exc}[/yellow]")
			continue

		try:
			result = service.run(
			 spec,
			 input_file=input_file,
			 output_dir=save_path,
			 options=options,
			)
		except Exception as exc:
			logger.exception("task failed before runtime completion", task_id=spec.id)
			result = _build_skipped_result(
			 task_id=spec.id,
			 model_name=options.model_name,
			 message=str(exc),
			 save_path=save_path,
			)
		persist_result(save_path, result)
		results.append(result)

	summary = write_summary(save_path, results)
	print_console(f"[bold]All tasks completed:[/bold] {summary.success}/{summary.total} succeeded.")
	return 0 if summary.error == 0 else 1


def _iter_task_lines(input_file: Path) -> list[tuple[int, str]]:
	lines: list[tuple[int, str]] = []
	with input_file.open(encoding="utf-8") as handle:
		for line_no, raw_line in enumerate(handle, start=1):
			line = raw_line.strip()
			if line:
				lines.append((line_no, line))
	return lines


def _parse_task_line(line: str, *, line_no: int, options: RunOptions) -> RunSpec:
	try:
		payload = json.loads(line)
	except json.JSONDecodeError as exc:
		raise TaskParseError(f"invalid JSON at line {line_no}: {exc}") from exc
	try:
		spec = RunSpec.model_validate(payload)
	except ValidationError as exc:
		raise TaskParseError(f"invalid task schema at line {line_no}: {exc}") from exc
	return _apply_run_options(spec, options)


def _apply_run_options(spec: RunSpec, options: RunOptions) -> RunSpec:
	prompt = spec.prompt
	if options.prompt_profile is not None or options.prompt_append is not None:
		prompt = PromptSpec(
		 profile=options.prompt_profile or spec.prompt.profile,
		 append=options.prompt_append
		 if options.prompt_append is not None
		 else spec.prompt.append,
		)
	runtime = spec.runtime
	if options.interactive and not runtime.interactive:
		runtime = runtime.model_copy(update={"interactive": True})
	return spec.model_copy(update={"prompt": prompt, "runtime": runtime})


def _build_skipped_result(
 task_id: str,
 model_name: str,
 message: str,
 save_path: Path,
) -> RunResult:
	safe_id = safe_name(task_id)
	paths = task_paths_for(save_path, safe_id)
	paths.log_path.parent.mkdir(parents=True, exist_ok=True)
	paths.log_path.write_text(f"{message}\n", encoding="utf-8")
	started_at = datetime.now(timezone.utc)
	result = RunResult(
	 id=task_id,
	 status=TaskStatus.SKIPPED,
	 started_at=started_at,
	 finished_at=started_at,
	 prepare_duration_ms=0,
	 prepare_breakdown_ms={},
	 duration_ms=0,
	 workspace_path="",
	 log_path=str(paths.log_path),
	 transcript_path=str(paths.transcript_path),
	 rendered_log_path=str(paths.rendered_log_path),
	 runner_log_path=str(paths.runner_log_path),
	 infra_log_path=str(paths.infra_log_path),
	 progress_log_path=str(paths.progress_log_path),
	 summary_path="",
	 prompt_profile=PromptProfile.ARTIFACT_EVAL_V1,
	 runtime=RuntimeResult(mode=RuntimeMode.LOCAL),
	 agent_kind="unknown",
	 agent=AgentResult(model=model_name, exit_code=1, output=""),
	 error=message,
	)
	write_task_report(paths.report_path, result, message)
	return result
