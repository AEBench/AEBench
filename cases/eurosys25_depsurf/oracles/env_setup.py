from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.workspace_path()
        bpftool_src_dir = repo_root / "depsurf" / "btf" / "bpftool" / "src"

        return (
            self.version_check(
                name="uv",
                cmd=("uv", "--version"),
                min_version=(0, 6, 11),
                version_regex=r"uv\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="make",
                cmd=("make", "--version"),
                min_version=(4, 3, 0),
                version_regex=r"GNU Make\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="patch",
                cmd=("patch", "--version"),
                min_version=(2, 7, 6),
                version_regex=r"patch\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="pkg_config",
                cmd=("pkg-config", "--version"),
                min_version=(0, 29, 2),
                version_regex=r"([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="clang",
                cmd=("clang", "--version"),
                min_version=(14, 0, 0),
                version_regex=r"clang version\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="llvm",
                cmd=("llvm-config", "--version"),
                min_version=(14, 0, 0),
                version_regex=r"([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="pahole",
                cmd=("pahole", "--version"),
                min_version=(1, 25, 0),
                version_regex=r"v?([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="libelf",
                cmd=("pkg-config", "--modversion", "libelf"),
                min_version=(0, 186, 0),
                version_regex=r"([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="libcap",
                cmd=("pkg-config", "--modversion", "libcap"),
                min_version=(2, 44, 0),
                version_regex=r"([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=10.0,
            ),
            self.path_check(
                name="repo_root_exists",
                path=repo_root,
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="bpftool_src_directory_exists",
                path=bpftool_src_dir,
                kind=PathKind.DIRECTORY,
            ),
        )