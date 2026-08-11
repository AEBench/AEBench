from __future__ import annotations

import fnmatch
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluator.oracles.utils import Checkable

from evaluator.oracles import utils
from evaluator.oracles.discovery import artifact_build
from evaluator.oracles.oracle_checks_runtime import OraclePath, RuntimeCheckExecutor, RuntimePath
from models import OracleInput

_PRIMARY_ARTIFACT = "edu.uchicago.cs.systems:wasabi"


def _sha256(path: OraclePath, *, executor: RuntimeCheckExecutor) -> str:
	result = executor.run_process_capture(
		cmd=(
			"python3",
			"-c",
			"import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
			str(executor.resolve_path(path)),
		),
		cwd=None,
		env=None,
		timeout_seconds=30.0,
	)
	if result.returncode != 0:
		raise OSError(result.stderr or f"failed to hash {path}")
	return result.stdout.strip()


def _pick_primary_jar(
	dir_path: OraclePath,
	artifact_id: str,
	version: str,
	*,
	executor: RuntimeCheckExecutor,
) -> OraclePath | None:
	if not executor.path_is_dir(dir_path):
		return None

	bad_tokens = ("-sources", "-javadoc", "-tests", "original-")
	pattern = f"{artifact_id}-{version}*.jar"
	candidates = [
		path
		for path in executor.glob(dir_path, "*.jar")
		if executor.path_is_file(RuntimePath.from_parts(path.as_posix()))
		and fnmatch.fnmatch(path.name, pattern)
		and not any(token in path.name for token in bad_tokens)
	]
	if not candidates:
		return None
	resolved = {str(path): path for path in candidates}
	result = executor.run_process_capture(
		cmd=(
			"python3",
			"-c",
			"import os, sys; print(max(sys.argv[1:], key=os.path.getmtime))",
			*resolved,
		),
		cwd=None,
		env=None,
		timeout_seconds=10.0,
	)
	if result.returncode != 0:
		raise OSError(result.stderr or f"failed to inspect jars under {dir_path}")
	return RuntimePath.from_parts(resolved[result.stdout.strip()].as_posix())


def _strip_ns(tag: str) -> str:
	return tag.split("}", 1)[-1]


def _xget(element: ET.Element | None, tag: str) -> str | None:
	if element is None:
		return None
	value = element.find(tag)
	if value is not None and value.text:
		return value.text.strip()
	for child in element:
		if _strip_ns(child.tag) == tag:
			return (child.text or "").strip()
	return None


def _parse_pom(
	pom_path: Path,
	*,
	top_defaults: dict[str, str] | None,
	executor: RuntimeCheckExecutor,
) -> dict[str, Any]:
	try:
		root = ET.fromstring(executor.read_file_text(pom_path))
	except Exception as exc:
		return {"dir": pom_path.parent, "pom": pom_path, "error": f"XML parse error: {exc}"}

	artifact_id = _xget(root, "artifactId")
	group_id = _xget(root, "groupId")
	version = _xget(root, "version")
	packaging = _xget(root, "packaging") or "jar"

	parent = next((child for child in root if _strip_ns(child.tag) == "parent"), None)
	if parent is not None:
		parent_group_id = _xget(parent, "groupId")
		parent_version = _xget(parent, "version")
		if not group_id and parent_group_id:
			group_id = parent_group_id
		if not version and parent_version:
			version = parent_version

	if top_defaults:
		group_id = group_id or top_defaults.get("groupId")
		version = version or top_defaults.get("version")

	return {
		"dir": pom_path.parent,
		"pom": pom_path,
		"groupId": group_id,
		"artifactId": artifact_id,
		"version": version,
		"packaging": packaging,
	}


def _find_poms(base: Path, *, executor: RuntimeCheckExecutor) -> list[Path]:
	runtime_base = Path(executor.resolve_path(base))
	return sorted(
		base / path.relative_to(runtime_base)
		for path in executor.glob(base, "**/pom.xml")
	)


def _repo_path(
	group_id: str, artifact_id: str, version: str, *, executor: RuntimeCheckExecutor
) -> RuntimePath:
	home = executor.read_env_var("HOME") or "/root"
	return RuntimePath.from_parts(
		home, ".m2", "repository", *group_id.split("."), artifact_id, version
	)


