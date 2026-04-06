from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from utils import safe_name as _safe_name

logger = logging.getLogger(__name__)


def create_workspace_root(task_id: str, root_parent: Path) -> Path:
    """Create an ephemeral workspace root for one task run."""
    root_parent.mkdir(parents=True, exist_ok=True)
    workspace = (
        Path(tempfile.mkdtemp(prefix=f"ae_workspace_{_safe_name(task_id)}_", dir=str(root_parent)))
        / "workspace"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def bundle_refs_path(input_file: Path) -> Path | None:
    refs_dir = input_file.resolve().parent / "refs"
    return refs_dir if refs_dir.is_dir() else None


def cleanup_workspace_tree(
    workspace_path: Path | str,
    *,
    preserve: bool,
    preserve_failed_workspace: bool,
) -> None:
    if preserve and preserve_failed_workspace:
        return

    workspace = Path(workspace_path).expanduser().resolve()
    temp_root = workspace.parent

    if workspace.name != "workspace":
        logger.warning("refusing to cleanup unexpected workspace path: %s", workspace)
        return
    if not temp_root.name.startswith("ae_workspace_"):
        logger.warning("refusing to cleanup unexpected temp root: %s", temp_root)
        return
    if not temp_root.exists():
        return

    try:
        shutil.rmtree(temp_root)
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("failed to cleanup workspace tree under %s", temp_root)