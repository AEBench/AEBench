from __future__ import annotations


def render_placeholder_oracle_files(case_id: str) -> dict[str, str]:
    custom_py = f"""\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import OraclePhaseError


def manual_implementation_required(context: OracleContext, *, phase_name: str) -> None:
    _ = context
    raise OraclePhaseError(
        f"Placeholder oracle phase {{phase_name}} for {case_id}.",
        error="Add custom validation logic in oracle/custom.py and update [oracle] in case.toml.",
    )
"""

    env_setup_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import ENV_SETUP, phase

from . import custom


@phase(ENV_SETUP, priority=100)
def env_setup(context: OracleContext) -> None:
    custom.manual_implementation_required(context, phase_name="env_setup")
"""

    artifact_build_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import ARTIFACT_BUILD, phase

from . import custom


@phase(ARTIFACT_BUILD, priority=200)
def artifact_build(context: OracleContext) -> None:
    custom.manual_implementation_required(context, phase_name="artifact_build")
"""

    benchmark_prep_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import BENCHMARK_PREP, phase

from . import custom


@phase(BENCHMARK_PREP, priority=300)
def benchmark_prep(context: OracleContext) -> None:
    custom.manual_implementation_required(context, phase_name="benchmark_prep")
"""

    experiment_runs_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import EXPERIMENT_RUNS, phase

from . import custom


@phase(EXPERIMENT_RUNS, priority=400)
def experiment_runs(context: OracleContext) -> None:
    custom.manual_implementation_required(context, phase_name="experiment_runs")
"""

    return {
        "custom.py": custom_py,
        "env_setup.py": env_setup_py,
        "artifact_build.py": artifact_build_py,
        "benchmark_prep.py": benchmark_prep_py,
        "experiment_runs.py": experiment_runs_py,
    }


def render_starter_oracle_files(
    *,
    instruction_path: str,
    expected_output_path: str,
) -> dict[str, str]:
    custom_py = f"""\
from __future__ import annotations

from pathlib import Path

INSTRUCTION_PATH = Path({instruction_path!r})
EXPECTED_OUTPUT_PATH = Path({expected_output_path!r})
EXPECTED_REF_PATH = Path("refs") / "expected_result.txt"
"""

    env_setup_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import ENV_SETUP, EnvSetupError, phase, require_path_exists

from . import custom


@phase(ENV_SETUP, priority=100)
def env_setup(context: OracleContext) -> str:
    require_path_exists(
        context.workspace_dir / custom.INSTRUCTION_PATH,
        "artifact instructions are missing from the workspace",
        kind="file",
        exc_type=EnvSetupError,
    )
    return "workspace contains the artifact instructions"
"""

    artifact_build_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import ARTIFACT_BUILD, ArtifactBuildError, phase, require_path_exists

from . import custom


@phase(ARTIFACT_BUILD, priority=200)
def artifact_build(context: OracleContext) -> str:
    output_root = (context.workspace_dir / custom.EXPECTED_OUTPUT_PATH).parent
    require_path_exists(
        output_root,
        "expected output directory is missing",
        kind="dir",
        exc_type=ArtifactBuildError,
    )
    return "expected output directory exists"
"""

    benchmark_prep_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import (
    BENCHMARK_PREP,
    BenchmarkPrepError,
    phase,
    require_command_exists,
    require_path_exists,
    require_python_module_exists,
)

from . import custom


@phase(BENCHMARK_PREP, priority=300)
def benchmark_prep(context: OracleContext) -> str:
    expected_ref_path = context.case_dir / custom.EXPECTED_REF_PATH
    require_path_exists(
        expected_ref_path,
        "expected reference file is missing",
        kind="file",
        exc_type=BenchmarkPrepError,
    )
    require_command_exists(
        "python",
        "python is required before benchmark execution",
        exc_type=BenchmarkPrepError,
    )
    require_python_module_exists(
        "pathlib",
        "pathlib must be importable before benchmark execution",
        exc_type=BenchmarkPrepError,
    )
    return "benchmark references and baseline dependencies are available"
"""

    experiment_runs_py = """\
from __future__ import annotations

from artevalbench.oracle import OracleContext
from artevalbench.oracle import (
    EXPERIMENT_RUNS,
    ExperimentRunsError,
    phase,
    require_file_text_equals,
)

from . import custom


@phase(EXPERIMENT_RUNS, priority=400)
def experiment_runs(context: OracleContext) -> str:
    expected_text = (context.case_dir / custom.EXPECTED_REF_PATH).read_text(encoding="utf-8").strip()
    require_file_text_equals(
        context.workspace_dir / custom.EXPECTED_OUTPUT_PATH,
        expected_text,
        "expected output text does not match the reference",
        exc_type=ExperimentRunsError,
    )
    return "expected output text matches the reference"
"""

    return {
        "custom.py": custom_py,
        "env_setup.py": env_setup_py,
        "artifact_build.py": artifact_build_py,
        "benchmark_prep.py": benchmark_prep_py,
        "experiment_runs.py": experiment_runs_py,
    }


__all__ = ["render_placeholder_oracle_files", "render_starter_oracle_files"]
