from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.workspace_path()

        parallelism = str(max(os.cpu_count() or 1, 1))

        bpftool_bin = repo_root / "depsurf" / "btf" / "bpftool" / "src" / "bpftool"
        bcc_output_dir = (
            repo_root / "data" / "software" / "bcc" / "libbpf-tools" / ".output"
        )
        tracee_bpf_object = (
            repo_root / "data" / "software" / "tracee" / "dist" / "tracee.bpf.o"
        )

        return (
            self.command_check(
                name="depsurf_uv_version",
                cwd=repo_root,
                cmd=("uv", "--version"),
                timeout_seconds=60.0,
            ),
            self.command_check(
                name="depsurf_uv_import",
                cwd=repo_root,
                cmd=(
                    "uv",
                    "run",
                    "python",
                    "-c",
                    "import depsurf; print('OK depsurf import')",
                ),
                timeout_seconds=60.0,
            ),
            self.command_check(
                name="depsurf_jupyter_lab_version",
                cwd=repo_root,
                cmd=("uv", "run", "jupyter", "lab", "--version"),
                timeout_seconds=60.0,
            ),
            self.command_check(
                name="depsurf_make_bpftool",
                cwd=repo_root,
                cmd=("make", "-C", "depsurf/btf/bpftool/src", "bpftool"),
                timeout_seconds=1800.0,
            ),
            self.path_check(
                name="depsurf_bpftool_binary_exists",
                path=bpftool_bin,
                kind=PathKind.FILE,
            ),
            self.command_check(
                name="depsurf_bpftool_version",
                cwd=repo_root,
                cmd=("./depsurf/btf/bpftool/src/bpftool", "version"),
                timeout_seconds=60.0,
            ),
            self.command_check(
                name="depsurf_build_bcc_libbpf_tools",
                cwd=repo_root,
                cmd=("make", "-C", "data/software/bcc/libbpf-tools", "-j", parallelism),
                timeout_seconds=1800.0,
            ),
            self.path_check(
                name="depsurf_bcc_output_dir_exists",
                path=bcc_output_dir,
                kind=PathKind.DIRECTORY,
            ),
            utils.Check(
                name="depsurf_bcc_objects_exist",
                fn=lambda: self._check_bcc_objects_exist(bcc_output_dir),
            ),
            self.command_check(
                name="depsurf_make_tracee_bpf",
                cwd=repo_root,
                cmd=("make", "-C", "data/software/tracee", "bpf", "-j", parallelism),
                timeout_seconds=1800.0,
            ),
            self.path_check(
                name="depsurf_tracee_bpf_object_exists",
                path=tracee_bpf_object,
                kind=PathKind.FILE,
            ),
        )

    def _check_bcc_objects_exist(self, output_dir: Path) -> utils.CheckResult:
        if not self.is_dir(output_dir):
            return utils.CheckResult.failure(
                f"BCC output directory missing or not a directory: {output_dir}"
            )

        try:
            objects = sorted(output_dir.glob("*.bpf.o"))
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to list BCC output directory {output_dir}: {exc}"
            )

        if not objects:
            return utils.CheckResult.failure(
                f"no .bpf.o files found under {output_dir}"
            )

        return utils.CheckResult.success(
            f"found {len(objects)} BCC BPF objects"
        )