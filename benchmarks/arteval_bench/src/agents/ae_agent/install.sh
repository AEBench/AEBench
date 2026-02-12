#!/bin/bash
# Setup AE Agent environment inside benchmark container.
# Ensures claude-agent-sdk is available so runner.py can run.
set -e
if ! python3 -c "import claude_agent_sdk" 2>/dev/null; then
  echo "Installing claude-agent-sdk..."
  pip3 install claude-agent-sdk==0.1.24 || pip3 install --break-system-packages claude-agent-sdk==0.1.24 || true
  if ! python3 -c "import claude_agent_sdk"; then
    echo "WARNING: claude_agent_sdk still not importable; runner may fail."
  fi
fi
# 48h Bash timeout for long-running artifact tasks
mkdir -p ~/.claude
cat > ~/.claude/settings.json << 'EOF'
{
  "env": {
    "BASH_MAX_TIMEOUT_MS": "172800000",
    "BASH_DEFAULT_TIMEOUT_MS": "172800000"
  }
}
EOF
echo "AE Agent environment ready (~/.claude/settings.json configured)."
