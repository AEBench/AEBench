from __future__ import annotations

import json
from pathlib import Path

from trace_viewer import export_trace_site
from trace_viewer.exporter import parse_claude_trace, parse_codex_trace


def test_parse_codex_trace_reads_completed_items_and_usage(tmp_path: Path) -> None:
	trace = tmp_path / "runner_output.log"
	trace.write_text(
		"Reading prompt from stdin...\n"
		'[2026-08-06T04:42:07Z] {"type":"thread.started","thread_id":"thread-1"}\n'
		'{"type":"item.started","item":{"id":"item-1","type":"command_execution",'
		'"command":"echo secret-value","status":"in_progress"}}\n'
		'{"type":"item.completed","item":{"id":"item-1","type":"command_execution",'
		'"command":"echo secret-value","aggregated_output":"secret-value\\n",'
		'"exit_code":0,"status":"completed"}}\n'
		'{"type":"item.completed","item":{"id":"item-2","type":"agent_message",'
		'"text":"done"}}\n'
		'{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}\n',
		encoding="utf-8",
	)

	events, usage = parse_codex_trace(trace, secrets={"TEST_TOKEN": "secret-value"})

	assert [event["type"] for event in events] == [
		"system",
		"codex_item",
		"codex_item",
	]
	assert events[0]["session_id"] == "thread-1"
	assert events[0]["ts"] == "2026-08-06T04:42:07Z"
	assert events[0]["raw"] == {"thread_id": "thread-1"}
	assert events[1]["session_id"] == "thread-1"
	assert events[1]["item"]["command"] == "echo [REDACTED:TEST_TOKEN]"
	assert events[1]["item"]["aggregated_output"] == "[REDACTED:TEST_TOKEN]\n"
	assert events[1]["item"]["exit_code"] == 0
	assert events[2]["item"]["text"] == "done"
	assert usage == {"input_tokens": 12, "output_tokens": 3}


def test_parse_claude_trace_normalizes_messages_and_result(tmp_path: Path) -> None:
	trace = tmp_path / "runner_output.log"
	trace.write_text(
		"Reading prompt from stdin...\n"
		'{"type":"system","subtype":"init","session_id":"session-1","uuid":"init-1",'
		'"cwd":"/repo","model":"claude-opus-4-8","permissionMode":"bypassPermissions",'
		'"tools":["Bash"]}\n'
		'{"type":"system","subtype":"thinking_tokens","session_id":"session-1",'
		'"estimated_tokens":100}\n'
		'{"type":"assistant","session_id":"session-1","uuid":"message-1",'
		'"timestamp":"2026-08-06T04:42:21Z","message":{"model":"claude-opus-4-8",'
		'"usage":{"input_tokens":10,"output_tokens":4},'
		'"content":[{"type":"tool_use","id":"tool-1","name":"Bash",'
		'"input":{"command":"echo secret-value"}}]}}\n'
		'{"type":"assistant","session_id":"session-1","uuid":"message-2",'
		'"message":{"model":"claude-opus-4-8",'
		'"usage":{"input_tokens":2,"output_tokens":1},'
		'"content":[{"type":"text","text":"done"}]}}\n'
		'{"type":"user","session_id":"session-1","uuid":"message-3",'
		'"message":{"content":[{"type":"tool_result","tool_use_id":"tool-1",'
		'"content":"secret-value"}]}}\n'
		'{"type":"result","subtype":"success","session_id":"session-1","uuid":"result-1",'
		'"duration_ms":1200,"num_turns":1,"total_cost_usd":0.25,'
		'"result":"done","usage":{"input_tokens":10,"output_tokens":4}}\n',
		encoding="utf-8",
	)

	events, usage = parse_claude_trace(trace, secrets={"TEST_TOKEN": "secret-value"})

	assert [event["type"] for event in events] == [
		"system",
		"assistant",
		"assistant",
		"user",
		"result",
	]
	assert events[0]["subtype"] == "init"
	assert "uuid" not in events[0]
	assert events[1]["blocks"][0]["input"]["command"] == "echo [REDACTED:TEST_TOKEN]"
	assert events[2]["blocks"] == [{"type": "text", "text": "done"}]
	assert events[3]["blocks"][0]["content"] == "[REDACTED:TEST_TOKEN]"
	assert events[4]["num_turns"] == 1
	assert events[4]["total_cost_usd"] == 0.25
	assert events[4]["result_text"] == "done"
	assert "uuid" not in events[4]
	assert usage == {"input_tokens": 12, "output_tokens": 5}


