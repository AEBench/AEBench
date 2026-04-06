"""Case bundle manifest writing."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from evaluator.constants import CASE_MANIFEST_FILENAME
from models import CaseConfig


def write_case_spec(case: CaseConfig, case_dir: Path) -> Path:
    case_dir.mkdir(parents=True, exist_ok=True)
    toml_path = case_dir / CASE_MANIFEST_FILENAME
    toml_path.write_text(case_spec_to_toml(case), encoding="utf-8")
    return toml_path


def case_spec_to_toml(case: CaseConfig) -> str:
    lines = [f'id = {json.dumps(case.id)}', ""]
    lines.extend(_table_lines("case_brief", case.case_brief.to_json_dict()))
    lines.extend(_table_lines("run", {"id": case.run.id}))
    lines.extend(_table_lines("run.instructions", {"path": case.run.instructions_path}))
    lines.extend(_table_lines("run.runtime", case.run.runtime.to_json_dict(exclude_none=True)))
    lines.extend(_table_lines("run.artifact_requirements", case.run.artifact_requirements.to_json_dict()))
    lines.extend(
        _table_lines(
            "run.prompt",
            {
                "profile": case.run.prompt_profile.value,
                "append": case.run.prompt_append,
            },
        )
    )
    lines.extend(_table_lines("oracle", case.oracle.to_json_dict(exclude_none=True)))
    lines.extend(_table_lines("upstream", case.upstream.to_json_dict(exclude_none=True)))
    return "\n".join(lines).rstrip() + "\n"


def _table_lines(name: str, payload: dict[str, Any]) -> list[str]:
    lines = [f"[{name}]"]
    for key, value in payload.items():
        if value is None:
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    return lines


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{json.dumps(k)} = {_toml_value(v)}" for k, v in value.items() if v is not None)
        return "{" + inner + "}"
    if isinstance(value, Enum):
        return json.dumps(value.value)
    raise TypeError(f"unsupported TOML value: {value!r}")