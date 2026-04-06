"""Experiment runs oracle for mock_apt_case — always passes."""
from evaluator.oracles.case_base import CaseOracleExperimentRunsBase


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self):
        return []
