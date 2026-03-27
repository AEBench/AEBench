from __future__ import annotations

from jinja2 import Environment, StrictUndefined

from .models import PromptBundle, PromptContext, PromptProfile, RuntimeMode

__all__ = ["build_prompt_bundle", "build_container_interactive_prompt", "PromptBundle", "PromptContext"]

LOCAL_TIMEOUT_GUIDANCE = (
    "TIMEOUT CONFIGURATION (CRITICAL):\n"
    "- Long-running commands (builds, tests, cluster creation) are expected.\n"
    "- Do not set short timeouts; let commands complete naturally.\n\n"
)

DOCKER_TIMEOUT_GUIDANCE = (
    "TIMEOUT CONFIGURATION (CRITICAL):\n"
    "- The Bash timeout is configured to {{ timeout_ms }} ms.\n"
    "- Do not specify timeout parameters in Bash commands.\n"
    "- Long-running commands can take hours.\n"
    "- Do not cancel long-running commands unless they are clearly stuck.\n\n"
)

OUTPUT_MANAGEMENT_GUIDANCE = (
    "OUTPUT MANAGEMENT (CRITICAL):\n"
    "- For commands likely to emit more than 200 lines (for example: docker build, mvn, gradle, pytest, cargo test, long shell scripts), redirect stdout/stderr to a log file instead of streaming the full output through the tool.\n"
    "- Do not use tee for long-running builds or tests.\n"
    "- Inspect progress and verify results with bounded summaries such as tail -n 200, grep, sed -n, or wc -l.\n"
    "- If a command is still running, report concise progress updates derived from those bounded summaries.\n\n"
)

VERIFY_RULE = (
    "You must execute every verification step the instructions require. "
    "Do not skip steps because they take a long time."
)

LOCAL_SYSTEM_PROMPT_TEMPLATE = """\
You are an experienced software engineer completing an artifact task.

ENVIRONMENT SETUP (HOST MACHINE):
- You are running directly on the host machine.
- Docker daemon may be available on this host.
- You may need sudo for some operations.

ARTIFACT LOCATION:
- The artifact repository is at {{ workspace_path }}
- Start by changing to this directory: cd {{ workspace_path }}

YOUR TASK:
{{ task_text }}

{{ local_timeout_guidance }}{{ output_management_guidance }}IMPORTANT GUIDELINES:
1. First, cd to {{ workspace_path }} and examine the directory structure.
2. Follow the instructions step by step.
3. {{ verify_rule }}
4. If you see 'sudo' in instructions, use it only when needed.
5. Use the Bash tool to run commands and the Read tool to inspect files.
6. Debug and resolve errors methodically.
{% if prompt_append %}

ADDITIONAL TASK RULES:
{{ prompt_append }}
{% endif %}
"""

DOCKER_SYSTEM_PROMPT_TEMPLATE = """\
You are an experienced software engineer completing an artifact task.

ENVIRONMENT SETUP (DOCKER):
- The benchmark task-execution environment is a Docker container.
- The artifact repository is available at {{ container_workspace_path or workspace_path }}.
- Read-only benchmark references are mounted at {{ refs_path or "/refs" }}.
{% if host_agent_controls_container_shell %}
- You are controlling this Docker environment from the host side.
- Use the container shell as the primary task-execution shell.
- The host workspace mirror is at {{ host_workspace_path or "(host workspace)" }}.
- The host shell is {{ host_shell_policy }} and is not the primary task-execution path.
{% else %}
- You are running inside the task-execution container with root permissions.
- You have access to Read, Write, and Bash tools.
{% endif %}

YOUR TASK:
{{ task_text }}

{{ docker_timeout_guidance }}{{ output_management_guidance }}IMPORTANT GUIDELINES:
1. First, explore the current directory structure.
2. Navigate to the artifact repository root at {{ container_workspace_path or workspace_path }}.
3. If you see 'sudo' in instructions, remove it because you already have root access.
4. Do not switch git branches.
5. Follow the instructions step by step.
6. {{ verify_rule }}
7. Debug and resolve errors methodically.
{% if prompt_append %}

ADDITIONAL TASK RULES:
{{ prompt_append }}
{% endif %}
"""

LOCAL_INITIAL_PROMPT_TEMPLATE = """\
Please start the artifact task. Begin by changing to the artifact directory at {{ workspace_path }} and examining its contents."""

DOCKER_INITIAL_PROMPT_TEMPLATE = """\
Please start the artifact task. Begin by changing to {{ workspace_path }} and examining its contents."""

DOCKER_INTERACTIVE_PROMPT_TEMPLATE = """\
You are an experienced software engineer in an interactive session.

ENVIRONMENT:
- You are inside a Docker container with root permissions.
- The artifact repository is at {{ workspace_path }}. Change to it: cd {{ workspace_path }}
- Benchmark reference files are available read-only at {{ refs_path or "/refs" }}.
- You have access to Read, Write, and Bash tools.

TIMEOUT: Long-running commands can take hours; do not set short timeouts.

You will receive follow-up instructions from the user. Complete each one and respond.
If the user asks to stop or says 'quit'/'exit', acknowledge and they will end the session."""

_JINJA = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def build_prompt_bundle(context: PromptContext) -> PromptBundle:
    profile = _resolve_profile(context.prompt_profile, context.runtime_mode)
    template_context = {
        **context.model_dump(mode="json"),
        "local_timeout_guidance": LOCAL_TIMEOUT_GUIDANCE,
        "docker_timeout_guidance": _render(DOCKER_TIMEOUT_GUIDANCE, context),
        "output_management_guidance": OUTPUT_MANAGEMENT_GUIDANCE,
        "verify_rule": VERIFY_RULE,
    }
    system_prompt = _render(_system_template_for(profile), template_context)
    initial_prompt = _render(_initial_template_for(context.runtime_mode), template_context)
    return PromptBundle(profile=profile, system_prompt=system_prompt, initial_prompt=initial_prompt)


def build_container_interactive_prompt() -> str:
    return _render(
        DOCKER_INTERACTIVE_PROMPT_TEMPLATE,
        {"workspace_path": "/repo"},
    )


def _resolve_profile(profile: PromptProfile, runtime_mode: RuntimeMode) -> PromptProfile:
    if profile == PromptProfile.ARTIFACT_EVAL_V1:
        if runtime_mode == RuntimeMode.LOCAL:
            return PromptProfile.ARTIFACT_EVAL_LOCAL_V1
        return PromptProfile.ARTIFACT_EVAL_DOCKER_V1
    return profile


def _system_template_for(profile: PromptProfile) -> str:
    if profile == PromptProfile.ARTIFACT_EVAL_LOCAL_V1:
        return LOCAL_SYSTEM_PROMPT_TEMPLATE
    return DOCKER_SYSTEM_PROMPT_TEMPLATE


def _initial_template_for(runtime_mode: RuntimeMode) -> str:
    if runtime_mode == RuntimeMode.LOCAL:
        return LOCAL_INITIAL_PROMPT_TEMPLATE
    return DOCKER_INITIAL_PROMPT_TEMPLATE


def _render(template: str, context: PromptContext | dict[str, object]) -> str:
    if isinstance(context, PromptContext):
        template_context = context.model_dump(mode="json")
    else:
        template_context = context
    return _JINJA.from_string(template).render(**template_context).strip()
