#!/bin/bash

# Do not use set -e; some commands may return non-zero without indicating failure

# Set the model and task as parameters (task can be text or path to file, e.g. /agent/current_task.txt)
if [ $# -ne 2 ]; then
    echo "Usage: $0 <model_location> <task_description_or_path>"
    echo "Example: $0 claude-sonnet-4-5-20250929 \"Install and run tests\""
    echo "         $0 claude-sonnet-4-5-20250929 /agent/current_task.txt"
    exit 1
fi

# Disable Python buffering for real-time log output
export PYTHONUNBUFFERED=1

# Claude Agent SDK Bash timeout: use env if set, else default 96h (must match Python utils.DEFAULT_TIMEOUT_MS = 345_600_000)
if [ -z "$BASH_MAX_TIMEOUT_MS" ]; then
    export BASH_MAX_TIMEOUT_MS=345600000
fi
if [ -z "$BASH_DEFAULT_TIMEOUT_MS" ]; then
    export BASH_DEFAULT_TIMEOUT_MS="$BASH_MAX_TIMEOUT_MS"
fi

# Invoke Python runner (-u for unbuffered output)
python3 -u /agent/runner.py "$1" "$2"