def test_parse_claude_trace_preserves_background_task_type(tmp_path: Path) -> None:
	trace = tmp_path / "runner_output.log"
	trace.write_text(
		'{"type":"system","subtype":"task_started","session_id":"session-1",'
		'"task_id":"task-1","tool_use_id":"tool-1","description":"Install libicu",'
		'"task_type":"local_bash"}\n'
		'{"type":"system","subtype":"task_notification","session_id":"session-1",'
		'"task_id":"task-1","tool_use_id":"tool-1","status":"completed",'
		'"summary":"Install libicu"}\n',
		encoding="utf-8",
	)

	events, _ = parse_claude_trace(trace)

	assert [event["subtype"] for event in events] == [
		"task_started",
		"task_notification",
	]
	assert [event["task_type"] for event in events] == ["local_bash", "local_bash"]
	assert "task_type" not in events[1]["raw"]


def test_export_trace_site_writes_run_data_and_updates_index(tmp_path: Path) -> None:
	run_dir = tmp_path / "run"
	run_dir.mkdir()
	(run_dir / "runner_output.log").write_text(
		'{"type":"item.completed","item":{"id":"item-1",'
		'"type":"agent_message","text":"complete"}}\n'
		'{"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":4}}\n',
		encoding="utf-8",
	)
	(run_dir / "result.jsonl").write_text(
		json.dumps(
			{
				"id": "case-one",
				"status": "success",
				"started_at": "2026-08-05T07:28:32Z",
				"finished_at": "2026-08-05T07:29:32Z",
				"duration_ms": 60_000,
				"prompt_profile": "artifact-eval-v1",
				"runtime": {"mode": "docker"},
				"agent_kind": "codex_non_api",
				"agent": {
					"model": "gpt-5.5",
					"exit_code": 0,
					"reasoning_effort": "high",
				},
			}
		)
		+ "\n",
		encoding="utf-8",
	)
	(run_dir / "case_result.json").write_text(
		json.dumps(
			{
				"status": "success",
				"case_brief": {"core_claim": "claim"},
				"oracle_result": {
					"status": "success",
					"score": 1,
					"summary": "Passed 1/1 phases.",
					"phases": [{"phase": "experiment_runs", "status": "success"}],
				},
			}
		),
		encoding="utf-8",
	)
	(run_dir / "aebench_prompt_case-one.md").write_text("# Prompt\n", encoding="utf-8")
	site_dir = tmp_path / "site"

	index_path = export_trace_site(run_dir, site_dir)

	assert index_path == site_dir / "index.html"
	assert (site_dir / "run.html").is_file()
	assert (site_dir / "config.js").is_file()
	assert (site_dir / "assets" / "run.js").is_file()
	assert (site_dir / "assets" / "styles.css").is_file()
	index = json.loads((site_dir / "data" / "index.json").read_text(encoding="utf-8"))
	assert len(index["runs"]) == 1
	meta = index["runs"][0]
	assert meta["case_id"] == "case-one"
	assert meta["model"] == "gpt-5.5"
	assert meta["score"] == 1
	assert meta["score_ratio"] == 1.0
	assert meta["prompt_profile"] == "artifact-eval-v1"
	assert meta["reasoning_effort"] == "high"
	assert index["prompt_profiles"] == ["artifact-eval-v1"]
	record = json.loads((site_dir / "data" / f"{meta['run_id']}.json").read_text(encoding="utf-8"))
	assert record["meta"]["reasoning_effort"] == "high"
	assert record["meta"]["prompt_profile"] == "artifact-eval-v1"
	assert record["summary"]["usage"] == {"input_tokens": 20, "output_tokens": 4}
	assert record["prompt"] == "# Prompt\n"
	assert record["events"][0]["item"]["text"] == "complete"


