from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..constants import (
    AGENT_SUMMARY_FALLBACK_MAX,
    INFRA_LOG_BASENAME,
    LOG_BASENAME_TEMPLATE,
    PROGRESS_LOG_BASENAME,
    PROMPT_BASENAME_TEMPLATE,
    RENDERED_LOG_BASENAME,
    REPORT_BASENAME_TEMPLATE,
    RUNNER_LOG_BASENAME,
    TOOL_OUTPUT_DIRNAME,
    TRANSCRIPT_BASENAME,
)
from ..domain.models import PromptBundle, RunResult


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
        report_path=output_dir / REPORT_BASENAME_TEMPLATE.format(safe_id=safe_id),
        prompt_path=output_dir / PROMPT_BASENAME_TEMPLATE.format(safe_id=safe_id),
        transcript_path=output_dir / TRANSCRIPT_BASENAME,
        rendered_log_path=output_dir / RENDERED_LOG_BASENAME,
        runner_log_path=output_dir / RUNNER_LOG_BASENAME,
        infra_log_path=output_dir / INFRA_LOG_BASENAME,
        progress_log_path=output_dir / PROGRESS_LOG_BASENAME,
        tool_output_dir=output_dir / TOOL_OUTPUT_DIRNAME,
    )


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


def read_agent_summary(summary_path: Path, result: RunResult) -> str:
    if summary_path.is_file():
        return summary_path.read_text(encoding="utf-8")
    return result.agent.output[:AGENT_SUMMARY_FALLBACK_MAX] or "(No summary captured)"


def write_task_report(report_path: Path, result: RunResult, agent_summary: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = result.runtime
    lines = [
        f"# AE Report: {result.id}",
        "",
        f"- Status: `{result.status.value}`",
        f"- Started: `{result.started_at.isoformat()}`",
        f"- Finished: `{result.finished_at.isoformat()}`",
        f"- Prepare duration: `{result.prepare_duration_ms} ms`",
        f"- Prepare breakdown: `{', '.join(f'{n}={d} ms' for n, d in sorted(result.prepare_breakdown_ms.items())) or 'n/a'}`",
        f"- Execution duration: `{result.duration_ms} ms`",
        f"- Workspace: `{result.workspace_path}`",
        f"- Runtime mode: `{runtime.mode.value}`",
        f"- Runtime image: `{runtime.image}`",
        f"- Container ID: `{runtime.container_id}`",
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
