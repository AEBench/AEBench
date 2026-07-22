from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleEnvSetupBase

class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[BaseCheck]:
        # 1. env_setup.py (Environment Setup)
        # Ensure the agent's base environment has the necessary tools to run the artifact before it even starts compiling or evaluating.
        return (
            # System Dependencies:
            # Use DependencyVersionCheck or command_check to verify the presence of:
            
            # - python3 (needed for the line-counting and plotting scripts)
            self.command_check(
                name="python3_installed",
                cmd=("python3", "--version"),
                timeout_seconds=5.0,
            ),
            
            # - cargo/rustc (needed for CapybaraKV)
            self.command_check(
                name="cargo_installed",
                cmd=("cargo", "--version"),
                timeout_seconds=5.0,
            ),
            
            # - dafny (needed for CapybaraNS verification)
            # Note: This check MUST be in Phase 1 (env_setup) to ensure that if Dafny is missing,
            # we correctly attribute the failure to a broken oracle environment/infrastructure,
            # rather than unfairly assuming the AI agent failed to build the artifact.
            # We search for the dafny executable dynamically in the user's home directory so that
            # we don't require it to be manually added to the system PATH via a global symlink.
            self.command_check(
                name="dafny_installed",
                cmd=("bash", "-c", "dafny_bin=$(find ~ -name dafny -type f -executable 2>/dev/null | grep -v 'Trash' | head -n 1); if [ -z \"$dafny_bin\" ]; then echo 'dafny not found'; exit 1; fi; $dafny_bin --version"),
                timeout_seconds=15.0,
            ),
            
            # - scons (the build system used for CapybaraNS)
            self.command_check(
                name="scons_installed",
                cmd=("scons", "--version"),
                timeout_seconds=5.0,
            ),
            
            # Hardware Validation (Optional): 
            # - Check that the PM device (e.g., /mnt/pmem/) is mounted and available, as the performance benchmarks rely on this.
            self.command_check(
                name="pmem_mounted",
                cmd=("mountpoint", "-q", "/mnt/pmem"),
                timeout_seconds=5.0,
                optional=True,
            ),
        )
