#!/bin/bash
set -euo pipefail

if [[ "$AEBENCH_MODEL" != deepseek-flash ]]; then
    printf 'Supported DeepSeek model: deepseek-flash\n' >&2
    exit 2
fi
unset OPENAI_API_KEY CODEX_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
unset DEEPSEEK_BASE_URL DEEPSEEK_SEARCH_BASE_URL
export DSH_HOME="$AEBENCH_STATE_DIR/.dsh"
export DSH_TELEMETRY_MODE=DISABLED
export DSH_PERMISSION_MODE=danger-full-access

printf '%s' "$PROMPT" | dsh --profile headless \
    --patch "$AEBENCH_AGENT_DIR/model.patch.yml" --json -
