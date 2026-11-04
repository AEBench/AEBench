#!/bin/bash
# Installs Claude Code into the AEBench agent image (run by the Dockerfile).
# claude_non_api uses the same binary.
set -euo pipefail

CLAUDE_CODE_VERSION=2.1.284

npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"
