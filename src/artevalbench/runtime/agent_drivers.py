from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast
from urllib import request as urllib_request

import anyio
from anyio import to_thread
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk import query as sdk_query
from claude_agent_sdk.types import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SandboxSettings,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base

from ..application.session import DriverSession
from ..display import DisplayEvent, DisplayKind, DisplayPanel
from ..domain.models import (
    AgentLaunchPlan,
    AgentRequest,
    AgentResult,
    LaunchRuntime,
    LaunchTopology,
)
from ..settings import AgentKind
from ..utils import write_claude_settings
from .bridge import (
    HOST_PATH_ENV,
    REAL_BASH_ENV,
    REAL_DOCKER_ENV,
    REAL_SH_ENV,
    SESSION_FILE_ENV,
    ensure_host_shell_wrappers,
    load_launch_result_payload,
    write_json_file,
)
from .config import ResolvedAgentConfig
from .events import EventSink

_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_WAIT_SEC = 60
_RATE_LIMIT_WAIT_MAX_SEC = 600

REQUEST_FILE_ENV = "ARTEVALBENCH_AGENT_REQUEST_FILE"
RESULT_FILE_ENV = "ARTEVALBENCH_AGENT_RESULT_FILE"
EVENT_FILE_ENV = "ARTEVALBENCH_AGENT_EVENT_FILE"


class AgentDriver(Protocol):
    name: str

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None: ...

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult: ...

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None: ...


def build_agent_driver(
    agent: ResolvedAgentConfig | None = None,
    *,
    kind: str | None = None,
    options: dict[str, object] | None = None,
) -> AgentDriver:
    if agent is not None:
        driver_kind = agent.kind
        driver_options = agent.driver_options()
    else:
        driver_kind = AgentKind(kind or "claude_sdk")
        driver_options = dict(options or {})
    if driver_kind == AgentKind.MOCK:
        return MockAgentDriver()
    if driver_kind == AgentKind.PYTHON:
        return PythonAgentDriver(target=_string_option(driver_options, "python_target"))
    if driver_kind == AgentKind.CLI:
        return CliAgentDriver(
            argv=_list_option(driver_options, "cli_argv"),
            env=_dict_option(driver_options, "cli_env"),
            shim_shells=_bool_option(driver_options, "cli_shim_shells"),
            expose_container_shell=_bool_option(driver_options, "cli_expose_container_shell"),
            expose_host_shell=_bool_option(driver_options, "cli_expose_host_shell"),
        )
    if driver_kind == AgentKind.REMOTE:
        return RemoteAgentDriver(
            base_url=_string_option(driver_options, "remote_base_url"),
            auth=_string_option(driver_options, "remote_auth"),
            protocol=_string_option(driver_options, "remote_protocol") or "http",
            headers=_dict_option(driver_options, "remote_headers"),
        )
    return ClaudeSdkAgentDriver(
        base_url=_string_option(driver_options, "claude_sdk_base_url"),
        max_buffer_size=_int_option(driver_options, "max_buffer_size"),
    )


def _string_option(options: dict[str, object], key: str) -> str | None:
    value = options.get(key)
    return value if isinstance(value, str) else None


def _list_option(options: dict[str, object], key: str) -> list[str]:
    value = options.get(key)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _dict_option(options: dict[str, object], key: str) -> dict[str, str]:
    value = options.get(key)
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
    return {}


def _int_option(options: dict[str, object], key: str) -> int | None:
    value = options.get(key)
    return value if isinstance(value, int) else None


def _bool_option(options: dict[str, object], key: str) -> bool:
    value = options.get(key)
    return value if isinstance(value, bool) else False


class MockAgentDriver:
    name = "mock"

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult:
        message = (
            "Mock agent driver completed successfully. "
            'Use [agent].kind = "mock" only for smoke tests and offline checks.'
        )
        if sink is not None:
            sink.emit(
                DisplayEvent(
                    case_id=session.run_spec.id,
                    kind=DisplayKind.STATUS.value,
                    panel=DisplayPanel.STATUS.value,
                    text=message,
                )
            )
        return AgentResult(
            model=f"mock:{request.model}",
            exit_code=0,
            message_count=1,
            output=message,
        )

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink


class PythonAgentDriver:
    name = "python"

    def __init__(self, *, target: str | None = None) -> None:
        self._target = target
        self._driver: AgentDriver | None = None

    def _load(self) -> AgentDriver:
        if self._driver is not None:
            return self._driver
        target = self._target
        if not target or ":" not in target:
            raise RuntimeError("python driver requires target in the form pkg.module:create_driver")
        module_name, attr_name = target.split(":", 1)
        module = import_module(module_name)
        factory = getattr(module, attr_name)
        driver = factory()
        self._driver = cast(AgentDriver, driver)
        return self._driver

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        await self._load().prepare(session, sink)

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult:
        return await self._load().execute(request, session, sink)

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        await self._load().cleanup(session, sink)


class RemoteAgentDriver:
    name = "remote"

    def __init__(
        self,
        *,
        base_url: str | None,
        auth: str | None = None,
        protocol: str = "http",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url
        self._auth = auth
        self._protocol = protocol
        self._headers = dict(headers or {})

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink
        if not self._base_url:
            raise RuntimeError("remote driver requires [agent.remote].base_url")

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult:
        if sink is not None:
            sink.emit(
                DisplayEvent(
                    case_id=session.run_spec.id,
                    kind=DisplayKind.STATUS.value,
                    panel=DisplayPanel.INFRA.value,
                    text=f"Calling remote agent endpoint: {self._base_url}",
                )
            )
        return await to_thread.run_sync(self._post_request, request)

    def _post_request(self, payload: AgentRequest) -> AgentResult:
        base_url = self._base_url
        assert base_url is not None
        body = json.dumps(payload.model_dump(mode="json")).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._headers}
        if self._auth:
            headers["Authorization"] = self._auth
        req = urllib_request.Request(base_url, data=body, headers=headers, method="POST")
        with urllib_request.urlopen(
            req, timeout=payload.timeout_ms / 1000 if payload.timeout_ms else None
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return AgentResult.model_validate(data)

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink


class CliAgentDriver:
    name = "cli"

    def __init__(
        self,
        *,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
        shim_shells: bool = False,
        expose_container_shell: bool = False,
        expose_host_shell: bool = False,
    ) -> None:
        self._argv = argv or []
        self._env = env or {}
        self._shim_shells = shim_shells
        self._expose_container_shell = expose_container_shell
        self._expose_host_shell = expose_host_shell

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink
        if not self._argv:
            raise RuntimeError("cli driver requires [agent.cli].argv")

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult:
        bridge_paths = session.artifacts.bridge_paths
        write_json_file(bridge_paths.request_host, request.model_dump(mode="json"))
        write_json_file(
            bridge_paths.session_host,
            session.bridge_context().model_dump(mode="json"),
        )

        base_env = {
            **session.settings.agent.subprocess_env(session.timeout_ms),
            **self._env,
        }
        host_path = base_env.get("PATH") or os.environ.get("PATH", "")
        argv = _resolve_entry_command(self._argv, host_path)
        runtime = LaunchRuntime.HOST
        env = {
            **base_env,
            REQUEST_FILE_ENV: str(bridge_paths.request_host),
            SESSION_FILE_ENV: str(bridge_paths.session_host),
            RESULT_FILE_ENV: str(bridge_paths.result_host),
            EVENT_FILE_ENV: str(bridge_paths.event_host),
        }
        if session.runtime_mode.value == "docker":
            needs_shell_bridge = (
                self._shim_shells or self._expose_container_shell or self._expose_host_shell
            )
            if needs_shell_bridge:
                bin_dir = ensure_host_shell_wrappers(
                    bin_dir=bridge_paths.host_dir / "bin",
                    python_executable=sys.executable,
                    shim_shells=self._shim_shells,
                    expose_container_shell=self._expose_container_shell,
                    expose_host_shell=self._expose_host_shell,
                )
                env[HOST_PATH_ENV] = host_path
                if self._shim_shells:
                    env["PATH"] = str(bin_dir)
                else:
                    env["PATH"] = _prepend_path(str(bin_dir), host_path)
                if self._shim_shells or self._expose_host_shell:
                    env[REAL_BASH_ENV] = _require_host_binary("bash", host_path)
                if self._shim_shells:
                    env[REAL_SH_ENV] = _require_host_binary("sh", host_path)
                if self._shim_shells or self._expose_container_shell:
                    env[REAL_DOCKER_ENV] = _require_host_binary("docker", host_path)
        plan = AgentLaunchPlan(
            runtime=runtime,
            topology=LaunchTopology.PLAIN,
            env=env,
            staged_paths=[],
            entry_command=argv,
            result_file=str(bridge_paths.result_host),
            event_file=str(bridge_paths.event_host),
        )
        launch = await session.require_runtime_backend().execute_plan(plan, request, session, sink)
        payload = load_launch_result_payload(launch)
        if payload is not None:
            return payload
        output = (launch.stderr or launch.stdout or "").strip()
        return AgentResult(model=self.name, exit_code=launch.exit_code, output=output)

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink


class ClaudeSdkAgentDriver:
    name = "claude_sdk"

    def __init__(self, *, base_url: str | None = None, max_buffer_size: int | None = None) -> None:
        self._base_url = base_url
        self._max_buffer_size = max_buffer_size

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = sink
        if not session.settings.agent.api_key:
            raise RuntimeError(
                "Missing Anthropic credentials. Checked ANTHROPIC_API_KEY. "
                "Set it before running the agent."
            )
        if session.runtime_mode.value == "local":
            write_claude_settings(session.timeout_ms)

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult:
        if session.runtime_mode.value == "local":
            return await self._execute_local(request, session, sink)
        return await self._execute_bridge(request, session, sink)

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        _ = session
        _ = sink

    async def _execute_local(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None,
    ) -> AgentResult:
        options = ClaudeAgentOptions(
            model=request.model,
            system_prompt=request.system_prompt,
            allowed_tools=["Read", "Write", "Bash"],
            setting_sources=["user"],
            cwd=str(session.workspace.host_workspace),
            add_dirs=_local_add_dirs(session),
            sandbox=_sandbox_settings(request.use_sdk_sandbox),
            max_buffer_size=self._max_buffer_size or session.settings.agent.max_buffer_size,
            can_use_tool=_build_tool_guard(session),
        )
        if sink is not None:
            sink.emit(
                DisplayEvent(
                    case_id=session.run_spec.id,
                    kind=DisplayKind.LIFECYCLE.value,
                    panel=DisplayPanel.INFRA.value,
                    text=f"Starting Claude Agent SDK with model: {request.model}",
                )
            )
        if request.interactive:
            return await self._run_interactive(request, session, options, sink)
        return await self._run_non_interactive(request, session, options, sink)

    async def _execute_bridge(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None,
    ) -> AgentResult:
        bridge_paths = session.artifacts.bridge_paths
        write_json_file(bridge_paths.request_host, request.model_dump(mode="json"))
        write_json_file(
            bridge_paths.session_host,
            session.bridge_context().model_dump(mode="json"),
        )
        env = {
            **session.settings.agent.subprocess_env(session.timeout_ms),
        }
        if session.bridge_context().stop_state_path:
            env["ARTEVALBENCH_RUNNER_STOP_PATH"] = session.bridge_context().stop_state_path or ""
        plan = AgentLaunchPlan(
            runtime=LaunchRuntime.CONTAINER,
            topology=LaunchTopology.PLAIN,
            env=env,
            entry_command=[
                "uvx",
                "--from",
                "/agent_pkg",
                "artevalbench-runner",
                "--driver",
                self.name,
                "--request-file",
                bridge_paths.request_runtime,
                "--session-file",
                bridge_paths.session_runtime,
                "--result-file",
                bridge_paths.result_runtime,
            ],
            result_file=str(bridge_paths.result_host),
            event_file=str(bridge_paths.event_host),
        )
        launch = await session.require_runtime_backend().execute_plan(plan, request, session, sink)
        payload = load_launch_result_payload(launch)
        if payload is not None:
            return payload
        return AgentResult(
            model=self.name,
            exit_code=launch.exit_code,
            output=(launch.stderr or launch.stdout or "").strip(),
        )

    async def _run_interactive(
        self,
        request: AgentRequest,
        session: DriverSession,
        options: ClaudeAgentOptions,
        sink: EventSink | None,
    ) -> AgentResult:
        processor = _MessageProcessor(sink=sink, case_id=session.run_spec.id)
        async with ClaudeSDKClient(options=options) as client:
            await client.query(_single_prompt_chunk(request.initial_prompt))
            message_count = 0
            result_text = ""
            async for message in _receive_messages(client.receive_response()):
                message_count, result_text = processor.process(message, message_count, result_text)
            while True:
                user_input = await to_thread.run_sync(input, "\n>>> ")
                user_input = user_input.strip()
                if not user_input or user_input.lower() in {"quit", "exit", "q"}:
                    break
                await client.query(_single_prompt_chunk(user_input))
                async for message in _receive_messages(client.receive_response()):
                    message_count, result_text = processor.process(
                        message, message_count, result_text
                    )
        return AgentResult(
            model=request.model,
            exit_code=0 if message_count > 0 else 1,
            message_count=message_count,
            output=result_text,
        )

    async def _run_non_interactive(
        self,
        request: AgentRequest,
        session: DriverSession,
        options: ClaudeAgentOptions,
        sink: EventSink | None,
    ) -> AgentResult:
        try:
            async for attempt in AsyncRetrying(
                sleep=anyio.sleep,
                retry=retry_if_exception(_should_retry_exception),
                stop=stop_after_attempt(_RATE_LIMIT_MAX_RETRIES),
                wait=_RetryAfterWait(),
                before_sleep=_log_before_sleep,
                reraise=True,
            ):
                with attempt:
                    return await self._run_non_interactive_once(request, session, options, sink)
        except asyncio.TimeoutError as exc:
            return AgentResult(model=request.model, exit_code=1, output=f"Timeout: {exc}")
        except Exception as exc:
            return AgentResult(model=request.model, exit_code=1, output=f"Error: {exc}")
        return AgentResult(
            model=request.model,
            exit_code=1,
            output="Claude SDK retry loop exited without a result.",
        )

    async def _run_non_interactive_once(
        self,
        request: AgentRequest,
        session: DriverSession,
        options: ClaudeAgentOptions,
        sink: EventSink | None,
    ) -> AgentResult:
        message_count = 0
        result_text = ""
        processor = _MessageProcessor(sink=sink, case_id=session.run_spec.id)
        async for message in _receive_messages(
            sdk_query(prompt=_single_prompt_chunk(request.initial_prompt), options=options)
        ):
            message_count, result_text = processor.process(message, message_count, result_text)
        if sink is not None:
            sink.emit(
                DisplayEvent(
                    case_id=session.run_spec.id,
                    kind=DisplayKind.STATUS.value,
                    panel=DisplayPanel.STATUS.value,
                    text=f"Completed. Total messages: {message_count}",
                )
            )
        return AgentResult(
            model=request.model,
            exit_code=0,
            message_count=message_count,
            output=result_text,
        )


async def _receive_messages(messages: AsyncIterator[object]) -> AsyncIterator[object]:
    async for message in messages:
        yield message


async def _single_prompt_chunk(prompt: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "message": {"role": "user", "content": prompt}}


def _local_add_dirs(session: DriverSession) -> list[str | Path]:
    paths: list[str | Path] = [session.workspace.host_workspace]
    if session.workspace.host_refs is not None:
        paths.append(session.workspace.host_refs)
    return paths


@dataclass(slots=True)
class _MessageProcessor:
    sink: EventSink | None
    case_id: str
    tool_calls: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def process(self, message: object, message_count: int, result_text: str) -> tuple[int, str]:
        message_count += 1
        if message.__class__.__name__ == "SystemMessage":
            return message_count, result_text
        if isinstance(message, AssistantMessage):
            return self._assistant(message, message_count, result_text)
        if isinstance(message, UserMessage):
            return self._user(message, message_count, result_text)
        if isinstance(message, ResultMessage):
            return self._result(message, message_count, result_text)
        return message_count, result_text

    def _assistant(
        self,
        message: AssistantMessage,
        message_count: int,
        result_text: str,
    ) -> tuple[int, str]:
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                self._emit(
                    DisplayKind.ASSISTANT_TEXT,
                    DisplayPanel.AGENT,
                    block.text,
                )
                result_text = block.text
            elif isinstance(block, ToolUseBlock):
                command = _tool_command(block.name, block.input)
                self.tool_calls[block.id] = {"tool_name": block.name, "command": command}
                self._emit(
                    DisplayKind.TOOL_CALL,
                    DisplayPanel.OUTPUT,
                    _tool_call_text(block.name, block.input, command),
                    tool_name=block.name,
                    command=command,
                    data={"input": block.input},
                )
            elif isinstance(block, ToolResultBlock):
                self._emit_tool_result(
                    tool_use_id=block.tool_use_id,
                    payload={"content": block.content, "is_error": bool(block.is_error)},
                    is_error=bool(block.is_error),
                )
        return message_count, result_text

    def _user(
        self,
        message: UserMessage,
        message_count: int,
        result_text: str,
    ) -> tuple[int, str]:
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    self._emit_tool_result(
                        tool_use_id=block.tool_use_id,
                        payload={"content": block.content, "is_error": bool(block.is_error)},
                        is_error=bool(block.is_error),
                    )
        return message_count, result_text

    def _result(
        self,
        message: ResultMessage,
        message_count: int,
        result_text: str,
    ) -> tuple[int, str]:
        text = f"result subtype={message.subtype} turns={message.num_turns} duration_ms={message.duration_ms}"
        if message.result:
            text = f"{text}\n{message.result}"
            result_text = message.result
        self._emit(
            DisplayKind.STATUS if not message.is_error else DisplayKind.ERROR,
            DisplayPanel.STATUS,
            text,
            is_error=message.is_error,
            data={
                "duration_ms": message.duration_ms,
                "duration_api_ms": message.duration_api_ms,
                "session_id": message.session_id,
                "usage": message.usage or {},
                "total_cost_usd": message.total_cost_usd,
            },
        )
        return message_count, result_text

    def _emit_tool_result(
        self,
        *,
        tool_use_id: str | None,
        payload: dict[str, Any],
        is_error: bool,
    ) -> None:
        meta = self.tool_calls.get(tool_use_id or "", {})
        self._emit(
            DisplayKind.TOOL_RESULT,
            DisplayPanel.OUTPUT,
            "",
            tool_name=meta.get("tool_name"),
            command=meta.get("command"),
            is_error=is_error,
            data=payload,
        )

    def _emit(
        self,
        kind: DisplayKind,
        panel: DisplayPanel,
        text: str,
        *,
        tool_name: str | None = None,
        command: str | None = None,
        is_error: bool = False,
        data: dict[str, Any] | None = None,
    ) -> None:
        if self.sink is None:
            return
        self.sink.emit(
            DisplayEvent(
                case_id=self.case_id,
                kind=kind.value,
                panel=panel.value,
                text=text,
                tool_name=tool_name,
                command=command,
                is_error=is_error,
                data=data or {},
            )
        )


class _RetryAfterWait(wait_base):
    def __call__(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        if outcome is None or not outcome.failed:
            return 0.0
        exc = outcome.exception()
        if exc is None:
            return 0.0
        return float(_retry_wait_seconds(exc, retry_state.attempt_number))


async def _log_before_sleep(retry_state: RetryCallState) -> None:
    _ = retry_state


def _should_retry_exception(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "429" in message or "rate limit" in message or "ratelimit" in message


def _retry_wait_seconds(exc: BaseException, attempt: int) -> int:
    match = re.search(r"wait\s+(\d+)\s*seconds", str(exc), re.I)
    if match is not None:
        wait_seconds = int(match.group(1))
        return wait_seconds if wait_seconds < _RATE_LIMIT_WAIT_MAX_SEC else _RATE_LIMIT_WAIT_MAX_SEC
    backoff = _RATE_LIMIT_WAIT_SEC * (2 ** (attempt - 1))
    return backoff if backoff < _RATE_LIMIT_WAIT_MAX_SEC else _RATE_LIMIT_WAIT_MAX_SEC


def _sandbox_settings(enabled: bool) -> SandboxSettings | None:
    if not enabled:
        return None
    return SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True)


def _build_tool_guard(
    session: DriverSession,
) -> Callable[
    [str, dict[str, object], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]:
    cwd = session.workspace.host_workspace
    read_roots = [cwd] + ([session.workspace.host_refs] if session.workspace.host_refs else [])
    write_roots = [cwd]

    async def _guard(
        tool_name: str,
        tool_input: dict[str, object],
        _context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name not in {"Read", "Write"}:
            return PermissionResultAllow()
        candidate_paths = _extract_tool_paths(tool_input, cwd)
        allowed_roots = write_roots if tool_name == "Write" else read_roots
        for candidate in candidate_paths:
            if not _is_under_any_root(
                candidate, [root for root in allowed_roots if root is not None]
            ):
                return PermissionResultDeny(
                    message=f"{tool_name} is restricted to {', '.join(str(root) for root in allowed_roots if root is not None)}",
                    interrupt=True,
                )
        return PermissionResultAllow()

    return _guard


def _extract_tool_paths(tool_input: dict[str, object], cwd: Path) -> list[Path]:
    candidates: list[Path] = []
    for key, value in tool_input.items():
        if key not in {"file_path", "path", "paths"}:
            continue
        if isinstance(value, str):
            candidates.append(_normalize_tool_path(value, cwd))
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    candidates.append(_normalize_tool_path(entry, cwd))
    return candidates


def _normalize_tool_path(raw_path: str, cwd: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (cwd / path).resolve()


def _is_under_any_root(candidate: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _tool_command(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name == "Bash":
        command = tool_input.get("command")
        return command if isinstance(command, str) else None
    if tool_name == "Read":
        path = tool_input.get("file_path") or tool_input.get("path")
        return path if isinstance(path, str) else None
    return None


def _tool_call_text(tool_name: str, tool_input: dict[str, Any], command: str | None) -> str:
    if tool_name == "Bash":
        description = tool_input.get("description")
        if isinstance(description, str) and description.strip():
            return f"Bash: {description}\n{command or ''}".strip()
        return f"Bash: {command or '(no command)'}"
    if tool_name == "Read":
        return f"Read: {command or tool_input}"
    return f"{tool_name}: {tool_input}"


def _resolve_entry_command(argv: list[str], host_path: str) -> list[str]:
    if not argv:
        return []
    entry = argv[0]
    if os.path.sep in entry:
        return list(argv)
    resolved = shutil.which(entry, path=host_path)
    if not resolved:
        raise RuntimeError(f"cli driver could not resolve host executable: {entry}")
    return [resolved, *argv[1:]]


def _require_host_binary(name: str, host_path: str) -> str:
    resolved = shutil.which(name, path=host_path)
    if not resolved:
        raise RuntimeError(f"docker host-bridge requires host binary: {name}")
    return resolved


def _prepend_path(prefix: str, existing: str) -> str:
    if not existing:
        return prefix
    return f"{prefix}{os.pathsep}{existing}"
