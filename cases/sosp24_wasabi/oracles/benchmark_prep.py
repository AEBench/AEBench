from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
	CaseOracleBenchmarkPrepBase,
	CommandCheck,
	PathCheck,
	PathKind,
	reporting,
)
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck
from models import OracleInput

from .common import find_eval_dir, missing_dir_check, run_in_runtime

_BENCHMARK_SPECS: dict[str, dict[str, str]] = {
	"hadoop": {
		"commit": "60867de",
		"pom_file": "pom.xml",
		"pom_backup": "pom-original.xml",
	},
	"hbase": {
		"commit": "89ca7f4",
		"pom_file": "pom.xml",
		"pom_backup": "pom-original.xml",
	},
	"hive": {
		"commit": "e08a600",
		"pom_file": "pom.xml",
		"pom_backup": "pom-original.xml",
	},
}
_WEAVING_PLUGIN_SIGNATURE = "aspectj-maven-plugin"
_ASPECTJ_MARKERS: tuple[str, ...] = (
	"ajc$preClinit",
	"ajc$initFailureCause",
	"ajc$tjp",
	"ajc$before$",
	"ajc$after$",
	"ajc$around$",
	"ajc$interField$",
	"ajc$interMethod$",
	"org.aspectj.runtime.reflect.Factory",
	"org.aspectj.runtime.internal.AroundClosure",
	"org.aspectj.lang.JoinPoint",
	"org.aspectj.lang.JoinPoint$StaticPart",
	"org.aspectj.lang.ProceedingJoinPoint",
	"org.aspectj.lang.Signature",
	"org.aspectj.lang.NoAspectBoundException",
)


# This should run inside inside the runtime system so it check one benchmark
# per processes instead of per class file.
# Main classes come first because woven (instrumented) test classes
# alone do not instrument the system.
def _find_woven_class(root: str, *markers: str) -> tuple[str, str] | int:
	import os

	classes = []
	for dirpath, dirnames, filenames in os.walk(root):
		dirnames[:] = [d for d in dirnames if d not in {".git", ".m2", ".gradle"}]
		classes += [os.path.join(dirpath, f) for f in filenames if f.endswith(".class")]
	classes.sort(key=lambda path: "/test-classes/" in path)
	for path in classes:
		with open(path, "rb") as f:
			data = f.read()
		if marker := next((m for m in markers if m.encode() in data), None):
			return path, marker
	return len(classes)


def oracle_benchmark_prep(
	context: OracleInput, executor: RuntimeCheckExecutor
) -> Sequence[BaseCheck]:
	benchmarks_root, searched = find_eval_dir(executor, context.workspace_dir, "benchmarks")
	if benchmarks_root is None:
		return (missing_dir_check("benchmarks_root_exists", searched),)
	reqs: list[BaseCheck] = [
		PathCheck(
			name="benchmarks_root_exists",
			path=benchmarks_root,
			kind=PathKind.DIRECTORY,
		),
	]

	for app, spec in sorted(_BENCHMARK_SPECS.items()):
		app_root = RuntimePath(benchmarks_root.value / app)
		pom_file = spec["pom_file"]
		pom_backup = spec["pom_backup"]
		expected_commit = spec["commit"]
		reqs.append(
			PathCheck(
				name=f"{app}_directory_exists",
				path=app_root,
				kind=PathKind.DIRECTORY,
			)
		)
		reqs.append(
			CommandCheck(
				name=f"{app}_clone",
				cwd=app_root,
				cmd=("git", "-c", "safe.directory=*", "rev-parse", "HEAD"),
				signature=expected_commit,
				timeout_seconds=10.0,
			)
		)

		def _make_weaving_check(name: str, root: RuntimePath) -> reporting.Check:
			def _check(executor: RuntimeCheckExecutor) -> reporting.CheckResult:
				if not executor.path_is_dir(root):
					return reporting.CheckResult.failure(f"{name}: directory not found: {root}")
				try:
					found = run_in_runtime(
						executor,
						_find_woven_class,
						str(executor.resolve_path(root)),
						*_ASPECTJ_MARKERS,
						timeout_seconds=600.0,
					)
				except RuntimeError as exc:
					return reporting.CheckResult.failure(f"{name}: class scan failed: {exc}")
				if isinstance(found, int):
					return reporting.CheckResult.failure(
						f"{name}: none of {found} .class files under {root} has AspectJ markers"
					)
				class_file, marker = found
				return reporting.CheckResult.success(
					f"{name}: found marker {marker!r} in {class_file}"
				)

			return reporting.Check(name=f"{name}_weaving", fn=_check)

		def _make_pom_swap_check(
			name: str,
			root: RuntimePath,
			active_pom: str,
			backup_pom: str,
		) -> reporting.Check:
			def _check(executor: RuntimeCheckExecutor) -> reporting.CheckResult:
				pom_path = RuntimePath(root.value / active_pom)
				backup_path = RuntimePath(root.value / backup_pom)
				if not executor.path_is_file(pom_path):
					return reporting.CheckResult.failure(f"{name}: missing active pom {pom_path}")
				if not executor.path_is_file(backup_path):
					return reporting.CheckResult.failure(
						f"{name}: missing backup pom {backup_path}"
					)
				cmp_result = executor.run_process_capture(
					cmd=(
						"cmp",
						"-s",
						str(executor.resolve_path(pom_path)),
						str(executor.resolve_path(backup_path)),
					),
					cwd=None,
					env=None,
					timeout_seconds=10.0,
				)
				if cmp_result.returncode == 0:
					return reporting.CheckResult.failure(
						f"{name}: active pom unexpectedly matches backup pom"
					)
				try:
					pom_text = executor.read_file_text(pom_path)
				except OSError as exc:
					return reporting.CheckResult.failure(f"{name}: failed to read pom: {exc}")
				if _WEAVING_PLUGIN_SIGNATURE not in pom_text:
					return reporting.CheckResult.failure(
						f"{name}: weaving plugin signature missing from {pom_path}"
					)
				return reporting.CheckResult.success(
					f"{name}: active pom differs from backup and contains weaving plugin"
				)

			return reporting.Check(name=f"{name}_pom_swap", fn=_check)

		reqs.append(_make_pom_swap_check(app, app_root, pom_file, pom_backup))
		if app == "hive":
			# run.py also swaps the standalone metastore's pom.
			reqs.append(
				_make_pom_swap_check(
					"hive_standalone_metastore",
					RuntimePath(app_root.value / "standalone-metastore"),
					pom_file,
					pom_backup,
				)
			)
		reqs.append(_make_weaving_check(app, app_root))
	return tuple(reqs)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return oracle_benchmark_prep(self.context, self.executor)
