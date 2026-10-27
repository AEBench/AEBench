from __future__ import annotations

from typing import Any

from trace_viewer.action_mapper import (
	ACTION_METADATA,
	SemanticAction,
	classify_command,
	classify_event,
	compute_action_summary,
	is_readonly_shell_command,
	unwrap_command,
)


def test_unwrap_command() -> None:
	assert unwrap_command("sudo time /bin/bash -lc 'ls -la'") == "ls -la"
	assert unwrap_command('bash -c "pip install numpy"') == "pip install numpy"
	assert unwrap_command("time pytest") == "pytest"


def test_classify_command_setup() -> None:
	assert classify_command("pip install torch") == SemanticAction.SETUP.value
	assert (
		classify_command("apt-get update && apt-get install -y libssl-dev")
		== SemanticAction.SETUP.value
	)
	assert classify_command("conda env create -f environment.yml") == SemanticAction.SETUP.value
	assert classify_command("export PATH=/opt/bin:$PATH") == SemanticAction.SETUP.value
	assert classify_command("source /venv/bin/activate") == SemanticAction.SETUP.value
	assert classify_command("wget https://example.com/dataset.tar.gz") == SemanticAction.SETUP.value
	assert classify_command("tar -xzf dataset.tar.gz") == SemanticAction.SETUP.value
	assert classify_command("unzip data.zip -d /data") == SemanticAction.SETUP.value
	assert classify_command("python download_data.py") == SemanticAction.SETUP.value
	assert (
		classify_command("python3 -c 'import zipfile; z=zipfile.ZipFile(\"/tmp/kondo-z3.zip\")'")
		== SemanticAction.SETUP.value
	)
	assert (
		classify_command("bash setup.sh", description="Install pinned requirements")
		== SemanticAction.SETUP.value
	)


def test_classify_command_build() -> None:
	assert classify_command("make -j4") == SemanticAction.BUILD.value
	assert classify_command("cmake -B build") == SemanticAction.BUILD.value
	assert classify_command("ninja -C build") == SemanticAction.BUILD.value
	assert classify_command("cargo build --release") == SemanticAction.BUILD.value
	assert classify_command("tail -20 /repo/build.log") == SemanticAction.BUILD.value


def test_classify_command_exploration_is_setup() -> None:
	assert classify_command("ls -la /workspace") == SemanticAction.SETUP.value
	assert classify_command("find . -name '*.py'") == SemanticAction.SETUP.value
	assert classify_command("cat README.md") == SemanticAction.SETUP.value
	assert classify_command("rg 'def evaluate' src/") == SemanticAction.SETUP.value
	assert classify_command("git status --short") == SemanticAction.SETUP.value
	assert classify_command("sed -n '227,249p' log.txt") == SemanticAction.SETUP.value
	assert (
		classify_command(
			"sed -n '1,220p' local-dafny/Makefile && sed -n '1,200p' kondoPrototypes/verify-all"
		)
		== SemanticAction.SETUP.value
	)
	assert (
		is_readonly_shell_command(
			"sed -n '1,220p' local-dafny/Makefile && sed -n '1,200p' kondoPrototypes/verify-all"
		)
		is True
	)


def test_classify_command_run() -> None:
	assert classify_command("pytest tests/") == SemanticAction.RUN.value
	assert classify_command("./run.sh") == SemanticAction.RUN.value
	assert classify_command("./reproduce_eval.sh") == SemanticAction.RUN.value
	assert classify_command("./verify-all > /tmp/kondo-verify-all.log") == SemanticAction.RUN.value
	assert (
		classify_command("./simplifiedLeaderElection/sync/verify > /tmp/sync.log")
		== SemanticAction.RUN.value
	)
	assert classify_command("python3 benchmark.py --epochs 10") == SemanticAction.RUN.value
	assert classify_command("make test") == SemanticAction.RUN.value


def test_classify_command_verify() -> None:
	assert classify_command("cat /refs/sloc.ref.json") == SemanticAction.VERIFY.value
	assert classify_command("diff output.csv /refs/gold.csv") == SemanticAction.VERIFY.value
	assert classify_command("sha256sum dataset.tar.gz") == SemanticAction.VERIFY.value
	assert classify_command("python plot_figures.py") == SemanticAction.VERIFY.value
	assert classify_command("tail -20 /tmp/kondo-verify-all.log") == SemanticAction.VERIFY.value
	assert (
		classify_command("rg -n 'Verifying Flexible' /tmp/kondo-verify-all.log")
		== SemanticAction.VERIFY.value
	)


def test_classify_command_modify() -> None:
	assert classify_command("sed -i 's/foo/bar/g' script.py") == SemanticAction.MODIFY.value
	assert classify_command("patch -p1 < fix.patch") == SemanticAction.MODIFY.value


def test_classify_command_misc() -> None:
	assert classify_command("kill -9 12345") == SemanticAction.MISC.value
	assert classify_command("ps aux") == SemanticAction.MISC.value


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
	action, label = classify_event(bash_event, trace_format="claude_code")
	assert action == SemanticAction.SETUP.value
	assert label == ACTION_METADATA[SemanticAction.SETUP.value]["short_label"]

	read_event = {
		"type": "assistant",
		"blocks": [{"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}}],
	}
	action, _ = classify_event(read_event, trace_format="claude_code")
	assert action == SemanticAction.SETUP.value

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
	action, _ = classify_event(read_task_output_event, trace_format="claude_code")
	assert action == SemanticAction.VERIFY.value

	thinking_event = {
		"type": "assistant",
		"blocks": [{"type": "thinking", "thinking": "Analyzing the algorithm steps..."}],
	}
	action, _ = classify_event(thinking_event, trace_format="claude_code")
	assert action == SemanticAction.THINK.value

	edit_event = {
		"type": "assistant",
		"blocks": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "main.py"}}],
	}
	action, _ = classify_event(edit_event, trace_format="claude_code")
	assert action == SemanticAction.MODIFY.value


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
	action, label = classify_event(user_event, trace_format="claude_code")
	assert action == SemanticAction.MISC.value
	assert label == ACTION_METADATA[SemanticAction.MISC.value]["short_label"]


