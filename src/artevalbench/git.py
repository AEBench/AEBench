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

from .project_config import GitCacheConfig, ProjectConfigState, load_project_config
from .utils import safe_name

from .domain.models import UpstreamSourceType

if TYPE_CHECKING:
	from .domain.models import CaseSpec

_METADATA_BASENAME = ".artevalbench-git-cache.json"
_LOCK_BASENAME = ".artevalbench-git-cache.lock"


@dataclass(frozen=True)
class ResolvedGitCheckout:
	url: str
	requested_ref: str | None
	resolved_ref: str
	repo_key: str
	repo_store: Path
	checkout_path: Path


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class GitCacheStatus:
	root: Path
	max_size_bytes: int
	total_size_bytes: int
	entry_count: int
	entries: list[GitCacheEntryStatus]


@dataclass(frozen=True)
class GitCachePruneResult:
	root: Path
	max_size_bytes: int
	before_size_bytes: int
	after_size_bytes: int
	removed_entries: list[GitCacheEntryStatus]
	skipped_entries: list[GitCacheEntryStatus]
	remaining_entries: list[GitCacheEntryStatus]


@dataclass(frozen=True)
class _CacheMetadata:
	url: str
	requested_ref: str | None
	resolved_ref: str
	repo_key: str
	repo_store: Path
	checkout_path: Path
	size_bytes: int
	last_accessed: datetime


@dataclass(frozen=True)
class _GitCacheContext:
	state: ProjectConfigState
	config: GitCacheConfig
	root: Path


