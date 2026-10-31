from __future__ import annotations

from typing import Any

from src.trace_viewer.action_mapper import SemanticAction
from src.trace_viewer.graph_builder import (
	build_trajectory_graph,
	canonical_node_signature,
	detect_outcome,
)


def test_detect_outcome() -> None:
	assert detect_outcome("error: command not found") == "failure"
	assert detect_outcome("AssertionError in line 42") == "failure"
	assert detect_outcome("Everything ok, 100% passed") == "success"
	assert detect_outcome("Some regular output with no indicators") == "neutral"
	assert detect_outcome("Random text", exit_code=1) == "failure"
	assert detect_outcome("Random text", exit_code=0) == "success"
	assert detect_outcome("Random text", is_error=True) == "failure"
	assert detect_outcome("=== OVERALL: FAIL ===") == "failure"
	assert detect_outcome("<tool_use_error>Sibling tool call errored</tool_use_error>") == "failure"
	assert (
		detect_outcome(
			"Error: value in source code", exit_code=0, tool="Read", command="src/main.rs"
		)
		== "neutral"
	)
	assert (
		detect_outcome(
			"Error: value in source code", exit_code=0, tool="Bash", command="cat src/main.rs"
		)
		== "neutral"
	)


def test_canonical_node_signature_bash() -> None:
	sig1, label1, cmd1 = canonical_node_signature("Bash", "sudo make -j8", "build")
	sig2, label2, cmd2 = canonical_node_signature("Bash", "make -j8", "build")
	assert sig1 == sig2
	assert cmd1 == "make -j8"
	assert label1 == "make -j8"


def test_canonical_node_signature_files() -> None:
	sig, label, cmd = canonical_node_signature("Read", "/path/to/main.py", "setup")
	assert sig == "Read::/path/to/main.py"
	assert label == "Read main.py"
	assert cmd == "/path/to/main.py"


def test_polling_command_normalization_collapses_signatures() -> None:
	sig1, _, _ = canonical_node_signature(
		"Bash",
		"ps -ef | rg 'verify-all' && wc -l /tmp/kondo-verify-all.log && tail -n 20 /tmp/kondo-verify-all.log",
		"verify",
	)
	sig2, _, _ = canonical_node_signature(
		"Bash",
		"tail -n 28 /tmp/kondo-verify-all.log; wc -l /tmp/kondo-verify-all.log",
		"verify",
	)
	assert sig1 == sig2


def test_empty_events_build_graph() -> None:
	graph = build_trajectory_graph([])
	assert len(graph.nodes) == 0
	assert len(graph.edges) == 0
	assert graph.stats["total_steps"] == 0
	assert graph.stats["compression_ratio"] == 0.0


def test_claude_events_deduplication_and_option_a_thinking() -> None:
	events: list[dict[str, Any]] = [
		# Event 0: Assistant thinks, then runs make -j8
		{
			"type": "assistant",
			"action": SemanticAction.BUILD.value,
			"ts": "2026-10-19T01:00:00Z",
			"blocks": [
				{"type": "thinking", "thinking": "Let us compile the artifact first."},
				{
					"type": "tool_use",
					"id": "tool_1",
					"name": "Bash",
					"input": {"command": "make -j8"},
				},
			],
		},
		# Event 1: Tool result for make -j8 (error)
		{
			"type": "user",
			"action": SemanticAction.BUILD.value,
			"blocks": [
				{
					"type": "tool_result",
					"tool_use_id": "tool_1",
					"content": "error: missing header foo.h",
					"is_error": True,
				}
			],
		},
		# Event 2: Assistant edits a file
		{
			"type": "assistant",
			"action": SemanticAction.MODIFY.value,
			"ts": "2026-10-19T01:01:00Z",
			"blocks": [
				{"type": "thinking", "thinking": "Fixing the missing include."},
				{
					"type": "tool_use",
					"id": "tool_2",
					"name": "Edit",
					"input": {"file_path": "src/main.c"},
				},
			],
		},
		# Event 3: Tool result for edit
		{
			"type": "user",
			"action": SemanticAction.MODIFY.value,
			"blocks": [
				{
					"type": "tool_result",
					"tool_use_id": "tool_2",
					"content": "File edited successfully",
				}
			],
		},
		# Event 4: Assistant runs make -j8 AGAIN (repeated command!)
		{
			"type": "assistant",
			"action": SemanticAction.BUILD.value,
			"ts": "2026-10-19T01:02:00Z",
			"blocks": [
				{"type": "thinking", "thinking": "Re-running compilation."},
				{
					"type": "tool_use",
					"id": "tool_3",
					"name": "Bash",
					"input": {"command": "make -j8"},
				},
			],
		},
		# Event 5: Tool result for make -j8 (success)
		{
			"type": "user",
			"action": SemanticAction.BUILD.value,
			"blocks": [
				{
					"type": "tool_result",
					"tool_use_id": "tool_3",
					"content": "Build succeeded: output binary generated",
				}
			],
		},
		# Event 6: Terminal thinking block (Option A: should become a Conclusion node)
		{
			"type": "assistant",
			"action": SemanticAction.THINK.value,
			"ts": "2026-10-19T01:03:00Z",
			"blocks": [
				{"type": "text", "text": "The build succeeded and the evaluation is complete."}
			],
		},
	]

	graph = build_trajectory_graph(events, trace_format="claude_code")

	# We had 4 steps: make -> edit -> make -> conclusion
	# Because 'make -j8' repeated, there should be 3 unique nodes!
	assert len(graph.nodes) == 3
	assert graph.stats["total_steps"] == 4
	assert graph.stats["unique_nodes"] == 3
	assert graph.stats["compression_ratio"] == 25.0

	make_node = next(n for n in graph.nodes if n.command == "make -j8")
	assert make_node.visit_count == 2
	assert make_node.is_loop_hub is True
	assert len(make_node.occurrences) == 2

	# First visit (assistant turn #1)
	assert make_node.occurrences[0].thought == "Let us compile the artifact first."
	assert make_node.occurrences[0].outcome == "failure"
	assert make_node.occurrences[0].turn_idx == 1
	# Second visit (assistant turn #3)
	assert make_node.occurrences[1].thought == "Re-running compilation."
	assert make_node.occurrences[1].outcome == "success"
	assert make_node.occurrences[1].turn_idx == 3

	# Check edit node (assistant turn #2)
	edit_node = next(n for n in graph.nodes if n.tool == "Edit")
	assert edit_node.visit_count == 1
	assert edit_node.occurrences[0].thought == "Fixing the missing include."
	assert edit_node.occurrences[0].turn_idx == 2

	# Check terminal conclusion node
	concl_node = next(n for n in graph.nodes if n.tool == "Think")
	assert concl_node.label == "Conclusion"
	assert "complete" in concl_node.occurrences[0].thought
	assert concl_node.occurrences[0].turn_idx == 4

	# Check edges: make -> edit -> make -> conclusion
	# Edges: (make, edit), (edit, make), (make, conclusion)
	assert len(graph.edges) == 3


