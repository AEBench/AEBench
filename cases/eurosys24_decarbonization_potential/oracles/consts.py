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

# The calculate scripts, one per scoped experiment (dir, script filename).
SCOPED_SCRIPTS = (
	("sim_trace_analysis/mean_and_cv", "calculate_mean_and_cv.py"),
	("sim_trace_analysis/change_over_time", "calculate_mean_and_cv.py"),
	("sim_spatial/geo_grouping_capacity", "calculate_capacity.py"),
	("sim_spatial/global_idle_capacity", "calculate_capacity.py"),
	("sim_spatial/capacity_latency", "calculate_capacity_latency.py"),
	("sim_spatial/one_and_inf", "calculate_one_inf.py"),
)

