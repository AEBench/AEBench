from __future__ import annotations

import shutil
from pathlib import Path

from evaluator.loader import load_case_spec
from models import LocalSource, OverlaySource, UpstreamSourceType
from project_config import ArtifactMode
from runtime.cases import task_from_case

_FIXTURE = (
	Path(__file__).parents[1]
	/ "integration"
	/ "mock-case"
	/ "fixture"
	/ "bundles"
	/ "mock_apt_case"
)


def _copy_case(tmp_path: Path) -> Path:
	case_dir = tmp_path / "mock_apt_case"
	shutil.copytree(_FIXTURE, case_dir)
	return case_dir


def test_local_artifact_mode_uses_case_artifact(tmp_path: Path) -> None:
	case_dir = _copy_case(tmp_path)
	case = load_case_spec(case_dir)
	case.upstream = case.upstream.model_copy(
		update={
			"artifact_mode": ArtifactMode.LOCAL,
			"source_type": None,
			"path": None,
		}
	)

	source = task_from_case(case_dir, case).source

	assert isinstance(source, LocalSource)
	assert Path(source.path) == case_dir / "artifact"


def test_upstream_artifact_mode_uses_configured_source(tmp_path: Path) -> None:
	case_dir = _copy_case(tmp_path)
	upstream_dir = case_dir / "upstream"
	upstream_dir.mkdir()
	case = load_case_spec(case_dir)
	case.upstream = case.upstream.model_copy(
		update={
			"artifact_mode": ArtifactMode.UPSTREAM,
			"source_type": UpstreamSourceType.LOCAL,
			"path": "upstream",
		}
	)

	source = task_from_case(case_dir, case).source

	assert isinstance(source, LocalSource)
	assert Path(source.path) == upstream_dir


def test_overlay_artifact_mode_combines_upstream_and_local_copy(tmp_path: Path) -> None:
	case_dir = _copy_case(tmp_path)
	upstream_dir = case_dir / "upstream"
	upstream_dir.mkdir()
	case = load_case_spec(case_dir)
	case.upstream = case.upstream.model_copy(
		update={
			"artifact_mode": ArtifactMode.OVERLAY,
			"source_type": UpstreamSourceType.LOCAL,
			"path": "upstream",
			"overlay_artifact": True,
		}
	)

	source = task_from_case(case_dir, case).source

	assert isinstance(source, OverlaySource)
	assert isinstance(source.base, LocalSource)
	assert Path(source.base.path) == upstream_dir
	assert Path(source.overlay.path) == case_dir / "artifact"
