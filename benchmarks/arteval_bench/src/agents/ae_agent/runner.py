#!/usr/bin/env python3
"""AE Agent runner for ArtEvalBench - Claude Agent SDK for artifact tasks.

Runs inside benchmark container: artifact at /repo, agent at /agent; task as CLI arg or path to file.
"""

import asyncio
import os
import sys

sys.path.insert(0, '/agent')

try:
    from utils import DEFAULT_TIMEOUT_MS
except ImportError:
    DEFAULT_TIMEOUT_MS = 172_800_000  # 48h fallback

try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    CLAUDE_SDK_AVAILABLE = True
except ImportError as e:
    print(f"ERROR: Failed to import claude_agent_sdk: {e}", file=sys.stderr)
    CLAUDE_SDK_AVAILABLE = False

try:
    from message_formatter import MessageFormatter
    FORMATTER_AVAILABLE = True
except ImportError:
    print("WARNING: message_formatter not available, will use basic output.", file=sys.stderr)
    FORMATTER_AVAILABLE = False

if not CLAUDE_SDK_AVAILABLE:
    print("ERROR: claude_agent_sdk is not available.", file=sys.stderr)
    sys.exit(1)

RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_WAIT_SEC = 60
RATE_LIMIT_WAIT_MAX_SEC = 600
RATE_LIMIT_WRAPPED_MAX_RETRIES = 3


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "ratelimitreached" in msg


def _is_possible_wrapped_rate_limit(exc: BaseException) -> bool:
    msg = str(exc)
    return ("command failed" in msg.lower() and "exit code 1" in msg.lower()) or "check stderr" in msg.lower()


def _parse_retry_after_seconds(exc: BaseException) -> int | None:
    import re
    m = re.search(r"wait\s+(\d+)\s*seconds", str(exc), re.I)
    return int(m.group(1)) if m else None


def get_default_work_dir():
    return os.environ.get('WORK_DIR', None)


