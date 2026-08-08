from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import CommandCheck, MinMatchingEntryCountCheck, PathCheck, PathKind

class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = self.workspace_path()
        return (
            # 1. Automate GDB interactions to run evaluation.vcl and export the ULK figures.
            # We replace the interactive GDB session (`make gdb-attach`) with a single automated
            # `docker exec` command. By using GDB's `--batch` and `-ex` flags, we can script
            # the exact same breakpoints and commands that a human would type, allowing AEBench
            # to run the entire evaluation non-interactively. We also add `--export` to save the results.
            # T3: docker exec -it visualinux_env /bin/bash
            # T3: make gdb-attach
            # T3: (in gdb) b security_task_getsid
            # T3: (in gdb) c
            # T3: (in gdb) vplot -f evaluation.vcl
            CommandCheck(
                name="run_evaluation_vcl",
                cwd=repo_root,
                cmd=(
                    "docker", "exec", "visualinux_eval",
                    "gdb", "kernel/vmlinux", "-ex", "target remote :26001", "-x", "scripts/gdb/config.gdb",
                    "--batch", "-ex", "b security_task_getsid", "-ex", "c", "-ex", "vplot -f evaluation.vcl --perf --export"
                ),
                timeout_seconds=300.0,
            ),
            # 2. Automate GDB interactions to run Dirty Pipe and StackRot CVEs and export the plots.
            # Using the exact same `--batch` scripting method as above, we automate the execution
            # of the specific ViewCL scripts that reproduce the CVE memory states.
            CommandCheck(
                name="run_dirty_pipe_vcl",
                cwd=repo_root,
                cmd=(
                    "docker", "exec", "visualinux_eval",
                    "gdb", "kernel/vmlinux", "-ex", "target remote :26001", "-x", "scripts/gdb/config.gdb",
                    "--batch", "-ex", "vplot -f evaluation/cases/dirty-pipe.vcl --export"
                ),
                timeout_seconds=300.0,
            ),
            CommandCheck(
                name="run_stackrot_vcl",
                cwd=repo_root,
                cmd=(
                    "docker", "exec", "visualinux_eval",
                    "gdb", "kernel/vmlinux", "-ex", "target remote :26001", "-x", "scripts/gdb/config.gdb",
                    "--batch", "-ex", "vplot -f evaluation/cases/stackrot.vcl --export"
                ),
                timeout_seconds=300.0,
            ),
            # 3. Verify that the profiling framework generated the raw perf output
            PathCheck(
                name="visualinux_sync_perf_exists",
                path=repo_root / "tmp" / "visualinux-sync.perf",
                kind=PathKind.FILE,
            ),
            # 4. Verify that the exported JSON plots are written to disk
            MinMatchingEntryCountCheck(
                name="exported_plots_exist",
                directory=repo_root / "out",
                pattern="**/*.json",
                min_count=10,  # We expect multiple plots for the textbook revival
            ),
        )