from __future__ import annotations

# ---------------------------------------------------------------------------
# Oracle 1: env_setup (host artifact side, golf-style)
# ---------------------------------------------------------------------------
DOCKER_MIN_VERSION = (20, 10, 0)
README_PATH = "README.md"
DOCKERFILE_PATH = "Dockerfile"

# ---------------------------------------------------------------------------
# Oracle 2: artifact_build (binaries present in the agent-built `crocus` image)
# ---------------------------------------------------------------------------
# Executables the experiments rely on; checked via `command -v` inside the image.
REQUIRED_BINARIES = {"z3":(4,12,2), "cargo":(1,72,1), "rustc":(1,72,1), "python3":(3,0,0)}
# Python packages the analysis scripts import.
REQUIRED_PY_MODULES = ("matplotlib", "tabulate")

# ---------------------------------------------------------------------------
# Oracle 3: benchmark_prep (experiment inputs present in the image)
# Paths are relative to app_dir = .../veri_engine.
# ---------------------------------------------------------------------------
CASE_STUDY_ISLE_FILES = (
	"examples/x86/amode_add_uextend_shl.isle",
	"examples/x86/amode_add_shl.isle",
	"examples/broken/udiv/udiv_cve_underlying.isle",
	"examples/broken/cls/broken_cls8.isle",
	"examples/broken/isub/broken_imm12neg_not_distinct.isle",
	"examples/mid-end/broken_bor_band_consts.isle",
)
EXPERIMENT_SCRIPTS = (
	"scripts/wasm1.0-to-aarch64.py",
	"scripts/cdf.py",
)

# ---------------------------------------------------------------------------
# Oracle 4: experiment_runs (agent-saved outputs copied into the artifact dir)
# The agent copies experiment outputs into `self.artifact_dir` under this folder
# (the artifact's native output folder name, per the README).
# ---------------------------------------------------------------------------
RESULTS_DIR = "script-results"

TABLE1_FILE = "table1.txt"
# Figure 4 CDF is written with a timestamped name (cdf-<ts>.pdf); match by glob.
CDF_PDF_GLOB = "cdf*.pdf"
COVERAGE_WASMTIME_FILE = "coverage_wasmtime.txt"
COVERAGE_RUSTC_FILE = "coverage_rustc.txt"

TABLE1_REF = "table1.ref.json"
COVERAGE_REF = "coverage.ref.json"

# Case study output file -> required signature substrings (all must appear).
# Exact counterexample hex/bit values vary between runs and are intentionally
# not checked.
CASE_STUDY_SIGNATURES = {
	"cs_4_3_1.txt": ("Verification failed",),
	"cs_4_3_2.txt": ("Verification failed",),
	"cs_4_3_3.txt": ("Verification failed",),
	"cs_4_4_1.txt": ("Verification succeeded", "Verification failed"),
	"cs_4_4_2.txt": ("Assertion list is only feasible for one input with distinct BV values!",),
	"cs_4_4_4.txt": ("Verification failed",),
}

# Table 1: tolerance allowed on success counts (Z3-version dependent). Totals and
# failure counts are checked exactly.
TABLE1_SUCCESS_TOLERANCE = 6

# Coverage: percentages are deterministic on the saved CSVs; small float epsilon.
COVERAGE_PCT_EPSILON = 0.2