def test_classify_event_codex() -> None:
	cmd_event = {
		"type": "codex_item",
		"item": {
			"type": "command_execution",
			"command": "make -j8",
		},
	}
	action, _ = classify_event(cmd_event, trace_format="codex")
	assert action == SemanticAction.BUILD.value

	file_event = {
		"type": "codex_item",
		"item": {
			"type": "file_change",
			"changes": [{"path": "a.txt", "kind": "add"}],
		},
	}
	action, _ = classify_event(file_event, trace_format="codex")
	assert action == SemanticAction.MODIFY.value

	reasoning_event = {
		"type": "codex_item",
		"item": {
			"type": "reasoning",
			"text": "Checking if dependencies are installed.",
		},
	}
	action, _ = classify_event(reasoning_event, trace_format="codex")
	assert action == SemanticAction.THINK.value

	message_event = {
		"type": "codex_item",
		"item": {
			"type": "agent_message",
			"text": "I will inspect the repository before running tests.",
		},
	}
	action, _ = classify_event(message_event, trace_format="codex")
	assert action == SemanticAction.THINK.value

	verified_msg_event = {
		"type": "codex_item",
		"item": {
			"type": "agent_message",
			"text": "The full verify-all run completed with 31 verification summaries, zero errors.",
		},
	}
	action, _ = classify_event(verified_msg_event, trace_format="codex")
	assert action == SemanticAction.THINK.value

	search_event = {
		"type": "codex_item",
		"item": {
			"type": "web_search",
			"query": "how to install dotnet 6 on debian 12",
		},
	}
	action, _ = classify_event(search_event, trace_format="codex")
	assert action == SemanticAction.THINK.value


def test_classify_event_session_init() -> None:
	init_event = {"type": "system", "subtype": "init"}
	action, label = classify_event(init_event, trace_format="claude_code")
	assert action == SemanticAction.SETUP.value
	assert label == ACTION_METADATA[SemanticAction.SETUP.value]["short_label"]

	codex_init = {"type": "system", "subtype": "init"}
	action_codex, label_codex = classify_event(codex_init, trace_format="codex")
	assert action_codex == SemanticAction.SETUP.value
	assert label_codex == ACTION_METADATA[SemanticAction.SETUP.value]["short_label"]


def test_compute_action_summary() -> None:
	events: list[dict[str, Any]] = [
		{"action": SemanticAction.SETUP.value},
		{"action": SemanticAction.SETUP.value},
		{"action": SemanticAction.BUILD.value},
		{"action": SemanticAction.RUN.value},
		{"action": SemanticAction.MISC.value},
	]
	summary = compute_action_summary(events)
	assert summary["total_events"] == 5
	assert summary["counts"][SemanticAction.SETUP.value] == 2
	assert summary["percentages"][SemanticAction.SETUP.value] == 40.0
	assert set(summary["actions_present"]) == {
		SemanticAction.SETUP.value,
		SemanticAction.BUILD.value,
		SemanticAction.RUN.value,
		SemanticAction.MISC.value,
	}


def test_classify_event_user_tool_result_inheritance() -> None:
	tool_map = {
		"toolu_run_1": SemanticAction.RUN.value,
		"toolu_verify_1": SemanticAction.VERIFY.value,
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
	action, label = classify_event(
		run_result_event, trace_format="claude_code", tool_action_map=tool_map
	)
	assert action == SemanticAction.RUN.value
	assert label == ACTION_METADATA[SemanticAction.RUN.value]["short_label"]

	# Verify tool_result event
	verify_result_event = {
		"type": "user",
		"blocks": [{"type": "tool_result", "tool_use_id": "toolu_verify_1", "content": "diff ok"}],
	}
	action, label = classify_event(
		verify_result_event, trace_format="claude_code", tool_action_map=tool_map
	)
	assert action == SemanticAction.VERIFY.value
	assert label == ACTION_METADATA[SemanticAction.VERIFY.value]["short_label"]

	# Unmapped tool_result falls back to misc
	unmapped_event = {
		"type": "user",
		"blocks": [{"type": "tool_result", "tool_use_id": "toolu_unknown", "content": "foo"}],
	}
	action, label = classify_event(
		unmapped_event, trace_format="claude_code", tool_action_map=tool_map
	)
	assert action == SemanticAction.MISC.value


def test_classify_event_system_task_started_inheritance() -> None:
	tool_map = {
		"toolu_bg_1": SemanticAction.RUN.value,
	}

	# Background task with tool_use_id inherits tool action
	task_event = {
		"type": "system",
		"subtype": "task_started",
		"raw": {"tool_use_id": "toolu_bg_1", "description": "Wait for all scripts done"},
	}
	action, label = classify_event(task_event, trace_format="claude_code", tool_action_map=tool_map)
	assert action == SemanticAction.RUN.value
	assert label == ACTION_METADATA[SemanticAction.RUN.value]["short_label"]
