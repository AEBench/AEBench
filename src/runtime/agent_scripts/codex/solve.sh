#!/bin/bash

unset ANTHROPIC_API_KEY
unset GEMINI_API_KEY
export CODEX_HOME="$HOME/.codex"

printf '%s' "$PROMPT" | codex --search exec --json \
    -c 'model_reasoning_effort="high"' \
    -c model_reasoning_summary=detailed \
    --skip-git-repo-check --yolo --model "$AGENT_CONFIG"
