"""Bundle-native artifact-evaluation runtime package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .constants import default_timeout_ms

if TYPE_CHECKING:
    from .models import PromptProfile, RuntimeMode


def cli_main() -> None:
    from .main import cli_main as _cli_main

    _cli_main()


def main(
    input_file: str | Path,
    model_name: str | None = None,
    save_path: str | Path | None = None,
    interactive: bool = False,
    prompt_profile: str | None = None,
    prompt_append: str | None = None,
) -> int:
    from .main import main as _main

    return _main(
        input_file=input_file,
        model_name=model_name,
        save_path=save_path,
        interactive=interactive,
        prompt_profile=prompt_profile,
        prompt_append=prompt_append,
    )


def build_system_prompt(
    task: str,
    *,
    runtime_mode: "RuntimeMode",
    workspace_path: str = ".",
    timeout_ms: int | None = None,
    interactive: bool = False,
    prompt_profile: "PromptProfile | None" = None,
    prompt_append: str | None = None,
) -> str:
    from .domain.models import PromptContext, PromptProfile
    from .prompting import build_prompt_bundle

    resolved_timeout_ms = timeout_ms if timeout_ms is not None else default_timeout_ms()
    bundle = build_prompt_bundle(
        PromptContext(
            task_text=task,
            workspace_path=workspace_path,
            runtime_mode=runtime_mode,
            timeout_ms=resolved_timeout_ms,
            interactive=interactive,
            prompt_profile=prompt_profile or PromptProfile.ARTIFACT_EVAL_V1,
            prompt_append=prompt_append,
        )
    )
    return bundle.system_prompt


__all__ = ["cli_main", "main", "build_system_prompt"]