async def run_agent(model_name: str, task_description: str):
    work_dir_hint = get_default_work_dir()
    work_dir_instruction = f"- You may start by checking: {work_dir_hint}\n" if work_dir_hint else ""

    try:
        timeout_ms_env = os.environ.get("BASH_MAX_TIMEOUT_MS")
        timeout_ms = int(timeout_ms_env) if timeout_ms_env is not None else DEFAULT_TIMEOUT_MS
    except ValueError:
        timeout_ms = DEFAULT_TIMEOUT_MS

    base_prompt = f"""You are an experienced software engineer.

ENVIRONMENT SETUP:
- You are running inside a Docker container with root permissions.
- The artifact repository should be in the current working directory or nearby.
- You should explore the directory structure to find the artifact repository.
{work_dir_instruction}- You have access to Read, Write, and Bash tools to complete the task.

YOUR TASK:
{task_description}

TIMEOUT CONFIGURATION (CRITICAL):
- The system has been configured with a default Bash timeout of {timeout_ms} ms (via BASH_MAX_TIMEOUT_MS).
- DO NOT specify timeout parameters in your Bash commands - the system default will be used automatically.
- Long-running commands (builds, tests, benchmarks) can take hours - this is normal and expected.
- If a command seems to be running long, DO NOT cancel or re-run it. Wait for completion.

IMPORTANT GUIDELINES:
1. First, explore the current directory structure to understand where you are and where the artifact is located.
2. Navigate to the artifact repository root directory.
3. If you see 'sudo' in any instructions, remove it (you already have root access).
4. Do NOT attempt to switch git branches (you are already on the correct branch).
5. Follow the README instructions step by step.
6. You MUST execute every verification step, test, or command that the README (or referenced docs like TESTBED.md) says is required for evaluation or reproduction. Do NOT skip any such step just because the README mentions that it may take a long time. Long runtimes are expected; run each verification and wait for completion.
7. Use the Bash tool to run commands, Read tool to inspect files, and Write tool to create/modify files.
8. Work systematically through environment setup, build/install, benchmark preparation, and experiment execution.
9. If you encounter errors, try to debug and resolve them using the available tools.
10. For long-running commands, let them complete naturally. Do NOT set short timeouts or interrupt them."""

    options = ClaudeAgentOptions(
        system_prompt=base_prompt,
        allowed_tools=["Read", "Write", "Bash"],
        setting_sources=["user"],
    )

    formatter = None
    if FORMATTER_AVAILABLE:
        try:
            formatter = MessageFormatter()
            formatter.print_header()
        except Exception as e:
            print(f"WARNING: Failed to initialize MessageFormatter: {e}", file=sys.stderr)

    print(f"\n{'='*60}", flush=True)
    print(f"Starting AE Agent (Claude SDK) with model: {model_name}", flush=True)
    print(f"Task: {task_description[:200]}..." if len(task_description) > 200 else f"Task: {task_description}", flush=True)
    print(f"{'='*60}\n", flush=True)

    last_exception = None
    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
        try:
            result_text = ""
            message_count = 0

            async for message in query(
                prompt="Please start working on the artifact task described in the system prompt. Begin by changing to the artifact repository directory and examining the README or instructions.",
                options=options
            ):
                message_count += 1
                if message_count % 10 == 0:
                    print(f"[Progress] Processed {message_count} messages...", flush=True)

                if formatter:
                    try:
                        formatter.format_message(message)
                    except Exception as e:
                        print(f"WARNING: Failed to format message: {e}", file=sys.stderr, flush=True)
                        print(str(message), flush=True)
                else:
                    print(str(message), flush=True)

                msg_str = str(message)
                if 'ResultMessage' in msg_str or 'TextBlock' in msg_str:
                    result_text = msg_str

            if formatter:
                formatter.print_footer()

            print(f"\n{'='*60}", flush=True)
            print(f"AE Agent execution completed. Total messages: {message_count}", flush=True)
            print(f"{'='*60}\n", flush=True)

            if formatter:
                try:
                    metadata = formatter.get_api_metadata()
                    if metadata:
                        print(f"\nAPI Usage Metadata:", flush=True)
                        print(f"  Input tokens: {metadata.get('input_tokens', 'N/A')}", flush=True)
                        print(f"  Output tokens: {metadata.get('output_tokens', 'N/A')}", flush=True)
                        print(f"  Total cost: ${metadata.get('total_cost', 'N/A')}", flush=True)
                except Exception as e:
                    print(f"WARNING: Failed to get metadata: {e}", file=sys.stderr, flush=True)

            return 0

        except asyncio.TimeoutError as e:
            print(f"\nERROR: AE Agent execution timed out: {e}", file=sys.stderr, flush=True)
            if formatter:
                formatter.print_footer()
            return 1
        except Exception as e:
            last_exception = e
            explicit_429 = _is_rate_limit_error(e)
            wrapped_possible_429 = _is_possible_wrapped_rate_limit(e) and not explicit_429
            max_retries = RATE_LIMIT_MAX_RETRIES if explicit_429 else RATE_LIMIT_WRAPPED_MAX_RETRIES
            is_retriable = (explicit_429 or wrapped_possible_429) and attempt < max_retries
            if is_retriable:
                parsed = _parse_retry_after_seconds(e)
                wait_sec = min(parsed, RATE_LIMIT_WAIT_MAX_SEC) if parsed is not None else min(
                    RATE_LIMIT_WAIT_SEC * (2 ** (attempt - 1)), RATE_LIMIT_WAIT_MAX_SEC
                )
                print(
                    f"\nRate limit or API error. Waiting {wait_sec}s before retry (attempt {attempt}/{max_retries})...",
                    file=sys.stderr, flush=True,
                )
                await asyncio.sleep(wait_sec)
                continue
            print(f"\nERROR: AE Agent execution failed: {e}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            if formatter:
                formatter.print_footer()
            return 1

    if last_exception:
        print(f"\nERROR: AE Agent failed after {RATE_LIMIT_MAX_RETRIES} attempts: {last_exception}", file=sys.stderr, flush=True)
    return 1


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 runner.py <model_name> <task_description_or_path>", file=sys.stderr)
        print("Example: python3 runner.py claude-sonnet-4-5-20250929 /agent/current_task.txt", file=sys.stderr)
        sys.exit(1)

    model_name = sys.argv[1]
    task_arg = sys.argv[2]
    if os.path.isfile(task_arg):
        with open(task_arg, 'r', encoding='utf-8') as f:
            task_description = f.read()
    else:
        task_description = task_arg

    if not os.environ.get('ANTHROPIC_API_KEY') and not os.environ.get('ANTHROPIC_FOUNDRY_API_KEY'):
        print("ERROR: ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    try:
        timeout_ms_env = os.environ.get("BASH_MAX_TIMEOUT_MS")
        timeout_ms = int(timeout_ms_env) if timeout_ms_env is not None else DEFAULT_TIMEOUT_MS
    except ValueError:
        timeout_ms = DEFAULT_TIMEOUT_MS
    timeout_s = timeout_ms / 1000.0

    try:
        exit_code = asyncio.run(
            asyncio.wait_for(
                run_agent(model_name, task_description),
                timeout=timeout_s,
            )
        )
    except asyncio.TimeoutError:
        print(f"ERROR: Agent execution exceeded timeout ({timeout_s} seconds).", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to run agent: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
