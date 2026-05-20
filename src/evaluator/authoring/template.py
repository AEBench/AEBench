"""Class-based starter oracle templates."""

from __future__ import annotations

from pathlib import Path

from evaluator.constants import ORACLE_DIRNAME

_EXPECTED_RESULT = "expected_result.txt"
_DEFAULT_OUTPUT = "demo-output/result.txt"
_TEMPLATE_FILENAMES = (
    "__init__.py",
    "case_constants.py",
    "env_setup.py",
    "artifact_build.py",
    "benchmark_prep.py",
    "experiment_runs.py",
)

def write_oracle_templates(
    case_dir: Path,
    *,
    instruction_path: str = "README.md",
    expected_output_path: str = _DEFAULT_OUTPUT,
    overwrite: bool = False,
) -> list[Path]:
    oracle_dir = case_dir / ORACLE_DIRNAME
    oracle_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for relative_path, content in render_oracle_templates(
        instruction_path=instruction_path,
        expected_output_path=expected_output_path,
    ).items():
        target = oracle_dir / relative_path
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written


def render_oracle_templates(*, instruction_path: str, expected_output_path: str) -> dict[str, str]:
    constants_py = f"""\
from __future__ import annotations

INSTRUCTION_PATH = {instruction_path!r}
EXPECTED_OUTPUT_PATH = {expected_output_path!r}
EXPECTED_RESULT_REF = {_EXPECTED_RESULT!r}
"""

    env_setup_py = """\
from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind

from .case_constants import INSTRUCTION_PATH


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence:
        return (
            self.path_check(
                name="instructions_exist",
                path=self.workspace_path(INSTRUCTION_PATH),
                kind=PathKind.FILE,
            ),
        )
"""

    artifact_build_py = """\
from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind

from .case_constants import EXPECTED_OUTPUT_PATH


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence:
        return (
            self.path_check(
                name="output_directory_exists",
                path=self.workspace_path(EXPECTED_OUTPUT_PATH).parent,
                kind=PathKind.DIRECTORY,
            ),
        )
"""

    benchmark_prep_py = """\
from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind

from .case_constants import EXPECTED_RESULT_REF


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence:
        return (
            self.path_check(
                name="expected_reference_exists",
                path=self.ref_path(EXPECTED_RESULT_REF),
                kind=PathKind.FILE,
            ),
        )
"""

    experiment_runs_py = """\
from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase

from .case_constants import EXPECTED_OUTPUT_PATH, EXPECTED_RESULT_REF


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence:
        return (
            self.text_file_equal(
                name="output_matches_reference",
                observed_path=self.workspace_path(EXPECTED_OUTPUT_PATH),
                reference_path=self.ref_path(EXPECTED_RESULT_REF),
            ),
        )
"""

    rendered = {
        "__init__.py": "\"\"\"Case-local oracle package.\"\"\"\n",
        "case_constants.py": constants_py,
        "env_setup.py": env_setup_py,
        "artifact_build.py": artifact_build_py,
        "benchmark_prep.py": benchmark_prep_py,
        "experiment_runs.py": experiment_runs_py,
    }
    return {name: rendered[name] for name in _TEMPLATE_FILENAMES}
