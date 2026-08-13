from __future__ import annotations

# ---------------------------------------------------------------------------
# Oracle 1: env_setup (interpreter + repo + third-party deps importable)
# ---------------------------------------------------------------------------
# requirements.txt pins pandas==2.0.0 / numpy==1.24.2 (CPython 3.8-3.11 wheels).
PYTHON_MIN_VERSION = (3, 8, 0)
README_PATH = "readme.md"
REQUIRED_PY_MODULES = ("pandas", "numpy", "scipy", "matplotlib", "seaborn", "statsmodels")

# ---------------------------------------------------------------------------
# Oracle 2: artifact_build (the artifact's own code loads / compiles)
# ---------------------------------------------------------------------------
# Shared helper library every experiment builds on (scripts add this to sys.path).
GLOBAL_MODULES_DIR = "global_modules"
GLOBAL_MODULES_IMPORTS = ("format_df", "regions", "graph_templates")

# ---------------------------------------------------------------------------
# Oracle 3: benchmark_prep (committed inputs + scoped experiment scripts present)
# Paths are relative to the repo root (the task target's working directory).
# ---------------------------------------------------------------------------
COMBINED_CARBON_PATH = "shared_data/combined_carbon.csv"
LATENCY_MATRIX_PATH = "shared_data/gcp_latency_matrix.csv"

# ---------------------------------------------------------------------------
# Oracle 4: experiment_runs
# Each scoped experiment writes CSV(s) into its own data_output/. Determinism is
# byte-exact on pinned deps; the oracle compares numeric cells against committed
# references (refs/<key>/<file>) with a small relative tolerance for cross-machine
# float-formatting drift. Every entry: (experiment_dir, output_file, ref_key).
# ---------------------------------------------------------------------------
DEFAULT_REL_TOL = 1e-3

# (experiment directory relative to repo root, output filename, ref subdir key)
SCOPED_OUTPUTS = (
	("sim_trace_analysis/mean_and_cv", "mean_and_cv_2020.csv", "mean_and_cv"),
	("sim_trace_analysis/mean_and_cv", "mean_and_cv_2021.csv", "mean_and_cv"),
	("sim_trace_analysis/mean_and_cv", "mean_and_cv_2022.csv", "mean_and_cv"),
	("sim_trace_analysis/change_over_time", "mean_and_cv_2020.csv", "change_over_time"),
	("sim_trace_analysis/change_over_time", "mean_and_cv_2021.csv", "change_over_time"),
	("sim_trace_analysis/change_over_time", "mean_and_cv_2022.csv", "change_over_time"),
	("sim_spatial/geo_grouping_capacity", "emissions.csv", "geo_grouping_capacity"),
	("sim_spatial/global_idle_capacity", "emissions.csv", "global_idle_capacity"),
	("sim_spatial/capacity_latency", "emissions.csv", "capacity_latency"),
	("sim_spatial/one_and_inf", "savings_mean.csv", "one_and_inf"),
)

# The calculate scripts, one per scoped experiment (dir, script filename).
SCOPED_SCRIPTS = (
	("sim_trace_analysis/mean_and_cv", "calculate_mean_and_cv.py"),
	("sim_trace_analysis/change_over_time", "calculate_mean_and_cv.py"),
	("sim_spatial/geo_grouping_capacity", "calculate_capacity.py"),
	("sim_spatial/global_idle_capacity", "calculate_capacity.py"),
	("sim_spatial/capacity_latency", "calculate_capacity_latency.py"),
	("sim_spatial/one_and_inf", "calculate_one_inf.py"),
)

# The claim checks below name the labels they expect. A truncated table must fail
# rather than silently verifying the claim over the subset the agent happened to
# emit ("holds in all 2 regions" is not the paper's claim).

# --- Paper claim (spatial, Fig 5/6): more shifting freedom -> more carbon savings.
# one_and_inf/savings_mean.csv: unlimited migration ("inf") saves at least as much
# as a single migration ("one") in every region.
ONE_AND_INF_FILE = "sim_spatial/one_and_inf/data_output/savings_mean.csv"
ONE_AND_INF_ONE_ROW = "one"
ONE_AND_INF_INF_ROW = "inf"
ONE_AND_INF_REGIONS = ("Asia", "Americas", "Global", "Europe", "Oceania")

# capacity_latency/emissions.csv: within each latency budget (row), emissions are
# non-increasing as idle capacity grows across the columns (0 -> 50 -> 99), i.e.
# more spare capacity enables more shifting to cleaner regions.
CAPACITY_LATENCY_FILE = "sim_spatial/capacity_latency/data_output/emissions.csv"
# Column order is load-bearing here: the check reads left-to-right as increasing
# idle capacity, so it verifies the labels rather than assuming them.
CAPACITY_LATENCY_CAPACITIES = ("0", "50", "99")
CAPACITY_LATENCY_ROWS = ("5", "50", "100", "150", "200", "250", "300")

# Small slack so float noise does not trip the relational claim checks.
CLAIM_TOL = 1e-6
