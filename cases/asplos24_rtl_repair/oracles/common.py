"""Helpers shared by the asplos24_rtl_repair oracle phases."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Sequence
from pathlib import PurePosixPath

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

# "relaxed" (default) also finds tools the agent installed outside the default
# PATH and only exported inline (e.g. `export PATH=/opt/oss-cad-suite/bin:$PATH`
# in each shell command). "strict" only uses PATH, as seen by a login shell,
# plus $OSS_CAD_SUITE and a bundled .oss-cad-suite directory.
TOOL_LOOKUP_ENV = "AE_RTL_REPAIR_TOOL_LOOKUP"
TOOL_LOOKUP_MODES = ("relaxed", "strict")

_OSS_CAD_SUITE_ROOTS = ("/opt", "/usr/local", "/tmp")
# Also one directory down, e.g. <workspace>/.toolchains/oss-cad-suite/bin.
# ".[!.]*" matches hidden directories without matching "..".
_OSS_CAD_SUITE_PATTERNS = (
	"oss-cad-suite*/bin",
	".oss-cad-suite*/bin",
	"*/oss-cad-suite*/bin",
	".[!.]*/oss-cad-suite*/bin",
)
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def env_mode(env_name: str, default: str) -> str:
	"""Reads an oracle mode switch from the evaluator environment."""
	raw = os.environ.get(env_name, default).strip().lower()
	return raw or default


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InvalidModeCheck(BaseCheck):
	"""Reports an unsupported value of an oracle mode switch."""

	env_name: str
	mode: str
	expected: Sequence[str]

	def check(self, _executor: RuntimeCheckExecutor) -> CheckResult:
		choices = " or ".join(repr(choice) for choice in self.expected)
		return CheckResult.failure(f"invalid {self.env_name}={self.mode!r}; expected {choices}")


class ToolLookup:
	"""Finds tools in the oracle runtime, including ones installed off PATH."""

	def __init__(self, executor: RuntimeCheckExecutor, *, workspace_dir: OraclePath) -> None:
		self._executor = executor
		self._workspace_dir = workspace_dir

	def env_dir(self, env_name: str) -> RuntimePath | None:
		"""Returns a directory named by a runtime environment variable."""
		raw = (self._executor.read_env_var(env_name) or "").strip()
		if not raw:
			return None
		if raw.startswith("~"):
			home = (self._executor.read_env_var("HOME") or "").strip()
			if not home:
				return None
			raw = home + raw[1:]
		return RuntimePath(PurePosixPath(raw))

	def _glob_dirs(self, root: OraclePath, patterns: Sequence[str]) -> list[RuntimePath]:
		found: list[RuntimePath] = []
		for pattern in patterns:
			try:
				matches = self._executor.glob(root, pattern)
			except OSError:
				continue
			found.extend(RuntimePath(PurePosixPath(str(match))) for match in sorted(matches))
		return found

	def oss_cad_suite_dirs(self) -> list[RuntimePath]:
		"""Returns OSS CAD Suite bin directories at common install locations."""
		roots: list[OraclePath] = [
			RuntimePath(PurePosixPath(root)) for root in _OSS_CAD_SUITE_ROOTS
		]
		home = self.env_dir("HOME")
		if home is not None:
			roots.append(home)
			roots.append(RuntimePath(home.value / ".local"))
		roots.append(self._workspace_dir)

		dirs: list[RuntimePath] = []
		for root in roots:
			dirs.extend(self._glob_dirs(root, _OSS_CAD_SUITE_PATTERNS))
		return dirs

	def cargo_dirs(self) -> list[RuntimePath]:
		"""Returns the bin directories that rustup installs cargo into."""
		dirs: list[RuntimePath] = []
		cargo_home = self.env_dir("CARGO_HOME")
		if cargo_home is not None:
			dirs.append(RuntimePath(cargo_home.value / "bin"))
		home = self.env_dir("HOME")
		if home is not None:
			dirs.append(RuntimePath(home.value / ".cargo" / "bin"))
		return dirs

	def find(self, tool: str, dirs: Sequence[OraclePath]) -> str | None:
		"""Returns the runtime path of the first ``tool`` found in ``dirs``."""
		for bin_dir in dirs:
			executable: OraclePath = (
				RuntimePath(bin_dir.value / tool)
				if isinstance(bin_dir, RuntimePath)
				else os.path.join(bin_dir, tool)
			)
			if self._executor.path_is_file(executable):
				return str(self._executor.resolve_path(executable))
		return None

	def command(
		self,
		tool: str,
		*args: str,
		mode: str,
		explicit_dirs: Sequence[OraclePath] = (),
		fallback_dirs: Sequence[OraclePath] = (),
	) -> tuple[str, ...]:
		"""Builds a command for ``tool``, preferring explicit dirs, then PATH, then fallbacks."""
		found = self.find(tool, explicit_dirs)
		if found is not None:
			return (found, *args)

		# Prefer whatever is on PATH; probe install locations only if the tool is missing.
		if fallback_dirs and mode == "relaxed" and self._executor.resolve_executable(tool) is None:
			found = self.find(tool, fallback_dirs)
			if found is not None:
				return (found, *args)

		return (tool, *args)

	def path_env(self, cmd: Sequence[str]) -> dict[str, str]:
		"""Puts the directory of an absolute ``cmd[0]`` on PATH, e.g. so cargo finds rustc."""
		executable = PurePosixPath(cmd[0])
		if not executable.is_absolute():
			return {}
		current = self._executor.read_env_var("PATH") or _DEFAULT_PATH
		return {"PATH": f"{executable.parent}{self._executor.path_separator}{current}"}
