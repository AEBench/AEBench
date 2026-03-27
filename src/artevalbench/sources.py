from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import urlparse
from urllib.request import urlopen

from .cache import ensure_git_checkout, touch_git_checkout
from .models import ArchiveSource, GitSource, LocalSource, OverlaySource, RuntimeMode, TaskSpec
from .project_config import ProjectConfigState, load_project_config
from .utils import resolve_ephemeral_workspace_root, safe_name

__all__ = [
    "prepare_workspace",
    "prepare_overlay_source",
    "create_ephemeral_workspace_root",
    "prepare_git_source",
    "prepare_local_source",
    "materialize_local_source",
    "prepare_archive_source",
]


def prepare_workspace(task: TaskSpec, input_file: Path, workspace_root: Path) -> Path:
    input_dir = input_file.resolve().parent
    project_state = load_project_config(input_dir)
    workspace_root.mkdir(parents=True, exist_ok=True)

    source = task.source
    if isinstance(source, OverlaySource):
        target_dir = workspace_root / safe_name(task.id)
        return prepare_overlay_source(source, input_dir, target_dir, project_state=project_state)
    if isinstance(source, LocalSource):
        if task.runtime.mode == RuntimeMode.DOCKER:
            target_dir = workspace_root / safe_name(task.id)
            return materialize_local_source(source, input_dir, target_dir)
        return prepare_local_source(source, input_dir)

    target_dir = workspace_root / safe_name(task.id)
    if isinstance(source, GitSource):
        return prepare_git_source(source, target_dir, project_state=project_state)
    if isinstance(source, ArchiveSource):
        return prepare_archive_source(source, input_dir, target_dir)
    raise TypeError(f"unsupported source type: {type(source)!r}")


def prepare_overlay_source(
    source: OverlaySource,
    input_dir: Path,
    target_dir: Path,
    *,
    project_state: ProjectConfigState | None = None,
) -> Path:
    base_root = _materialize_source(
        source.base,
        input_dir,
        target_dir,
        project_state=project_state,
    )
    overlay_root = prepare_local_source(source.overlay, input_dir)
    _merge_tree(overlay_root, base_root)
    return base_root


def _materialize_source(
    source: LocalSource | GitSource | ArchiveSource,
    input_dir: Path,
    target_dir: Path,
    *,
    project_state: ProjectConfigState | None = None,
) -> Path:
    if isinstance(source, LocalSource):
        return materialize_local_source(source, input_dir, target_dir)
    if isinstance(source, GitSource):
        return prepare_git_source(source, target_dir, project_state=project_state)
    if isinstance(source, ArchiveSource):
        return prepare_archive_source(source, input_dir, target_dir)
    raise TypeError(f"unsupported base source type: {type(source)!r}")


