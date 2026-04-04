from __future__ import annotations

import types
from pathlib import Path

from ...models import OracleContext
from ..constants import REFS_DIRNAME
from .artifact_build_checks import OracleArtifactBuildBase
from .benchmark_prep_checks import OracleBenchmarkPrepBase
from .env_setup_checks import OracleEnvSetupBase
from .experiment_runs_checks import OracleExperimentRunsBase


class _CaseOracleBase:
    def __init__(self, *, context: OracleContext, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._context = context
        self._case_dir = context.case_dir.resolve()
        self._artifact_dir = context.artifact_dir.resolve(strict=False)
        self._workspace_dir = context.workspace_dir.resolve(strict=False)
        self._output_dir = context.output_dir.resolve(strict=False)
        self._refs_dir = (context.case_dir / REFS_DIRNAME).resolve(strict=False)

    @property
    def context(self) -> OracleContext:
        return self._context

    @property
    def paths(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            case_dir=self._case_dir,
            artifact_dir=self._artifact_dir,
            workspace_dir=self._workspace_dir,
            output_dir=self._output_dir,
            refs_dir=self._refs_dir,
        )

    def case_path(self, *parts: str | Path) -> Path:
        return self._case_dir.joinpath(*parts) if parts else self._case_dir

    def artifact_path(self, *parts: str | Path) -> Path:
        return self._artifact_dir.joinpath(*parts) if parts else self._artifact_dir

    def workspace_path(self, *parts: str | Path) -> Path:
        return self._workspace_dir.joinpath(*parts) if parts else self._workspace_dir

    def output_path(self, *parts: str | Path) -> Path:
        return self._output_dir.joinpath(*parts) if parts else self._output_dir

    def ref_path(self, *parts: str | Path) -> Path:
        return self._refs_dir.joinpath(*parts) if parts else self._refs_dir


class CaseOracleEnvSetupBase(_CaseOracleBase, OracleEnvSetupBase):
    pass


class CaseOracleArtifactBuildBase(_CaseOracleBase, OracleArtifactBuildBase):
    pass


class CaseOracleBenchmarkPrepBase(_CaseOracleBase, OracleBenchmarkPrepBase):
    pass


class CaseOracleExperimentRunsBase(_CaseOracleBase, OracleExperimentRunsBase):
    pass
