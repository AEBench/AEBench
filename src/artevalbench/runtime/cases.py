from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..evaluator import artifact_dir_for, has_local_artifact
from ..evaluator.loader import load_case_spec
from ..cache.git import protected_git_checkout_paths, resolve_git_bundle_artifact
from ..domain.models import ArchiveSource, SourceSpec as BenchSource, GitSource, LocalSource, OverlaySource, UpstreamSourceType
from ..project_config import ArtifactMode, load_project_config

if TYPE_CHECKING:
    from ..domain.models import CaseSpec as CaseConfig, RunSpec as TaskConfig, UpstreamSpec as UpstreamConfig
    from ..project_config import ProjectConfigState as ProjectState


class CaseResolutionError(ValueError):
    pass


def resolve_case_dir(case_ref: str, *, project_state: ProjectState) -> Path:
    candidate = Path(case_ref).expanduser().resolve()
    if candidate.is_dir() and (candidate / "case.toml").is_file():
        return candidate

    registry_cases = project_state.registry.cases
    if case_ref in registry_cases:
        path = Path(registry_cases[case_ref].path)
        if not path.is_absolute():
            path = (project_state.root / path).resolve()
        if path.is_dir() and (path / "case.toml").is_file():
            return path

    candidate = (project_state.root / case_ref).resolve()
    if candidate.is_dir() and (candidate / "case.toml").is_file():
        return candidate

    bundles_dir = project_state.config.resolve_bundles_dir(project_state.root)
    candidate = (bundles_dir / case_ref).resolve()
    if candidate.is_dir() and (candidate / "case.toml").is_file():
        return candidate

    raise CaseResolutionError(f"cannot resolve case reference: {case_ref}")


def expand_case_dirs(inputs: list[str], *, project_state: ProjectState) -> list[Path]:
    if not inputs:
        return _all_registered_case_dirs(project_state)

    result: list[Path] = []
    seen: set[Path] = set()

    for ref in inputs:
        try:
            resolved = resolve_case_dir(ref, project_state=project_state)
            if resolved not in seen:
                result.append(resolved)
                seen.add(resolved)
            continue
        except CaseResolutionError:
            pass

        for path in sorted(project_state.root.glob(ref)):
            if path.is_dir() and (path / "case.toml").is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    result.append(resolved)
                    seen.add(resolved)

    return result


def task_from_case_dir(
    case_dir: Path,
    *,
    project_state: ProjectState | None = None,
) -> TaskConfig:
    case_root = case_dir.resolve()
    case = load_case_spec(case_root)
    return task_from_case_spec(case_root, case, project_state=project_state)


def task_from_case_spec(
    case_dir: Path,
    case: CaseConfig,
    *,
    project_state: ProjectState | None = None,
) -> TaskConfig:
    state = project_state or load_project_config(case_dir)
    source = _task_source_from_case(case_dir.resolve(), case, project_state=state)
    return case.run.model_copy(update={"source": source, "case_brief": case.case_brief})


def export_case_dirs(
    case_dirs: list[Path],
    output_path: Path,
    *,
    project_state: ProjectState | None = None,
) -> list[TaskConfig]:
    tasks = [task_from_case_dir(case_dir, project_state=project_state) for case_dir in case_dirs]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(task.model_dump_json())
            handle.write("\n")
    return tasks


def _task_source_from_case(
    case_dir: Path,
    case: CaseConfig,
    *,
    project_state: ProjectState,
) -> BenchSource:
    upstream = case.upstream
    configured_mode = upstream.artifact_mode
    artifact_dir = artifact_dir_for(case_dir)
    materialized = has_local_artifact(case_dir)

    if configured_mode is not None:
        return _configured_task_source_from_case(
            case_dir,
            case,
            project_state=project_state,
            configured_mode=configured_mode,
            artifact_dir=artifact_dir,
            materialized=materialized,
        )

    if upstream.source_type == UpstreamSourceType.GIT and upstream.url:
        resolve_git_bundle_artifact(
            case_dir,
            case,
            project_state=project_state,
            protected_paths=protected_git_checkout_paths(project_state),
        )
        return GitSource(url=upstream.url, ref=upstream.ref)

    if materialized:
        return LocalSource(path=str(artifact_dir))

    return _source_from_upstream(case_dir, upstream)


def _configured_task_source_from_case(
    case_dir: Path,
    case: CaseConfig,
    *,
    project_state: ProjectState,
    configured_mode: ArtifactMode,
    artifact_dir: Path,
    materialized: bool,
) -> BenchSource:
    upstream = case.upstream

    if configured_mode == ArtifactMode.VENDOR:
        if materialized:
            return LocalSource(path=str(artifact_dir))
        raise RuntimeError(
            f"case {case.id} uses upstream.artifact_mode=vendor but has no materialized artifact"
        )

    base_source = _source_from_upstream(case_dir, case.upstream)

    if configured_mode == ArtifactMode.POINTER:
        return base_source

    if configured_mode == ArtifactMode.HYBRID:
        if upstream.overlay_artifact:
            if not materialized:
                raise RuntimeError(
                    f"case {case.id} uses upstream.overlay_artifact=true but has no materialized artifact"
                )
            return OverlaySource(
                base=base_source,
                overlay=LocalSource(path=str(artifact_dir)),
            )
        return base_source

    raise RuntimeError(
        f"unsupported upstream artifact mode for case {case.id}: {configured_mode}"
    )


def _source_from_upstream(case_dir: Path, upstream: UpstreamConfig) -> BenchSource:
    if upstream.source_type == UpstreamSourceType.GIT and upstream.url:
        return GitSource(url=upstream.url, ref=upstream.ref)
    if upstream.source_type == UpstreamSourceType.LOCAL and upstream.path:
        return LocalSource(path=str(_resolve_upstream_path(case_dir, upstream.path)))
    if upstream.source_type == UpstreamSourceType.ARCHIVE:
        if upstream.url:
            return ArchiveSource(url=upstream.url)
        if upstream.path:
            return ArchiveSource(path=str(_resolve_upstream_path(case_dir, upstream.path)))
    raise RuntimeError(
        f"no usable upstream source "
        f"(source_type={upstream.source_type.value!r})"
    )


def _resolve_upstream_path(case_dir: Path, upstream_path: str) -> Path:
    path = Path(upstream_path)
    return path if path.is_absolute() else (case_dir / path).resolve()


def _all_registered_case_dirs(project_state: ProjectState) -> list[Path]:
    dirs: list[Path] = []
    for entry in project_state.registry.cases.values():
        path = Path(entry.path)
        if not path.is_absolute():
            path = (project_state.root / path).resolve()
        if path.is_dir() and (path / "case.toml").is_file():
            dirs.append(path)
    return sorted(dirs)
