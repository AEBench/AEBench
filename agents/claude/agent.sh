#!/bin/bash
# Claude Code, authenticated with an Anthropic API key (see agent.toml).
unset GEMINI_API_KEY
unset CODEX_API_KEY
unset ANTHROPIC_AUTH_TOKEN
export CLAUDE_CONFIG_DIR="$AEBENCH_STATE_DIR/.claude"

export BASH_MAX_TIMEOUT_MS="36000000"

# Hooks AEBench installs (workdir snapshots before every tool call), if any.
hook_settings=()
if [ -n "${AEBENCH_TOOL_HOOK_SETTINGS:-}" ]; then
    hook_settings=(--settings "$AEBENCH_TOOL_HOOK_SETTINGS")
fi

printf '%s' "$PROMPT" | claude --print --verbose --model "$AEBENCH_MODEL" \
    --output-format stream-json --thinking-display summarized \
    "${hook_settings[@]}" --dangerously-skip-permissions
