#!/bin/bash
unset ANTHROPIC_API_KEY
unset GEMINI_API_KEY

# Clear API keys so the CLI uses ChatGPT subscription authentication.
export CODEX_API_KEY=""
export OPENAI_API_KEY=""
export CODEX_HOME="$AEBENCH_AGENT_SUPPORT_DIR/.codex"

# Select ChatGPT authentication.
if ! grep -q "forced_login_method" "$CODEX_HOME/config.toml" 2>/dev/null; then
	printf '\nforced_login_method = "chatgpt"\n' >> "$CODEX_HOME/config.toml"
fi

printf '%s' "$PROMPT" | codex --search exec --json \
    -c "model_reasoning_effort=\"$AEBENCH_REASONING_EFFORT\"" \
    -c model_reasoning_summary=detailed \
    --skip-git-repo-check --yolo --model "$AGENT_CONFIG"
