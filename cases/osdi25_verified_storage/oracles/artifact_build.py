from __future__ import annotations

import os
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind

_BUILD_MODE_ENV = "AE_CAPYBARA_BUILD_MODE"

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[BaseCheck]:
        mode = (os.environ.get(_BUILD_MODE_ENV, "verify") or "verify").strip().lower()

        if mode == "command":
            return (
                self.command_check(
                    name="setup_capybarakv",
                    cwd=self.workspace_path("osdi25", "capybaraKV", "evaluation"),
                    cmd=("./setup.sh",),
                    timeout_seconds=1800.0,
                ),
            )

        # 2. artifact_build.py (Artifact Compilation)
        # Verify that the agent successfully compiled the systems (Goal 2).
        return (
            # CapybaraKV Build Checks:
            # The setup script (setup.sh) installs several unverified baselines and CapybaraKV. 
            # Ensure that the expected compiled binaries or directories exist for:
            # - pmem-Redis, pmem-RocksDB, Viper, YCSB bindings
            # (Note: These are cloned inside the evaluation directory by setup.sh)
            self.command_check(
                name="pmem_redis_exists",
                cmd=("test", "-d", str(self.workspace_path("osdi25", "capybaraKV", "evaluation", "pmem-redis"))),
                timeout_seconds=5.0,
            ),
            self.command_check(
                name="pmem_rocksdb_exists",
                cmd=("test", "-d", str(self.workspace_path("osdi25", "capybaraKV", "evaluation", "pmem-rocksdb"))),
                timeout_seconds=5.0,
            ),
            self.command_check(
                name="viper_exists",
                cmd=("test", "-d", str(self.workspace_path("osdi25", "capybaraKV", "evaluation", "viper"))),
                timeout_seconds=5.0,
            ),
            self.command_check(
                name="ycsb_bindings_exist",
                cmd=("test", "-d", str(self.workspace_path("osdi25", "capybaraKV", "evaluation", "YCSB"))),
                timeout_seconds=5.0,
            ),
            
            # CapybaraNS Build Checks:
            # - Check that the bin/NotaryServer (or .exe) executable was successfully created in the filesystem.
            self.path_check(
                name="notaryserver_executable_exists",
                path=self.workspace_path("osdi25", "capybaraNS", "bin", "NotaryServer"),
                kind=PathKind.FILE,
            ),
        )