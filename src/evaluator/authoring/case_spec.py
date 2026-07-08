"""Write the small case.toml used by `aebench case init`."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from constants import CASE_MANIFEST_FILENAME
from models import CaseConfig


def write_case_spec(case: CaseConfig, case_dir: Path) -> Path:
	case_dir.mkdir(parents=True, exist_ok=True)
	path = case_dir / CASE_MANIFEST_FILENAME
	path.write_text(case_spec_to_toml(case), encoding="utf-8")
	return path


def case_spec_to_toml(case: CaseConfig) -> str:
	lines: list[str] = [f"id = {_toml_value(case.id)}", ""]
	run_payload: dict[str, Any] = {"id": case.run.id}
	if case.run.required_evidence:
		run_payload["required_evidence"] = case.run.required_evidence
	sections: list[tuple[str, dict[str, Any]]] = [
		("case_brief", case.case_brief.model_dump(mode="json", exclude_none=True)),
		("run", run_payload),
		("run.instructions", case.run.instructions.model_dump(mode="json", exclude_none=True)),
		("run.runtime", case.run.runtime.model_dump(mode="json", exclude_none=True)),
		(
			"run.artifact_requirements",
			case.run.artifact_requirements.model_dump(mode="json", exclude_none=True),
		),
		("run.prompt", case.run.prompt.model_dump(mode="json", exclude_none=True)),
		("oracle", case.oracle.model_dump(mode="json", exclude_none=True)),
		("upstream", case.upstream.model_dump(mode="json", exclude_none=True)),
		("paper", case.paper.model_dump(mode="json", exclude_none=True)),
	]

	for name, payload in sections:
		lines.append(f"[{name}]")
		for key, value in payload.items():
			if key == "hardware" and isinstance(value, dict):
				continue
			lines.append(f"{key} = {_toml_value(value)}")
		lines.append("")

		hardware = payload.get("hardware")
		if isinstance(hardware, dict):
			lines.append(f"[{name}.hardware]")
			for key, value in hardware.items():
				lines.append(f"{key} = {_toml_value(value)}")
			lines.append("")

	return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: Any) -> str:
	if isinstance(value, Enum):
		return json.dumps(value.value)
	if isinstance(value, str):
		return json.dumps(value)
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, int | float):
		return str(value)
	if isinstance(value, list | tuple):
		return "[" + ", ".join(_toml_value(item) for item in value) + "]"
	if isinstance(value, dict):
		items = (
			f"{json.dumps(str(key))} = {_toml_value(item)}"
			for key, item in value.items()
			if item is not None
		)
		return "{" + ", ".join(items) + "}"
	if value is None:
		return '""'
	raise TypeError(f"unsupported TOML value: {value!r}")
