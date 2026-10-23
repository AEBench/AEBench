from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .action_mapper import SemanticAction, unwrap_command

# Canonical lifecycle rank for detecting backward transitions
PHASE_ORDER: dict[str, int] = {
	SemanticAction.SETUP.value: 1,
	SemanticAction.BUILD.value: 2,
	SemanticAction.THINK.value: 2,
	SemanticAction.MODIFY.value: 3,
	SemanticAction.RUN.value: 4,
	SemanticAction.VERIFY.value: 5,
	SemanticAction.MISC.value: 99,
}

_FAILURE_INDICATORS = (
	"error:",
	"failed",
	"failure",
	"traceback (most recent call last)",
	"assertionerror",
	"syntaxerror",
	"non-zero exit status",
	"command failed",
	"fatal:",
)


@dataclass
class StepOccurrence:
	step_idx: int
	event_index: int
	ts: str | None
	thought: str
	input_text: str
	output_text: str
	outcome: str  # "success" | "failure" | "neutral"
	duration_ms: int | None = None
	action: str = "misc"

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass
class GraphNode:
	id: str
	signature: str
	label: str
	tool: str
	command: str
	action: str
	phases: list[str] = field(default_factory=list)
	occurrences: list[StepOccurrence] = field(default_factory=list)

	@property
	def visit_count(self) -> int:
		return len(self.occurrences)

	@property
	def is_loop_hub(self) -> bool:
		return len(self.occurrences) > 1

	def to_dict(self) -> dict[str, Any]:
		return {
			"id": self.id,
			"signature": self.signature,
			"label": self.label,
			"tool": self.tool,
			"command": self.command,
			"action": self.action,
			"phases": list(dict.fromkeys(self.phases)),
			"visit_count": self.visit_count,
			"is_loop_hub": self.is_loop_hub,
			"occurrences": [occ.to_dict() for occ in self.occurrences],
		}


@dataclass
class GraphEdge:
	id: str
	source: str
	target: str
	count: int = 1
	is_self_loop: bool = False
	is_backtrack: bool = False
	steps: list[tuple[int, int]] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return {
			"id": self.id,
			"source": self.source,
			"target": self.target,
			"count": self.count,
			"is_self_loop": self.is_self_loop,
			"is_backtrack": self.is_backtrack,
			"steps": self.steps,
		}


@dataclass
class TrajectoryGraph:
	nodes: list[GraphNode] = field(default_factory=list)
	edges: list[GraphEdge] = field(default_factory=list)
	stats: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return {
			"nodes": [n.to_dict() for n in self.nodes],
			"edges": [e.to_dict() for e in self.edges],
			"stats": self.stats,
		}


def detect_outcome(output_text: str, exit_code: int | None = None, is_error: bool = False) -> str:
	"""Determine if a step produced success, failure, or neutral result."""
	if is_error or (exit_code is not None and exit_code != 0):
		return "failure"
	out_lower = (output_text or "").lower()
	if any(indicator in out_lower for indicator in _FAILURE_INDICATORS):
		return "failure"
	if exit_code == 0 or any(w in out_lower for w in ("success", "succeed", "passed", "ok", "completed", "finish")):
		return "success"
	return "neutral"


def _clean_command_label(cmd: str, max_len: int = 40) -> str:
	single_line = " ".join(cmd.strip().split())
	if len(single_line) <= max_len:
		return single_line
	return single_line[:max_len].rstrip() + "..."


def canonical_node_signature(tool: str, raw_input: str, action: str) -> tuple[str, str, str]:
	"""Derive (signature, label, canonical_command) for an action."""
	norm_tool = tool.strip()
	clean_inp = raw_input.strip()

	if norm_tool in ("Bash", "command_execution", "sh", "bash"):
		unwrapped = unwrap_command(clean_inp)
		canonical = " ".join(unwrapped.strip().split())
		label = _clean_command_label(canonical)
		sig = f"Bash::{action}::{canonical}"
		return sig, label, canonical

	if norm_tool in ("Read", "Edit", "Write", "file_change"):
		norm_path = clean_inp.replace("\\", "/").strip()
		base_name = norm_path.split("/")[-1] if "/" in norm_path else norm_path
		label = f"{norm_tool} {base_name}"
		sig = f"{norm_tool}::{action}::{norm_path}"
		return sig, label, norm_path

	if norm_tool == "Think":
		return f"Think::{action}::terminal", "Think (conclusion)", clean_inp

	# Generic tool
	label = f"{norm_tool} {clean_inp[:30]}"
	sig = f"{norm_tool}::{action}::{clean_inp}"
	return sig, label, clean_inp


