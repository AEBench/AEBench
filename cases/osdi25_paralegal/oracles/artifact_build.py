"""Meaningful build-evidence checks for the source and Docker paths."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult

from .common import CODEQL_VERSION, DOCKER_IMAGE, find_artifact_root, run_process


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		artifact_root = find_artifact_root(self.workspace_path())
		return (
			Check(
				name="working_source_or_docker_build",
				fn=lambda: self._check_build(artifact_root),
			),
		)

	def _check_build(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found")

		docker_result = self._check_docker_build()
		if docker_result.ok:
			return docker_result

		source_result = self._check_source_build(artifact_root)
		if source_result.ok:
			return source_result
		return CheckResult.failure(
			f"neither build path has usable artifacts; Docker: {docker_result.message}; "
			f"source: {source_result.message}"
		)

	def _check_docker_build(self) -> CheckResult:
		inspect = run_process(
			("docker", "image", "inspect", DOCKER_IMAGE),
			timeout_seconds=20.0,
		)
		if not inspect.ok:
			return CheckResult.failure(inspect.combined or f"{DOCKER_IMAGE} is unavailable")
		probe = run_process(
			(
				"docker",
				"run",
				"--rm",
				"--entrypoint",
				"/bin/sh",
				DOCKER_IMAGE,
				"-lc",
				"test -d /home/aec/artifact/paralegal && "
				"test -d /home/aec/artifact/paralegal-bench && "
				"test -d /home/aec/artifact/codeql-experimentation && "
				"rustc --version && "
				"codeql version --format=terse && "
				"python3 -c 'import matplotlib, pandas, six'",
			),
			timeout_seconds=120.0,
		)
		if not probe.ok:
			return CheckResult.failure(probe.combined or "Docker image probe failed")
		if CODEQL_VERSION not in probe.combined:
			return CheckResult.failure(f"Docker image does not report CodeQL {CODEQL_VERSION}")
		return CheckResult.success(f"validated dependency-complete image {DOCKER_IMAGE}")

	def _check_source_build(self, artifact_root: Path) -> CheckResult:
		commands = (
			(
				"cargo-paralegal-flow",
				("cargo-paralegal-flow", "--help"),
				artifact_root,
			),
			(
				"griswold",
				(
					str(artifact_root / "paralegal-bench" / "target" / "release" / "griswold"),
					"--help",
				),
				artifact_root / "paralegal-bench",
			),
			(
				"CodeQL runner",
				(
					str(
						artifact_root
						/ "codeql-experimentation"
						/ "runner"
						/ "target"
						/ "release"
						/ "runner"
					),
					"--help",
				),
				artifact_root / "codeql-experimentation",
			),
			(
				"plotting dependencies",
				("python3", "-c", "import matplotlib, pandas, six"),
				artifact_root,
			),
		)
		failures: list[str] = []
		for label, cmd, cwd in commands:
			result = run_process(cmd, cwd=cwd, timeout_seconds=30.0)
			if not result.ok:
				failures.append(f"{label}: {result.combined or 'not runnable'}")
		if failures:
			return CheckResult.failure("; ".join(failures))
		return CheckResult.success(
			"source build has runnable cargo-paralegal-flow, griswold, "
			"CodeQL runner, and plotting dependencies"
		)
