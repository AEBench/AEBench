"""Base classes and convenience helpers for case oracle phases."""

from __future__ import annotations

import abc
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from models import OracleInput

from ..constants import DEFAULT_ORACLE_CHECK_TIMEOUT, REFS_DIRNAME
from .checks import (
	CommandCheck,
	EnvMatchMode,
	EnvVarCheck,
	PathCheck,
	PathKind,
	TextFileEqualityCheck,
	VersionCheck,
	compute_similarity,
	elementwise_equal,
	elementwise_similarity_scores,
	elementwise_similarity_threshold,
)
from .oracle_checks_runtime import (
	RuntimeCheckExecutor,
	check_path_exists,
	check_path_is_dir,
	check_path_is_file,
	check_read_file_text,
	path_from_user_input,
	run_check_process_capture,
)
from .process import ProcResult
from .reporting import (
	BaseCheck,
	OracleReport,
	build_oracle_report,
	log_oracle_report,
)


class _OraclePhaseBase(abc.ABC):
	"""Defines the common lifecycle for an oracle phase.

	Subclasses provide the checks for a phase through <requirements()>.
	The base class evaluates those checks and gemerates an eval report.
	"""

	phase_label = "OraclePhase"

	def __init__(
		self,
		*,
		context: OracleInput,
		logger: logging.Logger,
	) -> None:
		"""Initializes an oracle phase.

		Args:
			context: Paths and runtime state for the oracle invocation.
			logger: Logger used for check results and diagnostics.
		"""
		self._context = context
		self._logger = logger

	@property
	def context(self) -> OracleInput:
		"""Returns the invocation context for this phase."""
		return self._context

	@property
	def logger(self) -> logging.Logger:
		"""Returns the logger used by this phase."""
		return self._logger

	@abc.abstractmethod
	def requirements(self) -> Sequence[BaseCheck]:
		"""Returns the checks evaluated by this phase."""
		raise NotImplementedError

	def report(self) -> OracleReport:
		"""Evaluates this phase and returns its structured report."""
		return build_oracle_report(
			logger=self._logger,
			requirements=self.requirements,
		)

	def run(self, *, verbose: bool = False) -> bool:
		"""Evaluates and logs this phase.

		Args:
			verbose: Whether to include successful checks in the log.

		Returns:
			True when all required checks pass.
		"""
		report = self.report()
		return log_oracle_report(
			self._logger,
			label=self.phase_label,
			report=report,
			verbose=verbose,
		)


