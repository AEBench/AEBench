#!/usr/bin/env python3
"""Interactive runner for AE Agent - runs inside container after main task.

Used when interactive=True: docker exec -it <container_id> python3 /agent/interactive_runner.py <model>
Artifact at /repo; API keys from container env.
"""

import asyncio
import os
import sys

sys.path.insert(0, '/agent')

try:
    from utils import DEFAULT_TIMEOUT_MS
except ImportError:
    DEFAULT_TIMEOUT_MS = 172_800_000

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
except ImportError as e:
    print(f"ERROR: claude_agent_sdk not available: {e}", file=sys.stderr)
    sys.exit(1)


def _build_system_prompt() -> str:
    try:
        timeout_ms_env = os.environ.get("BASH_MAX_TIMEOUT_MS")
        timeout_ms = int(timeout_ms_env) if timeout_ms_env else DEFAULT_TIMEOUT_MS
    except ValueError:
        timeout_ms = DEFAULT_TIMEOUT_MS

    return """You are an experienced software engineer in an interactive session.

ENVIRONMENT:
- You are inside a Docker container with root permissions.
- The artifact repository is at /repo. Change to it: cd /repo
- You have access to Read, Write, and Bash tools.

TIMEOUT: Long-running commands can take hours; do not set short timeouts.

You will receive follow-up instructions from the user. Complete each one and respond.
If the user asks to stop or says 'quit'/'exit', acknowledge and they will end the session."""


def _display_message(msg) -> None:
    if hasattr(msg, 'content'):
        for block in msg.content:
            if hasattr(block, 'text'):
                print(block.text, end='', flush=True)
    print(flush=True)


async def _interactive_loop(model_name: str) -> int:
    options = ClaudeAgentOptions(
        system_prompt=_build_system_prompt(),
        allowed_tools=["Read", "Write", "Bash"],
        setting_sources=["user"],
    )

    print("\n" + "=" * 60, flush=True)
    print("Interactive mode - Agent ready. Type your instructions (or 'quit'/'exit' to end).", flush=True)
    print("=" * 60 + "\n", flush=True)

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Please confirm you are in /repo and ready for the user's follow-up instructions. Reply briefly that you are ready."
        )
        async for msg in client.receive_response():
            _display_message(msg)

        while True:
            try:
                user_input = input("\n>>> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting interactive mode.", flush=True)
                return 0

            if not user_input:
                continue
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("Exiting interactive mode.", flush=True)
                return 0

            await client.query(user_input)
            async for msg in client.receive_response():
                _display_message(msg)

    return 0


def main() -> int:
    model_name = os.environ.get("AE_AGENT_MODEL", "claude-sonnet-4-5-20250929")
    if len(sys.argv) >= 2:
        model_name = sys.argv[1]

    if not os.environ.get('ANTHROPIC_API_KEY') and not os.environ.get('ANTHROPIC_FOUNDRY_API_KEY'):
        print("ERROR: ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY must be set.", file=sys.stderr)
        return 1

    return asyncio.run(_interactive_loop(model_name))


if __name__ == "__main__":
    sys.exit(main())
