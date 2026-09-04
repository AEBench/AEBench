#!/bin/bash
unset GEMINI_API_KEY
unset CODEX_API_KEY
unset ANTHROPIC_AUTH_TOKEN
unset CLAUDE_CODE_USE_BEDROCK
unset CLAUDE_CODE_USE_VERTEX
unset CLAUDE_CODE_USE_FOUNDRY
export CLAUDE_CONFIG_DIR="$AEBENCH_AGENT_SUPPORT_DIR/.claude"

# Clear the API key so the CLI uses the subscription OAuth token.
export ANTHROPIC_API_KEY=""

# Load the OAuth token from the temporary home directory prepared by AEBench.
if [ -f "$AEBENCH_AGENT_SUPPORT_DIR/oauth_token" ]; then
	export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$AEBENCH_AGENT_SUPPORT_DIR/oauth_token")"
else
	echo "ERROR: OAuth token file not found: $AEBENCH_AGENT_SUPPORT_DIR/oauth_token"
    exit 1
fi

export BASH_MAX_TIMEOUT_MS="36000000"

# Use the effort level selected by AEBench.
printf '%s' "$PROMPT" | claude --print --verbose --model "$AGENT_CONFIG" \
    --output-format stream-json --effort "$AEBENCH_REASONING_EFFORT" --thinking-display summarized \
    --dangerously-skip-permissions
