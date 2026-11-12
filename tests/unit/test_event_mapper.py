from __future__ import annotations

from typing import Any

from trace_viewer.event_mapper import (
	EVENT_METADATA,
	SemanticEvent,
	classify_command,
	classify_event,
	compute_event_summary,
	is_readonly_shell_command,
	unwrap_command,
)


def test_unwrap_command() -> None:
	assert unwrap_command("sudo time /bin/bash -lc 'ls -la'") == "ls -la"
	assert unwrap_command('bash -c "pip install numpy"') == "pip install numpy"
	assert unwrap_command("time pytest") == "pytest"


def test_classify_command_setup() -> None:
	assert classify_command("pip install torch") == SemanticEvent.SETUP.value
	assert (
		classify_command("apt-get update && apt-get install -y libssl-dev")
		== SemanticEvent.SETUP.value
	)
	assert classify_command("conda env create -f environment.yml") == SemanticEvent.SETUP.value
	assert classify_command("export PATH=/opt/bin:$PATH") == SemanticEvent.SETUP.value
	assert classify_command("source /venv/bin/activate") == SemanticEvent.SETUP.value
	assert classify_command("wget https://example.com/dataset.tar.gz") == SemanticEvent.SETUP.value
	assert classify_command("tar -xzf dataset.tar.gz") == SemanticEvent.SETUP.value
	assert classify_command("unzip data.zip -d /data") == SemanticEvent.SETUP.value
	assert classify_command("python download_data.py") == SemanticEvent.SETUP.value
	assert (
		classify_command("python3 -c 'import zipfile; z=zipfile.ZipFile(\"/tmp/kondo-z3.zip\")'")
		== SemanticEvent.SETUP.value
	)
	assert (
		classify_command("bash setup.sh", description="Install pinned requirements")
		== SemanticEvent.SETUP.value
	)


def test_classify_command_build() -> None:
	assert classify_command("make -j4") == SemanticEvent.BUILD.value
	assert classify_command("cmake -B build") == SemanticEvent.BUILD.value
	assert classify_command("ninja -C build") == SemanticEvent.BUILD.value
	assert classify_command("cargo build --release") == SemanticEvent.BUILD.value
	assert classify_command("tail -20 /repo/build.log") == SemanticEvent.BUILD.value


def test_classify_command_exploration_is_setup() -> None:
	assert classify_command("ls -la /workspace") == SemanticEvent.SETUP.value
	assert classify_command("find . -name '*.py'") == SemanticEvent.SETUP.value
	assert classify_command("cat README.md") == SemanticEvent.SETUP.value
	assert classify_command("rg 'def evaluate' src/") == SemanticEvent.SETUP.value
	assert classify_command("git status --short") == SemanticEvent.SETUP.value
	assert classify_command("sed -n '227,249p' log.txt") == SemanticEvent.SETUP.value
	assert (
		classify_command(
			"sed -n '1,220p' local-dafny/Makefile && sed -n '1,200p' kondoPrototypes/verify-all"
		)
		== SemanticEvent.SETUP.value
	)
	assert (
		is_readonly_shell_command(
			"sed -n '1,220p' local-dafny/Makefile && sed -n '1,200p' kondoPrototypes/verify-all"
		)
		is True
	)


def test_classify_command_run() -> None:
	assert classify_command("pytest tests/") == SemanticEvent.RUN.value
	assert classify_command("./run.sh") == SemanticEvent.RUN.value
	assert classify_command("./reproduce_eval.sh") == SemanticEvent.RUN.value
	assert classify_command("./verify-all > /tmp/kondo-verify-all.log") == SemanticEvent.RUN.value
	assert (
		classify_command("./simplifiedLeaderElection/sync/verify > /tmp/sync.log")
		== SemanticEvent.RUN.value
	)
	assert classify_command("python3 benchmark.py --epochs 10") == SemanticEvent.RUN.value
	assert classify_command("make test") == SemanticEvent.RUN.value


