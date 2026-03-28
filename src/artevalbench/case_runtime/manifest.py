from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.models import CaseSpec


def write_case_spec(case_file: Path, case: CaseSpec) -> None:
	case_file.parent.mkdir(parents=True, exist_ok=True)
	case_file.write_text(case_spec_to_toml(case), encoding="utf-8")


def case_spec_to_toml(case: CaseSpec) -> str:
	payload = case.model_dump(mode="json")
	run = payload["run"]
	lines = [f"id = {_toml_value(payload['id'])}", ""]
	lines.extend(_table_lines("case_card", payload["case_card"]))
	lines.extend(_table_lines("run", {"id": run["id"]}))
	lines.extend(_table_lines("run.instructions", run["instructions"]))
	lines.extend(_table_lines("run.runtime", run["runtime"]))
	lines.extend(_table_lines("run.artifact_requirements", run["artifact_requirements"]))
	lines.extend(_table_lines("run.prompt", run["prompt"]))
	lines.extend(_table_lines("oracle", payload["oracle"]))
	lines.extend(_table_lines("upstream", payload["upstream"]))
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
	if isinstance(value, int):
		return str(value)
	if isinstance(value, str):
		return json.dumps(value)
	if isinstance(value, list):
		return "[" + ", ".join(_toml_value(item) for item in value) + "]"
	raise TypeError(f"unsupported TOML value: {value!r}")
