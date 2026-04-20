"""Artifact build oracle checks."""

from __future__ import annotations

import abc
import dataclasses
import pathlib
import types
from collections.abc import Mapping, Sequence

from ..constants import DEFAULT_ORACLE_BUILD_TIMEOUT
from . import utils


def _summarize_process_output(stdout: str, stderr: str) -> str:
	out = stdout.strip()
	err = stderr.strip()
	if out and err:
		combined = f"stdout:\n{out}\n\nstderr:\n{err}"
	else:
		combined = out or err
	return utils.truncate_text(
		combined,
		utils.DEFAULT_MAX_TRUNCATED_MESSAGE_CHARS,
	)


def _require_directory(
	path: pathlib.Path,
	*,
	label: str,
	executor: utils.RuntimeCheckExecutor | None = None,
) -> str | None:
	if not utils.check_path_exists(path, executor=executor):
		return f"{label} missing: {path}"
	if not utils.check_path_is_dir(path, executor=executor):
		return f"{label} is not a directory: {path}"
	return None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BuildCommandCheck(utils.BaseCheck):
	cwd: pathlib.Path
	cmd: Sequence[str]
	relative_workdir: pathlib.Path | None = None
	timeout_seconds: float = DEFAULT_ORACLE_BUILD_TIMEOUT
	env_overrides: Mapping[str, str] = dataclasses.field(default_factory=dict)
	executor: utils.RuntimeCheckExecutor | None = dataclasses.field(
		default=None,
		repr=False,
		compare=False,
	)

	def __post_init__(self) -> None:
		object.__setattr__(self, "cwd", pathlib.Path(self.cwd))
		if self.relative_workdir is not None:
			object.__setattr__(
				self,
				"relative_workdir",
				pathlib.Path(self.relative_workdir),
			)

		if isinstance(self.cmd, (str, bytes)):
			raise TypeError(
				f"{self.name}: command must be a sequence of argv strings, not a single string/bytes"
			)

		if not self.cmd:
			raise ValueError(f"{self.name}: command must be non-empty")

		bad = [arg for arg in self.cmd if not isinstance(arg, str) or arg == ""]
		if bad:
			raise TypeError(
				f"{self.name}: all command argv entries must be non-empty str; bad entries: {bad!r}"
			)

		if self.timeout_seconds <= 0:
			raise ValueError(f"{self.name}: timeout_seconds must be > 0")

		env_dict: dict[str, str] = {}
		for key, value in dict(self.env_overrides).items():
			if key is None or str(key) == "":
				raise TypeError(
					f"{self.name}: env_overrides contains an empty env var name: {key!r}"
				)
			env_dict[str(key)] = str(value)

		if self.relative_workdir is not None and self.relative_workdir.is_absolute():
			raise ValueError(
				f"{self.name}: relative_workdir must be a relative path, got: {self.relative_workdir}"
			)

		object.__setattr__(self, "cmd", tuple(self.cmd))
		object.__setattr__(self, "env_overrides", types.MappingProxyType(env_dict))

	@staticmethod
	def _is_within_base_dir(*, base: pathlib.Path, target: pathlib.Path) -> bool:
		try:
			base_real = base.resolve(strict=True)
			target_real = target.resolve(strict=True)
			try:
				target_real.relative_to(base_real)
				return True
			except ValueError:
				return False
		except OSError:
			return False

	def check(self) -> utils.CheckResult:
		error = _require_directory(self.cwd, label="working directory", executor=self.executor)
		if error is not None:
			return utils.CheckResult.failure(error, cwd=self.cwd)

		workdir = self.cwd
		if self.relative_workdir is not None:
			workdir = self.cwd / self.relative_workdir
			error = _require_directory(workdir, label="working directory", executor=self.executor)
			if error is not None:
				return utils.CheckResult.failure(error, cwd=workdir)

			if not self._is_within_base_dir(base=self.cwd, target=workdir):
				return utils.CheckResult.failure(
					f"working directory escapes base cwd: base={self.cwd} workdir={workdir}",
					cwd=workdir,
				)

		env = dict(self.env_overrides) if self.env_overrides else None

		try:
			run = utils.run_check_process_capture(
				cmd=self.cmd,
				cwd=workdir,
				env=env,
				timeout_seconds=float(self.timeout_seconds),
				capture_limit_chars=utils.DEFAULT_MAX_CAPTURE_CHARS,
				drain_after_kill=True,
				executor=self.executor,
			)
		except (OSError, RuntimeError) as exc:
			return utils.CheckResult.failure(
				f"failed to run command: {exc}",
				stdout="",
				stderr=str(exc),
				returncode=None,
				timed_out=False,
				cwd=workdir,
			)

		if run.timed_out:
			return utils.CheckResult.failure(
				f"command timed out after {self.timeout_seconds}s",
				stdout=run.stdout,
				stderr=run.stderr,
				returncode=None,
				timed_out=True,
				cwd=workdir,
			)

		if run.returncode != 0:
			detail = _summarize_process_output(run.stdout, run.stderr)
			msg = f"command failed (rc = {run.returncode})"
			if detail:
				msg = f"{msg}: {detail}"
			return utils.CheckResult.failure(
				msg,
				stdout=run.stdout,
				stderr=run.stderr,
				returncode=run.returncode,
				timed_out=False,
				cwd=workdir,
			)

		return utils.CheckResult.success(
			stdout=run.stdout,
			stderr=run.stderr,
			returncode=run.returncode,
			cwd=workdir,
		)


class OracleArtifactBuildBase(utils._OraclePhaseBase):
	"""Base for artifact build oracle phases."""

	phase_label = "ArtifactBuild"

	@abc.abstractmethod
	def requirements(self) -> Sequence[utils.BaseCheck]:
		raise NotImplementedError