def test_export_trace_site_uses_latest_oracle_reevaluation(tmp_path: Path) -> None:
	run_dir = tmp_path / "run"
	run_dir.mkdir()
	(run_dir / "runner_output.log").write_text(
		'{"type":"turn.completed","usage":{}}\n', encoding="utf-8"
	)
	(run_dir / "result.jsonl").write_text(
		json.dumps(
			{
				"id": "case-one",
				"status": "error",
				"started_at": "2026-08-05T07:28:32Z",
				"runtime": {"mode": "docker"},
				"agent_kind": "codex_non_api",
				"agent": {"model": "gpt-5.5", "exit_code": 0},
			}
		)
		+ "\n",
		encoding="utf-8",
	)
	(run_dir / "case_result.json").write_text(
		json.dumps(
			{
				"status": "error",
				"oracle_result": {
					"status": "error",
					"score": 1,
					"phases": [{"phase": "env_check", "status": "success"}],
				},
			}
		),
		encoding="utf-8",
	)
	for name, evaluated_at, score in (
		("first", "2026-08-06T05:00:00Z", 2),
		("second", "2026-08-06T06:00:00Z", 4),
	):
		evaluation_dir = run_dir / "oracle-evaluations" / name
		evaluation_dir.mkdir(parents=True)
		(evaluation_dir / "evaluation.json").write_text(
			json.dumps(
				{
					"evaluated_at": evaluated_at,
					"oracle_result": {
						"status": "success" if score == 4 else "error",
						"score": score,
						"phases": [
							{"phase": f"phase-{index}", "status": "success"} for index in range(4)
						],
					},
				}
			),
			encoding="utf-8",
		)

	site_dir = tmp_path / "site"
	export_trace_site(run_dir, site_dir)

	index = json.loads((site_dir / "data" / "index.json").read_text(encoding="utf-8"))
	meta = index["runs"][0]
	assert meta["status"] == "success"
	assert meta["score"] == 4
	assert meta["expected_score"] == 4
	record = json.loads((site_dir / "data" / f"{meta['run_id']}.json").read_text(encoding="utf-8"))
	assert record["oracle"]["score"] == 4


def test_export_trace_site_detects_claude_runs(tmp_path: Path) -> None:
	run_dir = tmp_path / "run"
	run_dir.mkdir()
	(run_dir / "runner_output.log").write_text(
		'{"type":"system","subtype":"init","session_id":"session-1",'
		'"cwd":"/repo","model":"claude-opus-4-8","tools":["Bash"]}\n'
		'{"type":"assistant","session_id":"session-1","message":{'
		'"model":"claude-opus-4-8","usage":{"input_tokens":20,"output_tokens":4,'
		'"cache_creation_input_tokens":5,"cache_read_input_tokens":6},'
		'"content":[{"type":"text","text":"complete"}]}}\n'
		'{"type":"result","subtype":"success","session_id":"session-1",'
		'"duration_ms":400,"num_turns":1,"total_cost_usd":0.3,"result":"first"}\n'
		'{"type":"assistant","session_id":"session-1","message":{'
		'"model":"claude-opus-4-8","usage":{"input_tokens":2,"output_tokens":1},'
		'"content":[{"type":"text","text":"complete"}]}}\n'
		'{"type":"result","subtype":"success","session_id":"session-1",'
		'"duration_ms":600,"num_turns":1,"total_cost_usd":0.5,"result":"complete"}\n',
		encoding="utf-8",
	)
	(run_dir / "result.jsonl").write_text(
		json.dumps(
			{
				"id": "case-one",
				"status": "success",
				"started_at": "2026-08-06T04:41:59Z",
				"duration_ms": 1_000,
				"runtime": {"mode": "docker"},
				"agent_kind": "claude_non_api",
				"agent": {"model": "claude-opus-4-8", "exit_code": 0},
			}
		)
		+ "\n",
		encoding="utf-8",
	)
	(run_dir / "case_result.json").write_text(
		json.dumps(
			{
				"status": "success",
				"oracle_result": {
					"score": 1,
					"phases": [{"phase": "experiment_runs", "status": "success"}],
				},
			}
		),
		encoding="utf-8",
	)
	site_dir = tmp_path / "site"

	export_trace_site(run_dir, site_dir)

	index = json.loads((site_dir / "data" / "index.json").read_text(encoding="utf-8"))
	assert index["runs"][0]["trace_format"] == "claude_code"
	assert index["runs"][0]["num_turns"] == 2
	assert index["runs"][0]["total_cost_usd"] == 0.5
	record_path = site_dir / "data" / f"{index['runs'][0]['run_id']}.json"
	record = json.loads(record_path.read_text(encoding="utf-8"))
	assert record["sessions"][0]["cwd"] == "/repo"
	assert record["sessions"][0]["tools"] == ["Bash"]
	assert record["summary"]["usage_total"]["cache_creation_input_tokens"] == 5
	assert record["summary"]["usage_total"]["cache_read_input_tokens"] == 6
	assert record["summary"]["usage_total"]["input_tokens"] == 22
	assert record["summary"]["usage_total"]["output_tokens"] == 5
	assert record["summary"]["duration_ms"] == 1_000
	assert record["summary"]["final_result_text"] == "complete"
	assert record["events"][1]["blocks"][0]["text"] == "complete"
