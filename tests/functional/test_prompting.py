"""Prompt template generation tests."""

from __future__ import annotations

from models import PromptArgs, PromptProfile, RuntimeMode
from prompting import build_prompt_bundle


def _docker_ctx(**overrides) -> PromptArgs:
	defaults = dict(
		task_text="Run the benchmark.",
		workspace_path="/workspace/repo",
		runtime_mode=RuntimeMode.DOCKER,
		timeout_ms=3_600_000,
		prompt_profile="artifact-eval-v1",
	)
	return PromptArgs(**{**defaults, **overrides})


def _local_ctx(**overrides) -> PromptArgs:
	defaults = dict(
		task_text="Run the benchmark.",
		workspace_path="/home/user/repo",
		runtime_mode=RuntimeMode.LOCAL,
		prompt_profile="artifact-eval-v1",
	)
	return PromptArgs(**{**defaults, **overrides})


def test_docker_context_resolves_to_docker_profile() -> None:
	bundle = build_prompt_bundle(_docker_ctx())
	assert bundle.profile == PromptProfile.ARTIFACT_EVAL_DOCKER_V1


def test_local_context_resolves_to_local_profile() -> None:
	bundle = build_prompt_bundle(_local_ctx())
	assert bundle.profile == PromptProfile.ARTIFACT_EVAL_LOCAL_V1


def test_docker_system_prompt_contains_workspace_path() -> None:
	bundle = build_prompt_bundle(_docker_ctx(workspace_path="/workspace/repo"))
	assert "/workspace/repo" in bundle.system_prompt


def test_local_system_prompt_contains_workspace_path() -> None:
	bundle = build_prompt_bundle(_local_ctx(workspace_path="/home/user/repo"))
	assert "/home/user/repo" in bundle.system_prompt


def test_initial_prompt_contains_workspace_path() -> None:
	bundle = build_prompt_bundle(_docker_ctx(workspace_path="/workspace/repo"))
	assert "/workspace/repo" in bundle.initial_prompt


def test_task_text_appears_in_system_prompt() -> None:
	bundle = build_prompt_bundle(_docker_ctx(task_text="Do the artifact evaluation now."))
	assert "Do the artifact evaluation now." in bundle.system_prompt


def test_task_text_appears_in_local_prompt() -> None:
	bundle = build_prompt_bundle(_local_ctx(task_text="Run the local benchmark."))
	assert "Run the local benchmark." in bundle.system_prompt


def test_docker_prompt_includes_timeout_ms_value() -> None:
	bundle = build_prompt_bundle(_docker_ctx(timeout_ms=7_200_000))
	assert "7200000" in bundle.system_prompt


def test_local_prompt_does_not_include_specific_ms_value() -> None:
	bundle = build_prompt_bundle(_local_ctx())
	assert "TIMEOUT" in bundle.system_prompt
	assert "timeout_ms" not in bundle.system_prompt.lower()
	assert "None ms" not in bundle.system_prompt


def test_prompt_append_included_when_provided() -> None:
	bundle = build_prompt_bundle(_docker_ctx(prompt_append="Extra rule: do not skip steps."))
	assert "Extra rule: do not skip steps." in bundle.system_prompt


def test_prompt_append_section_absent_when_none() -> None:
	bundle = build_prompt_bundle(_docker_ctx(prompt_append=None))
	assert "ADDITIONAL TASK RULES" not in bundle.system_prompt


def test_prompt_append_present_when_set() -> None:
	bundle = build_prompt_bundle(_local_ctx(prompt_append="Custom constraint."))
	assert "ADDITIONAL TASK RULES" in bundle.system_prompt


def test_bundle_has_non_empty_system_and_initial_prompts() -> None:
	bundle = build_prompt_bundle(_docker_ctx())
	assert bundle.system_prompt
	assert bundle.initial_prompt


def test_bundle_system_prompt_contains_docker_guidance() -> None:
	bundle = build_prompt_bundle(_docker_ctx())
	assert "DOCKER" in bundle.system_prompt or "container" in bundle.system_prompt.lower()


def test_bundle_local_system_prompt_contains_host_guidance() -> None:
	bundle = build_prompt_bundle(_local_ctx())
	assert "host" in bundle.system_prompt.lower() or "HOST" in bundle.system_prompt
