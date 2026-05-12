from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import urlparse
from urllib.request import urlopen

from git import ensure_git_checkout, touch_git_checkout
from models import ArchiveSource, GitSource, LocalSource, OverlaySource, TaskConfig
from project_config import ProjectState, load_project_config
from utils import safe_name


def prepare_workspace(task: TaskConfig, input_file: Path, workspace_root: Path) -> Path:
    input_dir = input_file.resolve().parent
    project_state = load_project_config(input_dir)
    workspace_root.mkdir(parents=True, exist_ok=True)
    target_dir = workspace_root / safe_name(task.id)
    return _materialize(task.require_source(), input_dir, target_dir, project_state=project_state)


def _materialize(
    source: LocalSource | GitSource | ArchiveSource | OverlaySource,
    input_dir: Path,
    target_dir: Path,
    *,
    project_state: ProjectState | None,
) -> Path:
    if isinstance(source, LocalSource):
        return _copy_local_source(source, input_dir, target_dir)
    if isinstance(source, GitSource):
        return _prepare_git_source(source, target_dir, project_state=project_state)
    if isinstance(source, ArchiveSource):
        return _prepare_archive_source(source, input_dir, target_dir)
    if isinstance(source, OverlaySource):
        base = _materialize(source.base, input_dir, target_dir, project_state=project_state)
        _merge_tree(_local_source_root(source.overlay, input_dir), base)
        return base
    raise TypeError(f"unsupported source type: {type(source)!r}")


def _local_source_root(source: LocalSource, input_dir: Path) -> Path:
    root = Path(source.path)
    root = root if root.is_absolute() else input_dir / root
    root = root.resolve()
    if source.subdir:
        root = _resolve_subdir_under(root, source.subdir, "local source")
    if not root.is_dir():
        raise RuntimeError(f"local source path does not exist: {root}")
    return root


def _copy_local_source(source: LocalSource, input_dir: Path, target_dir: Path) -> Path:
    root = _local_source_root(source, input_dir)
    _replace_dir(target_dir)
    shutil.copytree(root, target_dir, symlinks=False)
    return target_dir


def _prepare_git_source(source: GitSource, target_dir: Path, *, project_state: ProjectState | None) -> Path:
    _replace_dir(target_dir)
    checkout = ensure_git_checkout(source.url, source.ref, project_state=project_state)
    _clone_cached_checkout(checkout.repo_store, checkout.resolved_ref, target_dir)
    touch_git_checkout(checkout.checkout_path)
    return _resolve_subdir_under(target_dir, source.subdir, "git source") if source.subdir else target_dir


def _clone_cached_checkout(repo_store: Path, resolved_ref: str, target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "clone", "--no-checkout", str(repo_store), str(target_dir)], capture_output=True, text=True, timeout=600, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git clone from cache failed: {result.stderr or result.stdout}")
    checkout = subprocess.run(["git", "-C", str(target_dir), "checkout", resolved_ref], capture_output=True, text=True, timeout=180, check=False)
    if checkout.returncode != 0:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RuntimeError(f"git checkout {resolved_ref} failed: {checkout.stderr or checkout.stdout}")


def _prepare_archive_source(source: ArchiveSource, input_dir: Path, target_dir: Path) -> Path:
    _replace_dir(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path, local_path = _fetch_archive(source, input_dir)
    try:
        if zipfile.is_zipfile(archive_path):
            _extract_zip_archive(archive_path, target_dir)
        elif tarfile.is_tarfile(archive_path):
            _extract_tar_archive(archive_path, target_dir)
        else:
            raise RuntimeError("unsupported archive format")
    finally:
        if archive_path != local_path and archive_path.exists():
            archive_path.unlink()
    return _resolve_extracted_root(target_dir, source.subdir)


def _fetch_archive(source: ArchiveSource, input_dir: Path) -> tuple[Path, Path]:
    if source.path is not None:
        path = Path(source.path)
        local_path = path if path.is_absolute() else (input_dir / path).resolve()
        if not local_path.is_file():
            raise RuntimeError(f"archive source not found: {local_path}")
        return local_path, local_path
    if source.url is None:
        raise RuntimeError("archive source must include path or url")
    fd, temp_name = tempfile.mkstemp(prefix="ae_archive_", suffix=_archive_temp_suffix(source.url))
    os.close(fd)
    temp_path = Path(temp_name)
    with urlopen(source.url, timeout=180) as src, temp_path.open("wb") as out:
        shutil.copyfileobj(cast(BinaryIO, src), out)
    return temp_path, temp_path


def _archive_temp_suffix(url: str) -> str:
    suffix = "".join(Path(urlparse(url).path).suffixes) or ".archive"
    return suffix if suffix.startswith(".") else f".{suffix}"


def _extract_zip_archive(archive_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as handle:
        for info in handle.infolist():
            if ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                raise RuntimeError(f"archive contains symlink entry: {info.filename}")
            target = _safe_join_under(target_dir, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info, "r") as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
            _apply_zip_mode(target, info)


def _extract_tar_archive(archive_path: Path, target_dir: Path) -> None:
    with tarfile.open(archive_path, mode="r:*") as handle:
        for member in handle.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"archive contains symlink entry: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise RuntimeError(f"archive contains unsupported entry type: {member.name}")
            target = _safe_join_under(target_dir, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, member.mode & 0o7777)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = handle.extractfile(member)
            if reader is not None:
                with reader, target.open("wb") as out:
                    shutil.copyfileobj(reader, out)
                os.chmod(target, member.mode & 0o7777)


def _merge_tree(source_root: Path, target_root: Path) -> None:
    for entry in source_root.iterdir():
        if entry.is_symlink():
            raise RuntimeError(f"overlay source contains symlink: {entry}")
        target = target_root / entry.name
        if entry.is_dir():
            if target.is_file() or target.is_symlink():
                target.unlink()
            shutil.copytree(entry, target, dirs_exist_ok=True, symlinks=False)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            shutil.copy2(entry, target)


def _safe_join_under(root: Path, member_name: str) -> Path:
    target = (root / member_name.replace("\\", "/")).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"archive contains unsafe path: {member_name}") from exc
    return target


def _apply_zip_mode(target: Path, info: zipfile.ZipInfo) -> None:
    mode = (info.external_attr >> 16) & 0o7777
    if mode:
        os.chmod(target, mode)


def _resolve_extracted_root(extract_dir: Path, subdir: str | None) -> Path:
    if subdir:
        target = _resolve_subdir_under(extract_dir, subdir, "archive source")
        if not target.is_dir():
            raise RuntimeError(f"archive subdir does not exist: {subdir}")
        return target
    entries = [entry for entry in extract_dir.iterdir() if entry.name != "__MACOSX"]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_dir


def _resolve_subdir_under(root: Path, subdir: str | None, label: str) -> Path:
    if subdir is None:
        return root
    rel = Path(subdir)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"{label} subdir escapes source root: {subdir}")
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} subdir escapes source root: {subdir}") from exc
    return target


def _replace_dir(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
