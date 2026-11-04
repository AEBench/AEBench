#!/bin/bash
# Installs the Codex CLI into the AEBench agent image (run by the Dockerfile).
# codex_non_api uses the same binary.
set -euo pipefail

CODEX_VERSION=0.159.1

npm install -g "@openai/codex@${CODEX_VERSION}"