def test_codex_events_and_backtracking_detection() -> None:
	events: list[dict[str, Any]] = [
		# Step 1: Run benchmark (action: run)
		{
			"type": "codex_item",
			"action": SemanticAction.RUN.value,
			"item": {
				"type": "command_execution",
				"command": "./run_benchmarks.sh",
				"aggregated_output": "Benchmarking failed: segmentation fault",
				"exit_code": 139,
			},
		},
		# Step 2: Reasoning followed by Edit (action: modify) -> BACKTRACK from RUN to MODIFY!
		{
			"type": "codex_item",
			"action": SemanticAction.THINK.value,
			"item": {
				"type": "reasoning",
				"text": "Crash detected. Fixing null pointer in driver.",
			},
		},
		{
			"type": "codex_item",
			"action": SemanticAction.MODIFY.value,
			"item": {
				"type": "file_change",
				"changes": [{"path": "driver.c", "kind": "modify"}],
			},
		},
	]

	graph = build_trajectory_graph(events, trace_format="codex")
	assert len(graph.nodes) == 2
	assert graph.stats["total_steps"] == 2
	assert len(graph.edges) == 1

	backtrack_edge = graph.edges[0]
	assert backtrack_edge.is_backtrack is True
	assert graph.stats["backtrack_edge_count"] == 1


def test_passive_read_after_run_not_flagged_as_backtrack() -> None:
	events: list[dict[str, Any]] = [
		{
			"type": "assistant",
			"action": SemanticAction.RUN.value,
			"blocks": [
				{
					"type": "tool_use",
					"id": "t1",
					"name": "Bash",
					"input": {"command": "./run_eval.sh"},
				}
			],
		},
		{
			"type": "assistant",
			"action": SemanticAction.SETUP.value,
			"blocks": [
				{
					"type": "tool_use",
					"id": "t2",
					"name": "Read",
					"input": {"file_path": "/workspace/output.txt"},
				}
			],
		},
	]
	graph = build_trajectory_graph(events, trace_format="claude_code")
	assert len(graph.edges) == 1
	assert graph.edges[0].is_backtrack is False
	assert graph.stats["backtrack_edge_count"] == 0


def test_cross_phase_node_deduplication_and_multi_file_codex() -> None:
	events: list[dict[str, Any]] = [
		{
			"type": "codex_item",
			"action": SemanticAction.SETUP.value,
			"item": {
				"type": "command_execution",
				"command": "cat /tmp/status.log",
				"aggregated_output": "initializing",
				"exit_code": 0,
			},
		},
		{
			"type": "codex_item",
			"action": SemanticAction.VERIFY.value,
			"item": {
				"type": "command_execution",
				"command": "cat /tmp/status.log",
				"aggregated_output": "done",
				"exit_code": 0,
			},
		},
		{
			"type": "codex_item",
			"action": SemanticAction.MODIFY.value,
			"item": {
				"type": "file_change",
				"changes": [
					{"path": "file_a.py", "kind": "modify"},
					{"path": "file_b.py", "kind": "add"},
				],
			},
		},
	]
	graph = build_trajectory_graph(events, trace_format="codex")
	# cat /tmp/status.log merged into 1 node + file_a.py + file_b.py = 3 unique nodes
	assert len(graph.nodes) == 3
	cat_node = next(n for n in graph.nodes if "status.log" in n.command)
	assert cat_node.visit_count == 2
	assert cat_node.phases == [SemanticAction.SETUP.value, SemanticAction.VERIFY.value]


def test_self_loop_detection() -> None:
	events: list[dict[str, Any]] = [
		{
			"type": "assistant",
			"action": SemanticAction.SETUP.value,
			"blocks": [
				{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep 1"}}
			],
		},
		{
			"type": "assistant",
			"action": SemanticAction.SETUP.value,
			"blocks": [
				{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "sleep 1"}}
			],
		},
	]
	graph = build_trajectory_graph(events, trace_format="claude_code")
	assert len(graph.nodes) == 1
	assert graph.nodes[0].visit_count == 2
	assert len(graph.edges) == 1
	assert graph.edges[0].is_self_loop is True
	assert graph.edges[0].count == 1
	assert graph.stats["loop_edge_count"] == 1
