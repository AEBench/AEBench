"""Recording the artifact's own files before a run and checking them afterwards."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from runtime.monitoring import (
	TRACKED_FILES_BASENAME,
	TRACKED_FILES_DIFF_BASENAME,
	check_tracked_files,
	write_tracked_files,
)


def _git(repo: Path, *args: str) -> str:
	return subprocess.run(
		["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
		capture_output=True,
		text=True,
		check=True,
	).stdout.strip()


def _artifact_checkout(tmp_path: Path) -> Path:
	repo = tmp_path / "workspace"
	(repo / "scripts").mkdir(parents=True)
	(repo / "src.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
	(repo / "scripts" / "aggregate.py").write_text("print('ok')\n", encoding="utf-8")
	os.symlink("src.c", repo / "link.c")
	_git(repo, "init", "-q")
	_git(repo, "add", ".")
	_git(repo, "commit", "-q", "-m", "artifact")
	return repo


def _recorded(trace: Path) -> dict[str, object]:
	return json.loads((trace / TRACKED_FILES_BASENAME).read_text(encoding="utf-8"))


def test_a_checkout_records_only_its_git_tracked_files(tmp_path: Path) -> None:
	repo = _artifact_checkout(tmp_path)
	(repo / "build").mkdir()
	(repo / "build" / "out.o").write_bytes(b"\0")
	trace = tmp_path / "trace"
	trace.mkdir()

	write_tracked_files(repo, trace)

	recorded = _recorded(trace)
	assert recorded["source"] == "git"
	assert recorded["commit"] == _git(repo, "rev-parse", "HEAD")
	files = recorded["files"]
	assert isinstance(files, dict)
	assert sorted(files) == ["link.c", "scripts/aggregate.py", "src.c"]
	source = files["src.c"]
	assert source["sha256"] == hashlib.sha256((repo / "src.c").read_bytes()).hexdigest()
	assert source["size"] == (repo / "src.c").stat().st_size
	assert source["mtime_ns"] == (repo / "src.c").stat().st_mtime_ns
	assert files["link.c"]["symlink"] is True


def test_the_check_reports_modified_and_deleted_tracked_files(tmp_path: Path) -> None:
	repo = _artifact_checkout(tmp_path)
	trace = tmp_path / "trace"
	trace.mkdir()
	write_tracked_files(repo, trace)

	# Same size and restored mtime: only the hash can tell.
	script = repo / "scripts" / "aggregate.py"
	before = script.stat()
	script.write_text("print('no')\n", encoding="utf-8")
	os.utime(script, ns=(before.st_atime_ns, before.st_mtime_ns))
	(repo / "src.c").unlink()
	(repo / "new.sh").write_text("echo new\n", encoding="utf-8")

	check = check_tracked_files(repo, trace)

	assert check is not None
	assert check.tracked == 3
	assert (check.modified_count, check.deleted_count) == (1, 1)
	assert check.created_count == 0
	diff = json.loads((trace / TRACKED_FILES_DIFF_BASENAME).read_text(encoding="utf-8"))
	assert diff == {
		"tracked": 3,
		"modified": ["scripts/aggregate.py"],
		"deleted": ["src.c"],
		"mode_changed": [],
	}


def test_a_chmod_is_reported_as_a_mode_change_not_a_modification(tmp_path: Path) -> None:
	repo = _artifact_checkout(tmp_path)
	script = repo / "scripts" / "aggregate.py"
	script.chmod(0o755)
	trace = tmp_path / "trace"
	trace.mkdir()
	write_tracked_files(repo, trace)
	assert _recorded(trace)["files"]["scripts/aggregate.py"]["mode"] == 0o755  # type: ignore[index]

	script.chmod(0o644)

	check = check_tracked_files(repo, trace)

	assert check is not None
	assert (check.modified_count, check.deleted_count, check.mode_changed_count) == (0, 0, 1)
	diff = json.loads((trace / TRACKED_FILES_DIFF_BASENAME).read_text(encoding="utf-8"))
	assert diff["mode_changed"] == ["scripts/aggregate.py"]
	assert diff["modified"] == []


def test_a_list_recorded_without_modes_reports_no_mode_changes(tmp_path: Path) -> None:
	repo = _artifact_checkout(tmp_path)
	trace = tmp_path / "trace"
	trace.mkdir()
	write_tracked_files(repo, trace)
	recorded = _recorded(trace)
	for state in recorded["files"].values():  # type: ignore[attr-defined]
		del state["mode"]
	(trace / TRACKED_FILES_BASENAME).write_text(json.dumps(recorded), encoding="utf-8")
	(repo / "src.c").chmod(0o600)

	check = check_tracked_files(repo, trace)

	assert check is not None
	assert check.mode_changed_count == 0


def test_an_untouched_artifact_reports_no_changes(tmp_path: Path) -> None:
	repo = _artifact_checkout(tmp_path)
	trace = tmp_path / "trace"
	trace.mkdir()
	write_tracked_files(repo, trace)
	# A build writing beside the sources is not a change to the artifact.
	(repo / "a.out").write_bytes(b"\0")

	check = check_tracked_files(repo, trace)

	assert check is not None
	assert (check.modified_count, check.deleted_count, check.mode_changed_count) == (0, 0, 0)


def test_a_directory_that_is_not_a_checkout_records_every_file(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	(workspace / "data").mkdir(parents=True)
	(workspace / "run.sh").write_text("echo run\n", encoding="utf-8")
	(workspace / "data" / "input.csv").write_text("a,b\n", encoding="utf-8")
	trace = tmp_path / "trace"
	trace.mkdir()

	write_tracked_files(workspace, trace)

	recorded = _recorded(trace)
	assert recorded["source"] == "tree"
	assert recorded["commit"] is None
	assert sorted(recorded["files"]) == ["data/input.csv", "run.sh"]  # type: ignore[arg-type]


def test_the_check_is_skipped_when_nothing_was_recorded(tmp_path: Path) -> None:
	assert check_tracked_files(tmp_path, tmp_path) is None
