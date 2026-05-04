from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator
from urllib.parse import urlparse

from models import UpstreamSourceType
from project_config import GitCacheConfig, ProjectState, load_project_config
from utils import safe_name

if TYPE_CHECKING:
    from models import CaseConfig

_METADATA_FILE = ".aebench-git-cache.json"
_LOCK_FILE = ".aebench-git-cache.lock"


@dataclass(frozen=True, slots=True)
class ResolvedGitCheckout:
    url: str
    requested_ref: str | None
    resolved_ref: str
    repo_key: str
    repo_store: Path
    checkout_path: Path


@dataclass(frozen=True, slots=True)
class GitCacheEntryStatus:
    repo_key: str
    url: str
    resolved_ref: str
    requested_ref: str | None
    checkout_path: Path
    repo_store: Path
    size_bytes: int
    last_accessed: datetime
    protected: bool


@dataclass(frozen=True, slots=True)
class GitCacheStatus:
    root: Path
    max_size_bytes: int
    total_size_bytes: int
    entry_count: int
    entries: list[GitCacheEntryStatus]


@dataclass(frozen=True, slots=True)
class GitCachePruneResult:
    root: Path
    max_size_bytes: int
    before_size_bytes: int
    after_size_bytes: int
    removed_entries: list[GitCacheEntryStatus]
    skipped_entries: list[GitCacheEntryStatus]
    remaining_entries: list[GitCacheEntryStatus]


@dataclass(frozen=True, slots=True)
class _CacheContext:
    state: ProjectState
    config: GitCacheConfig
    root: Path


def ensure_git_checkout(
    url: str,
    requested_ref: str | None,
    *,
    project_state: ProjectState | None = None,
    protected_paths: set[Path] | None = None,
) -> ResolvedGitCheckout:
    context = _cache_context(project_state)
    with _cache_lock(context.root):
        if context.config.prune_on_fetch:
            _prune_git_cache_locked(context, protected_paths=protected_paths)
        repo_key = _repo_key(url)
        repo_store = context.root / "repos" / repo_key / "repo"
        _ensure_repo_store(url, repo_store)
        resolved_ref = _resolve_commit(repo_store, requested_ref)
        checkout_path = context.root / "checkouts" / repo_key / resolved_ref
        if not checkout_path.exists():
            _add_worktree(repo_store, checkout_path, resolved_ref)
        return _write_metadata(
            checkout_path=checkout_path,
            repo_store=repo_store,
            url=url,
            requested_ref=requested_ref,
            resolved_ref=resolved_ref,
            repo_key=repo_key,
        )


def resolve_git_bundle_artifact(
    case_dir: Path,
    case: CaseConfig,
    *,
    project_state: ProjectState | None = None,
    protected_paths: set[Path] | None = None,
) -> ResolvedGitCheckout:
    if case.upstream.source_type != UpstreamSourceType.GIT or not case.upstream.url:
        raise RuntimeError(f"case {case.id} is not git-backed")
    context = _cache_context(project_state)
    checkout = ensure_git_checkout(
        case.upstream.url,
        case.upstream.ref,
        project_state=context.state,
        protected_paths=protected_paths,
    )
    ensure_bundle_artifact_link(case_dir, checkout.checkout_path, symlink_artifact=context.config.symlink_artifact)
    return checkout


def ensure_bundle_artifact_link(bundle_dir: Path, target_dir: Path, *, symlink_artifact: bool) -> Path:
    artifact_dir = bundle_dir / "artifact"
    target = target_dir.resolve()
    if symlink_artifact:
        if artifact_dir.is_symlink() and artifact_dir.resolve(strict=False) == target:
            _ensure_artifact_gitignore(bundle_dir)
            return artifact_dir.resolve(strict=False)
        _remove_existing_artifact_dir(artifact_dir)
        artifact_dir.symlink_to(target, target_is_directory=True)
        _ensure_artifact_gitignore(bundle_dir)
        return artifact_dir.resolve(strict=False)
    _remove_existing_artifact_dir(artifact_dir)
    shutil.copytree(target, artifact_dir, ignore=shutil.ignore_patterns(".git"))
    _ensure_artifact_gitignore(bundle_dir)
    return artifact_dir.resolve()


def touch_git_checkout(checkout_path: Path) -> None:
    checkout = read_cache_metadata(checkout_path)
    if checkout is not None:
        _write_metadata(
            checkout_path=checkout.checkout_path,
            repo_store=checkout.repo_store,
            url=checkout.url,
            requested_ref=checkout.requested_ref,
            resolved_ref=checkout.resolved_ref,
            repo_key=checkout.repo_key,
        )


