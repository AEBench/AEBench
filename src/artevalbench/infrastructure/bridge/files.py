from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...display import DisplayEvent
from ...models import AgentLaunchResult, AgentResult
from ...utils import safe_name
from ..events import EventSink

BRIDGE_STATE_DIRNAME = ".artevalbench-driver"
RUNTIME_BRIDGE_DIR = "/artevalbench-driver"


@dataclass(frozen=True, slots=True)
class BridgePaths:
	host_dir: Path
	runtime_dir: str
	request_host: Path
	request_runtime: str
	session_host: Path
	session_runtime: str
	result_host: Path
	result_runtime: str
	event_host: Path
	event_runtime: str
	mcp_config_host: Path
	mcp_config_runtime: str


def bridge_paths_for(
 *,
 output_dir: Path,
 run_id: str,
 runtime_mode: str,
) -> BridgePaths:
	host_dir = (output_dir / BRIDGE_STATE_DIRNAME / safe_name(run_id)).resolve()
	host_dir.mkdir(parents=True, exist_ok=True)
	runtime_dir = RUNTIME_BRIDGE_DIR if runtime_mode == "docker" else str(host_dir)
	return BridgePaths(
	 host_dir=host_dir,
	 runtime_dir=runtime_dir,
	 request_host=host_dir / "request.json",
	 request_runtime=f"{runtime_dir}/request.json",
	 session_host=host_dir / "session.json",
	 session_runtime=f"{runtime_dir}/session.json",
	 result_host=host_dir / "result.json",
	 result_runtime=f"{runtime_dir}/result.json",
	 event_host=host_dir / "events.jsonl",
	 event_runtime=f"{runtime_dir}/events.jsonl",
	 mcp_config_host=host_dir / "mcp-config.json",
	 mcp_config_runtime=f"{runtime_dir}/mcp-config.json",
	)


def load_launch_result_payload(result: AgentLaunchResult) -> AgentResult | None:
	if result.result_file is None:
		return None
	path = Path(result.result_file)
	if not path.is_file():
		return None
	return AgentResult.model_validate_json(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, payload: object) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def replay_event_file(path: Path, *, case_id: str, sink: EventSink | None, offset: int) -> int:
	if not path.is_file():
		return offset
	size = path.stat().st_size
	if size < offset:
		offset = 0
	with path.open("r", encoding="utf-8") as handle:
		handle.seek(offset)
		for line in handle:
			line = line.strip()
			if not line:
				continue
			event = DisplayEvent.model_validate_json(line)
			if event.case_id is None:
				event = event.model_copy(update={"case_id": case_id})
			if sink is not None:
				sink.emit(event)
		return handle.tell()
