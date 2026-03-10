#!/bin/bash
# Run ae_agent smoke test under arteval_bench (host + docker, with evaluation).
# Usage: ./run_ae_agent_smoke_test.sh [model_name]
# Default model: claude-sonnet-4-5-20250929

set -e
BENCH_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$BENCH_ROOT"
MODEL="${1:-claude-sonnet-4-5-20250929}"
OUT_DIR="./outputs/ae_agent_smoke_$(date +%Y%m%d_%H%M%S)"
echo "==> AE Agent smoke test (host + docker + evaluation)"
echo "    Model: $MODEL"
echo "    Output: $OUT_DIR"
echo ""
python src/main.py \
  -i ./data/benchmark/ae_agent_smoke_test.jsonl \
  -a ae_agent \
  -m "$MODEL" \
  -o "$OUT_DIR"
echo ""
echo "==> Done. Results: $OUT_DIR/result.jsonl and $OUT_DIR/avg_score.json"
