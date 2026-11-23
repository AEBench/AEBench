from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from evaluator.authoring.case_spec import write_case_spec
from evaluator.authoring.validate import validate_case_bundle
from evaluator.loader import load_case_spec
from models import ArtifactMode, LocalSource, OverlaySource, UpstreamConfig, UpstreamSourceType
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


@pytest.mark.parametrize("artifact", ["missing", "empty", "present"])
@pytest.mark.parametrize(
	("mode", "overlay_artifact"),
	[
		(None, False),
		(ArtifactMode.UPSTREAM, False),
		(ArtifactMode.LOCAL, False),
		(ArtifactMode.OVERLAY, False),
		(ArtifactMode.OVERLAY, True),
	],
)
def test_validate_and_runtime_agree_on_local_artifact(
	tmp_path: Path, mode: ArtifactMode | None, overlay_artifact: bool, artifact: str
) -> None:
	case_dir = _copy_case(tmp_path)
	(case_dir / "upstream").mkdir()
	manifest = case_dir / "case.toml"
	text = manifest.read_text(encoding="utf-8")
	upstream = ['source_type = "local"', 'path = "upstream"']
	if mode is not None:
		upstream.append(f'artifact_mode = "{mode.value}"')
	upstream.append(f"overlay_artifact = {str(overlay_artifact).lower()}")
	# The fixture's [upstream] table is last, so replace it wholesale.
	text = text[: text.index("[upstream]")] + "\n".join(["[upstream]", *upstream]) + "\n"
	manifest.write_text(text, encoding="utf-8")
	artifact_dir = case_dir / "artifact"
	shutil.rmtree(artifact_dir)
	if artifact != "missing":
		artifact_dir.mkdir()
	if artifact == "present":
		(artifact_dir / "README.md").write_text("x\n", encoding="utf-8")

	issues = {issue.code for issue in validate_case_bundle(case_dir).issues}
	try:
		task_from_case(case_dir, load_case_spec(case_dir))
	except RuntimeError:
		runtime_ok = False
	else:
		runtime_ok = True
	assert issues <= {"missing_artifact_dir"}
	assert ("missing_artifact_dir" not in issues) == runtime_ok


def test_unset_artifact_mode_is_upstream(tmp_path: Path) -> None:
	assert UpstreamConfig().artifact_mode == ArtifactMode.UPSTREAM
	assert not UpstreamConfig().uses_local_artifact
	case_dir = _copy_case(tmp_path)
	case = load_case_spec(case_dir)
	write_case_spec(case, case_dir)
	assert 'artifact_mode = "upstream"' in (case_dir / "case.toml").read_text(encoding="utf-8")
