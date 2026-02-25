#!/usr/bin/env python3
"""Core agent execution using Claude Agent SDK.

Works both as a package module (imported by run_eval for host mode) and as a
standalone script (uploaded to Docker container and run via runner.sh).

Provides:
- build_system_prompt(): unified prompt builder for all environments
- run_agent(): single implementation of SDK invocation with rate-limit retry
- docker_main(): standalone Docker entry point
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

# Import utils: as package module or standalone in Docker.
try:
    from .utils import (
        DEFAULT_MODEL,
        DEFAULT_TIMEOUT_MS,
        has_api_key,
        is_local_env,
        resolve_timeout_ms,
    )
except (ImportError, SystemError):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from utils import (
            DEFAULT_MODEL,
            DEFAULT_TIMEOUT_MS,
            has_api_key,
            is_local_env,
            resolve_timeout_ms,
        )
    except ImportError:
        # Fallback when utils is not importable (e.g. container has only runner.py).
        # Duplication intentional; single source is utils.py. Update both if default changes.
        DEFAULT_TIMEOUT_MS = 345_600_000  # 96h
        DEFAULT_MODEL = 'claude-sonnet-4-5-20250929'

        def is_local_env(env: str) -> bool:  # noqa: D103
            return str(env).strip().lower() == 'local'

        def has_api_key() -> bool:  # noqa: D103
            return bool(os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_FOUNDRY_API_KEY'))

        def resolve_timeout_ms(timeout_ms: int | None) -> int:  # noqa: D103
            return timeout_ms if timeout_ms is not None else DEFAULT_TIMEOUT_MS


try:
    from claude_agent_sdk import ClaudeAgentOptions, query

    CLAUDE_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_SDK_AVAILABLE = False

try:
    from claude_agent_sdk import ClaudeSDKClient
except ImportError:
    ClaudeSDKClient = None

_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_WAIT_SEC = 60
_RATE_LIMIT_WAIT_MAX_SEC = 600
_RATE_LIMIT_WRAPPED_MAX_RETRIES = 3
_PROGRESS_LOG_INTERVAL = 10


_RESULT_TYPE_NAMES = frozenset({'ResultMessage', 'TextBlock'})


def _process_message(message, message_count: int, result_text: str) -> tuple[int, str]:
    """Process one SDK message: print, update count, extract result text.

    Returns (new_message_count, new_result_text).
    """
    message_count += 1
    if message_count % _PROGRESS_LOG_INTERVAL == 0:
        print(f'[Progress] {message_count} messages...', flush=True)
    msg_str = str(message)
    print(msg_str, flush=True)
    if type(message).__name__ in _RESULT_TYPE_NAMES:
        result_text = msg_str
    return message_count, result_text


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return '429' in msg or 'rate limit' in msg or 'ratelimitreached' in msg


def _is_possible_wrapped_rate_limit(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return ('command failed' in msg and 'exit code 1' in msg) or 'check stderr' in msg


def _parse_retry_after(exc: BaseException) -> int | None:
    m = re.search(r'wait\s+(\d+)\s*seconds', str(exc), re.I)
    return int(m.group(1)) if m else None


# Shared prompt fragments (avoids duplication and keeps host/docker logic aligned).
_PROMPT_TIMEOUT_HOST = (
    'TIMEOUT CONFIGURATION (CRITICAL):\n'
    '- Long-running commands (builds, tests, Kind cluster creation) are expected\n'
    '- DO NOT set short timeouts - let commands complete naturally\n\n'
)
_PROMPT_TIMEOUT_DOCKER = (
    'TIMEOUT CONFIGURATION (CRITICAL):\n'
    '- The system has been configured with a Bash timeout of {timeout_ms} ms.\n'
    '- DO NOT specify timeout parameters in your Bash commands.\n'
    '- Long-running commands can take hours - this is normal.\n'
    '- If a command seems to be running long, DO NOT cancel or re-run it.\n\n'
)
_PROMPT_VERIFY_STEPS = (
    'You MUST execute every verification step the README requires. Do NOT skip steps because they take a long time.\n'
)


def build_system_prompt(
    task: str,
    *,
    env: str = 'docker',
    artifact_path: str | None = None,
    timeout_ms: int | None = None,
) -> str:
    """Build system prompt, parameterized by execution environment.

    Args:
        task: Task description text.
        env: 'local' for host execution, anything else for Docker.
        artifact_path: Path to artifact directory (used in host mode prompt).
        timeout_ms: Bash timeout in ms (shown in Docker mode prompt).
    """
    timeout_ms = resolve_timeout_ms(timeout_ms)

    if is_local_env(env):
        path = artifact_path or '.'
        return (
            'You are an experienced software engineer completing an artifact task.\n\n'
            'ENVIRONMENT SETUP (HOST MACHINE):\n'
            '- You are running DIRECTLY on the host machine (NOT inside a Docker container)\n'
            '- Docker daemon is already running on this host\n'
            '- You may need sudo for some operations\n\n'
            f'ARTIFACT LOCATION:\n'
            f'- The artifact repository is located at: {path}\n'
            f'- Start by changing to this directory: cd {path}\n\n'
            f'YOUR TASK:\n{task}\n\n' + _PROMPT_TIMEOUT_HOST + 'IMPORTANT GUIDELINES:\n'
            f'1. First, cd to {path} and examine the directory structure\n'
            '2. Follow the README instructions step by step\n'
            f'3. {_PROMPT_VERIFY_STEPS}'
            "4. If you see 'sudo' in instructions, you can use it (or skip if already root)\n"
            '5. Use the Bash tool to run commands, Read tool to inspect files\n'
            '6. Work systematically through setup, build, and experiment execution\n'
            '7. If you encounter errors, debug and resolve them using available tools\n'
            "8. For Kind clusters, they will work properly since you're on the host (not DinD)"
        )

    # Docker/container: when running under arteval_bench, artifact is at /repo
    path_hint = ''
    if artifact_path:
        path_hint = f'- The artifact repository is at: {artifact_path}. Change to it: cd {artifact_path}\n'
    else:
        path_hint = (
            '- The artifact repository should be in the current working directory or nearby.\n'
            '- Explore the directory structure to find the artifact repository.\n'
        )

    return (
        'You are an experienced software engineer.\n\n'
        'ENVIRONMENT SETUP:\n'
        '- You are running inside a Docker container with root permissions.\n'
        f'{path_hint}'
        '- You have access to Read, Write, and Bash tools.\n\n'
        f'YOUR TASK:\n{task}\n\n' + _PROMPT_TIMEOUT_DOCKER.format(timeout_ms=timeout_ms) + 'IMPORTANT GUIDELINES:\n'
        '1. First, explore the current directory structure\n'
        '2. Navigate to the artifact repository root directory\n'
        "3. If you see 'sudo' in instructions, remove it (you already have root access)\n"
        '4. Do NOT attempt to switch git branches\n'
        '5. Follow the README instructions step by step\n'
        f'6. {_PROMPT_VERIFY_STEPS}'
        '7. Use the Bash, Read, and Write tools to complete the task\n'
        '8. Work systematically through setup, build, and experiment execution\n'
        '9. If you encounter errors, debug and resolve them'
    )


async def run_agent(  # noqa: C901
    model_name: str,
    task: str,
    *,
    system_prompt: str | None = None,
    env: str = 'docker',
    artifact_path: str | None = None,
    timeout_ms: int | None = None,
    interactive: bool = False,
) -> dict:
    """Run the agent using Claude SDK. Single implementation for all modes.

    Args:
        model_name: Claude model name (e.g. claude-sonnet-4-5-20250929)
        task: Task description
        system_prompt: If provided, use directly; otherwise built from env/artifact_path/task.
        env: 'local' for host, else docker. Used to build prompt when system_prompt is None.
        artifact_path: Artifact directory path (for prompt and initial message).
        timeout_ms: Bash timeout in ms.
        interactive: If True, enter interactive multi-turn loop after initial task.

    Returns:
        dict with keys: exit_code (int), output (str), message_count (int)
    """
    if not CLAUDE_SDK_AVAILABLE:
        raise RuntimeError('claude_agent_sdk is not available. Install with: pip install claude-agent-sdk')

    timeout_ms = resolve_timeout_ms(timeout_ms)
    if system_prompt is None:
        system_prompt = build_system_prompt(task, env=env, artifact_path=artifact_path, timeout_ms=timeout_ms)

    options = ClaudeAgentOptions(
        model=model_name,
        system_prompt=system_prompt,
        allowed_tools=['Read', 'Write', 'Bash'],
        setting_sources=['user'],
    )

    initial_prompt = (
        f'Please start the artifact task. Begin by changing to the artifact '
        f'directory at {artifact_path} and examining its contents.'
        if artifact_path
        else 'Please start working on the artifact task. Begin by examining '
        'the current directory and finding the artifact repository.'
    )

    print(f'\n{"=" * 60}', flush=True)
    print(f'Starting Claude Agent SDK with model: {model_name}', flush=True)
    print(f'{"=" * 60}\n', flush=True)

    message_count = 0
    result_text = ''

    if interactive:
        if ClaudeSDKClient is None:
            raise RuntimeError('ClaudeSDKClient not available; cannot run interactive mode.')
        async with ClaudeSDKClient(options=options) as client:
            await client.query(initial_prompt)
            async for message in client.receive_response():
                message_count, result_text = _process_message(message, message_count, result_text)

            print(f'\nInitial task done ({message_count} messages).', flush=True)
            print('\n' + '=' * 60, flush=True)
            print(
                "Interactive mode — type instructions (or 'quit'/'exit' to end).",
                flush=True,
            )
            print('=' * 60 + '\n', flush=True)

            while True:
                try:
                    user_input = input('\n>>> ').strip()
                except (EOFError, KeyboardInterrupt):
                    print('\nExiting interactive mode.', flush=True)
                    break
                if not user_input:
                    continue
                if user_input.lower() in ('quit', 'exit', 'q'):
                    print('Exiting interactive mode.', flush=True)
                    break
                await client.query(user_input)
                async for msg in client.receive_response():
                    message_count, result_text = _process_message(msg, message_count, result_text)

        return {
            'exit_code': 0 if message_count > 0 else 1,
            'output': result_text,
            'message_count': message_count,
        }

    # Non-interactive with rate-limit retry
    last_exception = None
    for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
        try:
            result_text = ''
            message_count = 0
            async for message in query(prompt=initial_prompt, options=options):
                message_count, result_text = _process_message(message, message_count, result_text)

            print(f'Completed. Total messages: {message_count}', flush=True)
            return {
                'exit_code': 0,
                'output': result_text,
                'message_count': message_count,
            }

        except asyncio.TimeoutError as e:
            logger.error('Timed out: %s', e)
            return {
                'exit_code': 1,
                'output': f'Timeout: {e}',
                'message_count': message_count,
            }
        except Exception as e:
            last_exception = e
            explicit = _is_rate_limit_error(e)
            wrapped = _is_possible_wrapped_rate_limit(e) and not explicit
            max_r = _RATE_LIMIT_MAX_RETRIES if explicit else _RATE_LIMIT_WRAPPED_MAX_RETRIES
            if (explicit or wrapped) and attempt < max_r:
                parsed = _parse_retry_after(e)
                wait = (
                    min(parsed, _RATE_LIMIT_WAIT_MAX_SEC)
                    if parsed
                    else min(
                        _RATE_LIMIT_WAIT_SEC * (2 ** (attempt - 1)),
                        _RATE_LIMIT_WAIT_MAX_SEC,
                    )
                )
                logger.warning(
                    'Rate limit. Waiting %ds (attempt %d/%d)...',
                    wait,
                    attempt,
                    max_r,
                )
                await asyncio.sleep(wait)
                continue
            logger.error('%s', e, exc_info=True)
            return {
                'exit_code': 1,
                'output': f'Error: {e}',
                'message_count': message_count,
            }

    return {
        'exit_code': 1,
        'output': f'Failed after {_RATE_LIMIT_MAX_RETRIES} attempts: {last_exception}',
        'message_count': 0,
    }


# ---------------------------------------------------------------------------
# Standalone entry point (Docker container via runner.sh)
# ---------------------------------------------------------------------------


def _ensure_api_key() -> None:
    """Ensure at least one API key is set; exit with error otherwise."""
    if has_api_key():
        return
    logger.error('API key not set. Set ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY.')
    sys.exit(1)


_INTERACTIVE_SYSTEM_PROMPT = """\
You are an experienced software engineer in an interactive session.

