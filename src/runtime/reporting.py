from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from constants import (
	AGENT_SUMMARY_FALLBACK_MAX,
	INFRA_LOG_BASENAME,
	LOG_BASENAME_TEMPLATE,
	PROGRESS_LOG_BASENAME,
	PROMPT_BASENAME_TEMPLATE,
	RENDERED_LOG_BASENAME,
	RUNNER_LOG_BASENAME,
	TOOL_OUTPUT_DIRNAME,
	TRANSCRIPT_BASENAME,
)
from models import CaseRunResult, PromptBundle, RunResult
from utils import safe_name


@dataclass(frozen=True, slots=True)
class TaskPaths:
	log_path: Path
	report_path: Path
	prompt_path: Path
	transcript_path: Path
	rendered_log_path: Path
	runner_log_path: Path
	infra_log_path: Path
	progress_log_path: Path
	tool_output_dir: Path


def task_paths_for(output_dir: Path, safe_id: str) -> TaskPaths:
	return TaskPaths(
		log_path=output_dir / LOG_BASENAME_TEMPLATE.format(safe_id=safe_id),
		report_path=output_dir / f"{safe_id}_report.md",
		prompt_path=output_dir / PROMPT_BASENAME_TEMPLATE.format(safe_id=safe_id),
		transcript_path=output_dir / TRANSCRIPT_BASENAME,
		rendered_log_path=output_dir / RENDERED_LOG_BASENAME,
		runner_log_path=output_dir / RUNNER_LOG_BASENAME,
		infra_log_path=output_dir / INFRA_LOG_BASENAME,
		progress_log_path=output_dir / PROGRESS_LOG_BASENAME,
		tool_output_dir=output_dir / TOOL_OUTPUT_DIRNAME,
	)


def case_output_dir(case_id: str, *, root: Path, explicit: Path | None = None) -> Path:
	if explicit is not None:
		out = explicit.expanduser().resolve()
	else:
		stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%f")
		out = (root / safe_name(case_id) / stamp).resolve()
	out.mkdir(parents=True, exist_ok=True)
	return out


def write_prompt_file(prompt_path: Path, prompt_bundle: PromptBundle) -> None:
	prompt_path.parent.mkdir(parents=True, exist_ok=True)
	prompt_path.write_text(
		"# Prompt Bundle\n\n"
		f"- Profile: `{prompt_bundle.profile.value}`\n\n"
		"## System Prompt\n\n"
		f"{prompt_bundle.system_prompt}\n\n"
		"## Initial Prompt\n\n"
		f"{prompt_bundle.initial_prompt}\n",
		encoding="utf-8",
	)


def append_run_result(output_dir: Path, result: RunResult) -> None:
	output_dir.mkdir(parents=True, exist_ok=True)
	with (output_dir / "result.jsonl").open("a", encoding="utf-8") as handle:
		handle.write(result.model_dump_json())
		handle.write("\n")


def write_case_result(output_dir: Path, result: CaseRunResult) -> Path:
	path = output_dir / "case_result.json"
	path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
	return path


def _append_jsonl(path: Path, record: Any) -> None:
	with path.open("a", encoding="utf-8") as handle:
		handle.write(record.model_dump_json())
		handle.write("\n")


def _write_jsonl(path: Path, records: Sequence[Any]) -> None:
	with path.open("w", encoding="utf-8") as handle:
		for record in records:
			handle.write(record.model_dump_json())
			handle.write("\n")


def read_agent_summary(summary_path: Path, result: RunResult) -> str:
	if summary_path.is_file():
		return summary_path.read_text(encoding="utf-8")
	return result.agent.output[:AGENT_SUMMARY_FALLBACK_MAX] or "(No summary captured)"


def write_task_report(report_path: Path, result: RunResult, agent_summary: str) -> None:
	report_path.parent.mkdir(parents=True, exist_ok=True)
	runtime = result.runtime
	breakdown = (
		", ".join(f"{name}={ms} ms" for name, ms in sorted(result.prepare_breakdown_ms.items()))
		or "n/a"
	)
	lines = [
		f"# AE Report: {result.id}",
		"",
		f"- Status: `{result.status.value}`",
		f"- Started: `{result.started_at.isoformat()}`",
		f"- Finished: `{result.finished_at.isoformat()}`",
		f"- Prepare duration: `{result.prepare_duration_ms} ms`",
		f"- Prepare breakdown: `{breakdown}`",
		f"- Execution duration: `{result.duration_ms} ms`",
		f"- Workspace: `{result.workspace_path}`",
		f"- Runtime mode: `{runtime.mode.value}`",
		f"- Runtime image: `{runtime.image}`",
		f"- Container ID: `{runtime.container_id}`",
		f"- Saved image: `{runtime.saved_image}`",
		f"- Container stopped: `{runtime.container_stopped}`",
		f"- Agent driver: `{result.agent_kind}`",
		f"- Agent model: `{result.agent.model}`",
		f"- Agent exit code: `{result.agent.exit_code}`",
		f"- Agent messages: `{result.agent.message_count}`",
	]
	if result.error:
		lines.extend(["", "## Error", "", result.error])
	lines.extend(["", "## Agent Summary", "", agent_summary])
	report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_benchmark_outputs(
	output_dir: Path,
	case_results: Sequence[CaseRunResult],
	summary: Any,
	*,
	expected_scores: dict[str, int | None],
) -> tuple[str, str, str]:
	output_dir.mkdir(parents=True, exist_ok=True)
	results_path = output_dir / "benchmark_results.jsonl"
	summary_path = output_dir / "benchmark_summary.json"
	markdown_path = output_dir / "benchmark_summary.md"

	with results_path.open("w", encoding="utf-8") as fh:
		for result in case_results:
			fh.write(result.model_dump_json())
			fh.write("\n")

	summary_path.write_text(json.dumps(summary.model_dump(mode="json"), indent=2), encoding="utf-8")
	markdown_path.write_text(
		render_benchmark_summary_markdown(summary, case_results, expected_scores=expected_scores),
		encoding="utf-8",
	)
	return str(results_path), str(summary_path), str(markdown_path)


def render_benchmark_summary_markdown(
	summary: Any,
	case_results: Sequence[CaseRunResult],
	*,
	expected_scores: dict[str, int | None],
) -> str:
	lines = [
		"# Benchmark Summary",
		"",
		"## Cases",
		"",
		"| Case | Claim | Case status | Oracle | Score | Output dir |",
		"| --- | --- | --- | --- | --- | --- |",
	]
	for result in case_results:
		expected = expected_scores.get(result.id)
		score = result.oracle_result.score
		score_text = f"{score}/{expected}" if score is not None and expected is not None else "n/a"
		claim = _compact_text(result.case_brief.core_claim, max_length=96).replace("|", "\\|")
		lines.append(
			f"| `{result.id}` | {claim} | `{result.status.value}` | `{result.oracle_result.status.value}` | `{score_text}` | `{result.output_dir or 'n/a'}` |"
		)
	return "\n".join(lines) + "\n"


def _compact_text(value: str, *, max_length: int = 240) -> str:
	line = " ".join(part.strip() for part in value.splitlines() if part.strip())
	return line if len(line) <= max_length else line[: max_length - 3] + "..."
