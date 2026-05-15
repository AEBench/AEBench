from __future__ import annotations

from jinja2 import Environment, StrictUndefined

from models import PromptArgs, PromptBundle, PromptProfile, RuntimeMode

_JINJA = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)

LOCAL_TIMEOUT_GUIDANCE = """TIMEOUT CONFIGURATION (CRITICAL):
- Long-running commands are expected.
- Do not set short timeouts; let commands complete naturally.
"""

DOCKER_TIMEOUT_GUIDANCE = """TIMEOUT CONFIGURATION (CRITICAL):
- The Bash timeout is configured to {{ timeout_ms }} ms.
- Do not specify extra timeout parameters in Bash commands.
- Long-running commands can take hours.
"""

OUTPUT_MANAGEMENT_GUIDANCE = """OUTPUT MANAGEMENT (CRITICAL):
- Redirect very long command output to log files.
- Inspect progress with bounded summaries such as tail, grep, sed, and wc.
"""

VERIFY_RULE = "You must execute every verification step the instructions require. Do not skip steps because they take a long time."

LOCAL_SYSTEM_PROMPT_TEMPLATE = """\
You are an experienced software engineer completing an artifact task.

ENVIRONMENT:
- You are running directly on the host machine.
- The artifact repository is at {{ workspace_path }}.

YOUR TASK:
{{ task_text }}

{{ local_timeout_guidance }}
{{ output_management_guidance }}
IMPORTANT GUIDELINES:
1. First, cd to {{ workspace_path }} and examine the directory structure.
2. Follow the instructions step by step.
3. {{ verify_rule }}
4. Debug and resolve errors methodically.
{% if prompt_append %}

ADDITIONAL TASK RULES:
{{ prompt_append }}
{% endif %}
"""

DOCKER_SYSTEM_PROMPT_TEMPLATE = """\
You are an experienced software engineer completing an artifact task.

ENVIRONMENT:
- The task-execution environment is a Docker container.
- The artifact repository is available at {{ container_workspace_path or workspace_path }}.
- Read-only benchmark references are mounted at {{ refs_path or "/refs" }}.
{% if host_agent_controls_container_shell %}
- You are controlling this Docker environment from the host side.
- Use the container shell as the primary task-execution shell.
- The host workspace mirror is at {{ host_workspace_path or "(host workspace)" }}.
{% else %}
- You are running inside the task-execution container with root permissions.
{% endif %}

YOUR TASK:
{{ task_text }}

{{ docker_timeout_guidance }}
{{ output_management_guidance }}
IMPORTANT GUIDELINES:
1. First, explore the current directory structure.
2. Navigate to the artifact repository root at {{ container_workspace_path or workspace_path }}.
3. Remove sudo from commands when needed because the container already runs as root.
4. Do not switch git branches.
5. Follow the instructions step by step.
6. {{ verify_rule }}
7. Debug and resolve errors methodically.
{% if prompt_append %}

ADDITIONAL TASK RULES:
{{ prompt_append }}
{% endif %}
"""

LOCAL_INITIAL_PROMPT_TEMPLATE = "Please start the artifact task. Begin by changing to {{ workspace_path }} and examining its contents."
DOCKER_INITIAL_PROMPT_TEMPLATE = "Please start the artifact task. Begin by changing to {{ workspace_path }} and examining its contents."

DOCKER_INTERACTIVE_PROMPT_TEMPLATE = """\
You are an experienced software engineer in an interactive Docker session.
The artifact repository is at {{ workspace_path }}. Change to it before working.
Reference files are available read-only at {{ refs_path or "/refs" }}.
"""


def build_prompt_bundle(context: PromptArgs) -> PromptBundle:
    profile = _resolve_profile(context)
    values = {
        **context.model_dump(mode="json"),
        "local_timeout_guidance": LOCAL_TIMEOUT_GUIDANCE,
        "docker_timeout_guidance": _render(DOCKER_TIMEOUT_GUIDANCE, context),
        "output_management_guidance": OUTPUT_MANAGEMENT_GUIDANCE,
        "verify_rule": VERIFY_RULE,
    }
    docker = profile == PromptProfile.ARTIFACT_EVAL_DOCKER_V1
    return PromptBundle(
        profile=profile,
        system_prompt=_render(DOCKER_SYSTEM_PROMPT_TEMPLATE if docker else LOCAL_SYSTEM_PROMPT_TEMPLATE, values),
        initial_prompt=_render(DOCKER_INITIAL_PROMPT_TEMPLATE if docker else LOCAL_INITIAL_PROMPT_TEMPLATE, values),
    )


def build_container_interactive_prompt() -> str:
    return _render(DOCKER_INTERACTIVE_PROMPT_TEMPLATE, {"workspace_path": "/repo", "refs_path": "/refs"})


def _resolve_profile(context: PromptArgs) -> PromptProfile:
    profile = PromptProfile(context.prompt_profile)
    if profile == PromptProfile.ARTIFACT_EVAL_V1:
        return PromptProfile.ARTIFACT_EVAL_LOCAL_V1 if context.runtime_mode == RuntimeMode.LOCAL else PromptProfile.ARTIFACT_EVAL_DOCKER_V1

    profile_runtime = {
        PromptProfile.ARTIFACT_EVAL_LOCAL_V1: RuntimeMode.LOCAL,
        PromptProfile.ARTIFACT_EVAL_DOCKER_V1: RuntimeMode.DOCKER,
    }.get(profile)
    if profile_runtime is not None and profile_runtime != context.runtime_mode:
        raise ValueError(f"{profile_runtime.value} prompt profile cannot be used with runtime.mode='{context.runtime_mode.value}'")
    return profile


def _render(template: str, context: PromptArgs | dict[str, object]) -> str:
    data = context.model_dump(mode="json") if isinstance(context, PromptArgs) else context
    return _JINJA.from_string(template).render(**data).strip()
