from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import CommandCheck

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = self.workspace_path()

        return (
            # 1. Start the Docker container persistently in the background.
            # We use the command `docker run -d --name visualinux_eval --network host -v {repo_root}:/app -w /app visualinux:1.0 sleep infinity` to start a persistent background container
            # that stays alive forever without needing an interactive terminal. This allows us to
            # use `docker exec -d` in subsequent steps to fire off one-shot background processes
            # (like QEMU and the visualizer) without getting stuck waiting for them to finish.
            # We also proactively run `docker rm -f visualinux_eval` to forcibly destroy any lingering
            # container from a previous crashed/aborted run. This prevents the daemon from throwing a
            # fatal `rc=125` ("name is already in use") error when spinning up this new instance.
            # T1: docker run --name visualinux_env --network host --rm -it -v $(pwd):/app -w /app visualinux:1.0 /bin/bash
            CommandCheck(
                name="start_visualinux_eval_container",
                cwd=repo_root,
                cmd=(
                    "bash", "-c",
                    f"docker rm -f visualinux_eval || true; docker run -d --rm --name visualinux_eval --network host -v {repo_root}:/app -w /app visualinux:1.0 sleep infinity"
                ),
                timeout_seconds=60.0,
            ),
            # 2. Install missing pip dependency inside the persistent container
            # T2: pip3 install -r scripts/build/py-requirements.txt && pip3 install requests
            CommandCheck(
                name="install_python_deps",
                cwd=repo_root,
                cmd=("docker", "exec", "visualinux_eval", "bash", "-c", "pip3 install -r scripts/build/py-requirements.txt && pip3 install requests"),
                timeout_seconds=60.0,
            ),
            # 3. Start QEMU in the background.
            # We use `docker exec -d` (detached mode) to fire off the `make gdb-start` command
            # inside the persistent container. This starts the emulator in the background
            # and immediately returns control to AEBench without getting blocked indefinitely.
            # T1: make gdb-start
            CommandCheck(
                name="start_qemu",
                cwd=repo_root,
                cmd=("docker", "exec", "-d", "visualinux_eval", "make", "gdb-start"),
                timeout_seconds=30.0,
            ),
            # 4. Start visualizer in the background.
            # Similarly, we use `docker exec -d` to start the Node.js server in the background
            # so AEBench doesn't get stuck waiting for a web server that never naturally exits.
            # T2: docker exec -it visualinux_env /bin/bash
            # T2: cd visualizer/
            # T2: npm run dev
            CommandCheck(
                name="start_visualizer",
                cwd=repo_root,
                cmd=("docker", "exec", "-d", "-w", "/app/visualizer", "visualinux_eval", "bash", "-c", "source /root/.nvm/nvm.sh && npm run dev"),
                timeout_seconds=30.0,
            ),
            # 5. Verify that QEMU is running
            CommandCheck(
                name="verify_qemu_running",
                cwd=repo_root,
                cmd=("docker", "exec", "visualinux_eval", "pgrep", "-f", "qemu-system"),
                timeout_seconds=30.0,
            ),
        )