class _CaseOracleBase(_OraclePhaseBase):
	"""Provides normalized case paths and runtime-aware check abstractions."""

	def __init__(
		self,
		*,
		context: OracleInput,
		logger: logging.Logger,
	) -> None:
		"""Initializes shared state for a case oracle phase."""
		super().__init__(context=context, logger=logger)

		# Normalize paths once so derived phases use consistent absolute paths
		# <strict=False> allows paths that do not exist yet (e.g., logs/output files)
		self._case_dir = Path(context.case_dir).expanduser().resolve(strict=False)
		self._artifact_dir = Path(context.artifact_dir).expanduser().resolve(strict=False)
		self._workspace_dir = Path(context.workspace_dir).expanduser().resolve(strict=False)
		self._output_dir = Path(context.output_dir).expanduser().resolve(strict=False)
		self._refs_dir = (self._case_dir / REFS_DIRNAME).expanduser().resolve(strict=False)

		# Checks through the active task runtime, if missing use local I/O
		self._executor: RuntimeCheckExecutor | None = cast(
			RuntimeCheckExecutor | None,
			context.runtime_executor,
		)

	@property
	def executor(self) -> RuntimeCheckExecutor | None:
		"""Returns the executor used for runtime-aware checks."""
		return self._executor

	def case_path(self, *parts: str | Path) -> Path:
		"""Returns a path relative to the case directory."""
		return self._case_dir.joinpath(*parts) if parts else self._case_dir

	def artifact_path(self, *parts: str | Path) -> Path:
		"""Returns a path relative to the artifact directory."""
		return self._artifact_dir.joinpath(*parts) if parts else self._artifact_dir

	def workspace_path(self, *parts: str | Path) -> Path:
		"""Returns a path relative to the task workspace."""
		return self._workspace_dir.joinpath(*parts) if parts else self._workspace_dir

	def output_path(self, *parts: str | Path) -> Path:
		"""Returns a path relative to the oracle output directory."""
		return self._output_dir.joinpath(*parts) if parts else self._output_dir

	def ref_path(self, *parts: str | Path) -> Path:
		"""Returns a path relative to the case reference directory."""
		return self._refs_dir.joinpath(*parts) if parts else self._refs_dir

	def version_check(
		self,
		*,
		name: str,
		cmd: Sequence[str],
		min_version: tuple[int, int, int] | None = None,
		max_version: tuple[int, int, int] | None = None,
		version_regex: str | None = None,
		timeout_seconds: float = DEFAULT_ORACLE_CHECK_TIMEOUT,
		optional: bool = False,
	) -> VersionCheck:
		"""Creates a runtime-aware command version check."""
		return VersionCheck(
			name=name,
			optional=optional,
			cmd=cmd,
			min_version=min_version,
			max_version=max_version,
			version_regex=version_regex,
			timeout_seconds=timeout_seconds,
			executor=self._executor,
		)

	def env_var_check(
		self,
		*,
		name: str,
		env_var: str,
		expected: str,
		match_mode: EnvMatchMode = EnvMatchMode.EXACT,
		optional: bool = False,
	) -> EnvVarCheck:
		"""Creates a runtime-aware environment variable check."""
		return EnvVarCheck(
			name=name,
			optional=optional,
			env_var=env_var,
			expected=expected,
			match_mode=match_mode,
			executor=self._executor,
		)

	def path_check(
		self,
		*,
		name: str,
		path: str | Path,
		kind: PathKind = PathKind.ANY,
		optional: bool = False,
	) -> PathCheck:
		"""Creates a runtime-aware filesystem path check."""
		return PathCheck(
			name=name,
			optional=optional,
			path=path,
			kind=kind,
			executor=self._executor,
		)

	def command_check(
		self,
		*,
		name: str,
		cmd: str | Sequence[str],
		cwd: str | Path | None = None,
		timeout_seconds: float,
		env: Mapping[str, str] | None = None,
		use_shell: bool = False,
		signature: str | None = None,
		optional: bool = False,
	) -> CommandCheck:
		"""Creates a runtime-aware command execution check."""
		return CommandCheck(
			name=name,
			optional=optional,
			cmd=cmd,
			cwd=cwd,
			timeout_seconds=timeout_seconds,
			env={} if env is None else env,
			use_shell=use_shell,
			signature=signature,
			executor=self._executor,
		)

	def text_file_equal(
		self,
		*,
		name: str,
		observed_path: str | Path,
		reference_path: str | Path,
		optional: bool = False,
	) -> TextFileEqualityCheck:
		"""Creates a check that compares observed and reference text files."""
		return TextFileEqualityCheck(
			name=name,
			optional=optional,
			observed_path=observed_path,
			reference_path=reference_path,
			executor=self._executor,
		)

	def read_text(
		self,
		path: str | Path,
		*,
		encoding: str = "utf-8",
	) -> str:
		"""Reads a text file through the configured runtime."""
		return check_read_file_text(
			path_from_user_input(path),
			encoding=encoding,
			executor=self._executor,
		)

	def path_exists(self, path: str | Path) -> bool:
		"""Returns whether a path exists in the configured runtime."""
		return check_path_exists(
			path_from_user_input(path),
			executor=self._executor,
		)

	def is_file(self, path: str | Path) -> bool:
		"""Returns whether a path is a regular file."""
		return check_path_is_file(
			path_from_user_input(path),
			executor=self._executor,
		)

	def is_dir(self, path: str | Path) -> bool:
		"""Returns whether a path is a directory."""
		return check_path_is_dir(
			path_from_user_input(path),
			executor=self._executor,
		)

	def run_command(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: str | Path | None = None,
		env: Mapping[str, str] | None = None,
		timeout_seconds: float,
		use_shell: bool = False,
	) -> ProcResult:
		"""Runs a command through the configured runtime and captures output."""
		return run_check_process_capture(
			cmd=cmd,
			cwd=None if cwd is None else path_from_user_input(cwd),
			env=env,
			timeout_seconds=timeout_seconds,
			use_shell=use_shell,
			executor=self._executor,
		)


class CaseOracleEnvSetupBase(_CaseOracleBase):
	"""Base class for environment setup oracles."""

	phase_label = "EnvironmentSetup"


class CaseOracleArtifactBuildBase(_CaseOracleBase):
	"""Base class for artifact build oracles."""

	phase_label = "ArtifactBuild"


class CaseOracleBenchmarkPrepBase(_CaseOracleBase):
	"""Base class for benchmark preparation oracles."""

	phase_label = "BenchmarkPrep"


class CaseOracleExperimentRunsBase(_CaseOracleBase):
	"""Base class for experiment execution and result validation oracles."""

	phase_label = "ExperimentRuns"

	# Exposes a few common similarlity comparison helpers for raw data series
	similarity = staticmethod(compute_similarity)
	elementwise_equal = staticmethod(elementwise_equal)
	elementwise_similarity_scores = staticmethod(elementwise_similarity_scores)
	elementwise_similarity_threshold = staticmethod(elementwise_similarity_threshold)
