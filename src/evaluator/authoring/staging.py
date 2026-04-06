"""Staging dirs for case authoring."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

ARTIFACT_SRC = "artifact-src"
CANDIDATE_CASE = "candidate-case"
REPORTS = "reports"
FEEDBACK = "feedback"


def create_staging_dirs(
    staging_root: Path,
    case_id: str,
    *,
    overwrite: bool = False,
) -> Path:
    staging_dir = (staging_root / case_id).resolve()

    if staging_dir.exists() and any(staging_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"staging dir not empty: {staging_dir}")
        shutil.rmtree(staging_dir)

    for name in (ARTIFACT_SRC, "guidance", CANDIDATE_CASE, REPORTS, FEEDBACK):
        (staging_dir / name).mkdir(parents=True, exist_ok=True)

    return staging_dir


def clone_artifact(staging_dir: Path, git_url: str, git_ref: str) -> Path:
    dest = staging_dir / ARTIFACT_SRC

    if any(dest.iterdir()):
        raise RuntimeError(f"artifact-src/ not empty: {dest}")

    _run_git(["git", "clone", "--no-checkout", git_url, str(dest)])
    _run_git(["git", "-C", str(dest), "fetch", "--tags", "origin"], ok_on_nonzero=True)

    # Try direct checkout first; fall back to fetch+FETCH_HEAD for unreachable SHAs.
    rc = subprocess.run(
        ["git", "-C", str(dest), "checkout", git_ref],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if rc.returncode != 0:
        _run_git(["git", "-C", str(dest), "fetch", "origin", git_ref], ok_on_nonzero=True)
        rc2 = subprocess.run(
            ["git", "-C", str(dest), "checkout", "FETCH_HEAD"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if rc2.returncode != 0:
            raise RuntimeError(f"checkout failed for {git_ref!r}: {(rc.stderr or rc.stdout).strip()}")

    return dest


def build_guidance_dir(
    staging_dir: Path,
    *,
    aebench_root: Path,
    use_evaluator_src: bool,
    use_cases_as_examples: bool,
    example_case_ids: list[str],
) -> Path:
    guidance = staging_dir / "guidance"

    howtos_src = aebench_root / "docs" / "howtos"
    if howtos_src.is_dir():
        dst = guidance / "howtos"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(howtos_src, dst)

    if use_evaluator_src:
        src = aebench_root / "src" / "evaluator"
        if src.is_dir():
            dst = guidance / "primitives"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("authoring", "__pycache__"))

    if use_cases_as_examples:
        cases_root = aebench_root / "cases"
        examples = guidance / "examples"
        examples.mkdir(exist_ok=True)

        ids = list(example_case_ids) or [
            p.name for p in sorted(cases_root.iterdir())
            if p.is_dir() and (p / "case.toml").is_file()
        ]
        for cid in ids:
            src = cases_root / cid
            if not src.is_dir():
                continue
            dst = examples / cid
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "artifact", ".gitignore"))

    return guidance


def _run_git(cmd: list[str], *, ok_on_nonzero: bool = False) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if result.returncode != 0 and not ok_on_nonzero:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git failed: {cmd}")
