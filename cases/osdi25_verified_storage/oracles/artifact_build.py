from __future__ import annotations

# import os
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind

# _BUILD_MODE_ENV = "AE_CAPYBARA_BUILD_MODE"

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[BaseCheck]:
        # mode = (os.environ.get(_BUILD_MODE_ENV, "verify") or "verify").strip().lower()
        # 
        # if mode == "command":
        #     return (
        #         self.command_check(
        #             name="setup_capybarakv",
        #             cwd=self.workspace_path("osdi25", "capybaraKV", "evaluation"),
        #             cmd=("./setup.sh",),
        #             timeout_seconds=1800.0,
        #         ),
        #     )

        return (
            # CapybaraKV Build Checks:
            # The setup script (setup.sh) compiles several unverified baselines and CapybaraKV. 
            # We specifically check for the compiled binaries (rather than just the existing source directories)
            # to verify that the build commands (make, mvn, etc.) inside setup.sh actually succeeded.
            
            # - pmem-Redis: Built via `make USE_NVM=yes` (setup.sh lines 251-252).
            #   Outputs the main executable to src/redis-server.
            self.path_check(
                name="pmem_redis_exists",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "pmem-redis", "src", "redis-server"),
                kind=PathKind.FILE,
            ),
            # - pmem-RocksDB: Built via `make rocksdbjava` (setup.sh lines 245-246).
            #   Outputs the Java JNI native library to java/target/librocksdbjni-linux64.so.
            self.path_check(
                name="pmem_rocksdb_exists",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "pmem-rocksdb", "java", "target", "librocksdbjni-linux64.so"),
                kind=PathKind.FILE,
            ),
            # - Viper: Built via `make all` (setup.sh lines 283-284).
            #   Builds a C++ wrapper around Viper for YCSB, producing libviper_wrapper.so.
            self.path_check(
                name="viper_exists",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "viper_wrapper", "libviper_wrapper.so"),
                kind=PathKind.FILE,
            ),
            # - YCSB: Built via `mvn -pl ... package` (setup.sh lines 257-261).
            #   Produces the primary executable at bin/ycsb.
            self.path_check(
                name="ycsb_bindings_exist",
                path=self.workspace_path("osdi25", "capybaraKV", "evaluation", "YCSB", "bin", "ycsb"),
                kind=PathKind.FILE,
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