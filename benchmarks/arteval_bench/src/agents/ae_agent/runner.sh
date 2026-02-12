#!/bin/bash
# AE Agent runner for ArtEvalBench. Invoked as: runner.sh <model> <task_or_path>
# Do not use set -e; some commands may return non-zero without indicating failure.

if [ $# -ne 2 ]; then
    echo "Usage: $0 <model_location> <task_description_or_path>"
    echo "Example: $0 claude-sonnet-4-5-20250929 /agent/current_task.txt"
    exit 1
fi

export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
export PYTHONUNBUFFERED=1

# 48h = 172800000 ms (align with benchmark long-running agent timeout)
if [ -z "$BASH_MAX_TIMEOUT_MS" ]; then
    export BASH_MAX_TIMEOUT_MS=172800000
fi
if [ -z "$BASH_DEFAULT_TIMEOUT_MS" ]; then
    export BASH_DEFAULT_TIMEOUT_MS="$BASH_MAX_TIMEOUT_MS"
fi

# Invoke Python runner (-u for unbuffered output). Second arg can be task text or path to file.
python3 -u /agent/runner.py "$1" "$2"
