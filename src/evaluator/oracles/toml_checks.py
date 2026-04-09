"""Convert declarative TOML check declarations into Checkable instances."""

from __future__ import annotations

import csv
import dataclasses
import json
import pathlib
import statistics
from collections.abc import Sequence
from typing import Any, cast

import simpleeval

from models import (
	CheckDecl,
	CommandCheckDecl,
	EnvVarCheckDecl,
	ExprCheckDecl,
	OracleConfig,
	OracleInput,
	PathCheckDecl,
	VersionCheckDecl,
)

from ..constants import REFS_DIRNAME
from . import utils
from .benchmark_prep_checks import BenchmarkCommandCheck
from .discovery import (
	_PHASE_PRIORITIES,
	ARTIFACT_BUILD,
	BENCHMARK_PREP,
	ENV_SETUP,
	EXPERIMENT_RUNS,
	DiscoveredPhase,
	OraclePhase,
	PhaseKey,
)
from .env_setup_checks import (
	DependencyVersionCheck,
	EnvironmentVariableCheck,
	EnvMatchMode,
	FilesystemPathCheck,
	PathType,
	VersionCompare,
)
from .utils import Checkable


def _build_template_vars(context: OracleInput) -> dict[str, str]:
	case_dir = context.case_dir.resolve()
	return {
		"case_dir": str(case_dir),
		"artifact_dir": str(context.artifact_dir.resolve()),
		"workspace_dir": str(context.workspace_dir.resolve()),
		"output_dir": str(context.output_dir.resolve()),
		"refs_dir": str((case_dir / REFS_DIRNAME).resolve()),
	}


def _resolve(template: str, variables: dict[str, str]) -> str:
	try:
		return template.format_map(variables)
	except KeyError as exc:
		raise ValueError(f"unknown template variable {exc} in {template!r}") from exc


def _build_check(
	decl: CheckDecl,
	variables: dict[str, str],
	*,
	executor: utils.RuntimeCheckExecutor | None = None,
) -> Checkable:
	"""Convert a single TOML check declaration into a Checkable."""
	if isinstance(decl, PathCheckDecl):
		try:
			path_type = PathType(decl.path_type)
		except ValueError:
			raise ValueError(
				f"check {decl.name!r}: invalid path_type {decl.path_type!r}; "
				f"expected one of {[e.value for e in PathType]}"
			) from None
		return FilesystemPathCheck(
			name=decl.name,
			path=pathlib.Path(_resolve(decl.path, variables)),
			path_type=path_type,
			optional=decl.optional,
		)

	if isinstance(decl, VersionCheckDecl):
		try:
			compare = VersionCompare(decl.compare)
		except ValueError:
			raise ValueError(
				f"check {decl.name!r}: invalid compare {decl.compare!r}; "
				f"expected one of {[e.value for e in VersionCompare]}"
			) from None
		return DependencyVersionCheck(
			name=decl.name,
			cmd=tuple(decl.cmd),
			required_version=tuple(decl.required_version),  # type: ignore[arg-type]
			compare=compare,
			version_regex=decl.version_regex,
			timeout_seconds=decl.timeout_seconds,
			executor=executor,
			optional=decl.optional,
		)

	if isinstance(decl, EnvVarCheckDecl):
		try:
			match_mode = EnvMatchMode(decl.match_mode)
		except ValueError:
			raise ValueError(
				f"check {decl.name!r}: invalid match_mode {decl.match_mode!r}; "
				f"expected one of {[e.value for e in EnvMatchMode]}"
			) from None
		return EnvironmentVariableCheck(
			name=decl.name,
			env_var=decl.env_var,
			expected=decl.expected,
			match_mode=match_mode,
			executor=executor,
			optional=decl.optional,
		)

	if isinstance(decl, CommandCheckDecl):
		return BenchmarkCommandCheck(
			name=decl.name,
			cmd=tuple(decl.cmd) if isinstance(decl.cmd, list) else decl.cmd,
			cwd=pathlib.Path(_resolve(decl.cwd, variables)) if decl.cwd else None,
			signature=decl.signature,
			timeout_seconds=decl.timeout_seconds,
			env_overrides=decl.env_overrides,
			use_shell=decl.use_shell,
			executor=executor,
			optional=decl.optional,
		)

	if isinstance(decl, ExprCheckDecl):
		return ExpressionCheck(
			name=decl.name,
			expr=decl.expr,
			observed_path=(
				pathlib.Path(_resolve(decl.observed, variables)) if decl.observed else None
			),
			reference_path=(
				pathlib.Path(_resolve(decl.reference, variables)) if decl.reference else None
			),
			optional=decl.optional,
		)

	raise ValueError(f"unsupported check declaration type: {type(decl).__name__}")


def build_checks(
	declarations: Sequence[CheckDecl],
	variables: dict[str, str],
	*,
	executor: utils.RuntimeCheckExecutor | None = None,
) -> list[Checkable]:
	return [_build_check(decl, variables, executor=executor) for decl in declarations]


