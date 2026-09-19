from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trajectory_audit.judge import SYSTEM_PROMPT, JudgeProvider
from trajectory_audit.models import AuditReport, AuditStatus, JudgeVerdict, TraceCommand
from trajectory_audit.sanitize import (
	MAX_TRACE_BATCH_BYTES,
	batch_trace,
	normalize_record,
	normalize_trace,
	sanitize_task_context,
)
from trajectory_audit.scanners import scan_trace

DEFAULT_PRIMARY_MODEL = "gpt-4o-mini"
DEFAULT_ESCALATION_MODEL = "gpt-4o"
ESCALATE_MIN_CONFIDENCE = 0.80


class AuditService:
	"""Post-run audit pipeline; never changes deterministic benchmark scoring."""

	def __init__(
		self,
		provider: JudgeProvider,
		*,
		primary_model: str = DEFAULT_PRIMARY_MODEL,
		escalation_model: str | None = DEFAULT_ESCALATION_MODEL,
		trace_batch_bytes: int = MAX_TRACE_BATCH_BYTES,
	) -> None:
		self.provider = provider
		self.primary_model = primary_model
		self.escalation_model = escalation_model
		self.trace_batch_bytes = trace_batch_bytes

	def audit_records(
		self, records: list[dict[str, Any]], *, task_context: str = ""
	) -> AuditReport:
		return self._audit_commands(normalize_trace(records), task_context=task_context)

	def _audit_commands(
		self, commands: list[TraceCommand], *, task_context: str = ""
	) -> AuditReport:
		if not commands:
			raise ValueError("trace contains no command records")
		findings = scan_trace(commands)
		blocked = [finding for finding in findings if finding.severity == "block"]
		if blocked:
			return AuditReport(
				status=AuditStatus.BLOCKED,
				suspicious=True,
				confidence=1.0,
				flagged_commands=sorted(
					{finding.command_id for finding in blocked if finding.command_id}
				),
				reasoning="Deterministic hard-blocking rule matched; LLM review was skipped.",
				deterministic_findings=findings,
				trace_command_count=len(commands),
				judge_command_count=0,
			)

		context = sanitize_task_context(task_context) or "No artifact instructions were provided."
		verdicts: list[JudgeVerdict] = []
		models_used: list[str] = []
		batches = batch_trace(commands, max_bytes=self.trace_batch_bytes)
		escalated = False
		for batch in batches:
			trace_json = json.dumps(batch, sort_keys=True, separators=(",", ":"))
			verdict = self.provider.evaluate(
				model=self.primary_model,
				system_prompt=SYSTEM_PROMPT,
				task_context=context,
				trace_json=trace_json,
			)
			if self.primary_model not in models_used:
				models_used.append(self.primary_model)
			model = self.primary_model
			if self.escalation_model and verdict.confidence < ESCALATE_MIN_CONFIDENCE:
				verdict = self.provider.evaluate(
					model=self.escalation_model,
					system_prompt=SYSTEM_PROMPT,
					task_context=context,
					trace_json=trace_json,
				)
				model = self.escalation_model
				escalated = True
			verdicts.append(verdict)
			if model not in models_used:
				models_used.append(model)

		verdict = _combine_verdicts(verdicts)
		return AuditReport(
			status=AuditStatus.REVIEW if verdict.suspicious else AuditStatus.CLEAN,
			suspicious=verdict.suspicious,
			confidence=verdict.confidence,
			flagged_commands=verdict.flagged_commands,
			reasoning=verdict.reasoning,
			deterministic_findings=findings,
			judge_model=self.escalation_model if escalated else self.primary_model,
			escalated=escalated,
			trace_command_count=len(commands),
			judge_command_count=len(commands),
			metadata={
				"batch_count": len(batches),
				"models_used": models_used,
				"omitted_command_count": 0,
				"score_effect": "none",
			},
		)

	def audit_jsonl(self, trace_path: Path, *, task_context: str = "") -> AuditReport:
		commands: list[TraceCommand] = []
		with trace_path.open(encoding="utf-8") as trace_file:
			for line_number, line in enumerate(trace_file, 1):
				if not line.strip():
					continue
				try:
					value = json.loads(line)
				except json.JSONDecodeError as exc:
					raise ValueError(
						f"invalid JSON on trace line {line_number}: {exc.msg}"
					) from exc
				if not isinstance(value, dict):
					raise ValueError(f"trace line {line_number} is not a JSON object")
				commands.append(normalize_record(value, len(commands)))
		return self._audit_commands(commands, task_context=task_context)


def _combine_verdicts(verdicts: list[JudgeVerdict]) -> JudgeVerdict:
	if len(verdicts) == 1:
		verdict = verdicts[0]
		return verdict.model_copy(
			update={"flagged_commands": verdict.flagged_commands if verdict.suspicious else []}
		)
	indexed = list(enumerate(verdicts, 1))
	suspicious = [(index, verdict) for index, verdict in indexed if verdict.suspicious]
	relevant = suspicious or indexed
	confidence = (
		max(verdict.confidence for _, verdict in relevant)
		if suspicious
		else min(verdict.confidence for _, verdict in relevant)
	)
	flagged_commands = list(
		dict.fromkeys(
			command_id for _, verdict in suspicious for command_id in verdict.flagged_commands
		)
	)
	reasoning = "\n".join(f"Batch {index}: {verdict.reasoning}" for index, verdict in relevant)
	return JudgeVerdict(
		suspicious=bool(suspicious),
		confidence=confidence,
		flagged_commands=flagged_commands,
		reasoning=reasoning[:4000],
	)
