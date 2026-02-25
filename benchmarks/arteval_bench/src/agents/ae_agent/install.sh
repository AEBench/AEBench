#!/bin/bash
# Setup agent running environment inside Docker container.
# Ensures claude-agent-sdk is available so runner.py can import claude_agent_sdk.
set -e
if ! python3 -c "import claude_agent_sdk" 2>/dev/null; then
  echo "Installing claude-agent-sdk..."
  pip3 install claude-agent-sdk==0.1.24 || pip3 install --break-system-packages claude-agent-sdk==0.1.24 || true
  if ! python3 -c "import claude_agent_sdk"; then
    echo "WARNING: claude_agent_sdk still not importable; runner may fail."
  fi
fi
echo "Agent environment ready."
