#!/bin/bash
set -euo pipefail

if [[ "$AEBENCH_MODEL" != glm-5.3-flash ]]; then
    printf 'Supported ZCode model: glm-5.3-flash\n' >&2
    exit 2
fi
unset OPENAI_API_KEY CODEX_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
unset DEEPSEEK_API_KEY ZAI_API_KEY ZHIPU_API_KEY
export ZCODE_DATA_BASE_DIR="$AEBENCH_STATE_DIR"
export ZCODE_STORAGE_DIR="$AEBENCH_STATE_DIR/storage"
export ZCODE_SESSION_DB_PATH="$AEBENCH_STATE_DIR/session.sqlite"
export ZCODE_LOG_DIR="$AEBENCH_STATE_DIR/logs"
export ZCODE_PERSONAL_PROVIDER_CONFIG_FILE="$AEBENCH_STATE_DIR/provider.json"
export ZCODE_MODEL_TELEMETRY_ENABLED=false
node "$AEBENCH_AGENT_DIR/configure.cjs"
unset ZAI_CODING_PLAN_KEY

# sudo-created benchmark outputs must remain readable by the oracle.
umask 022
exec zcode --prompt "$PROMPT" --cwd "$PWD" --mode yolo \
    --output-format stream-json --no-color \
    --disallowed-tools 'AskUserQuestion EnterPlanMode ExitPlanMode'
