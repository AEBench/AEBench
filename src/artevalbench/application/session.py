from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..domain.models import AgentSessionContext, PromptBundle, PromptProfile, RunSpec, RuntimeMode
from ..runtime.bridge import BridgePaths, bridge_paths_for
from ..runtime.config import ResolvedSettings
from ..reporting.writer import TaskPaths
from ..run_control import RunControl

if TYPE_CHECKING:
	from ..runtime.runtimes import RuntimeBackend


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
	host_workspace: Path
	runtime_workspace: str
	host_refs: Path | None = None
	runtime_refs: str | None = None


@dataclass(frozen=True, slots=True)
class TaskArtifacts:
	output_dir: Path
	task_paths: TaskPaths
	summary_path: Path
	bridge_paths: BridgePaths


@dataclass(slots=True)
class DriverSession:
	run_spec: RunSpec
	workspace: WorkspacePaths
	artifacts: TaskArtifacts
	prompt: PromptBundle
	settings: ResolvedSettings
	run_control: RunControl | None
	runtime_backend: RuntimeBackend | None = None

	@property
	def task_id(self) -> str:
		return self.run_spec.id

	@property
	def runtime_mode(self) -> RuntimeMode:
		return self.run_spec.runtime.mode

	@property
	def timeout_ms(self) -> int:
		return self.run_spec.runtime.timeout_ms

	@property
	def prompt_profile(self) -> PromptProfile:
		return self.run_spec.prompt.profile

	@property
	def runtime_summary_path(self) -> str:
		if self.runtime_mode == RuntimeMode.DOCKER:
			return f"{self.workspace.runtime_workspace}/{self.artifacts.summary_path.name}"
		return str(self.artifacts.summary_path)

	def require_runtime_backend(self) -> RuntimeBackend:
		if self.runtime_backend is None:
			raise RuntimeError("runtime backend was not initialized for this session")
		return self.runtime_backend

	def bridge_context(self) -> AgentSessionContext:
		stop_state_path = None
		if self.runtime_mode == RuntimeMode.DOCKER:
			stop_state_path = "/tmp/artevalbench_runner.stop"
		return AgentSessionContext(
		 task_id=self.run_spec.id,
		 runtime_mode=self.runtime_mode,
		 workspace_path=self.workspace.runtime_workspace,
		 host_workspace_path=str(self.workspace.host_workspace),
		 container_workspace_path=(
		  self.workspace.runtime_workspace
		  if self.runtime_mode == RuntimeMode.DOCKER
		  else None
		 ),
		 refs_path=self.workspace.runtime_refs,
		 output_dir=str(self.artifacts.output_dir),
		 timeout_ms=self.timeout_ms,
		 prompt_profile=self.prompt_profile,
		 preferred_shell="container" if self.runtime_mode == RuntimeMode.DOCKER else "host",
		 host_shell_policy="auxiliary" if self.runtime_mode == RuntimeMode.DOCKER else "primary",
		 stop_state_path=stop_state_path,
		 event_stream_path=(
		  self.artifacts.bridge_paths.event_runtime
		  if self.runtime_mode == RuntimeMode.DOCKER
		  else str(self.artifacts.bridge_paths.event_host)
		 ),
		 summary_path=self.runtime_summary_path,
		)


def build_bridge_paths(
 *,
 output_dir: Path,
 run_spec: RunSpec,
 host_workspace: Path | None = None,
) -> BridgePaths:
	bridge_root = output_dir
	if run_spec.runtime.mode == RuntimeMode.DOCKER:
		if host_workspace is None:
			raise RuntimeError("docker bridge paths require host_workspace")
		bridge_root = host_workspace
	return bridge_paths_for(
	 output_dir=bridge_root,
	 run_id=run_spec.id,
	 runtime_mode=run_spec.runtime.mode.value,
	)
