from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import Config
from models import PromptBundle, TaskConfig
from run_control import RunControl

from .backend import BenchRuntime
from .reporting import TaskPaths


@dataclass(frozen=True, slots=True)
class RunSession:
	run_spec: TaskConfig
	prompt: PromptBundle
	settings: Config
	run_control: RunControl | None

	host_workspace: Path
	runtime_workspace: str
	host_refs: Path | None
	runtime_refs: str | None

	output_dir: Path
	task_paths: TaskPaths
	summary_path: Path

	runtime_backend: BenchRuntime

	@property
	def task_id(self) -> str:
		return self.run_spec.id

	@property
	def timeout_ms(self) -> int:
		return self.run_spec.runtime.timeout_ms

	def translate_host_path(self, path: Path | None) -> str | None:
		host_path = (path or self.host_workspace).resolve()
		workspace_root = self.host_workspace.resolve()

		translated = _translate_path_under_root(
			host_path, root=workspace_root, runtime_root=self.runtime_workspace
		)
		if translated is not None:
			return translated

		if self.host_refs is not None and self.runtime_refs is not None:
			refs_root = self.host_refs.resolve()
			translated = _translate_path_under_root(
				host_path, root=refs_root, runtime_root=self.runtime_refs
			)
			if translated is not None:
				return translated

		return str(host_path)


def _translate_path_under_root(path: Path, *, root: Path, runtime_root: str) -> str | None:
	try:
		relative = path.relative_to(root)
	except ValueError:
		return None
	if not relative.parts:
		return runtime_root
	if runtime_root == ".":
		return relative.as_posix()
	return f"{runtime_root.rstrip('/')}/{relative.as_posix()}"
