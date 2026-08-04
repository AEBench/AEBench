#!/bin/bash
unset GEMINI_API_KEY
unset CODEX_API_KEY
unset ANTHROPIC_AUTH_TOKEN
unset CLAUDE_CODE_USE_BEDROCK
unset CLAUDE_CODE_USE_VERTEX
unset CLAUDE_CODE_USE_FOUNDRY
export CLAUDE_CONFIG_DIR="$HOME/.claude"

# Clear API key so the CLI uses the OAuth token from subscription
export ANTHROPIC_API_KEY=""

# Load OAuth token from the per-run home prepared by AEBench
if [ -f "$HOME/oauth_token" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$HOME/oauth_token")"
else
    echo "ERROR: No oauth_token file found at $HOME/oauth_token"
    exit 1
fi

export BASH_MAX_TIMEOUT_MS="36000000"

# Set default effort level to high for consistency
printf '%s' "$PROMPT" | claude --print --verbose --model "$AGENT_CONFIG" \
    --output-format stream-json --effort high --thinking-display summarized \
    --dangerously-skip-permissions