class DotAccessDict:
	"""Wraps a dict for attribute-style access in simpleeval expressions."""

	def __init__(self, data: dict[str, Any]) -> None:
		self._data = data

	def __getattr__(self, key: str) -> Any:
		try:
			value = self._data[key]
		except KeyError:
			raise AttributeError(f"no field {key!r}") from None
		if isinstance(value, dict):
			return DotAccessDict(value)
		return value

	def __getitem__(self, key: str) -> Any:
		try:
			value = self._data[key]
		except KeyError:
			raise KeyError(key) from None
		if isinstance(value, dict):
			return DotAccessDict(value)
		return value

	def __repr__(self) -> str:
		return f"DotAccessDict({self._data!r})"


def _load_data_file(path: pathlib.Path) -> DotAccessDict:
	"""Load a JSON or CSV file into a DotAccessDict."""
	suffix = path.suffix.lower()
	if suffix == ".json":
		with path.open(encoding="utf-8") as fh:
			data = json.load(fh)
		if not isinstance(data, dict):
			return DotAccessDict({"_root": data})
		return DotAccessDict(data)

	if suffix == ".csv":
		with path.open(encoding="utf-8", newline="") as fh:
			reader = csv.DictReader(fh)
			columns: dict[str, list[float]] = {}
			for row in reader:
				for key, raw_value in row.items():
					if key is None:
						continue
					col = columns.setdefault(key, [])
					try:
						col.append(float(raw_value))
					except (ValueError, TypeError):
						col.append(float("nan"))
		return DotAccessDict(columns)

	raise ValueError(f"unsupported data file format: {path.suffix}")


def _avg(values: Sequence[float]) -> float:
	if not values:
		raise ValueError("avg() requires a non-empty sequence")
	return sum(values) / len(values)


def _median(values: Sequence[float]) -> float:
	if not values:
		raise ValueError("median() requires a non-empty sequence")
	return statistics.median(values)


_EXPR_FUNCTIONS: dict[str, Any] = {
	"avg": _avg,
	"sum": sum,
	"min": min,
	"max": max,
	"len": len,
	"abs": abs,
	"median": _median,
	"count": len,
}


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ExpressionCheck(utils.BaseCheck):
	"""Evaluates a simpleeval expression against loaded data files."""

	expr: str
	observed_path: pathlib.Path | None = None
	reference_path: pathlib.Path | None = None

	def check(self) -> utils.CheckResult:
		names: dict[str, Any] = {}

		if self.observed_path is not None:
			try:
				names["obs"] = _load_data_file(self.observed_path)
			except Exception as exc:
				return utils.CheckResult.failure(
					f"failed to load observed file {self.observed_path}: {exc}"
				)

		if self.reference_path is not None:
			try:
				names["ref"] = _load_data_file(self.reference_path)
			except Exception as exc:
				return utils.CheckResult.failure(
					f"failed to load reference file {self.reference_path}: {exc}"
				)

		# simpleeval is a sandboxed expression evaluator (no imports, no exec)
		evaluator = simpleeval.EvalWithCompoundTypes(
			names=names,
			functions=_EXPR_FUNCTIONS,
		)

		try:
			result = evaluator.eval(self.expr)
		except Exception as exc:
			return utils.CheckResult.failure(
				f"expression evaluation failed: {type(exc).__name__}: {exc}"
			)

		if not isinstance(result, bool):
			return utils.CheckResult.failure(
				f"expression must evaluate to bool, got {type(result).__name__}: {result!r}"
			)

		if result:
			return utils.CheckResult.success()
		return utils.CheckResult.failure(f"expression evaluated to False: {self.expr}")


_PHASE_CHECK_FIELDS: list[tuple[str, PhaseKey]] = [
	("env_setup", ENV_SETUP),
	("artifact_build", ARTIFACT_BUILD),
	("benchmark_prep", BENCHMARK_PREP),
	("experiment_runs", EXPERIMENT_RUNS),
]


def discover_toml_phases(
	oracle_config: OracleConfig,
	context: OracleInput,
) -> list[DiscoveredPhase]:
	"""Build DiscoveredPhase instances from TOML check declarations."""
	phases: list[DiscoveredPhase] = []

	for field_name, phase_key in _PHASE_CHECK_FIELDS:
		phase_config = getattr(oracle_config, field_name)
		if not phase_config.checks:
			continue

		declarations = list(phase_config.checks)

		def _make_requirements(
			decls: list[CheckDecl],
		) -> OraclePhase:
			def requirements(ctx: OracleInput) -> Sequence[Checkable]:
				executor = cast(utils.RuntimeCheckExecutor | None, ctx.runtime_executor)
				return build_checks(
					decls,
					_build_template_vars(ctx),
					executor=executor,
				)

			return cast(OraclePhase, requirements)

		phases.append(
			DiscoveredPhase(
				key=phase_key,
				priority=_PHASE_PRIORITIES[phase_key],
				requirements=_make_requirements(declarations),
				qualname=f"<toml:{field_name}>",
			)
		)

	return phases
