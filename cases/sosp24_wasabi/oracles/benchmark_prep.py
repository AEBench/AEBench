from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult

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


def _find_class_dirs(
	app_root: Path, *, executor: RuntimeCheckExecutor
) -> tuple[list[Path], str | None]:
	class_dirs: set[Path] = set()
	try:
		for class_file in executor.glob(app_root, "**/*.class"):
			if any(part in {".git", ".m2", ".gradle"} for part in class_file.parts):
				continue
			class_dirs.add(class_file.parent)
	except OSError as exc:
		return [], str(exc)
	return sorted(class_dirs), None


def _iter_class_files(
	classes_dir: Path, *, limit: int, executor: RuntimeCheckExecutor
) -> list[Path]:
	try:
		files = sorted(executor.glob(RuntimePath.from_parts(classes_dir.as_posix()), "**/*.class"))
	except OSError:
		return []
	if limit and len(files) > limit:
		step = max(len(files) // limit, 1)
		files = files[::step][:limit]
	return files


def _classfile_has_aspect_markers(
	class_path: Path, *, executor: RuntimeCheckExecutor
) -> tuple[bool, str]:
	try:
		result = executor.run_process_capture(
			cmd=(
				"python3",
				"-c",
				(
					"import pathlib, sys; data=pathlib.Path(sys.argv[1]).read_bytes(); "
					"print(next((m for m in sys.argv[2:] if m.encode() in data), ''))"
				),
				str(class_path),
				*_ASPECTJ_MARKERS,
			),
			cwd=None,
			env=None,
			timeout_seconds=10.0,
		)
	except (OSError, RuntimeError):
		return False, ""
	marker = result.stdout.strip() if result.returncode == 0 else ""
	return bool(marker), marker


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		benchmarks_root = self.workspace_path("benchmarks")
		reqs: list[BaseCheck] = [
			self.path_check(
				name="benchmarks_root_exists",
				path=benchmarks_root,
				kind=PathKind.DIRECTORY,
			),
		]

		for app, spec in sorted(_BENCHMARK_SPECS.items()):
			app_root = benchmarks_root / app
			pom_file = spec["pom_file"]
			pom_backup = spec["pom_backup"]
			expected_commit = spec["commit"]
			reqs.append(
				self.path_check(
					name=f"{app}_directory_exists",
					path=app_root,
					kind=PathKind.DIRECTORY,
				)
			)
			reqs.append(
				self.command_check(
					name=f"{app}_clone",
					cwd=app_root,
					cmd=("git", "rev-parse", "HEAD"),
					signature=expected_commit,
					timeout_seconds=10.0,
				)
			)

			def _make_weaving_check(name: str, root: Path) -> Check:
				def _check(executor: RuntimeCheckExecutor) -> CheckResult:
					if not executor.path_is_dir(root):
						return CheckResult.failure(f"{name}: directory not found: {root}")
					class_dirs, error = _find_class_dirs(root, executor=executor)
					if error is not None:
						return CheckResult.failure(f"{name}: {error}")
					if not class_dirs:
						return CheckResult.failure(
							f"{name}: no compiled .class files found under {root}"
						)

					for classes_dir in class_dirs[:200]:
						for class_file in _iter_class_files(
							classes_dir, limit=2000, executor=executor
						):
							matched, marker = _classfile_has_aspect_markers(
								class_file, executor=executor
							)
							if matched:
								return CheckResult.success(
									f"{name}: found marker {marker!r} in {class_file}"
								)
					return CheckResult.failure(
						f"{name}: scanned .class files but found no AspectJ markers"
					)

				return Check(name=f"{name}_weaving", fn=_check)

			def _make_pom_swap_check(
				name: str,
				root: Path,
				active_pom: str,
				backup_pom: str,
			) -> Check:
				def _check(executor: RuntimeCheckExecutor) -> CheckResult:
					pom_path = root / active_pom
					backup_path = root / backup_pom
					if not executor.path_is_file(pom_path):
						return CheckResult.failure(f"{name}: missing active pom {pom_path}")
					if not executor.path_is_file(backup_path):
						return CheckResult.failure(f"{name}: missing backup pom {backup_path}")
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
						return CheckResult.failure(
							f"{name}: active pom unexpectedly matches backup pom"
						)
					try:
						pom_text = executor.read_file_text(pom_path)
					except OSError as exc:
						return CheckResult.failure(f"{name}: failed to read pom: {exc}")
					if _WEAVING_PLUGIN_SIGNATURE not in pom_text:
						return CheckResult.failure(
							f"{name}: weaving plugin signature missing from {pom_path}"
						)
					return CheckResult.success(
						f"{name}: active pom differs from backup and contains weaving plugin"
					)

				return Check(name=f"{name}_pom_swap", fn=_check)

			reqs.append(_make_pom_swap_check(app, app_root, pom_file, pom_backup))
			reqs.append(_make_weaving_check(app, app_root))
		return tuple(reqs)