def git_cache_status(project_state: ProjectState | None = None) -> GitCacheStatus:
    context = _cache_context(project_state)
    entries = _scan_entries(context.root, protected_git_checkout_paths(context.state))
    total = sum(entry.size_bytes for entry in entries)
    return GitCacheStatus(
        root=context.root,
        max_size_bytes=context.config.max_size_bytes,
        total_size_bytes=total,
        entry_count=len(entries),
        entries=sorted(entries, key=lambda entry: entry.last_accessed, reverse=True),
    )


def prune_git_cache(project_state: ProjectState | None = None, *, protected_paths: set[Path] | None = None) -> GitCachePruneResult:
    context = _cache_context(project_state)
    with _cache_lock(context.root):
        return _prune_git_cache_locked(context, protected_paths=protected_paths)


def protected_git_checkout_paths(project_state: ProjectState | None = None) -> set[Path]:
    context = _cache_context(project_state)
    protected: set[Path] = set()
    for case_dir in _known_case_dirs(context.state):
        artifact_dir = case_dir / "artifact"
        if artifact_dir.is_symlink():
            target = artifact_dir.resolve(strict=False)
            if _is_relative_to(target, context.root):
                protected.add(target.resolve(strict=False))
    return protected


def read_cache_metadata(checkout_path: Path) -> ResolvedGitCheckout | None:
    metadata_path = checkout_path / _METADATA_FILE
    if not metadata_path.is_file():
        return None
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    return ResolvedGitCheckout(
        url=str(data["url"]),
        requested_ref=None if data.get("requested_ref") is None else str(data["requested_ref"]),
        resolved_ref=str(data["resolved_ref"]),
        repo_key=str(data["repo_key"]),
        repo_store=Path(str(data["repo_store"])).resolve(),
        checkout_path=Path(str(data["checkout_path"])).resolve(),
    )


def _cache_context(project_state: ProjectState | None) -> _CacheContext:
    state = project_state or load_project_config(Path.cwd())
    root = state.config.cache.git.resolve_root(state.root)
    root.mkdir(parents=True, exist_ok=True)
    return _CacheContext(state=state, config=state.config.cache.git, root=root)


def _ensure_repo_store(url: str, repo_store: Path) -> None:
    if (repo_store / ".git").is_dir():
        _git(["-C", str(repo_store), "fetch", "--tags", "--prune", "origin"])
        return
    if repo_store.exists():
        shutil.rmtree(repo_store)
    repo_store.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "--no-checkout", url, str(repo_store)])
    _git(["-C", str(repo_store), "fetch", "--tags", "--prune", "origin"])


def _resolve_commit(repo_store: Path, requested_ref: str | None) -> str:
    if not requested_ref:
        return _rev_parse(repo_store, "HEAD^{commit}")
    try:
        return _rev_parse(repo_store, f"{requested_ref}^{{commit}}")
    except RuntimeError:
        _git(["-C", str(repo_store), "fetch", "--tags", "origin", requested_ref])
        return _rev_parse(repo_store, "FETCH_HEAD^{commit}")


def _add_worktree(repo_store: Path, checkout_path: Path, resolved_ref: str) -> None:
    checkout_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(["git", "-C", str(repo_store), "worktree", "add", "--detach", str(checkout_path), resolved_ref], timeout=600)
    if result.returncode != 0:
        shutil.rmtree(checkout_path, ignore_errors=True)
        raise RuntimeError(_git_error(result, "git worktree add failed"))


def _write_metadata(*, checkout_path: Path, repo_store: Path, url: str, requested_ref: str | None, resolved_ref: str, repo_key: str) -> ResolvedGitCheckout:
    checkout_path.mkdir(parents=True, exist_ok=True)
    data = {
        "url": url,
        "requested_ref": requested_ref,
        "resolved_ref": resolved_ref,
        "repo_key": repo_key,
        "repo_store": str(repo_store.resolve()),
        "checkout_path": str(checkout_path.resolve()),
        "size_bytes": _directory_size(checkout_path),
        "last_accessed": datetime.now(timezone.utc).isoformat(),
    }
    (checkout_path / _METADATA_FILE).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return ResolvedGitCheckout(url, requested_ref, resolved_ref, repo_key, repo_store.resolve(), checkout_path.resolve())