def test_classify_command_verify() -> None:
	assert classify_command("cat /refs/sloc.ref.json") == SemanticEvent.VERIFY.value
	assert classify_command("diff output.csv /refs/gold.csv") == SemanticEvent.VERIFY.value
	assert classify_command("sha256sum dataset.tar.gz") == SemanticEvent.VERIFY.value
	assert classify_command("python plot_figures.py") == SemanticEvent.VERIFY.value
	assert classify_command("tail -20 /tmp/kondo-verify-all.log") == SemanticEvent.VERIFY.value
	assert (
		classify_command("rg -n 'Verifying Flexible' /tmp/kondo-verify-all.log")
		== SemanticEvent.VERIFY.value
	)


def test_classify_command_modify() -> None:
	assert classify_command("sed -i 's/foo/bar/g' script.py") == SemanticEvent.MODIFY.value
	assert classify_command("patch -p1 < fix.patch") == SemanticEvent.MODIFY.value


def test_classify_command_misc() -> None:
	assert classify_command("kill -9 12345") == SemanticEvent.MISC.value
	assert classify_command("ps aux") == SemanticEvent.MISC.value


def test_classify_event_claude() -> None:
	bash_event = {
		"type": "assistant",
		"blocks": [
			{
				"type": "tool_use",
				"name": "Bash",
				"input": {"command": "pip install pandas"},
			}
		],
	}
	sem_event, label = classify_event(bash_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.SETUP.value
	assert label == EVENT_METADATA[SemanticEvent.SETUP.value]["short_label"]

	read_event = {
		"type": "assistant",
		"blocks": [{"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}}],
	}
	sem_event, _ = classify_event(read_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.SETUP.value

	read_task_output_event = {
		"type": "assistant",
		"blocks": [
			{
				"type": "tool_use",
				"name": "Read",
				"input": {"file_path": "/tmp/claude-0/tasks/b0a7e4a.output"},
			}
		],
	}
	sem_event, _ = classify_event(read_task_output_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.VERIFY.value

	thinking_event = {
		"type": "assistant",
		"blocks": [{"type": "thinking", "thinking": "Analyzing the algorithm steps..."}],
	}
	sem_event, _ = classify_event(thinking_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.THINK.value

	edit_event = {
		"type": "assistant",
		"blocks": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "main.py"}}],
	}
	sem_event, _ = classify_event(edit_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.MODIFY.value


def test_classify_event_claude_user_is_misc() -> None:
	user_event = {
		"type": "user",
		"blocks": [
			{
				"type": "tool_result",
				"tool_use_id": "toolu_bash_1",
				"content": "output",
			}
		],
	}
	sem_event, label = classify_event(user_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.MISC.value
	assert label == EVENT_METADATA[SemanticEvent.MISC.value]["short_label"]


def test_classify_event_codex() -> None:
	cmd_event = {
		"type": "codex_item",
		"item": {
			"type": "command_execution",
			"command": "make -j8",
		},
	}
	sem_event, _ = classify_event(cmd_event, trace_format="codex")
	assert sem_event == SemanticEvent.BUILD.value

	file_event = {
		"type": "codex_item",
		"item": {
			"type": "file_change",
			"changes": [{"path": "a.txt", "kind": "add"}],
		},
	}
	sem_event, _ = classify_event(file_event, trace_format="codex")
	assert sem_event == SemanticEvent.MODIFY.value

	reasoning_event = {
		"type": "codex_item",
		"item": {
			"type": "reasoning",
			"text": "Checking if dependencies are installed.",
		},
	}
	sem_event, _ = classify_event(reasoning_event, trace_format="codex")
	assert sem_event == SemanticEvent.THINK.value

	message_event = {
		"type": "codex_item",
		"item": {
			"type": "agent_message",
			"text": "I will inspect the repository before running tests.",
		},
	}
	sem_event, _ = classify_event(message_event, trace_format="codex")
	assert sem_event == SemanticEvent.THINK.value

	verified_msg_event = {
		"type": "codex_item",
		"item": {
			"type": "agent_message",
			"text": "The full verify-all run completed with 31 verification summaries, zero errors.",
		},
	}
	sem_event, _ = classify_event(verified_msg_event, trace_format="codex")
	assert sem_event == SemanticEvent.THINK.value

	search_event = {
		"type": "codex_item",
		"item": {
			"type": "web_search",
			"query": "how to install dotnet 6 on debian 12",
		},
	}
	sem_event, _ = classify_event(search_event, trace_format="codex")
	assert sem_event == SemanticEvent.THINK.value


def test_classify_event_session_init() -> None:
	init_event = {"type": "system", "subtype": "init"}
	sem_event, label = classify_event(init_event, trace_format="claude_code")
	assert sem_event == SemanticEvent.SETUP.value
	assert label == EVENT_METADATA[SemanticEvent.SETUP.value]["short_label"]

	codex_init = {"type": "system", "subtype": "init"}
	event_codex, label_codex = classify_event(codex_init, trace_format="codex")
	assert event_codex == SemanticEvent.SETUP.value
	assert label_codex == EVENT_METADATA[SemanticEvent.SETUP.value]["short_label"]


def test_compute_event_summary() -> None:
	events: list[dict[str, Any]] = [
		{"event": SemanticEvent.SETUP.value},
		{"event": SemanticEvent.SETUP.value},
		{"event": SemanticEvent.BUILD.value},
		{"event": SemanticEvent.RUN.value},
		{"event": SemanticEvent.MISC.value},
	]
	summary = compute_event_summary(events)
	assert summary["total_events"] == 5
	assert summary["counts"][SemanticEvent.SETUP.value] == 2
	assert summary["percentages"][SemanticEvent.SETUP.value] == 40.0
	assert set(summary["events_present"]) == {
		SemanticEvent.SETUP.value,
		SemanticEvent.BUILD.value,
		SemanticEvent.RUN.value,
		SemanticEvent.MISC.value,
	}


def test_classify_event_user_tool_result_inheritance() -> None:
	tool_map = {
		"toolu_run_1": SemanticEvent.RUN.value,
		"toolu_verify_1": SemanticEvent.VERIFY.value,
	}

	# Run tool_result event
	run_result_event = {
		"type": "user",
		"blocks": [
			{
				"type": "tool_result",
				"tool_use_id": "toolu_run_1",
				"content": "Benchmark done in 10s",
			}
		],
	}
	sem_event, label = classify_event(
		run_result_event, trace_format="claude_code", tool_event_map=tool_map
	)
	assert sem_event == SemanticEvent.RUN.value
	assert label == EVENT_METADATA[SemanticEvent.RUN.value]["short_label"]

	# Verify tool_result event
	verify_result_event = {
		"type": "user",
		"blocks": [{"type": "tool_result", "tool_use_id": "toolu_verify_1", "content": "diff ok"}],
	}
	sem_event, label = classify_event(
		verify_result_event, trace_format="claude_code", tool_event_map=tool_map
	)
	assert sem_event == SemanticEvent.VERIFY.value
	assert label == EVENT_METADATA[SemanticEvent.VERIFY.value]["short_label"]

	# Unmapped tool_result falls back to misc
	unmapped_event = {
		"type": "user",
		"blocks": [{"type": "tool_result", "tool_use_id": "toolu_unknown", "content": "foo"}],
	}
	sem_event, label = classify_event(
		unmapped_event, trace_format="claude_code", tool_event_map=tool_map
	)
	assert sem_event == SemanticEvent.MISC.value


def test_classify_event_system_task_started_inheritance() -> None:
	tool_map = {
		"toolu_bg_1": SemanticEvent.RUN.value,
	}

	# Background task with tool_use_id inherits tool event
	task_event = {
		"type": "system",
		"subtype": "task_started",
		"raw": {"tool_use_id": "toolu_bg_1", "description": "Wait for all scripts done"},
	}
	sem_event, label = classify_event(
		task_event, trace_format="claude_code", tool_event_map=tool_map
	)
	assert sem_event == SemanticEvent.RUN.value
	assert label == EVENT_METADATA[SemanticEvent.RUN.value]["short_label"]
