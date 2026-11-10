#!/bin/bash
# Codex CLI, authenticated with an OpenAI API key (see agent.toml).

unset ANTHROPIC_API_KEY
unset GEMINI_API_KEY
export CODEX_API_KEY="$OPENAI_API_KEY"
export CODEX_HOME="$AEBENCH_STATE_DIR/.codex"

printf '%s' "$PROMPT" | codex --search exec --json \
    -c "model_reasoning_effort=\"$AEBENCH_REASONING_EFFORT\"" \
    -c model_reasoning_summary=detailed \
    --skip-git-repo-check --yolo --model "$AEBENCH_MODEL"
