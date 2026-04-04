from __future__ import annotations


def render_placeholder_oracle_files(case_id: str) -> dict[str, str]:
    env_setup_py = f"""\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleEnvSetupBase
from artevalbench.evaluator.oracles import utils


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError(
            "Placeholder oracle phase env_setup for {case_id}. "
            "Replace with real implementation in oracles/env_setup.py."
        )
"""

    artifact_build_py = f"""\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from artevalbench.evaluator.oracles import utils


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError(
            "Placeholder oracle phase artifact_build for {case_id}. "
            "Replace with real implementation in oracles/artifact_build.py."
        )
"""

    benchmark_prep_py = f"""\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from artevalbench.evaluator.oracles import utils


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError(
            "Placeholder oracle phase benchmark_prep for {case_id}. "
            "Replace with real implementation in oracles/benchmark_prep.py."
        )
"""

    experiment_runs_py = f"""\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from artevalbench.evaluator.oracles import utils


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError(
            "Placeholder oracle phase experiment_runs for {case_id}. "
            "Replace with real implementation in oracles/experiment_runs.py."
        )
"""

    return {
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
"""

    env_setup_py = """\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleEnvSetupBase
from artevalbench.evaluator.oracles import utils
from artevalbench.evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from . import custom


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            FilesystemPathCheck(
                name="instructions_exist",
                path=self.workspace_path(custom.INSTRUCTION_PATH),
                path_type=PathType.FILE,
            ),
        )
"""

    artifact_build_py = """\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from artevalbench.evaluator.oracles import utils
from artevalbench.evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from . import custom


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        output_root = self.workspace_path(custom.EXPECTED_OUTPUT_PATH).parent
        return (
            FilesystemPathCheck(
                name="output_directory_exists",
                path=output_root,
                path_type=PathType.DIRECTORY,
            ),
        )
"""

    benchmark_prep_py = """\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from artevalbench.evaluator.oracles import utils
from artevalbench.evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            FilesystemPathCheck(
                name="expected_reference_exists",
                path=self.ref_path("expected_result.txt"),
                path_type=PathType.FILE,
            ),
        )
"""

    experiment_runs_py = """\
from __future__ import annotations

from collections.abc import Sequence

from artevalbench.evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from artevalbench.evaluator.oracles import utils
from artevalbench.evaluator.oracles.requirements_common import TextFileEqualityCheck

from . import custom


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            TextFileEqualityCheck(
                name="output_matches_reference",
                observed_path=self.workspace_path(custom.EXPECTED_OUTPUT_PATH),
                reference_path=self.ref_path("expected_result.txt"),
            ),
        )
"""

    return {
        "custom.py": custom_py,
        "env_setup.py": env_setup_py,
        "artifact_build.py": artifact_build_py,
        "benchmark_prep.py": benchmark_prep_py,
        "experiment_runs.py": experiment_runs_py,
    }


__all__ = ["render_placeholder_oracle_files", "render_starter_oracle_files"]
