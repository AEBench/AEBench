#!/bin/bash
# Claude Code, authenticated with an Anthropic API key (see agent.toml).
unset GEMINI_API_KEY
unset CODEX_API_KEY
unset ANTHROPIC_AUTH_TOKEN
export CLAUDE_CONFIG_DIR="$AEBENCH_STATE_DIR/.claude"

export BASH_MAX_TIMEOUT_MS="36000000"

printf '%s' "$PROMPT" | claude --print --verbose --model "$AEBENCH_MODEL" \
    --output-format stream-json --thinking-display summarized \
    --dangerously-skip-permissions
