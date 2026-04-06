from __future__ import annotations

DEFAULT_TIMEOUT_MS: int = 345_600_000
DEFAULT_DOCKER_IMAGE: str = "aebench-agent:latest"
DEFAULT_MODEL: str = "claude-sonnet-4-5-20250929"
DEFAULT_PROMPT_PROFILE: str = "artifact-eval-v1"
DEFAULT_OUTPUTS_DIR: str = "./outputs"

ARTIFACT_SUBDIR: str = "artifact"

SUMMARY_BASENAME_TEMPLATE = "aebench_summary_{safe_id}.md"
PROMPT_BASENAME_TEMPLATE = "aebench_prompt_{safe_id}.md"
LOG_BASENAME_TEMPLATE = "aebench_log_{safe_id}.log"
REPORT_BASENAME_TEMPLATE = "aebench_report_{safe_id}.md"
TRANSCRIPT_BASENAME = "agent_transcript.jsonl"
RENDERED_LOG_BASENAME = "agent_rendered.log"
RUNNER_LOG_BASENAME = "runner_output.log"
INFRA_LOG_BASENAME = "infra.log"
PROGRESS_LOG_BASENAME = "progress.log"
TOOL_OUTPUT_DIRNAME = "artifacts/tool-output"
LIVE_EVENT_DIRNAME = ".aebench-live"
LIVE_TRANSCRIPT_BASENAME = "agent_events.jsonl"
LIVE_RENDERED_BASENAME = "agent_rendered.log"

SUMMARY_INSTRUCTION = (
    "\n\nAt the end, write a brief summary of what you did and the result to "
    "{basename} in the artifact root (so it can be included in the report)."
)

DEFAULT_TASK_TEMPLATE = (
    "You are asked to follow the artifact instructions in {file_path} to set up, install, "
    "build, and reproduce the results for this repository inside the benchmark-provided "
    "execution environment. Execute all required verification steps and complete the task "
    "without skipping long-running commands."
)

LOG_OUTPUT_TRUNCATE_BYTES = 50_000
AGENT_SUMMARY_FALLBACK_MAX = 8_000
DEFAULT_AGENT_MAX_BUFFER_SIZE = 8 * 1024 * 1024
DISPLAY_TOOL_OUTPUT_INLINE_BYTES = 32 * 1024
DISPLAY_TOOL_OUTPUT_HEAD_LINES = 20
DISPLAY_TOOL_OUTPUT_TAIL_LINES = 20