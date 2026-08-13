from __future__ import annotations

"""Shared helpers and constants for the UniZK ASPLOS'25 case oracles."""

from pathlib import Path

# Toolchain versions from the upstream README prerequisites.
RUSTC_MIN_VERSION = (1, 80, 0)
CMAKE_MIN_VERSION = (3, 22, 0)
GPP_MIN_VERSION = (11, 0, 0)

# Primary RamSim build product expected by src/config/ram_config.rs.
RAMULATOR_RELPATH = "thirdparty/ramsim/build/ramulator2"

# Scripts and layout required to configure, build, and run the artifact.
REQUIRED_SCRIPTS = (
    "dependency.sh",
    "docker.sh",
    "run_cpu_test.sh",
    "run_plonky2_test.sh",
    "run_starky_test.sh",
    "clean.sh",
)

REQUIRED_DIRECTORIES = (
    "configs",
    "examples",
    "src",
    "thirdparty/ramsim",
    "thirdparty/plonky2",
    "traces",
)

REQUIRED_CONFIGS = (
    "configs/Cargo.toml",
    "configs/Cargo.toml.v0.1",
    "configs/factorial.yaml",
    "configs/fibonacci.yaml",
    "configs/mvm.yaml",
    "configs/sha256.yaml",
    "configs/ecdsa.yaml",
    "configs/crop.yaml",
    "configs/aes_starky.yaml",
    "configs/aes_starky_recursive.yaml",
    "configs/sha256_starky.yaml",
    "configs/sha256_starky_recursive.yaml",
    "configs/fib_starky.yaml",
    "configs/fib_starky_recursive.yaml",
    "configs/fac_starky.yaml",
    "configs/fac_starky_recursive.yaml",
)

# Allowed RamConfig workload names (must match configs/<name>.yaml stems).
# Used as an allowlist so reference JSON cannot drive path traversal.
REQUIRED_WORKLOADS = (
    "factorial",
    "fibonacci",
    "mvm",
    "sha256",
)

# Simulation logs written by RamConfig::new + ramsim.run() for REQUIRED_WORKLOADS.
REQUIRED_SIM_LOGS = tuple(f"{name}.log" for name in REQUIRED_WORKLOADS)

# Metrics emitted by Ramulator2 TraceGen / GenericDRAM into <workload>.log.
METRIC_KEYS = (
    "memory_system_cycles",
    "total_num_read_requests",
    "total_num_write_requests",
    "s_total_mem_req",
)

RESULTS_REF = "simulation_metrics.ref.json"
SIMILARITY_THRESHOLD = 0.75

BUILD_MODE_ENV = "AE_UNIZK_BUILD_MODE"
BUILD_TIMEOUT_SECONDS = 3600.0
# Cap make parallelism (README uses unbounded `make -j`).
MAKE_JOBS_CAP = 8


def find_repo_root(workspace_dir: Path) -> Path:
    """Resolve the UniZK repository root inside a workspace.

    Accepts either a direct clone (workspace == repo) or a nested UniZK/
    directory created by common clone layouts.
    """
    candidates = (
        workspace_dir,
        workspace_dir / "UniZK",
        workspace_dir / "unizk",
    )
    for candidate in candidates:
        if (candidate / "Cargo.toml").is_file() and (candidate / "README.md").is_file():
            if (candidate / "thirdparty" / "ramsim").is_dir():
                return candidate
    return workspace_dir
