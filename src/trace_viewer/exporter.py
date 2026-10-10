from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

RUNNER_LOG = "runner_output.log"
RUN_RESULT = "result.jsonl"
CASE_RESULT = "case_result.json"
INDEX_FILE = "index.json"
ORACLE_EVALUATIONS_DIR = "oracle-evaluations"
EVALUATION_RECORD = "evaluation.json"
_TIMESTAMP_PREFIX = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+")
_CLAUDE_SYSTEM_EVENTS = {"init", "task_started", "task_notification"}


def export_trace_site(run_dir: Path, output_dir: Path) -> Path:
	run_dir = run_dir.expanduser().resolve()
	output_dir = output_dir.expanduser().resolve()
	if not run_dir.is_dir():
		raise ValueError(f"run directory does not exist: {run_dir}")

	run_result = _read_last_jsonl(run_dir / RUN_RESULT)
	case_result = _read_json(run_dir / CASE_RESULT)
	secrets = _environment_secrets()
	trace_format = _trace_format(str(run_result.get("agent_kind", "")))
	parser = parse_claude_trace if trace_format == "claude_code" else parse_codex_trace
	events, usage = parser(run_dir / RUNNER_LOG, secrets=secrets)
	record = _build_record(
		run_dir,
		run_result,
		case_result,
		events,
		usage,
		trace_format,
		secrets,
	)

	data_dir = output_dir / "data"
	data_dir.mkdir(parents=True, exist_ok=True)
	_copy_assets(output_dir)

	run_id = str(record["meta"]["run_id"])
	(data_dir / f"{run_id}.json").write_text(
		json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
	)
	_update_index(data_dir / INDEX_FILE, record["index_row"])
	return output_dir / "index.html"


