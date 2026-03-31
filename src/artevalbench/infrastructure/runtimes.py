from __future__ import annotations

import asyncio
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Callable, Protocol, TypeVar, cast

import anyio
from anyio import to_thread
from structlog.typing import FilteringBoundLogger

from ..application.session import DriverSession
from ..constants import default_docker_image
from ..display import (
    DisplayEvent,
    DisplayKind,
    DisplayPanel,
    activate_display_sink,
    active_progress_source,
    emit_display_event,
)
from ..domain.models import (
    AgentLaunchPlan,
    AgentLaunchResult,
    AgentRequest,
    LaunchRuntime,
    RuntimeMode,
    RuntimeResult,
)
from ..log import activate_infra_capture, get_logger
from ..utils import safe_name
from .bridge import replay_event_file
from .events import EventSink

logger: FilteringBoundLogger = get_logger(__name__)

_PROGRESS_LOG_INTERVAL_SEC = 300
_POLL_INTERVAL_SEC = 0.5
_DockerResult = TypeVar("_DockerResult")
_SIDECAR_IMAGE = "docker:27-dind"
_RUNNER_LOG_PATH = "/tmp/artevalbench_runner.live.log"
_RUNNER_EXIT_PATH = "/tmp/artevalbench_runner.exit"
_RUNNER_STOP_PATH = "/tmp/artevalbench_runner.stop"


