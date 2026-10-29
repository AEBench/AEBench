from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .common import TOOL_LOOKUP_ENV, TOOL_LOOKUP_MODES, InvalidModeCheck, ToolLookup, env_mode

_BUILD_MODE_ENV = "AE_RTL_REPAIR_BUILD_MODE"
_BUILD_MODES = ("verify", "command")
_BUILD_TIMEOUT_SECONDS = 1800.0
_IMPORT_TIMEOUT_SECONDS = 30.0
_SMOKE_TIMEOUT_SECONDS = 30.0

# Relative to the repo root, which every venv command uses as its cwd. The
# venv's python is a symlink to the runtime's interpreter, so it must be run
# through the venv path for the venv's site-packages to be used.
_VENV_PYTHON = "venv/bin/python"
_MIN_PYTHON = (3, 10)

_REQUIRED_PACKAGES = ("tomli", "ply", "vcdvcd", "jinja2", "psutil")
_BUILD_ARTIFACTS = (
	"synth/target/release/synth",
	"scripts/osdd/target/release/osdd",
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def _verify_requirements(self, repo_root: Path) -> list[BaseCheck]:
		reqs: list[BaseCheck] = [
			self.path_check(
				name="requirements_txt_exists",
				path=repo_root / "requirements.txt",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="artifact_venv_exists",
				path=repo_root / _VENV_PYTHON,
				kind=PathKind.FILE,
			),
			self.command_check(
				name="venv_python_version",
				cwd=repo_root,
				cmd=(
					_VENV_PYTHON,
					"-c",
					f"import sys; sys.exit(sys.version_info < {_MIN_PYTHON!r})",
				),
				timeout_seconds=_IMPORT_TIMEOUT_SECONDS,
			),
			self.command_check(
				name="verify_rtlrepair_syntax",
				cwd=repo_root,
				cmd=(_VENV_PYTHON, "-m", "py_compile", "rtlrepair.py"),
				timeout_seconds=_IMPORT_TIMEOUT_SECONDS,
			),
		]

		for package in _REQUIRED_PACKAGES:
			reqs.append(
				self.command_check(
					name=f"python_import_{package}",
					cwd=repo_root,
					cmd=(_VENV_PYTHON, "-c", f"import {package}"),
					timeout_seconds=_IMPORT_TIMEOUT_SECONDS,
				)
			)

		# Imports the whole rtlrepair package, not just the packages listed above.
		reqs.append(
			self.command_check(
				name="rtlrepair_cli_help",
				cwd=repo_root,
				cmd=(_VENV_PYTHON, "rtlrepair.py", "--help"),
				timeout_seconds=_SMOKE_TIMEOUT_SECONDS,
			)
		)

		for rel_path in _BUILD_ARTIFACTS:
			safe_name = rel_path.replace("/", "_").replace(".", "_")
			reqs.append(
				self.path_check(
					name=f"built_{safe_name}",
					path=repo_root / rel_path,
					kind=PathKind.FILE,
				)
			)
			reqs.append(
				self.command_check(
					name=f"run_{Path(rel_path).name}_help",
					cwd=repo_root,
					cmd=(rel_path, "--help"),
					timeout_seconds=_SMOKE_TIMEOUT_SECONDS,
				)
			)

		return reqs

	def _command_requirements(self, repo_root: Path, *, tool_lookup_mode: str) -> list[BaseCheck]:
		lookup = ToolLookup(self.executor, workspace_dir=repo_root)
		cargo_build = lookup.command(
			"cargo",
			"build",
			"--release",
			mode=tool_lookup_mode,
			fallback_dirs=lookup.cargo_dirs() if tool_lookup_mode == "relaxed" else (),
		)
		cargo_env = lookup.path_env(cargo_build)

		return [
			self.command_check(
				name="create_venv",
				cwd=repo_root,
				cmd=("python3", "-m", "venv", "venv"),
				timeout_seconds=120.0,
			),
			self.command_check(
				name="pip_install_requirements",
				cwd=repo_root,
				cmd=(_VENV_PYTHON, "-m", "pip", "install", "-r", "requirements.txt"),
				timeout_seconds=_BUILD_TIMEOUT_SECONDS,
			),
			self.command_check(
				name="build_synth_binary",
				cwd=repo_root / "synth",
				cmd=cargo_build,
				env=cargo_env,
				timeout_seconds=_BUILD_TIMEOUT_SECONDS,
			),
			self.command_check(
				name="build_osdd_binary",
				cwd=repo_root / "scripts" / "osdd",
				cmd=cargo_build,
				env=cargo_env,
				timeout_seconds=_BUILD_TIMEOUT_SECONDS,
			),
		]

	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()
		mode = env_mode(_BUILD_MODE_ENV, "verify")
		if mode not in _BUILD_MODES:
			return (
				InvalidModeCheck(
					name="build_mode_valid",
					env_name=_BUILD_MODE_ENV,
					mode=mode,
					expected=_BUILD_MODES,
				),
			)

		if mode == "verify":
			return tuple(self._verify_requirements(repo_root))

		tool_lookup_mode = env_mode(TOOL_LOOKUP_ENV, "relaxed")
		if tool_lookup_mode not in TOOL_LOOKUP_MODES:
			return (
				InvalidModeCheck(
					name="tool_lookup_mode_valid",
					env_name=TOOL_LOOKUP_ENV,
					mode=tool_lookup_mode,
					expected=TOOL_LOOKUP_MODES,
				),
			)

		# Run the builds, then verify what they produced.
		return (
			*self._command_requirements(repo_root, tool_lookup_mode=tool_lookup_mode),
			*self._verify_requirements(repo_root),
		)
