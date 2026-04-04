"""Bundle-native artifact-evaluation runtime package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .constants import default_timeout_ms

if TYPE_CHECKING:
	from .models import AgentResult, PromptProfile, RuntimeMode


def cli_main() -> None:
	from .cli import cli_main as _cli_main

	_cli_main()


def main(
 input_file: str | Path,
 model_name: str | None = None,
 save_path: str | Path | None = None,
 interactive: bool = False,
 prompt_profile: str | None = None,
 prompt_append: str | None = None,
) -> int:
	from .cli import main as _main

	return _main(
	 input_file=input_file,
	 model_name=model_name,
	 save_path=save_path,
	 interactive=interactive,
	 prompt_profile=prompt_profile,
	 prompt_append=prompt_append,
	)


async def run_agent(
 model_name: str,
 *,
 system_prompt: str,
 initial_prompt: str,
 interactive: bool = False,
) -> "AgentResult":
	from .runner import run_agent as _run_agent

	return await _run_agent(
	 model_name,
	 system_prompt=system_prompt,
	 initial_prompt=initial_prompt,
	 interactive=interactive,
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
	from .models import PromptProfile
	from .runner import build_system_prompt as _build_system_prompt

	return _build_system_prompt(
	 task,
	 runtime_mode=runtime_mode,
	 workspace_path=workspace_path,
	 timeout_ms=timeout_ms if timeout_ms is not None else default_timeout_ms(),
	 interactive=interactive,
	 prompt_profile=prompt_profile or PromptProfile.ARTIFACT_EVAL_V1,
	 prompt_append=prompt_append,
	)


__all__ = ["cli_main", "main", "run_agent", "build_system_prompt"]
