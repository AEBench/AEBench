"""Environment setup oracle for mock_apt_case — always passes."""
from evaluator.oracles.case_base import CaseOracleEnvSetupBase


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self):
        return []
