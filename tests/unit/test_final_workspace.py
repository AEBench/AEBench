"""Saving the workspace the agent left: final.diff, final_files.json and an archive."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from runtime import monitoring
from runtime.monitoring import (
	FINAL_DIFF_BASENAME,
	FINAL_FILES_BASENAME,
	TRACE_META_BASENAME,
	capture_final_workspace,
	write_trace_meta,
	write_tracked_files,
)


def _git(repo: Path, *args: str) -> str:
	return subprocess.run(
		["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
		capture_output=True,
		text=True,
		check=True,
	).stdout.strip()


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
	repo = tmp_path / "workspace"
	repo.mkdir()
	(repo / "run.sh").write_text("echo run\n", encoding="utf-8")
	(repo / "plot.py").write_text("print(1)\n", encoding="utf-8")
	(repo / ".gitignore").write_text("build/\n", encoding="utf-8")
	_git(repo, "init", "-q")
	_git(repo, "add", ".")
	_git(repo, "commit", "-q", "-m", "artifact")
	trace = tmp_path / "trace"
	trace.mkdir()
	write_tracked_files(repo, trace)
	return repo, trace


def _entries(trace: Path) -> dict[str, dict[str, object]]:
	listed = json.loads((trace / FINAL_FILES_BASENAME).read_text(encoding="utf-8"))
	return {entry["path"]: entry for entry in listed}


def _digest(path: Path) -> str:
	if path.is_symlink():
		return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
	return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_state(repo: Path) -> tuple[int, int]:
	objects = sum(len(files) for _, _, files in os.walk(repo / ".git" / "objects"))
	return (repo / ".git" / "index").stat().st_mtime_ns, objects


def test_the_final_diff_reproduces_the_listed_hashes_on_a_clean_checkout(tmp_path: Path) -> None:
	repo, trace = _workspace(tmp_path)
	commit = _git(repo, "rev-parse", "HEAD")
	(repo / "run.sh").write_text("echo faked\n", encoding="utf-8")
	(repo / "plot.py").unlink()
	(repo / "results.csv").write_text("a,b\n1,2\n", encoding="utf-8")
	(repo / "build").mkdir()
	(repo / "build" / "out.bin").write_bytes(bytes(range(256)))
	os.symlink("run.sh", repo / "latest")

	info = capture_final_workspace(repo, trace)

	assert info is not None
	entries = _entries(trace)
	assert {path: entry["status"] for path, entry in entries.items()} == {
		"run.sh": "M",
		"plot.py": "D",
		"results.csv": "A",
		"build/out.bin": "A",
		"latest": "A",
	}
	assert all(entry["in_diff"] for entry in entries.values())

	clean = tmp_path / "clean"
	subprocess.run(["git", "clone", "-q", str(repo), str(clean)], check=True)
	_git(clean, "checkout", "-q", commit)
	_git(clean, "apply", "--binary", str(trace / FINAL_DIFF_BASENAME))
	for path, entry in entries.items():
		if entry["status"] == "D":
			assert not os.path.lexists(clean / path)
		else:
			assert _digest(clean / path) == entry["sha256"]
			assert entry["sha256"] == _digest(repo / path)


def test_the_workspace_repository_is_left_untouched(tmp_path: Path) -> None:
	repo, trace = _workspace(tmp_path)
	(repo / "run.sh").write_text("echo faked\n", encoding="utf-8")
	(repo / "new.txt").write_text("new\n", encoding="utf-8")
	before = _git_state(repo)

	assert capture_final_workspace(repo, trace) is not None

	assert _git_state(repo) == before
	assert _git(repo, "status", "--porcelain") == "M run.sh\n?? new.txt"


def test_the_repository_config_of_the_agent_runs_nothing(tmp_path: Path) -> None:
	repo, trace = _workspace(tmp_path)
	marker = tmp_path / "ran"
	script = tmp_path / "evil.sh"
	script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n", encoding="utf-8")
	script.chmod(0o755)
	_git(repo, "config", "core.fsmonitor", str(script))
	_git(repo, "config", "diff.external", str(script))
	_git(repo, "config", "filter.evil.clean", str(script))
	_git(repo, "config", "diff.evil.textconv", str(script))
	(repo / ".gitattributes").write_text("* filter=evil diff=evil\n", encoding="utf-8")
	(repo / "run.sh").write_text("echo faked\n", encoding="utf-8")

	assert capture_final_workspace(repo, trace) is not None

	assert not marker.exists()
	assert "echo faked" in (trace / FINAL_DIFF_BASENAME).read_text(encoding="utf-8")


def test_an_over_cap_file_is_listed_but_left_out_of_the_diff(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setattr(monitoring, "MAX_DIFF_FILE_BYTES", 1024)
	repo, trace = _workspace(tmp_path)
	(repo / "data.bin").write_bytes(b"\1" * 4096)

	info = capture_final_workspace(repo, trace)

	assert info is not None and info.omitted == 1
	entry = _entries(trace)["data.bin"]
	assert entry["in_diff"] is False
	assert entry["reason_omitted"] == "over_cap"
	assert entry["size"] == 4096
	assert entry["sha256"] == _digest(repo / "data.bin")
	assert "data.bin" not in (trace / FINAL_DIFF_BASENAME).read_text(encoding="utf-8")


def test_secret_files_are_left_out_and_secret_values_redacted(tmp_path: Path) -> None:
	repo, trace = _workspace(tmp_path)
	(repo / ".env").write_text("OPENAI_API_KEY=sk-live-0123456789\n", encoding="utf-8")
	(repo / "id_rsa").write_text("private\n", encoding="utf-8")
	(repo / "notes.txt").write_text("key is sk-live-0123456789\n", encoding="utf-8")

	info = capture_final_workspace(repo, trace, {"OPENAI_API_KEY": "sk-live-0123456789"})

	assert info is not None
	diff = (trace / FINAL_DIFF_BASENAME).read_text(encoding="utf-8")
	assert "sk-live-0123456789" not in diff
	assert "[REDACTED:OPENAI_API_KEY]" in diff
	entries = _entries(trace)
	assert entries[".env"]["reason_omitted"] == "secret_pattern"
	assert entries[".env"]["sha256"] is None
	assert entries["id_rsa"]["reason_omitted"] == "secret_pattern"
	assert info.workspace_path is not None
	archived = _archive_names(Path(info.workspace_path))
	assert "notes.txt" in archived
	assert ".env" not in archived and "id_rsa" not in archived
	assert not any(name.startswith(".git/objects/") for name in archived)


def _archive_names(path: Path) -> set[str]:
	if path.suffix == ".zst":
		data = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True).stdout
		listing = path.with_suffix(".tar")
		listing.write_bytes(data)
		path = listing
	with tarfile.open(path) as archive:
		return set(archive.getnames())


def test_no_baseline_commit_writes_nothing(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	(workspace / "a.txt").write_text("a\n", encoding="utf-8")
	trace = tmp_path / "trace"
	trace.mkdir()
	write_tracked_files(workspace, trace)

	assert capture_final_workspace(workspace, trace) is None
	assert not (trace / FINAL_DIFF_BASENAME).exists()


def test_trace_meta_names_the_run_token_and_no_version(tmp_path: Path) -> None:
	trace = tmp_path / "a1b2c3d4"
	trace.mkdir()

	write_trace_meta(trace)

	meta = json.loads((trace / TRACE_META_BASENAME).read_text(encoding="utf-8"))
	assert meta == {"run_token": "a1b2c3d4", "start_events": False}