class RuntimeBackend(Protocol):
    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None: ...

    async def execute_plan(
        self,
        plan: AgentLaunchPlan,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentLaunchResult: ...

    async def collect_artifacts(
        self, session: DriverSession, sink: EventSink | None = None
    ) -> None: ...

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None: ...

    def runtime_result(self, session: DriverSession) -> RuntimeResult: ...


def build_runtime_backend(runtime_mode: RuntimeMode) -> RuntimeBackend:
    if runtime_mode == RuntimeMode.LOCAL:
        return LocalRuntimeBackend()
    return DockerRuntimeBackend()


class LocalRuntimeBackend:
    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink

    async def execute_plan(
        self,
        plan: AgentLaunchPlan,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentLaunchResult:
        _ = request
        if plan.runtime != LaunchRuntime.HOST:
            raise RuntimeError("local runtime can only execute host launch plans")

        self._materialize_staged_paths(plan)
        env = os.environ.copy()
        env.update(plan.env)
        for command in plan.setup_commands:
            await self._run_command(command, cwd=session.workspace.host_workspace, env=env)

        process = await asyncio.create_subprocess_exec(
            *plan.entry_command,
            cwd=str(session.workspace.host_workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_task = asyncio.create_task(
            self._drain_stream(
                process.stdout,
                chunks=stdout_chunks,
                case_id=session.task_id,
                sink=sink,
                stream_name="stdout",
            )
        )
        stderr_task = asyncio.create_task(
            self._drain_stream(
                process.stderr,
                chunks=stderr_chunks,
                case_id=session.task_id,
                sink=sink,
                stream_name="stderr",
            )
        )
        event_offset = 0
        graceful_stop_sent = False
        force_stop_sent = False
        grace_period_warned = False
        while True:
            if session.run_control is not None:
                if session.run_control.force_stop_requested and not force_stop_sent:
                    await self._force_stop_process(process)
                    force_stop_sent = True
                elif session.run_control.graceful_stop_requested and not graceful_stop_sent:
                    await self._request_process_stop(process)
                    graceful_stop_sent = True
                    if sink is not None:
                        sink.emit(
                            DisplayEvent(
                                case_id=session.task_id,
                                kind=DisplayKind.STATUS.value,
                                panel=DisplayPanel.STATUS.value,
                                text="Graceful stop requested; waiting for local process to exit",
                                is_error=True,
                            )
                        )
                elif (
                    session.run_control.graceful_stop_requested
                    and not session.run_control.force_stop_requested
                    and graceful_stop_sent
                    and session.run_control.grace_period_exceeded()
                    and not grace_period_warned
                ):
                    if sink is not None:
                        sink.emit(
                            DisplayEvent(
                                case_id=session.task_id,
                                kind=DisplayKind.STATUS.value,
                                panel=DisplayPanel.STATUS.value,
                                text="Graceful stop still pending; press Interrupt again to force stop",
                                is_error=True,
                            )
                        )
                    grace_period_warned = True
            try:
                await asyncio.wait_for(process.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
            if plan.event_file:
                event_offset = replay_event_file(
                    Path(plan.event_file),
                    case_id=session.task_id,
                    sink=sink,
                    offset=event_offset,
                )
            if process.returncode is not None:
                break

        await asyncio.gather(stdout_task, stderr_task)
        if plan.event_file:
            replay_event_file(
                Path(plan.event_file),
                case_id=session.task_id,
                sink=sink,
                offset=event_offset,
            )
        return AgentLaunchResult(
            exit_code=process.returncode or 0,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            result_file=plan.result_file,
            event_file=plan.event_file,
            runtime=plan.runtime,
            topology=plan.topology,
        )

    async def collect_artifacts(
        self, session: DriverSession, sink: EventSink | None = None
    ) -> None:
        _ = session
        _ = sink

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink

    def runtime_result(self, session: DriverSession) -> RuntimeResult:
        return RuntimeResult(mode=session.runtime_mode)

    def _materialize_staged_paths(self, plan: AgentLaunchPlan) -> None:
        for staged_path in plan.staged_paths:
            source = Path(staged_path.source)
            target = Path(staged_path.target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source, target)
                continue
            shutil.copy2(source, target)

    async def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"setup command failed with exit code {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace') or stdout.decode('utf-8', errors='replace')}"
            )

    async def _request_process_stop(self, process: asyncio.subprocess.Process) -> None:
        self._signal_process(process, signal.SIGTERM)

    async def _force_stop_process(self, process: asyncio.subprocess.Process) -> None:
        self._signal_process(process, signal.SIGKILL)

    def _signal_process(self, process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        if process.returncode is not None:
            return
        pid = getattr(process, "pid", None)
        if pid is not None and hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(pid), sig)
                return
            except (OSError, ProcessLookupError):
                pass
        try:
            process.send_signal(sig)
        except ProcessLookupError:
            return

    async def _drain_stream(
        self,
        stream: asyncio.StreamReader | None,
        *,
        chunks: list[str],
        case_id: str,
        sink: EventSink | None,
        stream_name: str,
    ) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            chunks.append(text)
            if sink is None:
                continue
            rendered = text.rstrip("\n")
            if not rendered:
                continue
            sink.emit(
                DisplayEvent(
                    case_id=case_id,
                    kind=DisplayKind.RUNNER_OUTPUT.value,
                    panel=DisplayPanel.OUTPUT.value,
                    text=rendered,
                    data={"stream": stream_name},
                )
            )


class _RuntimeProtocol(Protocol):
    _config: object

    async def create_session(self, request: object) -> object: ...

    async def upload(self, request: object) -> object: ...

    async def run_in_session(self, action: object) -> object: ...


class _DeploymentProtocol(Protocol):
    runtime: _RuntimeProtocol

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class _ProcessProtocol(Protocol):
    returncode: int | None
    stdout: object | None
    stderr: object | None

    async def wait(self) -> int: ...


class _ByteReceiveStream(Protocol):
    async def receive(self) -> bytes: ...


class _DockerDeploymentConfigProtocol(Protocol):
    def get_deployment(self) -> _DeploymentProtocol: ...


class _ContainerProtocol(Protocol):
    def commit(
        self, repository: str | None = None, tag: str | None = None, **kwargs: object
    ) -> object: ...

    def exec_run(self, cmd: object, **kwargs: object) -> object: ...

    def get_archive(self, path: str, **kwargs: object) -> tuple[list[bytes], object]: ...

    def remove(self, **kwargs: object) -> None: ...

    def stop(self, **kwargs: object) -> None: ...


@dataclass(frozen=True)
class _SwerexBindings:
    docker_config_ctor: Callable[..., _DockerDeploymentConfigProtocol]
    bash_action_ctor: Callable[..., object]
    create_session_ctor: Callable[[], object]
    upload_ctor: Callable[..., object]


@dataclass(slots=True)
class _DockerRuntimeState:
    container_id: str | None = None
    saved_image: str | None = None
    container_stopped: bool = False


@dataclass(slots=True)
class _DockerSidecarState:
    active: bool = False
    container_name: str | None = None
    network_name: str | None = None
    daemon_volume_name: str | None = None


class _DockerRemovableProtocol(Protocol):
    def remove(self, **kwargs: object) -> None: ...


class _DockerContainersProtocol(Protocol):
    def get(self, name: str) -> _ContainerProtocol: ...

    def run(self, image: str, **kwargs: object) -> object: ...


class _DockerNetworksProtocol(Protocol):
    def create(self, name: str, **kwargs: object) -> object: ...

    def get(self, name: str) -> _DockerRemovableProtocol: ...


class _DockerVolumesProtocol(Protocol):
    def create(self, name: str) -> object: ...

    def get(self, name: str) -> _DockerRemovableProtocol: ...


class _DockerImagesProtocol(Protocol):
    def get(self, name: str) -> object: ...

    def pull(self, repository: str, **kwargs: object) -> object: ...


class _DockerClientProtocol(Protocol):
    containers: _DockerContainersProtocol
    networks: _DockerNetworksProtocol
    volumes: _DockerVolumesProtocol
    images: _DockerImagesProtocol

    def close(self) -> None: ...


class DockerRuntimeBackend:
    def __init__(self, bindings: _SwerexBindings | None = None) -> None:
        self._bindings: _SwerexBindings = bindings or _load_swerex_bindings()
        self._deployment: _DeploymentProtocol | None = None
        self._runtime: _RuntimeProtocol | None = None
        self._state = _DockerRuntimeState()
        self._sidecar = _DockerSidecarState()

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        if sink is None:
            await self._prepare(session)
            return
        with activate_display_sink(sink), activate_infra_capture():
            await self._prepare(session)

    async def execute_plan(
        self,
        plan: AgentLaunchPlan,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentLaunchResult:
        _ = request
        if sink is None:
            return await self._execute_plan(plan, session, sink)
        with activate_display_sink(sink), activate_infra_capture():
            return await self._execute_plan(plan, session, sink)

    async def collect_artifacts(
        self, session: DriverSession, sink: EventSink | None = None
    ) -> None:
        _ = sink
        container_id = self._state.container_id
        if container_id:
            self._state.saved_image = _commit_container(container_id, session.task_id)

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        if sink is None:
            await self._cleanup()
            return
        with activate_display_sink(sink), activate_infra_capture():
            await self._cleanup()

    def runtime_result(self, session: DriverSession) -> RuntimeResult:
        return RuntimeResult(
            mode=session.runtime_mode,
            image=session.run_spec.runtime.image,
            container_id=self._state.container_id,
            saved_image=self._state.saved_image,
            container_stopped=self._state.container_stopped,
        )

    async def _prepare(self, session: DriverSession) -> None:
        image = session.run_spec.runtime.image or default_docker_image()
        docker_args = [
            "--privileged",
            "--cgroupns=host",
            "-e",
            "KIND_EXPERIMENTAL_CONTAINERD_SNAPSHOTTER=native",
        ]
        if session.run_spec.runtime.gpu:
            docker_args.extend(["--gpus", "all"])
        docker_args.extend(self._agent_mount_args(session))
        if self._needs_sidecar(session):
            self._start_sidecar(session)
            docker_args.extend(self._sidecar_agent_args())

        config = self._bindings.docker_config_ctor(
            image=image,
            startup_timeout=1200.0,
            docker_args=docker_args,
        )
        deployment = config.get_deployment()
        self._deployment = deployment
        await deployment.start()
        runtime = deployment.runtime
        self._runtime = runtime

        runtime_config = getattr(runtime, "_config", None)
        if runtime_config is not None and hasattr(runtime_config, "timeout"):
            setattr(runtime_config, "timeout", session.timeout_ms / 1000.0)

        await runtime.create_session(self._bindings.create_session_ctor())
        await self._upload_runner_package()
        if self._sidecar.active:
            await self._run_docker_preflight()
        self._state.container_id = await self._get_container_id()

    async def _execute_plan(
        self,
        plan: AgentLaunchPlan,
        session: DriverSession,
        sink: EventSink | None,
    ) -> AgentLaunchResult:
        await self._upload_plan_staged_paths(plan)
        if plan.runtime == LaunchRuntime.HOST:
            return await self._run_host_plan(session, plan, sink)
        for command in plan.setup_commands:
            await self._run_container_command(
                command,
                env=plan.env,
                cwd=session.workspace.runtime_workspace,
            )
        if session.run_spec.runtime.interactive and _stdin_is_tty():
            return await self._run_interactive_in_container(session, plan)
        return await self._monitor_runner(session, plan, sink)

    async def _cleanup(self) -> None:
        deployment_stopped = False
        if self._deployment is not None:
            try:
                await self._deployment.stop()
                deployment_stopped = True
            except Exception as exc:
                logger.warning("deployment stop failed", error=str(exc))
        if self._state.container_id:
            if deployment_stopped:
                self._state.container_stopped = True
            else:
                self._state.container_stopped = _stop_container(self._state.container_id)
        self._cleanup_sidecar()

    async def _upload_runner_package(self) -> None:
        runtime = self._require_runtime()
        with tempfile.TemporaryDirectory(prefix="artevalbench_pkg_") as tmpdir:
            tmp_root = Path(tmpdir)
            _stage_runner_package_tree(tmp_root, current_file=Path(__file__).resolve())
            await runtime.upload(
                self._bindings.upload_ctor(
                    source_path=str(tmp_root),
                    target_path="/agent_pkg",
                )
            )

    async def _upload_plan_staged_paths(self, plan: AgentLaunchPlan) -> None:
        if not plan.staged_paths:
            return
        runtime = self._require_runtime()
        for staged_path in plan.staged_paths:
            await runtime.upload(
                self._bindings.upload_ctor(
                    source_path=staged_path.source,
                    target_path=staged_path.target,
                )
            )

    async def _run_host_plan(
        self,
        session: DriverSession,
        plan: AgentLaunchPlan,
        sink: EventSink | None,
    ) -> AgentLaunchResult:
        env = os.environ.copy()
        env.update(plan.env)
        if self._state.container_id is not None:
            env.setdefault("ARTEVALBENCH_CONTAINER_ID", self._state.container_id)
        for command in plan.setup_commands:
            process = cast(
                _ProcessProtocol,
                await anyio.open_process(
                    command,
                    cwd=str(session.workspace.host_workspace),
                    env=env,
                ),
            )
            stdout, stderr = await self._collect_process_output(process)
            if process.returncode != 0:
                raise RuntimeError(
                    "host setup command failed: "
                    f"{stderr.decode('utf-8', errors='replace') or stdout.decode('utf-8', errors='replace')}"
                )
        process = cast(
            _ProcessProtocol,
            await anyio.open_process(
                plan.entry_command,
                cwd=str(session.workspace.host_workspace),
                env=env,
            ),
        )
        event_offset = 0
        wait_finished = False

        async def _wait_for_exit() -> None:
            nonlocal wait_finished
            await process.wait()
            wait_finished = True

        async with anyio.create_task_group() as task_group:
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            task_group.start_soon(self._drain_process_stream, process.stdout, stdout_chunks)
            task_group.start_soon(self._drain_process_stream, process.stderr, stderr_chunks)
            task_group.start_soon(_wait_for_exit)
            while not wait_finished:
                await anyio.sleep(0.1)
                if plan.event_file:
                    event_offset = replay_event_file(
                        Path(plan.event_file),
                        case_id=session.task_id,
                        sink=sink,
                        offset=event_offset,
                    )
            stdout = b"".join(stdout_chunks)
            stderr = b"".join(stderr_chunks)
        if plan.event_file:
            replay_event_file(
                Path(plan.event_file),
                case_id=session.task_id,
                sink=sink,
                offset=event_offset,
            )
        return AgentLaunchResult(
            exit_code=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            result_file=plan.result_file,
            event_file=plan.event_file,
            runtime=plan.runtime,
            topology=plan.topology,
        )

    async def _collect_process_output(self, process: _ProcessProtocol) -> tuple[bytes, bytes]:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(self._drain_process_stream, process.stdout, stdout_chunks)
            task_group.start_soon(self._drain_process_stream, process.stderr, stderr_chunks)
            await process.wait()
        return b"".join(stdout_chunks), b"".join(stderr_chunks)

    async def _drain_process_stream(self, stream: object | None, chunks: list[bytes]) -> None:
        if stream is None:
            return
        byte_stream = cast(_ByteReceiveStream, stream)
        try:
            while True:
                chunk = await byte_stream.receive()
                if not chunk:
                    return
                chunks.append(chunk)
        except anyio.EndOfStream:
            return

    async def _run_interactive_in_container(
        self,
        session: DriverSession,
        plan: AgentLaunchPlan,
    ) -> AgentLaunchResult:
        exec_args = ["docker", "exec", "-it"]
        exec_args.extend(_docker_exec_env_args(plan.env))
        exec_args.extend(
            [
                self._state.container_id or "",
                "sh",
                "-lc",
                _shell_command(plan.entry_command, session.workspace.runtime_workspace),
            ]
        )
        process = await to_thread.run_sync(_run_interactive_exec, exec_args)
        return AgentLaunchResult(
            exit_code=process.returncode,
            stdout="",
            stderr="",
            result_file=plan.result_file,
            event_file=plan.event_file,
            runtime=plan.runtime,
            topology=plan.topology,
        )

    async def _monitor_runner(
        self,
        session: DriverSession,
        plan: AgentLaunchPlan,
        sink: EventSink | None,
    ) -> AgentLaunchResult:
        pid = await self._start_runner_background(session, plan)
        emit_display_event(
            kind=DisplayKind.LIFECYCLE,
            panel=DisplayPanel.INFRA,
            case_id=session.task_id,
            text=f"Runner started (pid={pid or 'unknown'})",
        )
        start = time.monotonic()
        last_log = ""
        last_event_offset = 0
        last_progress_at = 0.0
        last_progress_signature: str | None = None
        last_progress_emit_at = 0.0
        last_progress_poll_at = 0.0
        graceful_stop_sent = False
        force_stop_sent = False
        grace_period_warned = False

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= session.timeout_ms / 1000.0:
                break
            if session.run_control is not None:
                if session.run_control.force_stop_requested and not force_stop_sent:
                    await self._force_stop_runner(pid)
                    force_stop_sent = True
                elif session.run_control.graceful_stop_requested and not graceful_stop_sent:
                    await self._request_runner_stop(pid)
                    graceful_stop_sent = True
                    emit_display_event(
                        kind=DisplayKind.STATUS,
                        panel=DisplayPanel.STATUS,
                        case_id=session.task_id,
                        text="Graceful stop requested; waiting for runner to exit",
                        is_error=True,
                    )
                elif (
                    session.run_control.graceful_stop_requested
                    and not session.run_control.force_stop_requested
                    and graceful_stop_sent
                    and session.run_control.grace_period_exceeded()
                    and not grace_period_warned
                ):
                    emit_display_event(
                        kind=DisplayKind.STATUS,
                        panel=DisplayPanel.STATUS,
                        case_id=session.task_id,
                        text="Graceful stop still pending; press q or Ctrl-C again to force stop",
                        is_error=True,
                    )
                    grace_period_warned = True

            if plan.event_file:
                last_event_offset = replay_event_file(
                    Path(plan.event_file),
                    case_id=session.task_id,
                    sink=sink,
                    offset=last_event_offset,
                )
            last_log = await self._read_runner_log(elapsed, last_log)
            progress_source = active_progress_source()
            if (
                progress_source is not None
                and progress_source.source_type == "container_file"
                and elapsed - last_progress_poll_at >= 5.0
            ):
                log_path = progress_source.log_path
                if log_path is not None:
                    last_progress_poll_at = elapsed
                    summary = await self._read_container_progress_summary(log_path)
                    if summary is not None:
                        signature = str(summary["signature"])
                        changed = signature != last_progress_signature
                        if changed or (elapsed - last_progress_emit_at) >= 30.0:
                            last_progress_signature = signature
                            last_progress_emit_at = elapsed
                            emit_display_event(
                                kind=DisplayKind.PROGRESS,
                                panel=DisplayPanel.PROGRESS,
                                case_id=session.task_id,
                                text=_format_container_progress_text(
                                    log_path,
                                    summary,
                                    stale_seconds=None if changed else 30,
                                ),
                                command=progress_source.command,
                                data={
                                    "log_path": log_path,
                                    "source_type": progress_source.source_type,
                                    "bytes": summary["bytes"],
                                    "lines": summary["lines"],
                                    "last_modified": summary["last_modified"],
                                },
                            )
            if elapsed - last_progress_at >= _PROGRESS_LOG_INTERVAL_SEC:
                emit_display_event(
                    kind=DisplayKind.STATUS,
                    panel=DisplayPanel.STATUS,
                    case_id=session.task_id,
                    text=f"still running @ {elapsed:.0f}s",
                    data={"elapsed_seconds": int(elapsed)},
                )
                last_progress_at = elapsed
            result = await self._check_runner_exited(plan)
            if result is not None:
                if plan.event_file:
                    replay_event_file(
                        Path(plan.event_file),
                        case_id=session.task_id,
                        sink=sink,
                        offset=last_event_offset,
                    )
                if force_stop_sent:
                    return AgentLaunchResult(
                        exit_code=137,
                        stdout=result.stdout,
                        stderr=result.stderr,
                        result_file=result.result_file,
                        event_file=result.event_file,
                        runtime=result.runtime,
                        topology=result.topology,
                    )
                if graceful_stop_sent:
                    return AgentLaunchResult(
                        exit_code=130,
                        stdout=result.stdout,
                        stderr=result.stderr,
                        result_file=result.result_file,
                        event_file=result.event_file,
                        runtime=result.runtime,
                        topology=result.topology,
                    )
                return result
            await anyio.sleep(_POLL_INTERVAL_SEC)

        await self._handle_runner_timeout(pid)
        raise TimeoutError(f"Runner exceeded timeout {session.timeout_ms}ms")

    async def _request_runner_stop(self, pid: str | None) -> None:
        await self._run_bash(f"touch {_RUNNER_STOP_PATH} 2>/dev/null || true")
        normalized_pid = _normalize_pid(pid)
        if normalized_pid is not None:
            return
        container_id = self._state.container_id
        if container_id is not None:
            await to_thread.run_sync(_stop_container, container_id)

    async def _force_stop_runner(self, pid: str | None) -> None:
        normalized_pid = _normalize_pid(pid)
        if normalized_pid is not None:
            await self._kill_runner_process_group(normalized_pid, signal_name="KILL")
        container_id = self._state.container_id
        if container_id is not None:
            await to_thread.run_sync(_force_stop_container, container_id)

    async def _kill_runner_process_group(self, pid: str, *, signal_name: str) -> None:
        await self._run_bash(
            f"kill -{signal_name} -- -{pid} 2>/dev/null || kill -{signal_name} {pid} 2>/dev/null || true"
        )

    async def _start_runner_background(
        self,
        session: DriverSession,
        plan: AgentLaunchPlan,
    ) -> str | None:
        runner_args = _shell_command(plan.entry_command, session.workspace.runtime_workspace)
        if plan.env:
            export_prefix = " && ".join(
                f"export {key}={shlex.quote(value)}" for key, value in sorted(plan.env.items())
            )
            runner_args = f"{export_prefix} && {runner_args}"
        buffered_runner = (
            f"exec stdbuf -oL -eL sh -lc {shlex.quote(runner_args)} > {_RUNNER_LOG_PATH} 2>&1"
        )
        await self._run_bash(
            f"rm -f {_RUNNER_LOG_PATH} {_RUNNER_EXIT_PATH} {_RUNNER_STOP_PATH} && touch {_RUNNER_LOG_PATH}"
        )
        output = await self._run_bash(
            (
                "("
                f"setsid sh -lc {shlex.quote(buffered_runner)} & "
                "RUNNER_PID=$!; "
                "sleep 1; "
                "echo RUNNER_PID=$RUNNER_PID; "
                'wait "$RUNNER_PID"; '
                f"echo $? > {_RUNNER_EXIT_PATH}"
                ") & sleep 1"
            ),
            timeout=30.0,
        )
        for line in output.splitlines():
            if "RUNNER_PID=" in line:
                pid = line.split("RUNNER_PID=", 1)[1].strip()
                if pid.isdigit():
                    return pid
        return None

    async def _read_runner_log(self, elapsed: float, last_log: str) -> str:
        current = await self._run_bash(
            f'cat {_RUNNER_LOG_PATH} 2>/dev/null || echo ""',
            timeout=30.0,
        )
        if current and current != last_log:
            new_output = (
                current[len(last_log):].strip() if current.startswith(last_log) else current
            )
            if new_output:
                emit_display_event(
                    kind=DisplayKind.RUNNER_OUTPUT,
                    panel=DisplayPanel.INFRA,
                    text=new_output,
                    data={"elapsed_seconds": int(elapsed)},
                )
            return current
        return last_log

    async def _check_runner_exited(self, plan: AgentLaunchPlan) -> AgentLaunchResult | None:
        exit_code_output = await self._run_bash(
            f"if [ -f {_RUNNER_EXIT_PATH} ]; then cat {_RUNNER_EXIT_PATH}; fi"
        )
        value = exit_code_output.strip()
        if not value:
            return None
        try:
            exit_code = int(value)
        except ValueError:
            exit_code = -1
        return AgentLaunchResult(
            exit_code=exit_code,
            stdout="",
            stderr="",
            result_file=plan.result_file,
            event_file=plan.event_file,
            runtime=plan.runtime,
            topology=plan.topology,
        )

    async def _handle_runner_timeout(self, pid: str | None) -> None:
        normalized_pid = _normalize_pid(pid)
        if normalized_pid is not None:
            await self._kill_runner_process_group(normalized_pid, signal_name="TERM")
            await anyio.sleep(2)
            await self._kill_runner_process_group(normalized_pid, signal_name="KILL")
        tail = await self._run_bash(f"tail -n 200 {_RUNNER_LOG_PATH}", timeout=30.0)
        emit_display_event(
            kind=DisplayKind.ERROR,
            panel=DisplayPanel.OUTPUT,
            text=f"Log tail (timeout):\n{tail}",
            is_error=True,
        )

    async def _get_container_id(self) -> str | None:
        cid = (
            await self._run_bash('cat /etc/hostname 2>/dev/null || hostname 2>/dev/null || echo ""')
        ).strip()
        return cid or None

    async def _run_container_command(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        cwd: str,
    ) -> str:
        return await self._run_bash(_shell_command(command, cwd), env=env, timeout=30.0)

    async def _read_container_progress_summary(
        self, log_path: str
    ) -> dict[str, str | int | list[str]] | None:
        escaped = shlex.quote(log_path)
        output = await self._run_bash(
            (
                f"if [ -f {escaped} ]; then "
                f"bytes=$(wc -c < {escaped} 2>/dev/null || echo 0); "
                f"lines=$(wc -l < {escaped} 2>/dev/null || echo 0); "
                f"mtime=$(stat -c %Y {escaped} 2>/dev/null || echo 0); "
                'printf \'__AE_PROGRESS_META__%s|%s|%s\\n\' "$bytes" "$lines" "$mtime"; '
                f"tail -n 20 {escaped} 2>/dev/null || true; "
                "fi"
            ),
            timeout=30.0,
        )
        if not output or "__AE_PROGRESS_META__" not in output:
            return None
        meta_line, *tail_lines = output.splitlines()
        meta = meta_line.replace("__AE_PROGRESS_META__", "", 1)
        bytes_text, lines_text, mtime_text = (meta.split("|", 2) + ["0", "0", "0"])[:3]
        signature = f"{bytes_text}|{lines_text}|{mtime_text}|{chr(10).join(tail_lines)}"
        return {
            "bytes": int(bytes_text) if bytes_text.isdigit() else 0,
            "lines": int(lines_text) if lines_text.isdigit() else 0,
            "last_modified": mtime_text,
            "tail": tail_lines,
            "signature": signature,
        }

    async def _run_bash(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> str:
        runtime = self._require_runtime()
        action = self._bindings.bash_action_ctor(command=command, timeout=timeout)
        if env:
            for key, value in env.items():
                command = f"export {key}={shlex.quote(value)} && {command}"
            action = self._bindings.bash_action_ctor(command=command, timeout=timeout)
        response = await runtime.run_in_session(action)
        return str(getattr(response, "output", "")).strip()

    def _require_runtime(self) -> _RuntimeProtocol:
        if self._runtime is None:
            raise RuntimeError("docker runtime is not initialized")
        return self._runtime

    def _needs_sidecar(self, session: DriverSession) -> bool:
        return session.run_spec.artifact_requirements.docker

    def _agent_mount_args(self, session: DriverSession) -> list[str]:
        args = [
            "--mount",
            f"type=bind,src={session.workspace.host_workspace.resolve()},dst=/repo",
            "--mount",
            f"type=bind,src={session.artifacts.bridge_paths.host_dir.resolve()},dst=/artevalbench-driver",
        ]
        if session.workspace.host_refs is not None:
            args.extend(
                [
                    "--mount",
                    f"type=bind,src={session.workspace.host_refs.resolve()},dst=/refs,readonly",
                ]
            )
        return args

    def _sidecar_agent_args(self) -> list[str]:
        if not self._sidecar.active:
            return []
        assert self._sidecar.network_name is not None
        assert self._sidecar.container_name is not None
        return [
            "--network",
            self._sidecar.network_name,
            "-e",
            f"DOCKER_HOST=tcp://{self._sidecar.container_name}:2375",
            "-e",
            "DOCKER_BUILDKIT=1",
            "-e",
            "COMPOSE_DOCKER_CLI_BUILD=1",
        ]

    def _start_sidecar(self, session: DriverSession) -> None:
        run_token = f"{safe_name(session.task_id)}-{time.time_ns()}"
        network_name = f"artevalbench-dind-net-{run_token}"
        container_name = f"artevalbench-dind-{run_token}"
        daemon_volume_name = f"artevalbench-dind-state-{run_token}"
        with closing(_docker_from_env()) as client:
            _ensure_image_present(client, _SIDECAR_IMAGE)
            client.networks.create(network_name, driver="bridge", check_duplicate=True)
            client.volumes.create(daemon_volume_name)
            client.containers.run(
                _SIDECAR_IMAGE,
                name=container_name,
                detach=True,
                privileged=True,
                environment={"DOCKER_TLS_CERTDIR": ""},
                network=network_name,
                volumes={
                    str(session.workspace.host_workspace.resolve()): {
                        "bind": "/repo",
                        "mode": "rw",
                    },
                    daemon_volume_name: {"bind": "/var/lib/docker", "mode": "rw"},
                },
            )
        self._sidecar = _DockerSidecarState(
            active=True,
            container_name=container_name,
            network_name=network_name,
            daemon_volume_name=daemon_volume_name,
        )

    def _cleanup_sidecar(self) -> None:
        if not self._sidecar.active:
            return
        with closing(_docker_from_env()) as client:
            container_name = self._sidecar.container_name
            network_name = self._sidecar.network_name
            daemon_volume_name = self._sidecar.daemon_volume_name
            if container_name:
                with suppress(Exception):
                    container = client.containers.get(container_name)
                    with suppress(Exception):
                        container.exec_run(
                            ["sh", "-lc", "docker system prune -af --volumes"], timeout=60
                        )
                    with suppress(Exception):
                        container.stop(timeout=10)
                    with suppress(Exception):
                        container.remove(force=True)
            if network_name:
                with suppress(Exception):
                    client.networks.get(network_name).remove()
            if daemon_volume_name:
                with suppress(Exception):
                    client.volumes.get(daemon_volume_name).remove(force=True)
        self._sidecar = _DockerSidecarState()

    async def _run_docker_preflight(self) -> None:
        for command in ("docker version", "docker compose version", "docker info"):
            last_output = ""
            for attempt in range(12):
                output = await self._run_bash(command, timeout=30.0)
                lower = output.lower()
                if output and not any(
                    marker in lower
                    for marker in (
                        "not found",
                        "cannot connect",
                        "is the docker daemon running",
                        "error during connect",
                        "connection refused",
                    )
                ):
                    break
                last_output = output
                if attempt < 11:
                    await anyio.sleep(2)
            else:
                raise RuntimeError(
                    "docker sidecar preflight failed for command "
                    f"{command!r}: {last_output or 'no output'}"
                )


def _load_swerex_bindings() -> _SwerexBindings:
    for package_name in ("swerex", "swe_rex"):
        try:
            mod_docker = __import__(
                f"{package_name}.deployment.docker", fromlist=["DockerDeploymentConfig"]
            )
            mod_runtime = __import__(
                f"{package_name}.runtime.abstract",
                fromlist=["BashAction", "CreateBashSessionRequest", "UploadRequest"],
            )
            return _SwerexBindings(
                docker_config_ctor=cast(
                    Callable[..., _DockerDeploymentConfigProtocol],
                    mod_docker.DockerDeploymentConfig,
                ),
                bash_action_ctor=cast(Callable[..., object], mod_runtime.BashAction),
                create_session_ctor=cast(
                    Callable[[], object], mod_runtime.CreateBashSessionRequest
                ),
                upload_ctor=cast(Callable[..., object], mod_runtime.UploadRequest),
            )
        except ImportError:
            continue
    raise RuntimeError(
        "swe-rex is not available. Install the project dependencies to use Docker mode."
    )


def _stage_runner_package_tree(destination: Path, *, current_file: Path) -> None:
    repo_root = current_file.parents[3]
    source_root = repo_root / "src"
    if (source_root / "artevalbench").is_dir() and (repo_root / "pyproject.toml").is_file():
        shutil.copytree(source_root, destination / "src")
        for name in ("pyproject.toml", "README.md"):
            source = repo_root / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        uv_lock = repo_root / "uv.lock"
        if uv_lock.is_file():
            shutil.copy2(uv_lock, destination / "uv.lock")
        return

    installed_package_root = current_file.parents[1]
    package_dest = destination / "src" / "artevalbench"
    package_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(installed_package_root, package_dest)
    (destination / "pyproject.toml").write_text(_generated_runner_pyproject(), encoding="utf-8")
    (destination / "README.md").write_text(
        "# ArtEvalBench Runtime Package\n\nGenerated from the installed package for Docker bridge execution.\n",
        encoding="utf-8",
    )


def _generated_runner_pyproject() -> str:
    distribution = metadata.distribution("artevalbench")
    version = distribution.version
    requires = distribution.requires or []
    requires_python = (
        distribution.metadata["Requires-Python"]
        if "Requires-Python" in distribution.metadata
        else ">=3.10"
    )
    return (
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "artevalbench"\n'
        f'version = "{version}"\n'
        'description = "Generated ArtEvalBench runtime package for Docker bridge execution"\n'
        'readme = "README.md"\n'
        f'requires-python = "{requires_python}"\n'
        f"dependencies = {json.dumps(requires, indent=2)}\n\n"
        "[project.scripts]\n"
        'artevalbench = "artevalbench.main:cli_main"\n'
        'artevalbench-runner = "artevalbench.runner:docker_main"\n'
        'artevalbench-mcp-server = "artevalbench.mcp.server:stdio_main"\n\n'
        "[tool.setuptools]\n"
        'package-dir = {"" = "src"}\n\n'
        "[tool.setuptools.packages.find]\n"
        'where = ["src"]\n\n'
        "[tool.setuptools.package-data]\n"
        'artevalbench = ["**/*"]\n'
    )


def _stdin_is_tty() -> bool:
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


def _docker_exec_env_args(env: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    return args


def _shell_command(argv: list[str], cwd: str | None) -> str:
    command = " ".join(shlex.quote(arg) for arg in argv)
    if cwd:
        return f"cd {shlex.quote(cwd)} && exec {command}"
    return f"exec {command}"


def _commit_container(container_id: str, task_id: str) -> str | None:
    image_tag = f"artevalbench-{safe_name(task_id, fallback='unknown-task')}:latest"
    repository, tag = image_tag.rsplit(":", 1)
    return _docker_client_call(
        container_id,
        lambda container: _commit_via_sdk(container, repository, tag, image_tag),
        "docker commit failed",
    )


def _stop_container(container_id: str) -> bool:
    result = _docker_client_call(
        container_id,
        lambda container: _stop_via_sdk(container),
        "docker stop failed",
    )
    return bool(result)


def _force_stop_container(container_id: str) -> bool:
    result = _docker_client_call(
        container_id,
        lambda container: _force_stop_via_sdk(container),
        "docker force stop failed",
    )
    return bool(result)


def _run_interactive_exec(exec_args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        exec_args,
        stdin=sys.__stdin__,
        stdout=sys.__stdout__,
        stderr=sys.__stderr__,
        text=True,
    )


def _docker_from_env() -> _DockerClientProtocol:
    try:
        import docker as docker_module
    except ImportError as exc:
        raise RuntimeError(
            "docker SDK is not available. Install the project dependencies to use Docker mode."
        ) from exc
    from_env = cast(Callable[[], _DockerClientProtocol], getattr(docker_module, "from_env"))
    return from_env()


def _docker_client_call(
    container_id: str,
    callback: Callable[[_ContainerProtocol], _DockerResult],
    on_fail_message: str,
) -> _DockerResult | None:
    try:
        from docker.errors import DockerException, NotFound
    except ImportError:
        return None

    docker_errors: tuple[type[BaseException], ...] = (
        cast(type[BaseException], DockerException),
        cast(type[BaseException], NotFound),
        OSError,
    )
    with closing(_docker_from_env()) as client:
        try:
            container = client.containers.get(container_id)
            return callback(container)
        except docker_errors as exc:
            logger.warning(on_fail_message, error=str(exc), container_id=container_id)
            return None


def _ensure_image_present(client: _DockerClientProtocol, image: str) -> None:
    try:
        client.images.get(image)
    except Exception:
        client.images.pull(image)


def _normalize_pid(pid: str | None) -> str | None:
    if pid is None or not pid.isdigit():
        return None
    value = int(pid)
    if value <= 0:
        return None
    return str(value)


def _format_container_progress_text(
    log_path: str,
    summary: dict[str, str | int | list[str]],
    *,
    stale_seconds: int | None,
) -> str:
    last_modified = _format_container_last_modified(str(summary["last_modified"]))
    header = [
        f"progress | path={log_path}",
        " | ".join(
            part
            for part in [
                f"size={summary['bytes']}B",
                f"lines={summary['lines']}",
                f"updated={last_modified}",
                f"no new log lines for {stale_seconds}s" if stale_seconds is not None else None,
            ]
            if part is not None
        ),
        "-" * 72,
    ]
    tail_lines = summary.get("tail", [])
    assert isinstance(tail_lines, list)
    return "\n".join([*header, *[str(line) for line in tail_lines]])


def _extract_via_sdk(container: _ContainerProtocol, path: str, dest_dir: Path) -> bool:
    stream, _stat = container.get_archive(path)
    archive_bytes = b"".join(stream)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as handle:
        _safe_extractall(handle, dest_dir)
    return True


def _format_container_last_modified(value: str) -> str:
    if not value.isdigit():
        return value
    try:
        dt = datetime.fromtimestamp(int(value), timezone.utc)
    except ValueError:
        return value
    now = datetime.now(timezone.utc)
    seconds_ago = max(0, int((now - dt).total_seconds()))
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({_format_container_age(seconds_ago)} ago)"


def _format_container_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {remaining_minutes}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d {remaining_hours}h"


def _resolve_container_extract_root(dest_dir: Path) -> Path:
    entries = list(dest_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest_dir


def _safe_extractall(tar: tarfile.TarFile, dest_dir: Path) -> None:
    resolved_dest = dest_dir.resolve()
    for member in tar.getmembers():
        member_path = (resolved_dest / member.name).resolve()
        if member_path != resolved_dest and not str(member_path).startswith(
            str(resolved_dest) + os.sep
        ):
            raise RuntimeError(f"tar member {member.name!r} would escape destination directory")
    tar.extractall(dest_dir)


def _commit_via_sdk(
    container: _ContainerProtocol, repository: str, tag: str, image_tag: str
) -> str:
    container.commit(repository=repository, tag=tag)
    return image_tag


def _stop_via_sdk(container: _ContainerProtocol) -> bool:
    container.stop(timeout=10)
    return True


def _force_stop_via_sdk(container: _ContainerProtocol) -> bool:
    container.stop(timeout=0)
    return True
