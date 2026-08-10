from __future__ import annotations

"""Shared helpers and constants for the Unison EuroSys'24 case oracles."""

from pathlib import Path

# Toolchain floors from the AE README (Ubuntu 22.04 recommended).
PYTHON_MIN_VERSION = (3, 8, 0)
CMAKE_MIN_VERSION = (3, 16, 0)
GPP_MIN_VERSION = (7, 0, 0)
GIT_MIN_VERSION = (2, 25, 0)
# OpenMPI / MPICH are required for distributed and several MPI experiments;
# optional for the functional Unison-vs-default accuracy suite (MTP only).
MPI_MIN_VERSION = (4, 0, 0)
MPI_VERSION_REGEX = r"(?:Open MPI\)\s+|Version:\s+)([0-9]+(?:\.[0-9]+){1,2})"

# Drivers and layout required by the unison-evaluations AE branch.
REQUIRED_SCRIPTS = (
    "exp.py",
    "process.py",
    "ns3",
)

REQUIRED_DIRECTORIES = (
    "scratch",
    "scratch/utils",
    "scratch/cdf",
    "scratch/topos",
    "src/mtp",
    "results",
)

REQUIRED_SCRATCH = (
    "scratch/fat-tree.cc",
    "scratch/torus.cc",
    "scratch/bcube.cc",
    "scratch/utils/common.cc",
    "scratch/utils/traffic-generator.cc",
    "scratch/cdf/web-search.txt",
)

# MTP-enabled fat-tree binary produced by:
#   ./ns3 configure -d optimized --enable-modules=... --enable-mtp
#   ./ns3 build fat-tree
FAT_TREE_BINARY_GLOB = "ns3*-fat-tree-optimized"
FAT_TREE_BINARY_DIR = "build/scratch"
# Shared library that proves the Unison MTP module was actually enabled.
MTP_LIBRARY_GLOB = "libns3*-mtp-*"
MTP_LIBRARY_DIR = "build/lib"

# Allowlisted accuracy-style runs (Unison vs default, cluster 2/4).
# Used so reference JSON cannot drive path traversal.
REQUIRED_RUNS = (
    "unison_c2",
    "unison_c4",
    "default_c2",
    "default_c4",
)

# Map (simulator, cluster) from exp.py accuracy CSV rows onto REQUIRED_RUNS.
RUN_KEY_BY_SIM_CLUSTER: dict[tuple[str, int], str] = {
    ("unison", 2): "unison_c2",
    ("unison", 4): "unison_c4",
    ("default", 2): "default_c2",
    ("default", 4): "default_c4",
}

# Flow-monitor fidelity metrics (Table 2 / figure 2 accuracy). Skip wall-clock `t`.
METRIC_KEYS = (
    "flow_count",
    "fflow_count",
    "fct",
    "ffct",
    "e2ed",
    "throughput",
    "nthroughput",
    "ev",
)

RESULTS_REF = "accuracy_metrics.ref.json"
RESULTS_AGGREGATE = "aebench_accuracy.json"
ACCURACY_CSV_GLOB = "accuracy-*.csv"
SIMILARITY_THRESHOLD = 0.75

BUILD_MODE_ENV = "AE_UNISON_BUILD_MODE"
BUILD_TIMEOUT_SECONDS = 3600.0
# Cap ns-3 / ninja parallelism (README / exp.py use unbounded -j).
MAKE_JOBS_CAP = 8

# Comma-separated ns-3 module list for README/exp.py configure.
ENABLE_MODULES = "applications,flow-monitor,mpi,mtp,nix-vector-routing,point-to-point"


def find_repo_root(workspace_dir: Path) -> Path:
    """Resolve the UNISON-for-ns-3 repository root inside a workspace.

    Accepts either a direct clone (workspace == repo) or a nested directory
    created by common clone layouts.
    """
    candidates = (
        workspace_dir,
        workspace_dir / "UNISON-for-ns-3",
        workspace_dir / "unison-for-ns-3",
        workspace_dir / "Unison-for-ns-3",
    )
    for candidate in candidates:
        if (candidate / "ns3").is_file() and (candidate / "exp.py").is_file():
            if (candidate / "src" / "mtp").is_dir():
                return candidate
    return workspace_dir