def _scan_entries(root: Path, protected: set[Path]) -> list[GitCacheEntryStatus]:
    protected_resolved = {path.resolve(strict=False) for path in protected}
    entries: list[GitCacheEntryStatus] = []
    for metadata_path in root.glob(f"checkouts/*/*/{_METADATA_FILE}"):
        checkout = read_cache_metadata(metadata_path.parent)
        if checkout is None:
            continue
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        checkout_path = checkout.checkout_path.resolve(strict=False)
        entries.append(
            GitCacheEntryStatus(
                repo_key=checkout.repo_key,
                url=checkout.url,
                resolved_ref=checkout.resolved_ref,
                requested_ref=checkout.requested_ref,
                checkout_path=checkout_path,
                repo_store=checkout.repo_store,
                size_bytes=int(data.get("size_bytes") or _directory_size(checkout_path)),
                last_accessed=datetime.fromisoformat(str(data["last_accessed"])),
                protected=checkout_path in protected_resolved,
            )
        )
    return entries


def _prune_git_cache_locked(context: _CacheContext, *, protected_paths: set[Path] | None = None) -> GitCachePruneResult:
    protected = set(protected_paths or set()) | protected_git_checkout_paths(context.state)
    entries = sorted(_scan_entries(context.root, protected), key=lambda entry: entry.last_accessed)
    before = sum(entry.size_bytes for entry in entries)
    current = before
    removed: list[GitCacheEntryStatus] = []
    skipped: list[GitCacheEntryStatus] = []
    remaining = list(entries)
    for entry in entries:
        if current <= context.config.max_size_bytes:
            break
        if entry.protected:
            skipped.append(entry)
            continue
        _remove_entry(entry)
        removed.append(entry)
        remaining.remove(entry)
        current -= entry.size_bytes
    return GitCachePruneResult(context.root, context.config.max_size_bytes, before, current, removed, skipped, sorted(remaining, key=lambda e: e.last_accessed, reverse=True))


def _remove_entry(entry: GitCacheEntryStatus) -> None:
    result = _run_git(["git", "-C", str(entry.repo_store), "worktree", "remove", "--force", str(entry.checkout_path)], timeout=120)
    if result.returncode != 0 and entry.checkout_path.exists():
        shutil.rmtree(entry.checkout_path, ignore_errors=True)
    _run_git(["git", "-C", str(entry.repo_store), "worktree", "prune"], timeout=60)


def _known_case_dirs(state: ProjectState) -> set[Path]:
    out: set[Path] = set()
    for entry in state.registry.cases.values():
        path = Path(entry.path)
        if not path.is_absolute():
            path = state.root / path
        if path.is_dir():
            out.add(path.resolve())
    bundles_dir = state.config.resolve_bundles_dir(state.root)
    if bundles_dir.exists():
        out.update(path.resolve() for path in bundles_dir.rglob("*") if path.is_dir() and (path / "case.toml").is_file())
    return out


def _remove_existing_artifact_dir(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _ensure_artifact_gitignore(bundle_dir: Path) -> None:
    path = bundle_dir / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if "/artifact" not in {line.strip() for line in existing.splitlines()}:
        path.write_text((existing.rstrip() + "\n/artifact\n").lstrip(), encoding="utf-8")


def _repo_key(url: str) -> str:
    parsed = urlparse(url)
    label = safe_name(Path(parsed.path or url).stem or "repo")
    return f"{label}-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"


def _rev_parse(repo_store: Path, expression: str) -> str:
    result = _run_git(["git", "-C", str(repo_store), "rev-parse", expression], timeout=60)
    if result.returncode != 0:
        raise RuntimeError(_git_error(result, f"git rev-parse failed: {expression}"))
    return result.stdout.strip()


def _git(args: list[str]) -> None:
    command = args if args and args[0] == "git" else ["git", *args]
    result = _run_git(command, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(_git_error(result, "git command failed"))


def _run_git(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _git_error(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    return result.stderr.strip() or result.stdout.strip() or fallback


def _directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _lock_is_stale(lock_path: Path) -> bool:
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, ValueError, PermissionError):
        return False
    return False


@contextmanager
def _cache_lock(root: Path, *, timeout_seconds: float = 120.0, poll_seconds: float = 0.1) -> Iterator[None]:
    lock_path = root / _LOCK_FILE
    started = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        except FileExistsError:
            if _lock_is_stale(lock_path):
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() - started >= timeout_seconds:
                raise RuntimeError(f"timed out acquiring git cache lock: {lock_path}")
            time.sleep(poll_seconds)
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
