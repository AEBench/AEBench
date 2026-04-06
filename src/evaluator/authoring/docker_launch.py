"""Docker container launcher for case authoring."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .authoring_request import AuthoringRequest
from .backends import AuthoringBackend
from .staging import ARTIFACT_SRC, CANDIDATE_CASE, FEEDBACK, REPORTS

logger = logging.getLogger(__name__)

_CONTAINER_ARTIFACT = "/workspace/artifact"
_CONTAINER_GUIDANCE = "/workspace/guidance"
_CONTAINER_CASE_BUNDLE = "/workspace/case_bundle"
_CONTAINER_REPORTS = "/workspace/reports"
_CONTAINER_FEEDBACK = "/workspace/feedback"
_CONTAINER_AGENT = "/workspace/agent"

DEFAULT_WALL_CLOCK_TIMEOUT_S: float = 6 * 3600.0


@dataclass(frozen=True, slots=True)
class ContainerRunResult:

    exit_code: int
    timed_out: bool
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def launch_authoring_container(
    backend: AuthoringBackend,
    request: AuthoringRequest,
    staging_dir: Path,
    *,
    attempt: int = 1,
    aebench_feedback_path: Path | None = None,
    wall_clock_timeout_s: float = DEFAULT_WALL_CLOCK_TIMEOUT_S,
) -> ContainerRunResult:
    """Run authoring agent inside a Docker container, auto-removed on exit."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in the environment.  "
            "The authoring agent requires it to call the Claude API."
        )

    reports = staging_dir / REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    log_path = reports / f"agent_docker_attempt{attempt}.log"

    cmd = _build_docker_cmd(
        backend=backend,
        request=request,
        staging_dir=staging_dir,
        api_key=api_key,
        aebench_feedback_path=aebench_feedback_path,
    )

    logger.info(
        "launching container image=%s attempt=%d log=%s",
        backend.docker_image, attempt, log_path,
    )

    timed_out = False
    exit_code = 1

    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(f"# attempt {attempt}\n# cmd: {' '.join(cmd)}\n\n")
            log_file.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

            try:
                exit_code = proc.wait(timeout=wall_clock_timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait(timeout=30)
                exit_code = 1
                log_file.write(
                    f"\n# TIMED OUT after {wall_clock_timeout_s:.0f}s\n"
                )

    except Exception as exc:
        raise RuntimeError(
            f"Failed to start authoring container: {exc}\n"
            f"Command: {' '.join(cmd)}"
        ) from exc

    if timed_out:
        logger.warning("container timed out after %.0fs", wall_clock_timeout_s)
    elif exit_code != 0:
        logger.warning("container exited with code %d", exit_code)
    else:
        logger.info("container completed successfully")

    return ContainerRunResult(exit_code=exit_code, timed_out=timed_out, log_path=log_path)


def _build_docker_cmd(
    *,
    backend: AuthoringBackend,
    request: AuthoringRequest,
    staging_dir: Path,
    api_key: str,
    aebench_feedback_path: Path | None,
) -> list[str]:
    artifact_src = staging_dir / ARTIFACT_SRC
    guidance_dir = staging_dir / "guidance"
    candidate_dir = staging_dir / CANDIDATE_CASE
    reports_dir = staging_dir / REPORTS
    feedback_dir = staging_dir / FEEDBACK

    cmd: list[str] = [
        "docker", "run",
        "--rm",
        "--init",
        "-v", f"{artifact_src}:{_CONTAINER_ARTIFACT}:ro",
        "-v", f"{guidance_dir}:{_CONTAINER_GUIDANCE}:ro",
        "-v", f"{candidate_dir}:{_CONTAINER_CASE_BUNDLE}",
        "-v", f"{reports_dir}:{_CONTAINER_REPORTS}",
        "-v", f"{feedback_dir}:{_CONTAINER_FEEDBACK}",
        "-v", f"{backend.repo_path}:{_CONTAINER_AGENT}:ro",
        "-e", f"ANTHROPIC_API_KEY={api_key}",
        "-w", _CONTAINER_AGENT,
        backend.docker_image,
    ]

    agent_cmd: list[str] = [
        "uv", "run", "python", "-u", backend.cli_module,
        "pipeline",
        "--artifact-id", request.case_id,
        "--artifact-dir", _CONTAINER_ARTIFACT,
        "--artifact-readme", f"{_CONTAINER_ARTIFACT}/{request.artifact_readme_path}",
        "--case-dir", _CONTAINER_CASE_BUNDLE,
        "--guidance-dir", _CONTAINER_GUIDANCE,
        "--upstream-url", request.artifact_git_url,
        "--upstream-ref", request.artifact_git_ref,
        "--report-path", f"{_CONTAINER_REPORTS}/agent_pipeline_report.json",
        "--max-repairs", str(request.agent_max_repairs),
        "--max-turns", str(request.agent_max_turns),
        "--max-budget-usd", str(request.agent_max_budget_usd),
        "--effort", request.agent_effort,
        "--verbose",
    ]

    if aebench_feedback_path is not None:
        container_feedback = f"{_CONTAINER_FEEDBACK}/{aebench_feedback_path.name}"
        agent_cmd.extend(["--feedback-path", container_feedback])

    cmd.extend(agent_cmd)
    return cmd
