from __future__ import annotations

from .oracles.discovery import (
    DiscoveredPhase,
    OracleLoadError,
    PhaseKey,
    ENV_SETUP,
    ARTIFACT_BUILD,
    BENCHMARK_PREP,
    EXPERIMENT_RUNS,
    phase_key_to_string,
    phase_string_to_key,
)
from .oracles.execution import run_phases as execute_oracle_phases

__all__ = [
    "DiscoveredPhase",
    "ENV_SETUP",
    "ARTIFACT_BUILD",
    "BENCHMARK_PREP",
    "EXPERIMENT_RUNS",
    "OracleLoadError",
    "PhaseKey",
    "execute_oracle_phases",
    "phase_key_to_string",
    "phase_string_to_key",
]