@artifact_build
def oracle_artifact_build(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	cache: dict[str, list[dict[str, Any]]] = {}

	def _load_modules(executor: RuntimeCheckExecutor) -> list[dict[str, Any]]:
		modules = cache.get("modules")
		if modules is not None:
			return modules

		if not executor.path_is_dir(repo_root):
			raise ValueError(f"base project directory not found: {repo_root}")

		poms = _find_poms(repo_root, executor=executor)
		if not poms:
			raise ValueError(f"no pom.xml files found under {repo_root}")

		root_pom = repo_root / "pom.xml"
		top_defaults: dict[str, str] = {}
		if executor.path_exists(root_pom):
			root_module = _parse_pom(root_pom, top_defaults=None, executor=executor)
			if "error" not in root_module:
				group_id = root_module.get("groupId")
				version = root_module.get("version")
				if isinstance(group_id, str) and group_id:
					top_defaults["groupId"] = group_id
				if isinstance(version, str) and version:
					top_defaults["version"] = version

		errors: list[str] = []
		parsed_modules: list[dict[str, Any]] = []
		for pom in poms:
			module = _parse_pom(pom, top_defaults=top_defaults, executor=executor)
			error = module.get("error")
			if isinstance(error, str):
				errors.append(f"{pom}: {error}")
				continue
			group_id = module.get("groupId")
			artifact_id = module.get("artifactId")
			version = module.get("version")
			if not (
				isinstance(group_id, str)
				and group_id
				and isinstance(artifact_id, str)
				and artifact_id
				and isinstance(version, str)
				and version
			):
				errors.append(f"{pom}: missing groupId/artifactId/version after inheritance")
				continue
			parsed_modules.append(module)

		if errors:
			head = "\n".join(errors[:5])
			if len(errors) > 5:
				head = f"{head}\n... ({len(errors) - 5} more)"
			raise ValueError(f"POM parsing errors present:\n{head}")

		cache["modules"] = parsed_modules
		return parsed_modules

	def _check_build_inputs(executor: RuntimeCheckExecutor) -> utils.CheckResult:
		try:
			_load_modules(executor)
		except ValueError as exc:
			return utils.CheckResult.failure(str(exc))
		return utils.CheckResult.success(f"loaded Maven module metadata under {repo_root}")

	def _check_primary_module_artifact(executor: RuntimeCheckExecutor) -> utils.CheckResult:
		try:
			modules = _load_modules(executor)
		except ValueError as exc:
			return utils.CheckResult.failure(str(exc))

		want_group_id, want_artifact_id = _PRIMARY_ARTIFACT.split(":", 1)
		chosen: dict[str, Any] | None = None
		for module in modules:
			group_id = module.get("groupId")
			artifact_id = module.get("artifactId")
			if group_id == want_group_id and artifact_id == want_artifact_id:
				chosen = module
				break

		if chosen is None:
			return utils.CheckResult.failure(
				f"primary module not found for selector {_PRIMARY_ARTIFACT!r}"
			)

		packaging = str(chosen.get("packaging") or "jar").strip()
		if packaging == "pom":
			return utils.CheckResult.failure("primary module resolved to packaging=pom")

		group_id = str(chosen["groupId"]).strip()
		artifact_id = str(chosen["artifactId"]).strip()
		version = str(chosen["version"]).strip()
		module_dir = Path(chosen["dir"])

		built = _pick_primary_jar(
			module_dir / "target", artifact_id, version, executor=executor
		)
		installed = _pick_primary_jar(
			_repo_path(group_id, artifact_id, version, executor=executor),
			artifact_id,
			version,
			executor=executor,
		)
		if built is None or installed is None:
			return utils.CheckResult.failure("missing built jar and/or installed artifact")

		built_sha = _sha256(built, executor=executor)
		installed_sha = _sha256(installed, executor=executor)
		if built_sha != installed_sha:
			return utils.CheckResult.failure(
				"primary artifact mismatch: target jar does not match local Maven repo jar"
			)

		return utils.CheckResult.success(
			f"primary module artifact matches local Maven repository: {artifact_id}-{version}"
		)

	return (
		utils.Check(
			name="build_inputs",
			fn=_check_build_inputs,
		),
		utils.Check(
			name="primary_module_artifact",
			fn=_check_primary_module_artifact,
		),
	)