def build_trajectory_graph(
	events: list[dict[str, Any]],
	trace_format: str = "claude_code",
) -> TrajectoryGraph:
	"""
	Compresses a linear sequence of trace events into a deduplicated directed multigraph.
	Associates preceding thoughts with ensuing action nodes (Option A),
	collapses identical commands into multi-visit nodes, and detects loops/backtracks.
	"""
	if not events:
		return TrajectoryGraph(
			nodes=[],
			edges=[],
			stats={
				"total_steps": 0,
				"unique_nodes": 0,
				"unique_edges": 0,
				"compression_ratio": 0.0,
				"loop_edge_count": 0,
				"backtrack_edge_count": 0,
				"most_visited": [],
			},
		)

	# Pre-index Claude Code tool results by tool_use_id
	tool_results: dict[str, dict[str, Any]] = {}
	if trace_format == "claude_code":
		for ev in events:
			if ev.get("type") == "user":
				for b in ev.get("blocks", []):
					if isinstance(b, dict) and b.get("type") == "tool_result":
						t_id = b.get("tool_use_id")
						if t_id:
							tool_results[str(t_id)] = b

	nodes_by_sig: dict[str, GraphNode] = {}
	nodes_order: list[GraphNode] = []
	edges_by_pair: dict[tuple[str, str], GraphEdge] = {}
	edge_order: list[GraphEdge] = []

	pending_thoughts: list[str] = []
	prev_node_id: str | None = None
	step_counter = 0

	for ev_idx, ev in enumerate(events):
		ev_type = str(ev.get("type", ""))
		ev_action = str(ev.get("action", SemanticAction.MISC.value))
		ts = ev.get("ts")
		duration_ms = ev.get("duration_ms")

		# ── Claude Code trace format ──
		if trace_format == "claude_code" or "blocks" in ev:
			if ev_type == "assistant":
				blocks = ev.get("blocks", [])
				tool_uses: list[dict[str, Any]] = []

				for b in blocks:
					if not isinstance(b, dict):
						continue
					b_type = b.get("type")
					if b_type in ("thinking", "text"):
						text = str(b.get("thinking") or b.get("text") or "").strip()
						if text:
							pending_thoughts.append(text)
					elif b_type == "tool_use":
						tool_uses.append(b)

				# Process tool actions in this assistant event
				for t_use in tool_uses:
					step_counter += 1
					tool_name = str(t_use.get("name", "tool"))
					inp = t_use.get("input", {})
					inp_dict: dict[str, Any] = inp if isinstance(inp, dict) else {}
					raw_cmd = str(inp_dict.get("command") or inp_dict.get("file_path") or inp_dict.get("path") or "")

					tool_id = str(t_use.get("id") or "")
					res_block = tool_results.get(tool_id, {})
					res_out = str(res_block.get("content") or "")
					is_err = bool(res_block.get("is_error", False))
					outcome = detect_outcome(res_out, is_error=is_err)

					sig, label, canonical_cmd = canonical_node_signature(tool_name, raw_cmd, ev_action)
					thought_str = "\n\n".join(pending_thoughts)
					pending_thoughts.clear()

					# Deduplicate or update node
					if sig not in nodes_by_sig:
						node_id = f"node_{len(nodes_by_sig)}"
						node = GraphNode(
							id=node_id,
							signature=sig,
							label=label,
							tool=tool_name,
							command=canonical_cmd,
							action=ev_action,
							phases=[ev_action],
							occurrences=[],
						)
						nodes_by_sig[sig] = node
						nodes_order.append(node)
					else:
						node = nodes_by_sig[sig]
						node.phases.append(ev_action)

					occ = StepOccurrence(
						step_idx=step_counter,
						event_index=ev_idx,
						ts=ts,
						thought=thought_str,
						input_text=raw_cmd,
						output_text=res_out[:4096],
						outcome=outcome,
						duration_ms=duration_ms,
						action=ev_action,
					)
					node.occurrences.append(occ)

					# Build or update execution edge
					if prev_node_id is not None:
						edge_key = (prev_node_id, node.id)
						if edge_key not in edges_by_pair:
							src_node = next(n for n in nodes_order if n.id == prev_node_id)
							is_self = prev_node_id == node.id
							src_rank = PHASE_ORDER.get(src_node.action, 99)
							tgt_rank = PHASE_ORDER.get(node.action, 99)
							is_backtrack = (src_rank in (4, 5) and tgt_rank in (1, 2, 3)) and not is_self

							edge = GraphEdge(
								id=f"edge_{len(edges_by_pair)}",
								source=prev_node_id,
								target=node.id,
								count=1,
								is_self_loop=is_self,
								is_backtrack=is_backtrack,
								steps=[(step_counter - 1, step_counter)],
							)
							edges_by_pair[edge_key] = edge
							edge_order.append(edge)
						else:
							edge = edges_by_pair[edge_key]
							edge.count += 1
							edge.steps.append((step_counter - 1, step_counter))

					prev_node_id = node.id

		# ── Codex trace format ──
		elif ev_type == "codex_item":
			item = ev.get("item")
			if isinstance(item, dict):
				item_type = str(item.get("type", ""))

				if item_type in ("reasoning", "agent_message"):
					txt = str(item.get("text", "")).strip()
					if txt:
						pending_thoughts.append(txt)
				elif item_type == "command_execution":
					step_counter += 1
					raw_cmd = str(item.get("command", "")).strip()
					res_out = str(item.get("aggregated_output", ""))
					exit_code = item.get("exit_code")
					outcome = detect_outcome(res_out, exit_code=exit_code)

					sig, label, canonical_cmd = canonical_node_signature("Bash", raw_cmd, ev_action)
					thought_str = "\n\n".join(pending_thoughts)
					pending_thoughts.clear()

					if sig not in nodes_by_sig:
						node_id = f"node_{len(nodes_by_sig)}"
						node = GraphNode(
							id=node_id,
							signature=sig,
							label=label,
							tool="Bash",
							command=canonical_cmd,
							action=ev_action,
							phases=[ev_action],
							occurrences=[],
						)
						nodes_by_sig[sig] = node
						nodes_order.append(node)
					else:
						node = nodes_by_sig[sig]
						node.phases.append(ev_action)

					occ = StepOccurrence(
						step_idx=step_counter,
						event_index=ev_idx,
						ts=ts,
						thought=thought_str,
						input_text=raw_cmd,
						output_text=res_out[:4096],
						outcome=outcome,
						duration_ms=duration_ms,
						action=ev_action,
					)
					node.occurrences.append(occ)

					if prev_node_id is not None:
						edge_key = (prev_node_id, node.id)
						if edge_key not in edges_by_pair:
							src_node = next(n for n in nodes_order if n.id == prev_node_id)
							is_self = prev_node_id == node.id
							src_rank = PHASE_ORDER.get(src_node.action, 99)
							tgt_rank = PHASE_ORDER.get(node.action, 99)
							is_backtrack = (src_rank in (4, 5) and tgt_rank in (1, 2, 3)) and not is_self

							edge = GraphEdge(
								id=f"edge_{len(edges_by_pair)}",
								source=prev_node_id,
								target=node.id,
								count=1,
								is_self_loop=is_self,
								is_backtrack=is_backtrack,
								steps=[(step_counter - 1, step_counter)],
							)
							edges_by_pair[edge_key] = edge
							edge_order.append(edge)
						else:
							edge = edges_by_pair[edge_key]
							edge.count += 1
							edge.steps.append((step_counter - 1, step_counter))

					prev_node_id = node.id

				elif item_type == "file_change":
					step_counter += 1
					changes = item.get("changes", [])
					first_path = str(changes[0].get("path", "file")) if changes else "file"
					sig, label, canonical_path = canonical_node_signature("Edit", first_path, ev_action)
					thought_str = "\n\n".join(pending_thoughts)
					pending_thoughts.clear()

					if sig not in nodes_by_sig:
						node_id = f"node_{len(nodes_by_sig)}"
						node = GraphNode(
							id=node_id,
							signature=sig,
							label=label,
							tool="Edit",
							command=canonical_path,
							action=ev_action,
							phases=[ev_action],
							occurrences=[],
						)
						nodes_by_sig[sig] = node
						nodes_order.append(node)
					else:
						node = nodes_by_sig[sig]
						node.phases.append(ev_action)

					occ = StepOccurrence(
						step_idx=step_counter,
						event_index=ev_idx,
						ts=ts,
						thought=thought_str,
						input_text=str(item),
						output_text="File modified",
						outcome="success",
						duration_ms=duration_ms,
						action=ev_action,
					)
					node.occurrences.append(occ)

					if prev_node_id is not None:
						edge_key = (prev_node_id, node.id)
						if edge_key not in edges_by_pair:
							src_node = next(n for n in nodes_order if n.id == prev_node_id)
							is_self = prev_node_id == node.id
							src_rank = PHASE_ORDER.get(src_node.action, 99)
							tgt_rank = PHASE_ORDER.get(node.action, 99)
							is_backtrack = (src_rank in (4, 5) and tgt_rank in (1, 2, 3)) and not is_self

							edge = GraphEdge(
								id=f"edge_{len(edges_by_pair)}",
								source=prev_node_id,
								target=node.id,
								count=1,
								is_self_loop=is_self,
								is_backtrack=is_backtrack,
								steps=[(step_counter - 1, step_counter)],
							)
							edges_by_pair[edge_key] = edge
							edge_order.append(edge)
						else:
							edge = edges_by_pair[edge_key]
							edge.count += 1
							edge.steps.append((step_counter - 1, step_counter))

					prev_node_id = node.id

	# Option A: Handle trailing thoughts with no following tool call
	if pending_thoughts:
		step_counter += 1
		terminal_thought = "\n\n".join(pending_thoughts)
		sig, label, cmd = canonical_node_signature("Think", terminal_thought[:50], SemanticAction.THINK.value)
		node_id = f"node_{len(nodes_by_sig)}"
		term_node = GraphNode(
			id=node_id,
			signature=sig,
			label="Conclusion",
			tool="Think",
			command="Think (conclusion)",
			action=SemanticAction.THINK.value,
			phases=[SemanticAction.THINK.value],
			occurrences=[
				StepOccurrence(
					step_idx=step_counter,
					event_index=len(events) - 1,
					ts=events[-1].get("ts") if events else None,
					thought=terminal_thought,
					input_text="",
					output_text="",
					outcome="neutral",
					action=SemanticAction.THINK.value,
				)
			],
		)
		nodes_by_sig[sig] = term_node
		nodes_order.append(term_node)

		if prev_node_id is not None:
			edge_key = (prev_node_id, term_node.id)
			edge = GraphEdge(
				id=f"edge_{len(edges_by_pair)}",
				source=prev_node_id,
				target=term_node.id,
				count=1,
				is_self_loop=False,
				is_backtrack=False,
				steps=[(step_counter - 1, step_counter)],
			)
			edges_by_pair[edge_key] = edge
			edge_order.append(edge)

	# Calculate summary statistics
	unique_nodes_count = len(nodes_order)
	comp_ratio = (
		round((1.0 - (unique_nodes_count / step_counter)) * 100.0, 1)
		if step_counter > 0 and unique_nodes_count <= step_counter
		else 0.0
	)
	loop_edges = [e for e in edge_order if e.is_self_loop or e.count > 1]
	backtracks = [e for e in edge_order if e.is_backtrack]

	stats = {
		"total_steps": step_counter,
		"unique_nodes": unique_nodes_count,
		"unique_edges": len(edge_order),
		"compression_ratio": comp_ratio,
		"loop_edge_count": len(loop_edges),
		"backtrack_edge_count": len(backtracks),
		"most_visited": [
			{"label": n.label, "count": n.visit_count, "action": n.action}
			for n in sorted(nodes_order, key=lambda x: x.visit_count, reverse=True)[:5]
			if n.visit_count > 1
		],
	}

	return TrajectoryGraph(nodes=nodes_order, edges=edge_order, stats=stats)
