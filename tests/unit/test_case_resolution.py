from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import cli_main
from evaluator.registry import CaseResolutionError, resolve_case_ref
from project_config import ProjectState, load_project_config


def _make_case(path: Path) -> Path:
	path.mkdir(parents=True)
	(path / "case.toml").write_text('id = "x"\n', encoding="utf-8")
	return path.resolve()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
	"""Builds a workspace and makes its root the current directory."""
	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	root = tmp_path / "ws"
	root.mkdir()
	(root / "aebench.toml").write_text("", encoding="utf-8")
	(root / "cases.json").write_text(
		json.dumps(
			{
				"cases_dir": "cases",
				"cases": {
					"listed": {"path": "cases/listed"},
					"moved": {"path": "elsewhere/moved"},
				},
			}
		),
		encoding="utf-8",
	)
	_make_case(root / "cases" / "listed")
	_make_case(root / "cases" / "unlisted")
	_make_case(root / "elsewhere" / "moved")
	_make_case(root / "drafts" / "loose")
	monkeypatch.chdir(root)
	return root.resolve()


def _state(root: Path) -> ProjectState:
	return load_project_config(root)


@pytest.mark.parametrize(
	"ref",
	["cases/listed", "./cases/listed", "listed"],
)
def test_relative_paths_and_registered_ids_resolve_to_the_case(workspace: Path, ref: str) -> None:
	assert resolve_case_ref(ref, project_state=_state(workspace)) == workspace / "cases" / "listed"


def test_absolute_path_resolves_to_itself(workspace: Path) -> None:
	ref = str(workspace / "cases" / "listed")

	assert resolve_case_ref(ref, project_state=_state(workspace)) == workspace / "cases" / "listed"


def test_registered_id_uses_the_cases_json_path(workspace: Path) -> None:
	assert (
		resolve_case_ref("moved", project_state=_state(workspace))
		== workspace / "elsewhere" / "moved"
	)


def test_unregistered_case_resolves_by_path(workspace: Path) -> None:
	state = _state(workspace)

	assert resolve_case_ref("drafts/loose", project_state=state) == workspace / "drafts" / "loose"
	assert resolve_case_ref("cases/unlisted", project_state=state) == (
		workspace / "cases" / "unlisted"
	)


def test_unregistered_id_in_the_cases_dir_resolves(workspace: Path) -> None:
	assert (
		resolve_case_ref("unlisted", project_state=_state(workspace))
		== workspace / "cases" / "unlisted"
	)


def test_paths_relative_to_the_root_resolve_from_a_subdirectory(
	workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.chdir(workspace / "drafts")
	state = load_project_config(Path.cwd())

	assert resolve_case_ref("loose", project_state=state) == workspace / "drafts" / "loose"
	assert resolve_case_ref("cases/listed", project_state=state) == workspace / "cases" / "listed"


@pytest.mark.parametrize("ref", ["nope", "cases/nope", "cases/cases/listed"])
def test_unknown_ref_raises_a_clear_error(workspace: Path, ref: str) -> None:
	with pytest.raises(CaseResolutionError, match=f"cannot resolve case reference: {ref}"):
		resolve_case_ref(ref, project_state=_state(workspace))


def test_registered_id_with_a_missing_dir_names_the_registered_path(workspace: Path) -> None:
	(workspace / "elsewhere" / "moved" / "case.toml").unlink()

	with pytest.raises(CaseResolutionError, match="elsewhere/moved"):
		resolve_case_ref("moved", project_state=_state(workspace))


def test_case_template_writes_into_the_referenced_case(workspace: Path) -> None:
	assert cli_main(["case", "template", "cases/unlisted"]) == 0

	assert (workspace / "cases" / "unlisted" / "oracles").is_dir()
	assert not (workspace / "cases" / "cases").exists()


def test_case_command_reports_an_unknown_ref(
	workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
	assert cli_main(["case", "validate", "cases/nope"]) == 2

	assert "cannot resolve case reference: cases/nope" in capsys.readouterr().err
	assert not (workspace / "cases" / "cases").exists()


def test_case_init_places_a_new_case_in_the_cases_dir_or_target_dir(workspace: Path) -> None:
	assert cli_main(["case", "init", "--blank", "--id", "fresh"]) == 0
	assert cli_main(["case", "init", "--blank", "--id", "other", "--target-dir", "drafts/new"]) == 0

	assert (workspace / "cases" / "fresh" / "case.toml").is_file()
	assert (workspace / "drafts" / "new" / "case.toml").is_file()


def test_case_init_prints_the_new_case_dir(
	workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
	assert cli_main(["case", "init", "--blank", "--id", "fresh"]) == 0

	assert f"Initialized case bundle: {workspace / 'cases' / 'fresh'}\n" in capsys.readouterr().out
