from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AppContext as Config
from ..domain.models import PromptBundle, RunSpec as TaskConfig
from ..run_control import RunControl
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
