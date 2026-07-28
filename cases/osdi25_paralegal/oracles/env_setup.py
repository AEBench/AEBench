"""Environment and checkout provenance checks for Paralegal."""

from __future__ import annotations

import platform
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleEnvSetupBase
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult

from .common import (
	CODEQL_VERSION,
	DOCKER_IMAGE,
	WRAPPER_COMMIT,
	find_artifact_root,
	load_json_object,
	run_process,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		artifact_root = find_artifact_root(self.workspace_path())
		return (
			Check(name="linux_x86_64_host", fn=self._check_platform),
			self.version_check(
				name="git_version",
				cmd=("git", "--version"),
				min_version=(2, 25, 0),
				timeout_seconds=10.0,
			),
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 8, 0),
				timeout_seconds=10.0,
			),
			Check(
				name="wrapper_and_submodule_revisions",
				fn=lambda: self._check_revisions(artifact_root),
			),
			Check(
				name="source_or_docker_prerequisites",
				fn=lambda: self._check_execution_prerequisites(artifact_root),
			),
		)

	def _check_platform(self) -> CheckResult:
		system = platform.system().lower()
		machine = platform.machine().lower()
		if system != "linux":
			return CheckResult.failure(f"Paralegal reproduction requires Linux, found {system}")
		if machine not in {"x86_64", "amd64"}:
			return CheckResult.failure(
				f"published Paralegal image requires x86_64, found {machine}"
			)
		return CheckResult.success(f"Linux {machine}")

	def _check_revisions(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("Paralegal wrapper checkout was not found in the workspace")
		try:
			reference = load_json_object(self.ref_path("submodules.ref.json"))
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"cannot load submodule reference: {exc}")

		expected_wrapper = reference.get("wrapper_commit")
		raw_submodules = reference.get("submodules")
		if expected_wrapper != WRAPPER_COMMIT or not isinstance(raw_submodules, dict):
			return CheckResult.failure("submodule reference metadata is malformed")

		mismatches: list[str] = []
		wrapper_result = run_process(
			("git", "-C", str(artifact_root), "rev-parse", "HEAD"),
			timeout_seconds=10.0,
		)
		if not wrapper_result.ok:
			mismatches.append(f"wrapper revision unavailable: {wrapper_result.combined}")
		elif wrapper_result.stdout.strip() != expected_wrapper:
			mismatches.append(
				f"wrapper at {wrapper_result.stdout.strip()}, expected {expected_wrapper}"
			)

		for rel_path, expected_commit in sorted(raw_submodules.items()):
			if not isinstance(rel_path, str) or not isinstance(expected_commit, str):
				mismatches.append("submodule reference contains a non-string entry")
				continue
			submodule_root = artifact_root / rel_path
			result = run_process(
				("git", "-C", str(submodule_root), "rev-parse", "HEAD"),
				timeout_seconds=10.0,
			)
			if not result.ok:
				mismatches.append(f"{rel_path}: uninitialized or unreadable")
			elif result.stdout.strip() != expected_commit:
				mismatches.append(
					f"{rel_path}: observed {result.stdout.strip()}, expected {expected_commit}"
				)

		if mismatches:
			return CheckResult.failure("; ".join(mismatches))
		return CheckResult.success(
			f"wrapper and {len(raw_submodules)} recursive submodule revisions match"
		)

	def _check_execution_prerequisites(self, artifact_root: Path | None) -> CheckResult:
		if artifact_root is None:
			return CheckResult.failure("cannot check prerequisites without the wrapper checkout")

		docker_result = run_process(
			("docker", "image", "inspect", DOCKER_IMAGE),
			timeout_seconds=20.0,
		)
		if docker_result.ok:
			probe = run_process(
				(
					"docker",
					"run",
					"--rm",
					"--entrypoint",
					"/bin/sh",
					DOCKER_IMAGE,
					"-lc",
					"rustc --version && "
					"codeql version --format=terse && "
					"python3 -c 'import matplotlib, pandas, six'",
				),
				timeout_seconds=120.0,
			)
			if probe.ok and CODEQL_VERSION in probe.combined:
				return CheckResult.success(
					f"Docker image {DOCKER_IMAGE} has Rust, CodeQL {CODEQL_VERSION}, "
					"and plotting dependencies"
				)
			docker_failure = probe.combined or "container prerequisite probe failed"
		else:
			docker_failure = docker_result.combined or f"{DOCKER_IMAGE} is unavailable"

		source_commands = (
			(
				"stable Rust 1.75",
				("rustup", "run", "1.75", "rustc", "--version"),
				"rustc 1.75.",
			),
			(
				"Paralegal nightly",
				("rustup", "run", "nightly-2023-08-25", "rustc", "--version"),
				"rustc 1.74.0-nightly (58eefc33a 2023-08-24)",
			),
			(
				f"CodeQL {CODEQL_VERSION}",
				("codeql", "version", "--format=terse"),
				CODEQL_VERSION,
			),
			(
				"plotting dependencies",
				("python3", "-c", "import matplotlib, pandas, six"),
				"",
			),
		)
		failures: list[str] = []
		for label, cmd, signature in source_commands:
			result = run_process(cmd, cwd=artifact_root, timeout_seconds=30.0)
			if not result.ok or (signature and signature not in result.combined):
				failures.append(f"{label}: {result.combined or 'check failed'}")

		if failures:
			return CheckResult.failure(
				"neither execution path is ready; Docker: "
				+ docker_failure
				+ "; source: "
				+ "; ".join(failures)
			)
		return CheckResult.success(
			f"source prerequisites include Rust 1.75, nightly-2023-08-25, "
			f"CodeQL {CODEQL_VERSION}, and plotting dependencies"
		)
