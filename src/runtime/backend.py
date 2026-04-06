"""Docker and local runtime backends."""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .session import RunSession

from models import RuntimeMode, RuntimeInfo
from utils import safe_name

logger = logging.getLogger(__name__)


class BenchRuntime(Protocol):
    def prepare(self, session: RunSession, listener=None) -> None: ...

    def run_process(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]: ...

    def open_shell(
        self,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int: ...

    def collect_artifacts(self, session: RunSession, listener=None) -> None: ...
    def cleanup(self, session: RunSession, listener=None) -> None: ...
    def runtime_result(self, session: RunSession) -> RuntimeInfo: ...


@dataclass(slots=True)
class DockerRuntime:
    image: str | None = None
    gpu: bool = False
    container_id: str | None = None
    container_name: str | None = None
    resolved_image: str | None = None
    last_container_id: str | None = None
    container_removed: bool = False

    def prepare(self, session, listener=None) -> None:
        image = (
            self.image
            or session.run_spec.runtime.image
            or getattr(session.settings, "default_docker_image", None)
        )
        if not image:
            raise RuntimeError("docker runtime requires a runtime image")

        self.resolved_image = image
        self.container_removed = False
        self.last_container_id = None

        safe_task_id = safe_name(session.task_id)
        self.container_name = f"aebench-{safe_task_id}-{uuid.uuid4().hex[:8]}"

        cmd = [
            "docker",
            "run",
            "-d",
            "--init",
            "--name",
            self.container_name,
            "-v",
            f"{session.host_workspace}:/repo",
            "-w",
            "/repo",
        ]
        if session.host_refs is not None:
            cmd.extend(["-v", f"{session.host_refs}:/refs:ro"])
        if self.gpu or bool(getattr(session.run_spec.runtime, "gpu", False)):
            cmd.extend(["--gpus", "all"])
        cmd.extend([image, "sleep", "infinity"])

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"docker run failed: {(result.stderr or result.stdout).strip()}")

        self.container_id = result.stdout.strip()
        logger.info(
            "started docker container %s (%s) for task %s",
            self.container_id,
            self.container_name,
            session.task_id,
        )

    def run_process(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self.container_id is None:
            raise RuntimeError("docker runtime not prepared")

        docker_cmd = ["docker", "exec"]
        if stdin_text is not None:
            docker_cmd.append("-i")
        if cwd:
            docker_cmd.extend(["-w", cwd])
        if env:
            for key, value in env.items():
                docker_cmd.extend(["-e", f"{key}={value}"])
        docker_cmd.append(self.container_id)
        docker_cmd.extend(cmd)

        return subprocess.run(
            docker_cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def open_shell(self, *, cwd: str | None = None, env: dict[str, str] | None = None) -> int:
        if self.container_id is None:
            raise RuntimeError("docker runtime not prepared")

        shell = (
            "bash"
            if subprocess.run(
                ["docker", "exec", self.container_id, "sh", "-lc", "command -v bash >/dev/null 2>&1"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
            else "sh"
        )

        cmd = ["docker", "exec", "-it"]
        if cwd:
            cmd.extend(["-w", cwd])
        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self.container_id, shell])
        return subprocess.call(cmd)

    def collect_artifacts(self, _session, listener=None) -> None:
        pass

    def cleanup(self, _session, listener=None) -> None:
        # Try container_id first, fall back to container_name so that
        # containers from a partially-failed docker-run are still removed.
        target = self.container_id or self.container_name
        if target is None:
            return

        if self.container_id is not None:
            self.last_container_id = self.container_id

        result = subprocess.run(
            ["docker", "rm", "-f", target],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("removed docker container %s", target)
            self.container_id = None
            self.container_name = None
            self.container_removed = True
            return

        self.container_removed = False
        logger.warning(
            "failed to remove docker container %s: %s",
            target,
            (result.stderr or result.stdout).strip(),
        )

    def runtime_result(self, _session) -> RuntimeInfo:
        return RuntimeInfo(
            mode=RuntimeMode.DOCKER,
            image=self.resolved_image or self.image,
            container_id=self.container_id or self.last_container_id,
            saved_image=None,
            container_stopped=self.container_removed or self.container_id is None,
        )


@dataclass(slots=True)
class LocalRuntime:
    workspace: str | None = None

    def prepare(self, session, listener=None) -> None:
        self.workspace = str(session.host_workspace)

    def run_process(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        return subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=cwd or self.workspace,
            env=run_env,
            timeout=timeout,
            check=False,
        )

    def open_shell(self, *, cwd: str | None = None, env: dict[str, str] | None = None) -> int:
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        shell = os.environ.get("SHELL", "bash")
        return subprocess.call([shell], cwd=cwd or self.workspace, env=run_env)

    def collect_artifacts(self, _session, listener=None) -> None:
        pass

    def cleanup(self, _session, listener=None) -> None:
        pass

    def runtime_result(self, _session) -> RuntimeInfo:
        return RuntimeInfo(
            mode=RuntimeMode.LOCAL,
            image=None,
            container_id=None,
            saved_image=None,
            container_stopped=True,
        )


def get_runtime(mode: str | RuntimeMode, **kwargs: Any) -> BenchRuntime:
    resolved = mode if isinstance(mode, RuntimeMode) else RuntimeMode(mode)
    if resolved == RuntimeMode.LOCAL:
        return LocalRuntime(workspace=kwargs.get("workspace"))
    if resolved == RuntimeMode.DOCKER:
        return DockerRuntime(
            image=kwargs.get("image"),
            gpu=bool(kwargs.get("gpu", False)),
        )
    raise ValueError(f"unsupported runtime mode: {resolved!r}")