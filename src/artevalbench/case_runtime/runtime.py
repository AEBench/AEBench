from __future__ import annotations

from pathlib import Path

from ..cache import protected_git_checkout_paths, resolve_git_bundle_artifact
from ..models import ArchiveSource, GitSource, LocalSource, OverlaySource, TaskSpec
from ..project_config import ArtifactMode, ProjectConfigState, load_project_config
from .loader import load_case_spec
from ..domain.models import CaseSpec, UpstreamSourceType


class CaseRuntimeError(RuntimeError):
	pass


def artifact_dir_for(case_dir: Path) -> Path:
	return (case_dir.resolve() / "artifact").resolve(strict=False)


def has_materialized_artifact(case_dir: Path) -> bool:
	artifact_dir = artifact_dir_for(case_dir)
	if not artifact_dir.exists():
		return False
	for entry in artifact_dir.iterdir():
		if entry.name.startswith("."):
			continue
		return True
	return False


def task_from_case_dir(
 case_dir: Path, *, project_state: ProjectConfigState | None = None
) -> TaskSpec:
	case_root = case_dir.resolve()
	case = load_case_spec(case_root)
	return task_from_case_spec(case_root, case, project_state=project_state)


def task_from_case_spec(
 case_dir: Path,
 case: CaseSpec,
 *,
 project_state: ProjectConfigState | None = None,
) -> TaskSpec:
	source = _task_source_from_case(
	 case_dir.resolve(), case, project_state=project_state or load_project_config(case_dir)
	)
	return case.run.model_copy(update={"source": source, "case_card": case.case_card})


def export_case_dirs(
 case_dirs: list[Path],
 output_path: Path,
 *,
 project_state: ProjectConfigState | None = None,
) -> list[TaskSpec]:
	tasks = [task_from_case_dir(case_dir, project_state=project_state) for case_dir in case_dirs]
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open("w", encoding="utf-8") as handle:
		for task in tasks:
			handle.write(task.model_dump_json() + "\n")
	return tasks


def _task_source_from_case(
 case_dir: Path,
 case: CaseSpec,
 *,
 project_state: ProjectConfigState,
) -> LocalSource | GitSource | ArchiveSource | OverlaySource:
	upstream = case.upstream
	configured_mode = upstream.artifact_mode
	artifact_dir = artifact_dir_for(case_dir)
	materialized_artifact = has_materialized_artifact(case_dir)

	if configured_mode is not None:
		return _configured_task_source_from_case(
		 case_dir,
		 case,
		 project_state=project_state,
		 configured_mode=configured_mode,
		 artifact_dir=artifact_dir,
		 materialized_artifact=materialized_artifact,
		)

	if upstream.source_type == UpstreamSourceType.GIT and upstream.url:
		upstream_url = upstream.url
		resolve_git_bundle_artifact(
		 case_dir,
		 case,
		 project_state=project_state,
		 protected_paths=protected_git_checkout_paths(project_state),
		)
		return GitSource(url=upstream_url, ref=upstream.ref)

	if materialized_artifact:
		return LocalSource(path=str(artifact_dir))

	if upstream.source_type == UpstreamSourceType.LOCAL and upstream.path:
		return LocalSource(path=str(_resolve_upstream_path(case_dir, upstream.path)))
	if upstream.source_type == UpstreamSourceType.ARCHIVE:
		if upstream.url:
			return ArchiveSource(url=upstream.url)
		if upstream.path:
			return ArchiveSource(path=str(_resolve_upstream_path(case_dir, upstream.path)))
	raise CaseRuntimeError(
	 f"case {case.id} has no materialized artifact and no usable upstream source"
	)


def _configured_task_source_from_case(
 case_dir: Path,
 case: CaseSpec,
 *,
 project_state: ProjectConfigState,
 configured_mode: ArtifactMode,
 artifact_dir: Path,
 materialized_artifact: bool,
) -> LocalSource | GitSource | ArchiveSource | OverlaySource:
	upstream = case.upstream
	if configured_mode == ArtifactMode.VENDOR:
		if materialized_artifact:
			return LocalSource(path=str(artifact_dir))
		raise CaseRuntimeError(
		 f"case {case.id} uses upstream.artifact_mode=vendor but has no materialized artifact"
		)

	base_source = _upstream_base_source(case_dir, case, project_state=project_state)
	if configured_mode == ArtifactMode.POINTER:
		return base_source
	if configured_mode == ArtifactMode.HYBRID:
		if upstream.overlay_artifact:
			if not materialized_artifact:
				raise CaseRuntimeError(
				 f"case {case.id} uses upstream.overlay_artifact=true but has no materialized artifact"
				)
			return OverlaySource(
			 base=base_source,
			 overlay=LocalSource(path=str(artifact_dir)),
			)
		return base_source
	raise CaseRuntimeError(
	 f"unsupported upstream artifact mode for case {case.id}: {configured_mode}"
	)


def _upstream_base_source(
 case_dir: Path,
 case: CaseSpec,
 *,
 project_state: ProjectConfigState,
) -> LocalSource | GitSource | ArchiveSource:
	upstream = case.upstream
	if upstream.source_type == UpstreamSourceType.GIT and upstream.url:
		return GitSource(url=upstream.url, ref=upstream.ref)
	if upstream.source_type == UpstreamSourceType.LOCAL and upstream.path:
		return LocalSource(path=str(_resolve_upstream_path(case_dir, upstream.path)))
	if upstream.source_type == UpstreamSourceType.ARCHIVE:
		if upstream.url:
			return ArchiveSource(url=upstream.url)
		if upstream.path:
			return ArchiveSource(path=str(_resolve_upstream_path(case_dir, upstream.path)))
	raise CaseRuntimeError(
	 f"case {case.id} has no usable upstream base source for configured artifact mode"
	)


def _resolve_upstream_path(case_dir: Path, upstream_path: str) -> Path:
	path = Path(upstream_path)
	return path if path.is_absolute() else (case_dir / path).resolve()
