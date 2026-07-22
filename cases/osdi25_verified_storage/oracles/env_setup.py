from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleEnvSetupBase

class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # - python3 (needed for the line-counting and plotting scripts)
            self.version_check(
                name="python3_version",
                cmd=("python3", "--version"),
                min_version=(3, 0, 0),
            ),
            
            # - cargo/rustc (needed for CapybaraKV)
            self.version_check(
                name="cargo_version",
                cmd=("cargo", "--version"),
                min_version=(1, 0, 0),
            ),
            
            # - scons (the build system used for CapybaraNS)
            self.version_check(
                name="scons_version",
                cmd=("scons", "--version"),
                min_version=(1, 0, 0),
            ),

            # - dafny (needed for CapybaraNS verification)
            self.command_check(
                name="dafny_exists",
                cmd=("bash", "-c", "find ~ -name Dafny.dll -type f 2>/dev/null | grep -q 'Dafny.dll'"),
                timeout_seconds=30.0,
            ),
            
            # - Check that the PM device (e.g., /mnt/pmem/) is mounted and available, as the performance benchmarks rely on this.
            self.command_check(
                name="pmem_mounted",
                cmd=("mountpoint", "-q", "/mnt/pmem"),
                timeout_seconds=5.0,
                optional=True,
            ),
        )
