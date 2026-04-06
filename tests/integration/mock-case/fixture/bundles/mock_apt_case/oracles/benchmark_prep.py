"""Benchmark prep oracle for mock_apt_case — always passes."""
from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self):
        return []
