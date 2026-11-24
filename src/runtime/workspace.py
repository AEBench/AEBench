from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from utils import safe_name

from . import btrfs

logger = logging.getLogger(__name__)

# Beside the workspace subvolume: snapshot N of a workdir-snapshot run is
# <tmp root>/snapshots/<N as 6 digits>, a read-only btrfs snapshot.
SNAPSHOTS_DIRNAME = "snapshots"
# Also beside it: the harness's tool hook and settings, mounted read-only into the container.
TOOL_HOOKS_DIRNAME = "tool-hooks"


def create_workspace(task_id: str, root_parent: Path, *, subvolume: bool = False) -> Path:
	"""Creates <root_parent>/ae_workspace_<id>_<random>/workspace and returns it.

	With `subvolume`, the workspace is a btrfs subvolume (root_parent must be on btrfs).
	"""
	root_parent.mkdir(parents=True, exist_ok=True)
	tmp_root = Path(
		tempfile.mkdtemp(prefix=f"ae_workspace_{safe_name(task_id)}_", dir=str(root_parent))
	)
	workspace = tmp_root / "workspace"
	if subvolume:
		btrfs.create_subvolume(workspace)
	else:
		workspace.mkdir()
	return workspace


def refs_dir_for_case_manifest(case_manifest: Path) -> Path | None:
	refs_dir = case_manifest.resolve().parent / "refs"
	return refs_dir if refs_dir.is_dir() else None


def cleanup_workspace(workspace: Path | str, *, keep: bool) -> None:
	if keep:
		return

	workspace_path = Path(workspace).expanduser().resolve()
	tmp_root = workspace_path.parent

	if workspace_path.name != "workspace":
		logger.warning("refusing to clean unexpected workspace path: %s", workspace_path)
		return
	if not tmp_root.name.startswith("ae_workspace_"):
		logger.warning("refusing to clean unexpected temp root: %s", tmp_root)
		return

	try:
		shutil.rmtree(tmp_root)
	except FileNotFoundError:
		return
	except Exception:
		logger.exception("failed to clean workspace tree under %s", tmp_root)


# Backward-compatible aliases for old call sites during migration.
create_workspace_root = create_workspace
bundle_refs_path = refs_dir_for_case_manifest
cleanup_workspace_tree = cleanup_workspace
