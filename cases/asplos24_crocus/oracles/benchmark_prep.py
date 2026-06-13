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