ENVIRONMENT:
- You are inside a Docker container with root permissions.
- The artifact repository is at /repo. Change to it: cd /repo
- You have access to Read, Write, and Bash tools.

TIMEOUT: Long-running commands can take hours; do not set short timeouts.

You will receive follow-up instructions from the user. Complete each one and respond.
If the user asks to stop or says 'quit'/'exit', acknowledge and they will end the session."""

# When running under arteval_bench, artifact is always at /repo
_ARTIFACT_PATH_IN_CONTAINER = '/repo'


def docker_main():
    """Standalone entry point for running inside a Docker container via runner.sh."""
    raw_args = sys.argv[1:]
    interactive = '--interactive' in raw_args
    args = [a for a in raw_args if a != '--interactive']

    # Mode 1 — interactive-only (no task): runner.py --interactive [model]
    if interactive and len(args) <= 1:
        model = args[0] if args else os.environ.get('AE_AGENT_MODEL', DEFAULT_MODEL)
        _ensure_api_key()
        result = asyncio.run(
            run_agent(
                model,
                'Please confirm you are in /repo and ready for follow-up instructions. Reply briefly.',
                system_prompt=_INTERACTIVE_SYSTEM_PROMPT,
                interactive=True,
            )
        )
        sys.exit(result['exit_code'])

    # Mode 2 — task execution: runner.py <model> <task_or_path> [--interactive]
    if len(args) != 2:
        print(
            'Usage: python3 runner.py <model> <task_or_path> [--interactive]\n'
            '       python3 runner.py --interactive [model]',
            file=sys.stderr,
        )
        sys.exit(1)

    model_name = args[0]
    task_arg = args[1]
    if os.path.isfile(task_arg):
        with open(task_arg, encoding='utf-8') as f:
            task = f.read()
    else:
        task = task_arg

    _ensure_api_key()

    try:
        raw = os.environ.get('BASH_MAX_TIMEOUT_MS')
        timeout_ms = int(raw) if raw else None
    except ValueError:
        timeout_ms = None
    timeout_ms = resolve_timeout_ms(timeout_ms)

    # In container (arteval_bench): artifact is at /repo
    artifact_path = _ARTIFACT_PATH_IN_CONTAINER if os.path.isdir(_ARTIFACT_PATH_IN_CONTAINER) else None

    try:
        if interactive:
            result = asyncio.run(
                run_agent(
                    model_name,
                    task,
                    env='docker',
                    artifact_path=artifact_path,
                    timeout_ms=timeout_ms,
                    interactive=True,
                )
            )
        else:
            result = asyncio.run(
                asyncio.wait_for(
                    run_agent(
                        model_name,
                        task,
                        env='docker',
                        artifact_path=artifact_path,
                        timeout_ms=timeout_ms,
                    ),
                    timeout=timeout_ms / 1000.0,
                )
            )
        sys.exit(result['exit_code'])
    except asyncio.TimeoutError:
        logger.error('Agent exceeded timeout.')
        sys.exit(1)
    except Exception as e:
        logger.error('%s', e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    docker_main()
