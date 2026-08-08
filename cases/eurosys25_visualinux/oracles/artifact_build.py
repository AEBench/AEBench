from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import CommandCheck

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = self.workspace_path()

        return (
            # 1. Build the Docker container
            # T1: docker build -t visualinux:1.0 .
            CommandCheck(
                name="build_docker_image",
                cwd=repo_root / "scripts" / "build",
                cmd=("docker", "build", "-t", "visualinux:1.0", "."),
                timeout_seconds=1200.0,
            ),
            # 2. Run initenv inside the container. We start an ephemeral container here.
            # We use the command `docker run --rm -v {repo_root}:/app -w /app visualinux:1.0 <command>` for build steps to isolate
            # the build phase. This creates a temporary container that compiles the code, saves
            # the resulting binaries (e.g., vmlinux) back to the host machine via the volume mount,
            # and then instantly destroys itself. A persistent container isn't started until the
            # benchmark_prep phase.
            #
            # We explicitly `rm -rf workload/src/io_uring/liburing` before running initenv-no-vscode.sh.
            # The script ends with `git clone https://.../liburing.git`, which fails with a fatal rc=128
            # if the directory already exists. Since AEBench mounts the host directory, this directory
            # persists across runs. Cleaning it first ensures the command is idempotent.
            # T1: ./scripts/initenv-no-vscode.sh default
            CommandCheck(
                name="run_initenv",
                cwd=repo_root,
                cmd=("docker", "run", "--rm", "-v", f"{repo_root}:/app", "-w", "/app", "visualinux:1.0", "bash", "-c", "rm -rf workload/src/io_uring/liburing && ./scripts/initenv-no-vscode.sh default"),
                timeout_seconds=1200.0,
            ),
            # 3. Trigger kernel build inside container (with missing apt packages chained)
            # We pass `--init` to docker run to ensure a tiny init process (tini) reaps any zombie processes.
            # CRITICAL FIX: We redirect the output of `apt-get` to `/dev/null`. When `apt-get` installs complex
            # packages, it sometimes spawns background configuration daemons. If these daemons inherit the
            # stdout/stderr pipes, `docker run` will hang forever waiting for the pipes to close. Redirecting
            # to `/dev/null` severs this pipe connection, guaranteeing `docker run` exits when `make build` finishes.
            # T1: apt-get update && apt-get install -y clang libbpf-dev && make build
            CommandCheck(
                name="build_kernel",
                cwd=repo_root,
                cmd=(
                    "docker", "run", "--rm", "--init", "-v", f"{repo_root}:/app", "-w", "/app", "visualinux:1.0",
                    "bash", "-c", "export DEBIAN_FRONTEND=noninteractive; (apt-get update && apt-get install -y clang libbpf-dev) >/dev/null 2>&1 && make build"
                ),
                timeout_seconds=3600.0,
            ),
            # 4. Validate visualinux frontend deps are built inside the container
            # T1: (cd visualizer/ && npm install)
            CommandCheck(
                name="build_visualizer_deps",
                cwd=repo_root,
                cmd=("docker", "run", "--rm", "-v", f"{repo_root}:/app", "-w", "/app/visualizer", "visualinux:1.0", "bash", "-c", "source /root/.nvm/nvm.sh && npm install"),
                timeout_seconds=300.0,
            ),
            # 5. Check for compiled binaries using test -f inside the container
            CommandCheck(
                name="vmlinux_exists",
                cwd=repo_root,
                cmd=("docker", "run", "--rm", "-v", f"{repo_root}:/app", "-w", "/app", "visualinux:1.0", "test", "-f", "kernel/vmlinux"),
                timeout_seconds=30.0,
            ),
            CommandCheck(
                name="bzImage_exists",
                cwd=repo_root,
                cmd=("docker", "run", "--rm", "-v", f"{repo_root}:/app", "-w", "/app", "visualinux:1.0", "test", "-f", "kernel/arch/x86/boot/bzImage"),
                timeout_seconds=30.0,
            ),
        )