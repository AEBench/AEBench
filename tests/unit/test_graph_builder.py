from __future__ import annotations

from typing import Any

from src.trace_viewer.action_mapper import SemanticAction
from src.trace_viewer.graph_builder import (
	GraphEdge,
	GraphNode,
	TrajectoryGraph,
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


def test_canonical_node_signature_bash() -> None:
	sig1, label1, cmd1 = canonical_node_signature("Bash", "sudo make -j8", "build")
	sig2, label2, cmd2 = canonical_node_signature("Bash", "make -j8", "build")
	assert sig1 == sig2
	assert cmd1 == "make -j8"
	assert label1 == "make -j8"


def test_canonical_node_signature_files() -> None:
	sig, label, cmd = canonical_node_signature("Read", "/path/to/main.py", "setup")
	assert sig == "Read::setup::/path/to/main.py"
	assert label == "Read main.py"
	assert cmd == "/path/to/main.py"


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

	# First visit
	assert make_node.occurrences[0].thought == "Let us compile the artifact first."
	assert make_node.occurrences[0].outcome == "failure"
	# Second visit
	assert make_node.occurrences[1].thought == "Re-running compilation."
	assert make_node.occurrences[1].outcome == "success"

	# Check edit node
	edit_node = next(n for n in graph.nodes if n.tool == "Edit")
	assert edit_node.visit_count == 1
	assert edit_node.occurrences[0].thought == "Fixing the missing include."

	# Check terminal conclusion node
	concl_node = next(n for n in graph.nodes if n.tool == "Think")
	assert concl_node.label == "Conclusion"
	assert "complete" in concl_node.occurrences[0].thought

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
