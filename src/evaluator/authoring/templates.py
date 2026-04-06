from __future__ import annotations


def render_starter_oracle_files(
    *,
    instruction_path: str,
    expected_output_path: str,
) -> dict[str, str]:
    """Generate starter oracle files for quick-check cases."""
    custom_py = f"""\
from __future__ import annotations

from pathlib import Path

INSTRUCTION_PATH = Path({instruction_path!r})
EXPECTED_OUTPUT_PATH = Path({expected_output_path!r})
"""

    env_setup_py = """\
from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.case_base import CaseOracleEnvSetupBase
from evaluator.oracles import utils
from evaluator.oracles.env_setup_checks import (
    FilesystemPathCheck,
    PathType,
)

from . import custom


class OracleEnvSetup(CaseOracleEnvSetupBase):
    \"\"\"Minimal starter check for the case instructions file.\"\"\"

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

from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from evaluator.oracles import utils
from evaluator.oracles.env_setup_checks import (
    FilesystemPathCheck,
    PathType,
)

from . import custom


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    \"\"\"Minimal starter check for the expected output directory.\"\"\"

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

from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from evaluator.oracles import utils
from evaluator.oracles.env_setup_checks import (
    FilesystemPathCheck,
    PathType,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    \"\"\"Minimal starter check for the quick-check reference file.\"\"\"

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

from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles import utils
from evaluator.oracles.requirements_common import TextFileEqualityCheck

from . import custom


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    \"\"\"Minimal starter check that compares the observed output to the reference.\"\"\"

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