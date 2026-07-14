from __future__ import annotations

# ---------------------------------------------------------------------------
# GAIA (ASPLOS'24 "Going Green for Less Green") is a pure-Python, deterministic
# carbon-aware scheduling *simulator*. There is no compiled artifact and no
# custom Docker image: the agent pip-installs the deps and runs four figure
# scripts, each of which invokes `python3 src/run.py` several times and writes
# per-run CSVs. Every oracle phase therefore runs in the default `task` target
# (the committed agent container), where both the installed deps and the
# produced `results/` live.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Oracle 1: env_setup
# ---------------------------------------------------------------------------
# requirements.txt pins pandas==1.4.3, which ships wheels for CPython 3.8-3.10.
PYTHON_MIN_VERSION = (3, 8, 0)

README_PATH = "README.md"
RUN_PY_PATH = "src/run.py"

# ---------------------------------------------------------------------------
# Oracle 2: artifact_build (deps importable + entrypoint imports cleanly)
# ---------------------------------------------------------------------------
# numpy is intentionally listed: requirements.txt under-pins it, and a too-new
# numpy breaks pandas 1.4.3's ABI, so a working env must have a compatible numpy.
REQUIRED_PY_MODULES = ("pandas", "numpy", "matplotlib", "seaborn")

# ---------------------------------------------------------------------------
# Oracle 3: benchmark_prep (experiment inputs + figure scripts present)
# Paths are relative to the repo root (the task target's working directory).
# ---------------------------------------------------------------------------
TASK_TRACE_PATH = "src/cluster_traces/pai_1k.csv"
CARBON_TRACE_PATH = "src/traces/AU-SA.csv"
FIGURE_SCRIPTS = (
	"src/figure8-9.sh",
	"src/figure10.sh",
	"src/figure11.sh",
	"src/figure12.sh",
)

# ---------------------------------------------------------------------------
# Oracle 4: experiment_runs
# The figure scripts write one summary CSV per run under this directory (header
# `carbon_cost,dollar_cost`, one data row of cluster totals). The exhaustive set
# of expected filenames is the key set of refs/gaia_results.ref.json, so the
# oracle does not hardcode it here (avoids drift). Below are only the semantic
# groupings needed for the paper's claim checks.
# ---------------------------------------------------------------------------
RESULTS_SUBDIR = "results/simulation/pai_1k"
RESULTS_REF = "gaia_results.ref.json"

# Fallback tolerance if the ref JSON omits "rel_tolerance". The simulation is
# deterministic; 1% absorbs cross-machine float-formatting drift while catching
# wrong policies / traces / fabricated numbers.
DEFAULT_REL_TOL = 0.01

# --- Paper claim 1 (Fig 8/9): carbon-aware shifting reduces carbon emissions.
# Every carbon-aware policy's carbon_cost must be below the "No Jobs Wait"
# (carbon-agnostic) baseline. All at reserved=0.
FIG89_BASELINE = "carbon-7000-oracle-AU-SA-0-0x0.csv"
FIG89_CARBON_AWARE = (
	"carbon-7000-lowest-AU-SA-0-6x24.csv",
	"carbon-7000-waiting-AU-SA-0-6x24.csv",
	"carbon-7000-cst_average-AU-SA-0-6x24.csv",
	"suspend-resume-threshold-7000-oracle-AU-SA-0-6x24.csv",
	"suspend-resume-7000-oracle-AU-SA-0-6x24.csv",
)

# --- Paper claim 2 (Fig 11): allocating reserved instances lowers dollar cost.
# In the carbon-cost / cst_average sweep, every run with reserved instances (r>0)
# costs less than the no-reserved (r=0) run. The curve is U-shaped (idle reserved
# capacity eventually raises cost again), so this is a reduction-vs-baseline claim,
# not a monotonic one; the full curve is pinned by the per-run numeric checks.
FIG11_RESERVED_BASELINE = "carbon-cost-7000-cst_average-AU-SA-0-6x24.csv"
FIG11_RESERVED_STEPS = tuple(
	f"carbon-cost-7000-cst_average-AU-SA-{r}-6x24.csv"
	for r in (3, 6, 9, 12, 15, 18, 21, 24)
)
