from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleArtifactBuildBase, CommandCheck, PathCheck, PathKind
from evaluator.oracles.utils import BaseCheck, CheckResult

_BUILD_MODE_ENV = "AE_GOLF_BUILD_MODE"
_DOCKER_IMAGE = "golf"

_EXPECTED_BUILD_ARTIFACTS = (
	"baseline/bin/go",
	"golf/bin/go",
	"tester/golf-tester",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class DockerImageExistsCheck(BaseCheck):
	"""Fail if the Docker image is not found locally."""

	image_name: str

	def check(self) -> CheckResult:
		import subprocess

		result = subprocess.run(
			["docker", "image", "inspect", self.image_name],
			capture_output=True,
			text=True,
			timeout=30,
		)
		if result.returncode != 0:
			return CheckResult.failure(
				f"Docker image {self.image_name!r} not found. Has ./run.sh been executed?"
			)
		return CheckResult.success(message=f"Docker image {self.image_name!r} exists")


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(BaseCheck):
	mode: str

	def check(self) -> CheckResult:
		return CheckResult.failure(
			f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
		)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	@staticmethod
	def _build_mode() -> str:
		raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
		return raw or "verify"

	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.artifact_path()

		mode = self._build_mode()

		if mode == "command":
			return (
				CommandCheck(
					name="run_sh_build",
					cwd=repo_root,
					cmd=("bash", "./run.sh"),
					timeout_seconds=7200,
				),
			)

		if mode == "verify":
			checks: list[BaseCheck] = [
				DockerImageExistsCheck(
					name="docker_image_golf",
					image_name=_DOCKER_IMAGE,
				),
			]
			for rel_path in _EXPECTED_BUILD_ARTIFACTS:
				checks.append(
					PathCheck(
						name=f"built_{Path(rel_path).name}",
						path=repo_root / rel_path,
						kind=PathKind.FILE,
					)
				)
			return tuple(checks)

		return (InvalidBuildModeCheck(name="build_mode_valid", mode=mode),)
