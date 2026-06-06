from __future__ import annotations

# ── Oracle 1 (env_setup) ──────────────────────────────────────────────────────
DOCKER_MIN_VERSION = (26, 0, 0)
README_PATH = "README.md"
RUN_SH_PATH = "run.sh"
DOCKERFILE_PATH = "Dockerfile"

# ── Oracle 2 & 3 (artifact_build, benchmark_prep) ────────────────────────────

# Paths relative to GOLF_CONTAINER_ROOT (docker mode) or workspace root (local mode)
GOLF_BINARY_PATH = "golf/bin/go"
BASELINE_BINARY_PATH = "baseline/bin/go"
TESTER_BINARY_PATH = "tester/golf-tester"

# ── Oracle 3 (benchmark_prep) ─────────────────────────────────────────────────
BENCHMARKS_REF = "benchmarks.ref.json"
DEADLOCK_GOKER_PATH = "tester/tests/deadlock/gobench/goker"
DEADLOCK_CGO_PATH = "tester/tests/deadlock/cgo-examples"
CORRECT_PATH = "tester/tests/correct"
