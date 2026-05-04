from __future__ import annotations

from pathlib import Path
from typing import Any

from constants import SUMMARY_INSTRUCTION


def read_instruction_text(workspace_path: Path, instruction_path: str) -> str:
    root = workspace_path.resolve()
    target = (root / instruction_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"instruction path escapes workspace: {instruction_path}") from exc
    if not target.is_file():
        raise FileNotFoundError(f"instruction file not found inside workspace: {instruction_path}")
    return target.read_text(encoding="utf-8")


def append_summary_instruction(task_text: str, summary_name: str) -> str:
    return task_text.rstrip() + SUMMARY_INSTRUCTION.format(basename=summary_name)


def prepend_case_brief(task_text: str, case_brief: Any) -> str:
    if case_brief is None:
        return task_text
    return (
        "## Case Brief\n\n"
        f"- Core Claim: {case_brief.core_claim}\n"
        f"- Acceptable Evidence: {case_brief.acceptable_evidence}\n"
        f"- Allowed Tolerance: {case_brief.allowed_tolerance}\n\n"
        f"{task_text.lstrip()}"
    )
