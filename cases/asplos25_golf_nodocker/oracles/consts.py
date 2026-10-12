from __future__ import annotations

# Oracle 1 (env_setup)
# make.bash builds the runtime's cgo parts and the cgo-examples suite needs a C compiler.
GCC_MIN_VERSION = (5, 0, 0)
README_PATH = "README.md"
RUN_SH_PATH = "run.sh"
DOCKERFILE_PATH = "Dockerfile"

# Oracle 2 & 3 (artifact_build, benchmark_prep)

# Paths relative to the workspace (the agent builds in place; no golf container)
GOLF_BINARY_PATH = "golf/bin/go"
BASELINE_BINARY_PATH = "baseline/bin/go"
TESTER_BINARY_PATH = "tester/golf-tester"

# Both toolchains are built from go1.22.5 sources (baseline/VERSION, golf/VERSION).
GO_TOOLCHAIN_VERSION = (1, 22, 5)
GO_VERSION_REGEX = r"go version go(\d+\.\d+(?:\.\d+)?)"

# Oracle 3 (benchmark_prep)
BENCHMARKS_REF = "benchmarks.ref.json"
DEADLOCK_GOKER_PATH = "tester/tests/deadlock/gobench/goker"
DEADLOCK_CGO_PATH = "tester/tests/deadlock/cgo-examples"
CORRECT_PATH = "tester/tests/correct"
