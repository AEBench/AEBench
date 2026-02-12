#!/bin/bash
# Run ArtEval benchmark with ae_agent. Execute this script from the benchmark root.
# Usage: ./run_ae_agent.sh [optional: model name, default claude-sonnet-4-5-20250929]

set -e
BENCH_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODEL_NAME="${1:-claude-sonnet-4-5-20250929}"
cd "$BENCH_ROOT"
echo "==> ArtEval benchmark root: $BENCH_ROOT"
echo "==> Model: $MODEL_NAME"
echo "==> Agent: ae_agent"
python src/main.py \
  -i ./data/benchmark/arteval_tasks.jsonl \
  -a ae_agent \
  -m "$MODEL_NAME" \
  -o "./outputs/ae_agent_${MODEL_NAME//\//_}_$(date +%Y-%m-%d_%H-%M-%S)"
