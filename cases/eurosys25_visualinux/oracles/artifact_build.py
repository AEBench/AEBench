from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import CommandCheck, PathCheck, PathKind

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # 1. Check if the visualinux Docker image was built successfully
            CommandCheck(
                name="visualinux_image_exists",
                cwd=self.workspace_path(),
                cmd=("docker", "image", "inspect", "visualinux:1.0"),
                timeout_seconds=30.0,
            ),
            # 2. Check for the compiled kernel binary (vmlinux)
            PathCheck(
                name="vmlinux_exists",
                path=self.workspace_path("kernel", "vmlinux"),
                kind=PathKind.FILE,
            ),
            # 3. Check for the compiled boot image (bzImage)
            PathCheck(
                name="bzImage_exists",
                path=self.workspace_path("kernel", "arch", "x86", "boot", "bzImage"),
                kind=PathKind.FILE,
            ),
        )