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
        target = (path or self.host_workspace).resolve()
        workspace = self.host_workspace.resolve()

        if target == workspace:
            return self.runtime_workspace

        try:
            return _join_runtime_path(self.runtime_workspace, target.relative_to(workspace))
        except ValueError:
            pass

        if self.host_refs is not None and self.runtime_refs is not None:
            refs = self.host_refs.resolve()
            if target == refs:
                return self.runtime_refs
            try:
                return _join_runtime_path(self.runtime_refs, target.relative_to(refs))
            except ValueError:
                pass

        return str(target)


def _join_runtime_path(root: str, relative: Path) -> str:
    rel = relative.as_posix()
    if root == ".":
        return rel
    return f"{root.rstrip('/')}/{rel}"
