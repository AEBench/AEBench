"""Agent driver implementations."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from ..config import AppContext as Config
from ..domain.models import AgentRequest, AgentResult, RuntimeMode
from ..settings import AgentKind as AgentType


class Agent(Protocol):
    name: str

    def prepare(self, session, listener=None) -> None: ...
    def execute(self, request: AgentRequest, session, listener=None) -> AgentResult: ...
    def cleanup(self, session, listener=None) -> None: ...


class _AgentBase:
    """Shared no-op prepare/cleanup for built-in drivers."""

    def prepare(self, _session, listener=None) -> None:
        pass

    def cleanup(self, _session, listener=None) -> None:
        pass


@dataclass(slots=True)
class MockAgent(_AgentBase):
    name: str = AgentType.MOCK.value

    def execute(self, request: AgentRequest, _session, listener=None) -> AgentResult:
        return AgentResult(
            model=request.model,
            exit_code=0,
            output="mock agent completed",
            message_count=1,
        )


@dataclass(slots=True)
class CliAgent(_AgentBase):
    argv: list[str]
    env: dict[str, str]
    name: str = AgentType.CLI.value

    def execute(self, request: AgentRequest, session, listener=None) -> AgentResult:
        if not self.argv:
            raise RuntimeError("cli driver requires a non-empty cli_argv")

        runtime_summary_path = os.path.join(session.runtime_workspace, session.summary_path.name)

        run_env = dict(self.env)
        run_env["AEBENCH_SYSTEM_PROMPT"] = request.system_prompt
        run_env["AEBENCH_INITIAL_PROMPT"] = request.initial_prompt
        run_env["AEBENCH_WORKSPACE"] = session.runtime_workspace
        run_env["AEBENCH_SUMMARY_PATH"] = runtime_summary_path
        if session.runtime_refs:
            run_env["AEBENCH_REFS"] = session.runtime_refs

        try:
            result = session.runtime_backend.run_process(
                self.argv,
                cwd=session.runtime_workspace,
                env=run_env,
                stdin_text=request.initial_prompt,
                timeout=(request.timeout_ms / 1000.0) if request.timeout_ms else None,
            )
        except subprocess.TimeoutExpired as exc:
            parts = []
            if exc.stdout:
                text = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", errors="replace")
                parts.append(text.strip())
            if exc.stderr:
                text = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")
                parts.append(text.strip())
            output = "\n".join(p for p in parts if p)
            return AgentResult(
                model=request.model,
                exit_code=1,
                output=output or f"agent timed out after {exc.timeout}s",
                message_count=0,
            )
        output = "\n".join(
            part for part in [result.stdout.strip(), result.stderr.strip()] if part
        ).strip()
        return AgentResult(
            model=request.model,
            exit_code=result.returncode,
            output=output,
            message_count=0,
        )

@dataclass(slots=True)
class PythonAgent(_AgentBase):
    target: str
    name: str = AgentType.PYTHON.value

    def execute(self, request: AgentRequest, session, listener=None) -> AgentResult:
        if session.run_spec.runtime.mode == RuntimeMode.DOCKER:
            raise RuntimeError(
                "python driver is not supported with runtime.mode='docker'; "
                "it executes on the host. Use a CLI/MCP-style driver instead."
            )

        if ":" not in self.target:
            raise ValueError(f"python target must be module:attribute, got {self.target!r}")

        module_name, attr_name = self.target.split(":", 1)
        obj = getattr(importlib.import_module(module_name), attr_name)

        kwargs = {
            "prompt": request.initial_prompt,
            "system_prompt": request.system_prompt,
            "initial_prompt": request.initial_prompt,
            "cwd": session.host_workspace,
            "env": {},
            "timeout": (request.timeout_ms / 1000.0) if request.timeout_ms else None,
            "workspace_path": str(session.host_workspace),
            "refs_path": str(session.host_refs) if session.host_refs else None,
            "session": session,
            "request": request,
        }

        result = _call_python_target(obj, kwargs)

        if isinstance(result, AgentResult):
            return result
        if isinstance(result, str):
            return AgentResult(model=request.model, exit_code=0, output=result, message_count=0)
        if isinstance(result, dict):
            return AgentResult.model_validate(result)
        raise TypeError(f"python driver target returned unsupported value: {type(result)!r}")


@dataclass(slots=True)
class RemoteAgent(_AgentBase):
    base_url: str
    auth: str | None = None
    headers: dict[str, str] | None = None
    name: str = AgentType.REMOTE.value

    def execute(self, request: AgentRequest, session, listener=None) -> AgentResult:
        if session.run_spec.runtime.mode == RuntimeMode.DOCKER:
            raise RuntimeError(
                "remote driver is not supported with runtime.mode='docker' unless the remote "
                "service is explicitly container-aware. Use a CLI/MCP-style driver instead."
            )

        body = {
            "model": request.model,
            "system_prompt": request.system_prompt,
            "initial_prompt": request.initial_prompt,
            "workspace_path": session.runtime_workspace,
            "refs_path": session.runtime_refs,
            "timeout_ms": request.timeout_ms,
            "interactive": request.interactive,
        }
        headers = {"Content-Type": "application/json"}
        if self.headers:
            headers.update(self.headers)
        if self.auth:
            headers["Authorization"] = self.auth

        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        timeout_s = ((request.timeout_ms / 1000.0) + 5.0) if request.timeout_ms else None
        if timeout_s is not None:
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                raw = response.read().decode("utf-8")
        else:
            with urllib.request.urlopen(req) as response:
                raw = response.read().decode("utf-8")

        return AgentResult.model_validate_json(raw)


def get_agent(settings: Config) -> Agent:
    agent = settings.agent
    agent_type = agent.agent_type

    if agent_type == AgentType.MOCK:
        return MockAgent()

    if agent_type == AgentType.CLI:
        return CliAgent(argv=list(agent.cli_argv), env=dict(agent.cli_env))

    if agent_type == AgentType.PYTHON:
        if not agent.python_target:
            raise RuntimeError("python driver requires settings.agent.python_target")
        return PythonAgent(target=agent.python_target)

    if agent_type == AgentType.REMOTE:
        if not agent.remote_base_url:
            raise RuntimeError("remote driver requires settings.agent.remote_base_url")
        return RemoteAgent(
            base_url=agent.remote_base_url,
            auth=agent.remote_auth,
            headers=dict(agent.remote_headers),
        )

    if agent_type == AgentType.MCP_CLIENT:
        return CliAgent(
            argv=list(agent.cli_argv),
            env=dict(agent.cli_env),
            name=AgentType.MCP_CLIENT.value,
        )

    if agent_type == AgentType.CLAUDE_SDK:
        if agent.python_target:
            return PythonAgent(target=agent.python_target, name=AgentType.CLAUDE_SDK.value)
        if agent.cli_argv:
            return CliAgent(
                argv=list(agent.cli_argv),
                env=dict(agent.cli_env),
                name=AgentType.CLAUDE_SDK.value,
            )
        raise RuntimeError(
            "claude_sdk driver requires settings.agent.python_target or settings.agent.cli_argv"
        )

    raise RuntimeError(f"unsupported agent type: {agent_type!r}")


def _call_python_target(obj, kwargs: dict):
    target = obj.run if hasattr(obj, "run") else obj
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        signature = None

    if signature is None:
        legacy_kwargs = {
            "prompt": kwargs["prompt"],
            "cwd": kwargs["cwd"],
            "env": kwargs["env"],
            "timeout": kwargs["timeout"],
        }
        return target(**legacy_kwargs)

    params = signature.parameters.values()
    accepts_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)

    if accepts_var_kwargs:
        accepted_kwargs = kwargs
    else:
        accepted_names = {param.name for param in params}
        accepted_kwargs = {name: value for name, value in kwargs.items() if name in accepted_names}

    return target(**accepted_kwargs)