def _merge_tree(source_root: Path, target_root: Path) -> None:
    for entry in source_root.iterdir():
        target = target_root / entry.name
        if entry.is_dir():
            if target.is_file() or target.is_symlink():
                target.unlink()
            shutil.copytree(entry, target, dirs_exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.is_symlink() or target.is_file():
            target.unlink()
        shutil.copy2(entry, target)


def create_ephemeral_workspace_root(task_id: str) -> Path:
    root_parent = resolve_ephemeral_workspace_root()
    root_parent.mkdir(parents=True, exist_ok=True)
    root = Path(
        tempfile.mkdtemp(prefix=f"ae_workspace_{safe_name(task_id)}_", dir=str(root_parent))
    )
    workspace_root = root / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root


def prepare_git_source(
    source: GitSource,
    target_dir: Path,
    *,
    project_state: ProjectConfigState | None = None,
) -> Path:
    if target_dir.exists() and any(target_dir.iterdir()):
        return _resolve_subdir(target_dir, source.subdir)
    if target_dir.exists():
        target_dir.rmdir()

    resolved = ensure_git_checkout(source.url, source.ref, project_state=project_state)
    _clone_cached_checkout(resolved.repo_store, resolved.resolved_ref, target_dir)
    touch_git_checkout(resolved.checkout_path)
    return _resolve_subdir(target_dir, source.subdir)


def _clone_cached_checkout(repo_store: Path, resolved_ref: str, target_dir: Path) -> None:
    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--no-checkout", str(repo_store), str(target_dir)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone from cache failed: {result.stderr or result.stdout}")

    checkout = subprocess.run(
        ["git", "-C", str(target_dir), "checkout", resolved_ref],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if checkout.returncode == 0:
        return

    shutil.rmtree(target_dir, ignore_errors=True)
    raise RuntimeError(f"git checkout {resolved_ref} failed: {checkout.stderr or checkout.stdout}")


def _resolve_subdir(root: Path, subdir: str | None) -> Path:
    if not subdir:
        return root
    target = (root / subdir).resolve()
    if not target.is_dir():
        raise RuntimeError(f"git source subdir does not exist: {subdir}")
    return target


def prepare_local_source(source: LocalSource, input_dir: Path) -> Path:
    path = Path(source.path)
    root = path if path.is_absolute() else (input_dir / path)
    root = root.resolve()
    if source.subdir:
        root = (root / source.subdir).resolve()
    if not root.is_dir():
        raise RuntimeError(f"local source path does not exist: {root}")
    return root


def materialize_local_source(source: LocalSource, input_dir: Path, target_dir: Path) -> Path:
    root = prepare_local_source(source, input_dir)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(root, target_dir)
    return target_dir


def prepare_archive_source(source: ArchiveSource, input_dir: Path, target_dir: Path) -> Path:
    if target_dir.exists() and any(target_dir.iterdir()):
        return _resolve_extracted_root(target_dir, source.subdir)
    if target_dir.exists():
        target_dir.rmdir()
    target_dir.mkdir(parents=True, exist_ok=True)

    archive_path = _materialize_archive(source, input_dir)
    try:
        if zipfile.is_zipfile(archive_path):
            _extract_zip_archive(archive_path, target_dir)
        elif tarfile.is_tarfile(archive_path):
            _extract_tar_archive(archive_path, target_dir)
        else:
            raise RuntimeError("unsupported archive format")
    finally:
        if archive_path != _local_source_path(source, input_dir) and archive_path.exists():
            archive_path.unlink()
    return _resolve_extracted_root(target_dir, source.subdir)


def _local_source_path(source: ArchiveSource, input_dir: Path) -> Path:
    if source.path is None:
        return Path()
    path = Path(source.path)
    return path if path.is_absolute() else (input_dir / path).resolve()


def _materialize_archive(source: ArchiveSource, input_dir: Path) -> Path:
    if source.path is not None:
        path = _local_source_path(source, input_dir)
        if not path.is_file():
            raise RuntimeError(f"archive source not found: {path}")
        return path

    if source.url is None:
        raise RuntimeError("archive source must include path or url")
    url = source.url

    fd, temp_name = tempfile.mkstemp(prefix="ae_archive_", suffix=_archive_temp_suffix(url))
    os.close(fd)
    temp_path = Path(temp_name)
    with urlopen(url, timeout=180) as src, temp_path.open("wb") as out:
        shutil.copyfileobj(cast(BinaryIO, src), out)
    return temp_path


def _archive_temp_suffix(url: str) -> str:
    suffix = "".join(Path(urlparse(url).path).suffixes) or ".archive"
    if not suffix.startswith("."):
        return f".{suffix}"
    return suffix


def _safe_join_under(root: Path, member_name: str) -> Path:
    target = (root / member_name.replace("\\", "/")).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise RuntimeError(f"archive contains unsafe path: {member_name}")
    return target


def _extract_zip_archive(archive_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as handle:
        for info in handle.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise RuntimeError(f"archive contains symlink entry: {info.filename}")
            target = _safe_join_under(target_dir, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


def _extract_tar_archive(archive_path: Path, target_dir: Path) -> None:
    with tarfile.open(archive_path, mode="r:*") as handle:
        for member in handle.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"archive contains symlink entry: {member.name}")
            target = _safe_join_under(target_dir, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                reader = handle.extractfile(member)
                if reader is None:
                    continue
                with reader, target.open("wb") as out:
                    shutil.copyfileobj(reader, out)


def _resolve_extracted_root(extract_dir: Path, subdir: str | None) -> Path:
    if subdir:
        target = (extract_dir / subdir).resolve()
        if not target.is_dir():
            raise RuntimeError(f"archive subdir does not exist: {subdir}")
        return target
    entries = [entry for entry in extract_dir.iterdir() if entry.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_dir
