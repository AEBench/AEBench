from __future__ import annotations
from pathlib import Path
from .domain.models import PromptContext, PromptProfile, RuntimeMode
from .constants import default_timeout_ms
from .prompting import build_prompt_bundle

def build_system_prompt(task: str, *, runtime_mode: RuntimeMode = RuntimeMode.DOCKER, workspace_path: str = ".", timeout_ms: int | None = None, interactive: bool = False, prompt_profile: PromptProfile = PromptProfile.ARTIFACT_EVAL_V1, prompt_append: str | None = None) -> str:
    bundle = build_prompt_bundle(PromptContext(task_text=task, workspace_path=workspace_path, runtime_mode=runtime_mode, timeout_ms=timeout_ms if timeout_ms is not None else default_timeout_ms(), interactive=interactive, prompt_profile=prompt_profile, prompt_append=prompt_append))
    return bundle.system_prompt
