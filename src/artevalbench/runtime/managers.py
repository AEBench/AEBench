from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from ..application.session import DriverSession
from ..domain.models import AgentRequest, AgentResult, AgentLaunchResult, RuntimeMode
from .agent_drivers import AgentDriver, build_agent_driver
from .config import ResolvedAgentConfig, ResolvedSettings
from .events import EventSink
from .runtimes import RuntimeBackend, build_runtime_backend


class BackendLifecycleHook(Protocol):
    """Optional hook invoked around backend prepare/cleanup."""

    def on_prepare(self, session: DriverSession) -> None: ...

    def on_cleanup(self, session: DriverSession) -> None: ...


class DriverLifecycleHook(Protocol):
    """Optional hook invoked around driver execute."""

    def on_execute_start(self, request: AgentRequest, session: DriverSession) -> None: ...

    def on_execute_end(
        self, request: AgentRequest, session: DriverSession, result: AgentResult
    ) -> None: ...


@dataclass
class RuntimeManager:
    """Wraps a RuntimeBackend with optional lifecycle hooks and retry policy."""

    settings: ResolvedSettings
    mode: RuntimeMode
    hooks: list[BackendLifecycleHook] = field(default_factory=list)
    _backend: RuntimeBackend | None = field(default=None, init=False, repr=False)

    @property
    def backend(self) -> RuntimeBackend:
        if self._backend is None:
            self._backend = build_runtime_backend(self.mode)
        return self._backend

    def register_hook(self, hook: BackendLifecycleHook) -> None:
        self.hooks.append(hook)

    async def prepare(self, session: DriverSession, sink: EventSink | None = None) -> None:
        for hook in self.hooks:
            hook.on_prepare(session)
        await self.backend.prepare(session, sink)

    async def execute_plan(
        self,
        plan: object,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentLaunchResult:
        from ..domain.models import AgentLaunchPlan
        assert isinstance(plan, AgentLaunchPlan)
        return await self.backend.execute_plan(plan, request, session, sink)

    async def collect_artifacts(
        self, session: DriverSession, sink: EventSink | None = None
    ) -> None:
        await self.backend.collect_artifacts(session, sink)

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        for hook in self.hooks:
            hook.on_cleanup(session)
        await self.backend.cleanup(session, sink)


@dataclass
class DriverManager:
    """Wraps an AgentDriver with optional lifecycle hooks and logging."""

    settings: ResolvedSettings
    hooks: list[DriverLifecycleHook] = field(default_factory=list)
    _driver: AgentDriver | None = field(default=None, init=False, repr=False)
    _logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("artevalbench.driver"), init=False, repr=False
    )

    def register_hook(self, hook: DriverLifecycleHook) -> None:
        self.hooks.append(hook)

    def get_driver(self, agent: ResolvedAgentConfig | None = None) -> AgentDriver:
        if self._driver is None:
            self._driver = build_agent_driver(agent or self.settings.agent)
        return self._driver

    def reset(self) -> None:
        """Force the next call to get_driver to construct a fresh driver instance."""
        self._driver = None

    async def prepare(
        self,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> None:
        await self.get_driver().prepare(session, sink)

    async def execute(
        self,
        request: AgentRequest,
        session: DriverSession,
        sink: EventSink | None = None,
    ) -> AgentResult:
        driver = self.get_driver()
        for hook in self.hooks:
            hook.on_execute_start(request, session)
        result = await driver.execute(request, session, sink)
        for hook in self.hooks:
            hook.on_execute_end(request, session, result)
        return result

    async def cleanup(self, session: DriverSession, sink: EventSink | None = None) -> None:
        await self.get_driver().cleanup(session, sink)


def build_runtime_manager(settings: ResolvedSettings, mode: RuntimeMode) -> RuntimeManager:
    return RuntimeManager(settings=settings, mode=mode)


def build_driver_manager(settings: ResolvedSettings) -> DriverManager:
    return DriverManager(settings=settings)
