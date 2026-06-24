"""Base classes and helpers for evaluation oracles."""

from __future__ import annotations

import abc
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

from constants import DEFAULT_ORACLE_CHECK_TIMEOUT, REFS_DIRNAME
from models import (
	OracleInput,
	OraclePhaseName,
)

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
	CheckPath,
	OracleRuntimeRegistry,
	RuntimeCheckExecutor,
	RuntimePath,
	check_path_exists,
	check_path_is_dir,
	check_path_is_file,
	check_read_file_text,
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

	Subclasses provide checks through ``requirements()``. The base class
	evaluates those checks and generates an evaluation report.
	"""

	phase_label: OraclePhaseName

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

	@property
	def phase_display_label(self) -> str:
		"""Returns a human-readable label for this phase."""
		return self.phase_label.value.replace(
			"_",
			" ",
		).title()

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
			label=self.phase_display_label,
			report=report,
			verbose=verbose,
		)


class _CaseOracleBase(_OraclePhaseBase):
	"""Provides normalized case paths and target-aware check helpers."""

	def __init__(
		self,
		*,
		context: OracleInput,
		logger: logging.Logger,
	) -> None:
		"""Initializes shared state for a case oracle phase.

		Args:
			context: Paths and runtime state for the oracle invocation.
			logger: Logger used for check results and diagnostics.

		Raises:
			RuntimeError: If the runtime registry has not been initialized.
			TypeError: If the phase class does not use ``OraclePhaseName``.
		"""
		super().__init__(
			context=context,
			logger=logger,
		)

		# Normalize host paths once. strict=False permits paths for outputs
		# and build products that do not exist yet
		self._case_dir = Path(context.case_dir).expanduser().resolve(strict=False)
		self._artifact_dir = Path(context.artifact_dir).expanduser().resolve(strict=False)
		self._workspace_dir = Path(context.workspace_dir).expanduser().resolve(strict=False)
		self._output_dir = Path(context.output_dir).expanduser().resolve(strict=False)
		self._refs_dir = (self._case_dir / REFS_DIRNAME).expanduser().resolve(strict=False)

		self._runtime_registry = cast(
			OracleRuntimeRegistry | None,
			context.runtime_registry,
		)
		if self._runtime_registry is None:
			raise RuntimeError("oracle runtime registry is not initialized")

		if not isinstance(self.phase_label, OraclePhaseName):
			raise TypeError(f"{type(self).__name__}.phase_label must be an OraclePhaseName")

		self._default_target_name = context.oracle_phase_targets.target_for_phase(self.phase_label)

	@property
	def default_target_name(self) -> str:
		"""Returns the configured default target for this phase."""
		return self._default_target_name

	def executor_for(
		self,
		target: str | None = None,
	) -> RuntimeCheckExecutor:
		"""Returns the executor for a target or the phase default.

		Args:
			target: Optional named target override. When omitted, the
				configured phase target is used.

		Returns:
			The executor associated with the selected target.
		"""
		target_name = self._default_target_name if target is None else target
		return self._runtime_registry.executor_for(target_name)

	@property
	def executor(self) -> RuntimeCheckExecutor:
		"""Returns the phase-default runtime executor."""
		return self.executor_for()

	def case_path(self, *parts: str | Path) -> Path:
		"""Returns a host path relative to the case directory."""
		return self._case_dir.joinpath(*parts) if parts else self._case_dir

	def artifact_path(self, *parts: str | Path) -> Path:
		"""Returns a host path relative to the artifact directory."""
		return self._artifact_dir.joinpath(*parts) if parts else self._artifact_dir

	def workspace_path(self, *parts: str | Path) -> Path:
		"""Returns a host path relative to the task workspace."""
		return self._workspace_dir.joinpath(*parts) if parts else self._workspace_dir

	def output_path(self, *parts: str | Path) -> Path:
		"""Returns a host path relative to the oracle output directory."""
		return self._output_dir.joinpath(*parts) if parts else self._output_dir

	def ref_path(self, *parts: str | Path) -> Path:
		"""Returns a host path relative to the reference directory."""
		return self._refs_dir.joinpath(*parts) if parts else self._refs_dir

	def runtime_path(
		self,
		*parts: str | PurePosixPath,
	) -> RuntimePath:
		"""Returns a path native to the target executing an operation.

		Relative paths are resolved against the selected target's runtime
		working directory. Absolute paths remain absolute in that target.
		"""
		return RuntimePath.from_parts(*parts)

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
		target: str | None = None,
	) -> VersionCheck:
		"""Creates a target-aware command version check."""
		return VersionCheck(
			name=name,
			optional=optional,
			cmd=cmd,
			min_version=min_version,
			max_version=max_version,
			version_regex=version_regex,
			timeout_seconds=timeout_seconds,
			executor=self.executor_for(target),
		)

	def env_var_check(
		self,
		*,
		name: str,
		env_var: str,
		expected: str,
		match_mode: EnvMatchMode = EnvMatchMode.EXACT,
		optional: bool = False,
		target: str | None = None,
	) -> EnvVarCheck:
		"""Creates a target-aware environment variable check."""
		return EnvVarCheck(
			name=name,
			optional=optional,
			env_var=env_var,
			expected=expected,
			match_mode=match_mode,
			executor=self.executor_for(target),
		)

	def path_check(
		self,
		*,
		name: str,
		path: CheckPath,
		kind: PathKind = PathKind.ANY,
		optional: bool = False,
		target: str | None = None,
	) -> PathCheck:
		"""Creates a target-aware filesystem path check."""
		return PathCheck(
			name=name,
			optional=optional,
			path=path,
			kind=kind,
			executor=self.executor_for(target),
		)

	def command_check(
		self,
		*,
		name: str,
		cmd: str | Sequence[str],
		cwd: CheckPath | None = None,
		timeout_seconds: float,
		env: Mapping[str, str] | None = None,
		use_shell: bool = False,
		signature: str | None = None,
		optional: bool = False,
		target: str | None = None,
	) -> CommandCheck:
		"""Creates a target-aware command execution check."""
		return CommandCheck(
			name=name,
			optional=optional,
			cmd=cmd,
			cwd=cwd,
			timeout_seconds=timeout_seconds,
			env={} if env is None else env,
			use_shell=use_shell,
			signature=signature,
			executor=self.executor_for(target),
		)

	def text_file_equal(
		self,
		*,
		name: str,
		observed_path: CheckPath,
		reference_path: CheckPath,
		optional: bool = False,
		target: str | None = None,
	) -> TextFileEqualityCheck:
		"""Creates a target-aware text-file equality check."""
		return TextFileEqualityCheck(
			name=name,
			optional=optional,
			observed_path=observed_path,
			reference_path=reference_path,
			executor=self.executor_for(target),
		)

	def read_text(
		self,
		path: CheckPath,
		*,
		encoding: str = "utf-8",
		target: str | None = None,
	) -> str:
		"""Reads a text file through the selected target."""
		return check_read_file_text(
			path,
			encoding=encoding,
			executor=self.executor_for(target),
		)

	def path_exists(
		self,
		path: CheckPath,
		*,
		target: str | None = None,
	) -> bool:
		"""Returns whether a path exists in the selected target."""
		return check_path_exists(
			path,
			executor=self.executor_for(target),
		)

	def is_file(
		self,
		path: CheckPath,
		*,
		target: str | None = None,
	) -> bool:
		"""Returns whether a path is a regular file in the target."""
		return check_path_is_file(
			path,
			executor=self.executor_for(target),
		)

	def is_dir(
		self,
		path: CheckPath,
		*,
		target: str | None = None,
	) -> bool:
		"""Returns whether a path is a directory in the target."""
		return check_path_is_dir(
			path,
			executor=self.executor_for(target),
		)

	def run_command(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: CheckPath | None = None,
		env: Mapping[str, str] | None = None,
		timeout_seconds: float,
		use_shell: bool = False,
		target: str | None = None,
	) -> ProcResult:
		"""Runs a command through the selected target."""
		return run_check_process_capture(
			cmd=cmd,
			cwd=cwd,
			env=env,
			timeout_seconds=timeout_seconds,
			use_shell=use_shell,
			executor=self.executor_for(target),
		)


class CaseOracleEnvSetupBase(_CaseOracleBase):
	"""Base class for environment setup oracles."""

	phase_label = OraclePhaseName.ENV_SETUP


class CaseOracleArtifactBuildBase(_CaseOracleBase):
	"""Base class for artifact build oracles."""

	phase_label = OraclePhaseName.ARTIFACT_BUILD


class CaseOracleBenchmarkPrepBase(_CaseOracleBase):
	"""Base class for benchmark preparation oracles."""

	phase_label = OraclePhaseName.BENCHMARK_PREP


class CaseOracleExperimentRunsBase(_CaseOracleBase):
	"""Base class for experiment execution and result validation oracles."""

	phase_label = OraclePhaseName.EXPERIMENT_RUNS

	# Expose common similarity comparison helpers for raw data series
	similarity = staticmethod(compute_similarity)
	elementwise_equal = staticmethod(elementwise_equal)
	elementwise_similarity_scores = staticmethod(elementwise_similarity_scores)
	elementwise_similarity_threshold = staticmethod(elementwise_similarity_threshold)
