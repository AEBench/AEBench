"""Translate an AEBench case into a runtime task."""

from __future__ import annotations

from pathlib import Path

from evaluator import artifact_dir_for, has_local_artifact
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
	artifact_dir = artifact_dir_for(case_dir)

	if upstream.artifact_mode == ArtifactMode.UPSTREAM:
		if not has_local_artifact(case_dir):
			raise RuntimeError(f"case {case.id} has no upstream artifact")
		return LocalSource(path=str(artifact_dir))

	base = _source_from_upstream(case_dir, upstream)

	if upstream.artifact_mode == ArtifactMode.OVERLAY and upstream.overlay_artifact:
		if not has_local_artifact(case_dir):
			raise RuntimeError(f"case {case.id} has no artifact overlay")
		return OverlaySource(
			base=base,
			overlay=LocalSource(path=str(artifact_dir)),
		)

	return base


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