def parse_codex_trace(
	path: Path, *, secrets: Mapping[str, str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
	if not path.is_file():
		raise ValueError(f"runner log does not exist: {path}")

	secret_values = secrets or {}
	events: list[dict[str, Any]] = []
	usage: dict[str, int] = {}
	session_id: str | None = None

	for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
		line = raw_line.strip()
		if not line:
			continue
		timestamp: str | None = None
		match = _TIMESTAMP_PREFIX.match(line)
		if match:
			timestamp = match.group(1)
			line = line[match.end() :]
		try:
			payload = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(payload, dict):
			continue

		event_type = str(payload.get("type", "unknown"))
		if event_type == "thread.started":
			session_id = str(payload.get("thread_id") or "default")
			events.append(_codex_event(payload, timestamp, session_id, secret_values))
			continue
		if event_type == "item.completed":
			events.append(_codex_event(payload, timestamp, session_id or "default", secret_values))
			continue
		if event_type == "turn.completed":
			raw_usage = payload.get("usage")
			if isinstance(raw_usage, dict):
				usage = {
					str(key): int(value)
					for key, value in raw_usage.items()
					if key in {"input_tokens", "output_tokens"} and isinstance(value, int)
				}
	return events, usage


def parse_claude_trace(
	path: Path, *, secrets: Mapping[str, str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
	if not path.is_file():
		raise ValueError(f"runner log does not exist: {path}")

	secret_values = secrets or {}
	events: list[dict[str, Any]] = []
	usage: dict[str, int] = {}
	session_indexes: dict[str, int] = {}
	task_types: dict[tuple[str, str], str] = {}

	for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
		line = raw_line.strip()
		if not line:
			continue
		timestamp: str | None = None
		match = _TIMESTAMP_PREFIX.match(line)
		if match:
			timestamp = match.group(1)
			line = line[match.end() :]
		try:
			payload = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(payload, dict):
			continue

		event_type = payload.get("type")
		if event_type not in {"system", "assistant", "user", "result"}:
			continue
		if event_type == "system" and payload.get("subtype") not in _CLAUDE_SYSTEM_EVENTS:
			continue
		session_id = str(payload.get("session_id") or "default")
		session_idx = session_indexes.setdefault(session_id, len(session_indexes))
		event_timestamp = payload.get("timestamp") or timestamp
		event = _claude_event(
			payload,
			str(event_timestamp) if event_timestamp else None,
			session_id,
			session_idx,
			secret_values,
		)
		if event_type == "system":
			task_id = payload.get("task_id")
			task_type = payload.get("task_type")
			if isinstance(task_id, str):
				task_key = (session_id, task_id)
				if isinstance(task_type, str):
					task_types[task_key] = task_type
				else:
					task_type = task_types.get(task_key)
			if isinstance(task_type, str):
				event["task_type"] = task_type
		events.append(event)
		if event_type == "assistant":
			message = payload.get("message")
			raw_usage = message.get("usage") if isinstance(message, dict) else None
			if isinstance(raw_usage, dict):
				for key, value in raw_usage.items():
					if isinstance(value, int):
						usage[str(key)] = usage.get(str(key), 0) + value

	return events, usage


def _claude_event(
	payload: dict[str, Any],
	timestamp: str | None,
	session_id: str,
	session_idx: int,
	secrets: Mapping[str, str],
) -> dict[str, Any]:
	event_type = str(payload["type"])
	event: dict[str, Any] = {
		"ts": timestamp,
		"type": event_type,
		"session_id": session_id,
		"session_idx": session_idx,
		"parent_tool_use_id": payload.get("parent_tool_use_id"),
	}
	if event_type == "system":
		event["subtype"] = payload.get("subtype") or "info"
		event["raw"] = _redact_value(payload, secrets)
	elif event_type in {"assistant", "user"}:
		event["uuid"] = payload.get("uuid")
		message = payload.get("message")
		message = message if isinstance(message, dict) else {}
		blocks = message.get("content")
		event["blocks"] = _redact_value(blocks if isinstance(blocks, list) else [], secrets)
		if isinstance(message.get("usage"), dict):
			event["usage"] = message["usage"]
		if message.get("model"):
			event["model"] = message["model"]
	else:
		event.update(
			subtype=payload.get("subtype"),
			duration_ms=payload.get("duration_ms"),
			num_turns=payload.get("num_turns"),
			total_cost_usd=payload.get("total_cost_usd"),
			stop_reason=payload.get("stop_reason"),
			usage=_redact_value(payload.get("usage", {}), secrets),
			result_text=_redact(str(payload.get("result") or ""), secrets),
		)
	return event


def _codex_event(
	payload: dict[str, Any],
	timestamp: str | None,
	session_id: str,
	secrets: Mapping[str, str],
) -> dict[str, Any]:
	event_type = str(payload.get("type", "unknown"))
	event: dict[str, Any] = {
		"type": "system",
		"subtype": event_type.replace(".", "_"),
		"ts": timestamp,
		"session_id": session_id,
		"session_idx": 0,
		"parent_tool_use_id": None,
	}
	item = payload.get("item")
	if not isinstance(item, dict):
		if event_type == "thread.started":
			event["subtype"] = "init"
			event["raw"] = {"thread_id": _redact(session_id, secrets)}
		return event

	item_type = str(item.get("type", "item"))
	event.update(
		type="codex_item",
		subtype=item_type,
		phase=event_type.removeprefix("item."),
	)
	event["item"] = _redact_value(item, secrets)
	return event


def _build_record(
	run_dir: Path,
	run_result: dict[str, Any],
	case_result: dict[str, Any],
	events: list[dict[str, Any]],
	usage: dict[str, int],
	trace_format: str,
	secrets: Mapping[str, str],
) -> dict[str, Any]:
	runtime_value = run_result.get("runtime")
	runtime: dict[str, Any] = runtime_value if isinstance(runtime_value, dict) else {}
	agent_value = run_result.get("agent")
	agent: dict[str, Any] = agent_value if isinstance(agent_value, dict) else {}
	reasoning_effort_value = agent.get("reasoning_effort")
	reasoning_effort = reasoning_effort_value if isinstance(reasoning_effort_value, str) else None
	oracle, was_reevaluated = _current_oracle_result(run_dir, case_result)
	phases_value = oracle.get("phases")
	phases: list[Any] = phases_value if isinstance(phases_value, list) else []
	case_id = str(run_result.get("id") or case_result.get("id") or run_dir.name)
	started_at = run_result.get("started_at")
	run_id = _safe_run_id(f"{case_id}-{started_at or run_dir.name}")
	model = str(agent.get("model", "unknown"))
	agent_kind = str(run_result.get("agent_kind", "unknown"))
	runtime_mode = str(runtime.get("mode", "unknown"))
	score = oracle.get("score")
	expected_score = len(phases)
	score_ratio = (
		float(score) / expected_score
		if isinstance(score, (int, float)) and expected_score
		else None
	)
	result_events = [event for event in events if event.get("type") == "result"]
	result_event = result_events[-1] if result_events else {}
	if trace_format == "claude_code":
		reported_turns = [event.get("num_turns") for event in result_events]
		turn_count = sum(turns for turns in reported_turns if isinstance(turns, int))
	else:
		turn_count = sum(event.get("subtype") == "agent_message" for event in events)
	total_cost = result_event.get("total_cost_usd")
	total_cost = float(total_cost) if isinstance(total_cost, (int, float)) else None
	sessions = _sessions(events, model)
	meta = {
		"run_id": run_id,
		"case_id": case_id,
		"trace_format": trace_format,
		"prompt_profile": run_result.get("prompt_profile") or "aebench",
		"status": (
			oracle.get("status")
			if was_reevaluated
			else case_result.get("status") or run_result.get("status")
		),
		"agent_kind": agent_kind,
		"model": model,
		"reasoning_effort": reasoning_effort,
		"runtime": runtime_mode,
		"started_at": started_at,
		"finished_at": run_result.get("finished_at"),
		"duration_ms": run_result.get("duration_ms"),
		"score": score,
		"expected_score": expected_score,
		"event_count": len(events),
	}
	index_row = {
		"run_id": run_id,
		"prompt_profile": meta["prompt_profile"],
		"case_id": case_id,
		"model": model,
		"agent_kind": agent_kind,
		"reasoning_effort": reasoning_effort,
		"trace_format": trace_format,
		"runtime": runtime_mode,
		"status": meta["status"],
		"score_ratio": score_ratio,
		"score": score,
		"expected_score": expected_score,
		"duration_ms": run_result.get("duration_ms"),
		"num_turns": turn_count,
		"session_count": len(sessions),
		"total_cost_usd": total_cost,
		"started_at": started_at,
	}
	prompt_path = next(run_dir.glob("aebench_prompt_*.md"), None)
	prompt = prompt_path.read_text(encoding="utf-8") if prompt_path else ""
	summary: dict[str, Any] = {
		"agent_exit_code": agent.get("exit_code"),
		"prompt_profile": run_result.get("prompt_profile"),
		"usage": usage,
		"usage_total": {
			"input_tokens": usage.get("input_tokens"),
			"output_tokens": usage.get("output_tokens"),
			"cache_creation_input_tokens": usage.get(
				"cache_creation_input_tokens", usage.get("cache_write_input_tokens")
			),
			"cache_read_input_tokens": usage.get(
				"cache_read_input_tokens", usage.get("cached_input_tokens")
			),
		},
		"models": [model],
		"session_count": len(sessions),
		"session_ids": [session["session_id"] for session in sessions],
		"num_turns": turn_count,
	}
	if trace_format == "claude_code":
		init_raw = _claude_init(events)
		summary.update(
			tools_offered=sessions[0]["tools"],
			permission_mode=sessions[0]["permission_mode"],
			cwd=sessions[0]["cwd"],
			claude_code_version=init_raw.get("claude_code_version"),
			duration_ms=sum(
				duration
				for event in result_events
				if isinstance((duration := event.get("duration_ms")), int)
			),
			total_cost_usd=total_cost,
			stop_reasons=[
				reason
				for event in result_events
				if isinstance((reason := event.get("stop_reason")), str) and reason
			],
			final_result_text=result_event.get("result_text"),
		)
	return {
		"meta": meta,
		"index_row": index_row,
		"summary": summary,
		"sessions": sessions,
		"system_monitor": [],
		"case_brief": _redact_value(case_result.get("case_brief", {}), secrets),
		"oracle": _redact_value(oracle, secrets),
		"prompt": _redact(prompt, secrets),
		"events": events,
	}


def _current_oracle_result(
	run_dir: Path, case_result: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
	evaluations: list[tuple[str, str, dict[str, Any]]] = []
	for path in (run_dir / ORACLE_EVALUATIONS_DIR).glob(f"*/{EVALUATION_RECORD}"):
		record = _read_json_if_present(path)
		oracle = record.get("oracle_result")
		if isinstance(oracle, dict):
			evaluations.append((str(record.get("evaluated_at") or ""), str(path), oracle))
	if evaluations:
		return max(evaluations, key=lambda item: (item[0], item[1]))[2], True

	oracle = case_result.get("oracle_result")
	if isinstance(oracle, dict):
		return oracle, False
	return _read_json_if_present(run_dir / "oracle_result.json"), False


def _trace_format(agent_kind: str) -> str:
	if agent_kind in {"claude", "claude_non_api"}:
		return "claude_code"
	return "codex"


def _sessions(events: list[dict[str, Any]], fallback_model: str) -> list[dict[str, Any]]:
	sessions: dict[int, dict[str, Any]] = {}
	for event in events:
		session_idx = event.get("session_idx")
		if not isinstance(session_idx, int):
			continue
		session = sessions.setdefault(
			session_idx,
			{
				"session_idx": session_idx,
				"session_id": event.get("session_id"),
				"ts_start": event.get("ts"),
				"model": fallback_model,
				"cwd": None,
				"permission_mode": None,
				"tools": [],
				"slash_commands": [],
				"agents": [],
				"skills": [],
			},
		)
		if event.get("type") != "system" or event.get("subtype") != "init":
			continue
		raw = event.get("raw")
		if not isinstance(raw, dict):
			continue
		session.update(
			model=raw.get("model") or fallback_model,
			cwd=raw.get("cwd"),
			permission_mode=raw.get("permissionMode") or raw.get("permission_mode"),
			tools=raw.get("tools") if isinstance(raw.get("tools"), list) else [],
			slash_commands=(
				raw.get("slash_commands") if isinstance(raw.get("slash_commands"), list) else []
			),
			agents=raw.get("agents") if isinstance(raw.get("agents"), list) else [],
			skills=raw.get("skills") if isinstance(raw.get("skills"), list) else [],
		)
	return [sessions[index] for index in sorted(sessions)] or [
		{
			"session_idx": 0,
			"session_id": None,
			"ts_start": None,
			"model": fallback_model,
			"cwd": None,
			"permission_mode": None,
			"tools": [],
			"slash_commands": [],
			"agents": [],
			"skills": [],
		}
	]


def _claude_init(events: list[dict[str, Any]]) -> dict[str, Any]:
	for event in events:
		if event.get("type") == "system" and event.get("subtype") == "init":
			raw = event.get("raw")
			if isinstance(raw, dict):
				return raw
	return {}


def _update_index(path: Path, index_row: dict[str, Any]) -> None:
	index = _read_json_if_present(path)
	runs_value = index.get("runs")
	runs: list[dict[str, Any]] = (
		[run for run in runs_value if isinstance(run, dict)] if isinstance(runs_value, list) else []
	)
	runs = [run for run in runs if run.get("run_id") != index_row["run_id"]]
	runs.append(index_row)
	runs.sort(key=lambda run: str(run.get("started_at") or ""), reverse=True)
	path.write_text(
		json.dumps(
			{
				"runs": runs,
				"prompt_profiles": sorted(
					{str(run["prompt_profile"]) for run in runs if run.get("prompt_profile")}
				),
				"cases": sorted({str(run["case_id"]) for run in runs if run.get("case_id")}),
				"build_ts": runs[0].get("started_at") if runs else None,
			},
			indent=2,
		),
		encoding="utf-8",
	)


def _copy_assets(output_dir: Path) -> None:
	asset_root = files("trace_viewer").joinpath("assets")
	for name in ("index.html", "run.html", "config.js"):
		with asset_root.joinpath(name).open("rb") as source:
			with (output_dir / name).open("wb") as target:
				shutil.copyfileobj(source, target)
	output_assets = output_dir / "assets"
	output_assets.mkdir(exist_ok=True)
	for name in (
		"catalog.js",
		"index.js",
		"run.js",
		"theme.js",
		"tooltip.js",
		"styles.css",
	):
		with asset_root.joinpath(name).open("rb") as source:
			with (output_assets / name).open("wb") as target:
				shutil.copyfileobj(source, target)


def _read_last_jsonl(path: Path) -> dict[str, Any]:
	if not path.is_file():
		raise ValueError(f"run result does not exist: {path}")
	lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
	if not lines:
		raise ValueError(f"run result is empty: {path}")
	value = json.loads(lines[-1])
	if not isinstance(value, dict):
		raise ValueError(f"run result must contain a JSON object: {path}")
	return value


def _read_json(path: Path) -> dict[str, Any]:
	if not path.is_file():
		raise ValueError(f"case result does not exist: {path}")
	return _read_json_if_present(path)


def _read_json_if_present(path: Path) -> dict[str, Any]:
	if not path.is_file():
		return {}
	value = json.loads(path.read_text(encoding="utf-8"))
	return value if isinstance(value, dict) else {}


def _safe_run_id(value: str) -> str:
	return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def _environment_secrets() -> dict[str, str]:
	return {
		name: value
		for name, value in os.environ.items()
		if len(value) >= 8 and any(token in name.upper() for token in ("KEY", "TOKEN", "SECRET"))
	}


def _redact(text: str, secrets: Mapping[str, str]) -> str:
	for name, value in sorted(secrets.items(), key=lambda pair: len(pair[1]), reverse=True):
		text = text.replace(value, f"[REDACTED:{name}]")
	return text


def _redact_value(value: Any, secrets: Mapping[str, str]) -> Any:
	if isinstance(value, str):
		return _redact(value, secrets)
	if isinstance(value, list):
		return [_redact_value(item, secrets) for item in value]
	if isinstance(value, dict):
		return {key: _redact_value(item, secrets) for key, item in value.items()}
	return value
