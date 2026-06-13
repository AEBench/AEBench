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
