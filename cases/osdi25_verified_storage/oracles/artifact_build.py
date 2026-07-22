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

        return (
            # CapybaraKV Build Checks:
            # The setup script (setup.sh) installs several unverified baselines and CapybaraKV. 
            # Ensure that the expected compiled binaries or directories exist for:
            # - pmem-Redis, pmem-RocksDB, Viper, YCSB bindings
            # (Note: These are cloned inside the evaluation directory by setup.sh)
            self.path_check(
                name="pmem_redis_exists",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "pmem-redis"),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="pmem_rocksdb_exists",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "pmem-rocksdb"),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="viper_exists",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "viper"),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="ycsb_bindings_exist",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "YCSB"),
                kind=PathKind.DIRECTORY,
            ),
            
            # CapybaraNS Build Checks:
            # - Check that the bin/NotaryServer (or .exe) executable was successfully created in the filesystem.
            self.command_check(
                name="notaryserver_executable_exists",
                cwd=self.workspace_path("osdi25", "capybaraNS", "bin"),
                cmd=("bash", "-c", "ls NotaryServer* > /dev/null 2>&1"),
                timeout_seconds=5.0,
            ),
        )