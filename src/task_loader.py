"""Read the instructions and case brief supplied to an agent."""

from __future__ import annotations

from pathlib import Path

from models import CasePlan


def read_instruction_text(workspace: Path, relative_path: str) -> str:
	target = (workspace / relative_path).resolve()
	try:
		target.relative_to(workspace.resolve())
	except ValueError as exc:
		raise ValueError(f"instruction path escapes workspace: {relative_path}") from exc
	if not target.is_file():
		raise FileNotFoundError(f"instruction file not found in workspace: {relative_path}")
	return target.read_text(encoding="utf-8")


def compose_task_text(instructions: str, case_brief: CasePlan) -> str:
	return (
		"## Case Brief\n\n"
		f"- Core Claim: {case_brief.core_claim}\n"
		f"- Acceptable Evidence: {case_brief.acceptable_evidence}\n"
		f"- Allowed Tolerance: {case_brief.allowed_tolerance}\n\n"
		f"{instructions.strip()}"
	)


__all__ = ["compose_task_text", "read_instruction_text"]