def ensure_git_checkout(
	url: str,
	requested_ref: str | None,
	*,
	project_state: ProjectConfigState | None = None,
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
			_ensure_checkout_worktree(repo_store, checkout_path, resolved_ref)

		metadata = _write_cache_metadata(
			checkout_path=checkout_path,
			repo_store=repo_store,
			url=url,
			requested_ref=requested_ref,
			resolved_ref=resolved_ref,
			repo_key=repo_key,
		)
		return ResolvedGitCheckout(
			url=metadata.url,
			requested_ref=metadata.requested_ref,
			resolved_ref=metadata.resolved_ref,
			repo_key=metadata.repo_key,
			repo_store=metadata.repo_store,
			checkout_path=metadata.checkout_path,
		)


def ensure_bundle_artifact_link(
	bundle_dir: Path,
	target_dir: Path,
	*,
	symlink_artifact: bool,
) -> Path:
	artifact_path = bundle_dir / "artifact"

	if symlink_artifact:
		if artifact_path.is_symlink():
			current = artifact_path.resolve(strict=False)
			if current == target_dir.resolve():
				_write_bundle_gitignore(bundle_dir)
				return current
			artifact_path.unlink()
		elif artifact_path.exists() and not artifact_path.is_dir():
			raise RuntimeError(f"bundle artifact path exists and is not a directory: {artifact_path}")
		elif artifact_path.exists() and any(artifact_path.iterdir()):
			raise RuntimeError(f"bundle artifact path is not empty: {artifact_path}")
		elif artifact_path.exists():
			artifact_path.rmdir()

		artifact_path.symlink_to(target_dir.resolve(), target_is_directory=True)
		_write_bundle_gitignore(bundle_dir)
		return artifact_path.resolve(strict=False)

	if artifact_path.is_symlink() or artifact_path.is_file():
		artifact_path.unlink()
	elif artifact_path.is_dir():
		shutil.rmtree(artifact_path)

	shutil.copytree(
		target_dir,
		artifact_path,
		ignore=shutil.ignore_patterns(".git"),
	)
	_write_bundle_gitignore(bundle_dir)
	return artifact_path.resolve()


def resolve_git_bundle_artifact(
	case_dir: Path,
	case: "CaseSpec",
	*,
	project_state: ProjectConfigState | None = None,
	protected_paths: set[Path] | None = None,
) -> ResolvedGitCheckout:
	if (
		case.upstream.source_type != UpstreamSourceType.GIT
		or not case.upstream.url
	):
		raise RuntimeError(f"case {case.id} is not git-backed")
	upstream_url = case.upstream.url
	context = _cache_context(project_state)
	resolved = ensure_git_checkout(
		upstream_url,
		case.upstream.ref,
		project_state=context.state,
		protected_paths=protected_paths,
	)
	ensure_bundle_artifact_link(
		case_dir,
		resolved.checkout_path,
		symlink_artifact=context.config.symlink_artifact,
	)
	return resolved


def touch_git_checkout(checkout_path: Path) -> None:
	metadata = read_cache_metadata(checkout_path)
	if metadata is None:
		return
	_write_cache_metadata(
		checkout_path=metadata.checkout_path,
		repo_store=metadata.repo_store,
		url=metadata.url,
		requested_ref=metadata.requested_ref,
		resolved_ref=metadata.resolved_ref,
		repo_key=metadata.repo_key,
	)


def git_cache_status(project_state: ProjectConfigState | None = None) -> GitCacheStatus:
	context = _cache_context(project_state)
	protected = protected_git_checkout_paths(context.state)
	entries = _scan_cache_entries(context.root, protected)
	return GitCacheStatus(
		root=context.root,
		max_size_bytes=context.config.max_size_bytes,
		total_size_bytes=sum(entry.size_bytes for entry in entries),
		entry_count=len(entries),
		entries=sorted(entries, key=lambda entry: entry.last_accessed, reverse=True),
	)


def prune_git_cache(
	project_state: ProjectConfigState | None = None,
	*,
	protected_paths: set[Path] | None = None,
) -> GitCachePruneResult:
	context = _cache_context(project_state)
	with _cache_lock(context.root):
		return _prune_git_cache_locked(context, protected_paths=protected_paths)


def protected_git_checkout_paths(project_state: ProjectConfigState | None = None) -> set[Path]:
	context = _cache_context(project_state)
	protected: set[Path] = set()

	candidate_case_dirs: set[Path] = set()
	for entry in context.state.registry.cases.values():
		path = Path(entry.path)
		if not path.is_absolute():
			path = (context.state.root / path).resolve()
		if path.is_dir():
			candidate_case_dirs.add(path)

	bundles_root = context.state.config.resolve_bundles_dir(context.state.root)
	if bundles_root.exists():
		for case_dir in bundles_root.rglob("*"):
			if case_dir.is_dir() and (case_dir / "case.toml").is_file():
				candidate_case_dirs.add(case_dir.resolve())

	for case_dir in candidate_case_dirs:
		artifact_path = case_dir / "artifact"
		if artifact_path.is_symlink():
			resolved = artifact_path.resolve(strict=False)
			if _is_under_cache(context.root, resolved):
				protected.add(resolved.resolve(strict=False))

	return protected


def read_cache_metadata(checkout_path: Path) -> _CacheMetadata | None:
	metadata_path = checkout_path / _METADATA_BASENAME
	if not metadata_path.is_file():
		return None
	payload = json.loads(metadata_path.read_text(encoding="utf-8"))
	return _CacheMetadata(
		url=str(payload["url"]),
		requested_ref=_optional_str(payload.get("requested_ref")),
		resolved_ref=str(payload["resolved_ref"]),
		repo_key=str(payload["repo_key"]),
		repo_store=Path(str(payload["repo_store"])).resolve(),
		checkout_path=Path(str(payload["checkout_path"])).resolve(),
		size_bytes=int(payload["size_bytes"]),
		last_accessed=datetime.fromisoformat(str(payload["last_accessed"])),
	)


def _cache_context(project_state: ProjectConfigState | None) -> _GitCacheContext:
	state = project_state or load_project_config(Path.cwd())
	root = state.config.cache.git.resolve_root(state.root)
	root.mkdir(parents=True, exist_ok=True)
	return _GitCacheContext(state=state, config=state.config.cache.git, root=root)


def _is_lock_stale(lock_path: Path) -> bool:
	try:
		content = lock_path.read_text(encoding="utf-8").strip()
		pid = int(content)
	except (OSError, ValueError):
		return False
	try:
		os.kill(pid, 0)
	except ProcessLookupError:
		return True
	except PermissionError:
		# Process exists but we can't signal it — not stale.
		return False
	return False


@contextmanager
def _cache_lock(root: Path, *, timeout_seconds: float = 120.0, poll_seconds: float = 0.1) -> Iterator[None]:
	lock_path = root / _LOCK_BASENAME
	start = time.monotonic()
	fd: int | None = None
	stale_checked = False

	while True:
		try:
			fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
			os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
			break
		except FileExistsError:
			if not stale_checked and _is_lock_stale(lock_path):
				stale_checked = True
				try:
					lock_path.unlink()
				except FileNotFoundError:
					pass
				continue
			if (time.monotonic() - start) >= timeout_seconds:
				raise RuntimeError(f"timed out acquiring git cache lock: {lock_path}")
			time.sleep(poll_seconds)

	try:
		yield
	finally:
		if fd is not None:
			os.close(fd)
		try:
			lock_path.unlink()
		except FileNotFoundError:
			pass


def _repo_key(url: str) -> str:
	parsed = urlparse(url)
	path = parsed.path or url
	label = safe_name(Path(path).stem or Path(path).name or "repo")
	digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
	return f"{label}-{digest}"


def _ensure_repo_store(url: str, repo_store: Path) -> None:
	if (repo_store / ".git").exists():
		_run_git(["git", "-C", str(repo_store), "fetch", "--tags", "--prune", "origin"])
		return
	if repo_store.exists():
		shutil.rmtree(repo_store)
	repo_store.parent.mkdir(parents=True, exist_ok=True)
	_run_git(["git", "clone", "--no-checkout", url, str(repo_store)])
	_run_git(["git", "-C", str(repo_store), "fetch", "--tags", "--prune", "origin"])


def _resolve_commit(repo_store: Path, requested_ref: str | None) -> str:
	if requested_ref is None:
		return _rev_parse(repo_store, "HEAD^{commit}")
	try:
		return _rev_parse(repo_store, f"{requested_ref}^{{commit}}")
	except RuntimeError:
		_run_git(["git", "-C", str(repo_store), "fetch", "--tags", "origin", requested_ref])
		try:
			return _rev_parse(repo_store, "FETCH_HEAD^{commit}")
		except RuntimeError:
			return _rev_parse(repo_store, f"origin/{requested_ref}^{{commit}}")


def _rev_parse(repo_store: Path, expr: str) -> str:
	result = subprocess.run(
		["git", "-C", str(repo_store), "rev-parse", expr],
		capture_output=True,
		text=True,
		timeout=60,
		check=False,
	)
	if result.returncode != 0:
		raise RuntimeError(
			result.stderr.strip() or result.stdout.strip() or f"rev-parse failed: {expr}"
		)
	return result.stdout.strip()


def _write_cache_metadata(
	*,
	checkout_path: Path,
	repo_store: Path,
	url: str,
	requested_ref: str | None,
	resolved_ref: str,
	repo_key: str,
) -> _CacheMetadata:
	checkout_path.mkdir(parents=True, exist_ok=True)
	size_bytes = _directory_size(checkout_path)
	last_accessed = datetime.now(timezone.utc)
	metadata = {
		"url": url,
		"requested_ref": requested_ref,
		"resolved_ref": resolved_ref,
		"repo_key": repo_key,
		"repo_store": str(repo_store.resolve()),
		"checkout_path": str(checkout_path.resolve()),
		"size_bytes": size_bytes,
		"last_accessed": last_accessed.isoformat(),
	}
	(checkout_path / _METADATA_BASENAME).write_text(
		json.dumps(metadata, indent=2, sort_keys=True),
		encoding="utf-8",
	)
	return _CacheMetadata(
		url=url,
		requested_ref=requested_ref,
		resolved_ref=resolved_ref,
		repo_key=repo_key,
		repo_store=repo_store.resolve(),
		checkout_path=checkout_path.resolve(),
		size_bytes=size_bytes,
		last_accessed=last_accessed,
	)


def _directory_size(root: Path) -> int:
	total = 0
	for path in root.rglob("*"):
		if path.is_file():
			try:
				total += path.stat().st_size
			except FileNotFoundError:
				continue
	return total


def _scan_cache_entries(root: Path, protected: set[Path]) -> list[GitCacheEntryStatus]:
	entries: list[GitCacheEntryStatus] = []
	if not root.exists():
		return entries
	normalized_protected = {path.resolve(strict=False) for path in protected}
	for metadata_path in root.glob(f"checkouts/*/*/{_METADATA_BASENAME}"):
		metadata = read_cache_metadata(metadata_path.parent)
		if metadata is None:
			continue
		checkout_path = metadata.checkout_path.resolve(strict=False)
		entries.append(
			GitCacheEntryStatus(
				repo_key=metadata.repo_key,
				url=metadata.url,
				resolved_ref=metadata.resolved_ref,
				requested_ref=metadata.requested_ref,
				checkout_path=checkout_path,
				repo_store=metadata.repo_store,
				size_bytes=metadata.size_bytes,
				last_accessed=metadata.last_accessed,
				protected=checkout_path in normalized_protected,
			)
		)
	return entries


def _remove_cache_entry(entry: GitCacheEntryStatus) -> None:
	result = subprocess.run(
		[
			"git",
			"-C",
			str(entry.repo_store),
			"worktree",
			"remove",
			"--force",
			str(entry.checkout_path),
		],
		capture_output=True,
		text=True,
		timeout=120,
		check=False,
	)
	if result.returncode != 0 and entry.checkout_path.exists():
		shutil.rmtree(entry.checkout_path, ignore_errors=True)
	subprocess.run(
		["git", "-C", str(entry.repo_store), "worktree", "prune"],
		capture_output=True,
		text=True,
		timeout=60,
		check=False,
	)


def _run_git(command: list[str]) -> None:
	result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")


def _ensure_checkout_worktree(repo_store: Path, checkout_path: Path, resolved_ref: str) -> None:
	checkout_path.parent.mkdir(parents=True, exist_ok=True)
	result = subprocess.run(
		[
			"git",
			"-C",
			str(repo_store),
			"worktree",
			"add",
			"--detach",
			str(checkout_path),
			resolved_ref,
		],
		capture_output=True,
		text=True,
		timeout=600,
		check=False,
	)
	if result.returncode == 0:
		return
	# Clean up any partial directory left by a failed worktree add.
	if checkout_path.exists():
		shutil.rmtree(checkout_path, ignore_errors=True)
	raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git worktree add failed")


def _write_bundle_gitignore(bundle_dir: Path) -> None:
	gitignore_path = bundle_dir / ".gitignore"
	line = "/artifact"
	existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
	if line in {l.strip() for l in existing.splitlines()}:
		return
	gitignore_path.write_text((existing.rstrip() + "\n" + line + "\n").lstrip(), encoding="utf-8")


def _optional_str(value: object) -> str | None:
	if value is None:
		return None
	return str(value)


def _is_under_cache(cache_root: Path, candidate: Path) -> bool:
	try:
		candidate.resolve(strict=False).relative_to(cache_root.resolve())
		return True
	except ValueError:
		return False


def _prune_git_cache_locked(
	context: _GitCacheContext,
	*,
	protected_paths: set[Path] | None = None,
) -> GitCachePruneResult:
	protected = set(protected_paths or set()) | protected_git_checkout_paths(context.state)
	entries = _scan_cache_entries(context.root, protected)
	entries_by_age = sorted(entries, key=lambda entry: entry.last_accessed)
	total_size = sum(entry.size_bytes for entry in entries_by_age)
	removed: list[GitCacheEntryStatus] = []
	skipped: list[GitCacheEntryStatus] = []
	remaining = list(entries_by_age)

	for entry in entries_by_age:
		if total_size <= context.config.max_size_bytes:
			break
		if entry.protected:
			skipped.append(entry)
			continue
		_remove_cache_entry(entry)
		removed.append(entry)
		total_size -= entry.size_bytes
		remaining.remove(entry)

	return GitCachePruneResult(
		root=context.root,
		max_size_bytes=context.config.max_size_bytes,
		before_size_bytes=sum(entry.size_bytes for entry in entries_by_age),
		after_size_bytes=total_size,
		removed_entries=removed,
		skipped_entries=skipped,
		remaining_entries=sorted(remaining, key=lambda entry: entry.last_accessed, reverse=True),
	)
