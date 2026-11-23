"""Translate an AEBench case into a runtime task."""

from __future__ import annotations

from pathlib import Path

from evaluator import artifact_dir_for, local_artifact_error
from models import (
	ArchiveSource,
	BenchSource,
	CaseConfig,
	GitSource,
	LocalSource,
	OverlaySource,
	TaskConfig,
	UpstreamConfig,
	UpstreamSourceType,
)
from project_config import ArtifactMode


def task_from_case(case_dir: Path, case: CaseConfig) -> TaskConfig:
	return case.run.model_copy(
		update={
			"source": _source_from_case(case_dir.resolve(), case),
			"case_brief": case.case_brief,
		}
	)


def _source_from_case(case_dir: Path, case: CaseConfig) -> BenchSource:
	upstream = case.upstream
	error = local_artifact_error(case_dir, upstream)
	if error is not None:
		raise RuntimeError(f"case {case.id}: {error}")
	if not upstream.uses_local_artifact:
		return _source_from_upstream(case_dir, upstream)

	local = LocalSource(path=str(artifact_dir_for(case_dir)))
	if upstream.artifact_mode == ArtifactMode.LOCAL:
		return local
	return OverlaySource(base=_source_from_upstream(case_dir, upstream), overlay=local)


def _source_from_upstream(case_dir: Path, upstream: UpstreamConfig) -> BenchSource:
	if upstream.source_type == UpstreamSourceType.GIT and upstream.url:
		return GitSource(url=upstream.url, ref=upstream.ref)
	if upstream.source_type == UpstreamSourceType.LOCAL and upstream.path:
		return LocalSource(path=str(_resolve(case_dir, upstream.path)))
	if upstream.source_type == UpstreamSourceType.ARCHIVE:
		if upstream.url:
			return ArchiveSource(url=upstream.url)
		if upstream.path:
			return ArchiveSource(path=str(_resolve(case_dir, upstream.path)))

	source_type = upstream.source_type.value if upstream.source_type is not None else "none"
	raise RuntimeError(f"no usable upstream source ({source_type})")


def _resolve(case_dir: Path, value: str) -> Path:
	path = Path(value)
	return path.resolve() if path.is_absolute() else (case_dir / path).resolve()


__all__ = ["task_from_case"]
