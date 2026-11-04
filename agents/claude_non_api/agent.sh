#!/bin/bash
# Claude Code, authenticated with a Claude subscription OAuth token (see agent.toml).

unset GEMINI_API_KEY
unset CODEX_API_KEY
unset ANTHROPIC_AUTH_TOKEN
unset CLAUDE_CODE_USE_BEDROCK
unset CLAUDE_CODE_USE_VERTEX
unset CLAUDE_CODE_USE_FOUNDRY
export CLAUDE_CONFIG_DIR="$AEBENCH_STATE_DIR/.claude"

# Clear the API key so the CLI uses the subscription OAuth token
# (CLAUDE_CODE_OAUTH_TOKEN, delivered by AEBench from agent.toml [[secrets]]).
export ANTHROPIC_API_KEY=""

# Disable Claude Code background-task functionality
export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1

# Allow long-running foreground commands
export BASH_DEFAULT_TIMEOUT_MS=3600000
export BASH_MAX_TIMEOUT_MS=36000000

# Limit Bash output returned to Claude (characters)
export BASH_MAX_OUTPUT_LENGTH=16000

# Use the effort level selected by AEBench
printf '%s' "$PROMPT" | claude --print --verbose --model "$AEBENCH_MODEL" \
    --output-format stream-json --effort "$AEBENCH_REASONING_EFFORT" --thinking-display summarized \
    --dangerously-skip-permissions
