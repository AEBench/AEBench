from __future__ import annotations

from collections.abc import Sequence
from filecmp import cmp
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.benchmark_prep_checks import BenchmarkCommandCheck
from evaluator.oracles.discovery import benchmark_prep
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.utils import Checkable
from models import OracleInput

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


def _find_class_dirs(app_root: Path) -> tuple[list[Path], str | None]:
	class_dirs: set[Path] = set()
	try:
		for class_file in app_root.rglob("*.class"):
			if any(part in {".git", ".m2", ".gradle"} for part in class_file.parts):
				continue
			class_dirs.add(class_file.parent)
	except OSError as exc:
		return [], str(exc)
	return sorted(class_dirs), None


def _iter_class_files(classes_dir: Path, *, limit: int) -> list[Path]:
	try:
		files = sorted(classes_dir.rglob("*.class"))
	except OSError:
		return []
	if limit and len(files) > limit:
		step = max(len(files) // limit, 1)
		files = files[::step][:limit]
	return files


def _classfile_has_aspect_markers(class_path: Path) -> tuple[bool, str]:
	try:
		content = class_path.read_bytes()
	except OSError:
		return False, ""
	for marker in _ASPECTJ_MARKERS:
		if marker.encode("utf-8") in content:
			return True, marker
	return False, ""


@benchmark_prep
def oracle_benchmark_prep(context: OracleInput) -> Sequence[Checkable]:
	benchmarks_root = context.workspace_dir / "benchmarks"
	reqs: list[Checkable] = [
		FilesystemPathCheck(
			name="benchmarks_root_exists",
			path=benchmarks_root,
			path_type=PathType.DIRECTORY,
		),
	]

	for app, spec in sorted(_BENCHMARK_SPECS.items()):
		app_root = benchmarks_root / app
		pom_file = spec["pom_file"]
		pom_backup = spec["pom_backup"]
		expected_commit = spec["commit"]
		reqs.append(
			FilesystemPathCheck(
				name=f"{app}_directory_exists",
				path=app_root,
				path_type=PathType.DIRECTORY,
			)
		)
		reqs.append(
			BenchmarkCommandCheck(
				name=f"{app}_clone",
				cwd=app_root,
				cmd=("git", "rev-parse", "HEAD"),
				signature=expected_commit,
				timeout_seconds=10.0,
			)
		)

		def _make_weaving_check(name: str, root: Path) -> utils.Check:
			def _check() -> utils.CheckResult:
				if not root.is_dir():
					return utils.CheckResult.failure(f"{name}: directory not found: {root}")
				class_dirs, error = _find_class_dirs(root)
				if error is not None:
					return utils.CheckResult.failure(f"{name}: {error}")
				if not class_dirs:
					return utils.CheckResult.failure(
						f"{name}: no compiled .class files found under {root}"
					)

				for classes_dir in class_dirs[:200]:
					for class_file in _iter_class_files(classes_dir, limit=2000):
						matched, marker = _classfile_has_aspect_markers(class_file)
						if matched:
							return utils.CheckResult.success(
								f"{name}: found marker {marker!r} in {class_file}"
							)
				return utils.CheckResult.failure(
					f"{name}: scanned .class files but found no AspectJ markers"
				)

			return utils.Check(name=f"{name}_weaving", fn=_check)

		def _make_pom_swap_check(
			name: str,
			root: Path,
			active_pom: str,
			backup_pom: str,
		) -> utils.Check:
			def _check() -> utils.CheckResult:
				pom_path = root / active_pom
				backup_path = root / backup_pom
				if not pom_path.is_file():
					return utils.CheckResult.failure(f"{name}: missing active pom {pom_path}")
				if not backup_path.is_file():
					return utils.CheckResult.failure(f"{name}: missing backup pom {backup_path}")
				if cmp(pom_path, backup_path, shallow=False):
					return utils.CheckResult.failure(
						f"{name}: active pom unexpectedly matches backup pom"
					)
				try:
					pom_text = pom_path.read_text(encoding="utf-8", errors="replace")
				except OSError as exc:
					return utils.CheckResult.failure(f"{name}: failed to read pom: {exc}")
				if _WEAVING_PLUGIN_SIGNATURE not in pom_text:
					return utils.CheckResult.failure(
						f"{name}: weaving plugin signature missing from {pom_path}"
					)
				return utils.CheckResult.success(
					f"{name}: active pom differs from backup and contains weaving plugin"
				)

			return utils.Check(name=f"{name}_pom_swap", fn=_check)

		reqs.append(_make_pom_swap_check(app, app_root, pom_file, pom_backup))
		reqs.append(_make_weaving_check(app, app_root))
	return tuple(reqs)
