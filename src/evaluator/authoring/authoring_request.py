"""TOML spec for automated case-authoring runs."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

@dataclass(frozen=True, slots=True)
class AuthoringRequest:

    case_id: str

    # Artifact source
    artifact_git_url: str
    artifact_git_ref: str           # full 40-char SHA
    artifact_readme_path: str       # relative path inside the repo, e.g. "README.md"

    # Runtime config written into the generated case.toml
    runtime_mode: str = "docker"
    timeout_ms: int = 14_400_000
    gpu: bool = False
    interactive: bool = False
    prompt_profile: str = "artifact-eval-v1"

    # Authoring agent configs (forwarded as CLI flags to the agent pipeline)
    agent_max_turns: int = 80
    agent_max_budget_usd: float = 5.0
    agent_effort: str = "medium"
    agent_max_repairs: int = 2      # budget internal to the agent; separate from native_max_repairs

    # Output, logging, and staging dir for adding new artifacts (cases)
    staging_root: str = "~/ae-workspace/aebench/staging"
    native_max_repairs: int = 1     # outer AEBench repair loop; separate from agent_max_repairs

    # Case template configs
    use_evaluator_src: bool = True
    use_cases_as_examples: bool = True
    example_case_ids: tuple[str, ...] = ()


def load_authoring_request(path: Path) -> AuthoringRequest:
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    artifact = data.get("artifact", {})
    run = data.get("run", {})
    agent = data.get("agent", {})
    guidance = data.get("guidance", {})
    output = data.get("output", {})

    return AuthoringRequest(
        case_id=str(data["case_id"]),
        artifact_git_url=str(artifact["git_url"]),
        artifact_git_ref=str(artifact["git_ref"]),
        artifact_readme_path=str(artifact.get("readme_path", "README.md")),
        runtime_mode=str(run.get("runtime_mode", "docker")),
        timeout_ms=int(run.get("timeout_ms", 14_400_000)),
        gpu=bool(run.get("gpu", False)),
        interactive=bool(run.get("interactive", False)),
        prompt_profile=str(run.get("prompt_profile", "artifact-eval-v1")),
        agent_max_turns=int(agent.get("max_turns", 80)),
        agent_max_budget_usd=float(agent.get("max_budget_usd", 5.0)),
        agent_effort=str(agent.get("effort", "medium")),
        agent_max_repairs=int(agent.get("max_repairs", 2)),
        use_evaluator_src=bool(guidance.get("use_evaluator_src", True)),
        use_cases_as_examples=bool(guidance.get("use_cases_as_examples", True)),
        example_case_ids=tuple(str(cid) for cid in guidance.get("example_case_ids", [])),
        staging_root=str(output.get("staging_root", "~/ae-workspace/aebench/staging")),
        native_max_repairs=int(output.get("native_max_repairs", 1)),
    )
