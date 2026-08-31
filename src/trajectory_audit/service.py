from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trajectory_audit.judge import SYSTEM_PROMPT, JudgeProvider
from trajectory_audit.models import AuditReport, AuditStatus, JudgeVerdict
from trajectory_audit.sanitize import compress_trace, normalize_trace
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
	) -> None:
		self.provider = provider
		self.primary_model = primary_model
		self.escalation_model = escalation_model

	def audit_records(self, records: list[dict[str, Any]]) -> AuditReport:
		commands = normalize_trace(records)
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

		compressed = compress_trace(commands)
		trace_json = json.dumps(compressed, sort_keys=True, separators=(",", ":"))
		verdict = self.provider.evaluate(
			model=self.primary_model, system_prompt=SYSTEM_PROMPT, trace_json=trace_json
		)
		model = self.primary_model
		escalated = False
		if self.escalation_model and verdict.confidence < ESCALATE_MIN_CONFIDENCE:
			verdict = self.provider.evaluate(
				model=self.escalation_model,
				system_prompt=SYSTEM_PROMPT,
				trace_json=trace_json,
			)
			model = self.escalation_model
			escalated = True

		return _report_from_verdict(
			verdict,
			findings=findings,
			model=model,
			escalated=escalated,
			trace_count=len(commands),
			judge_count=int(compressed["included_commands"]),
			omitted_count=int(compressed["omitted_commands"]),
		)

	def audit_jsonl(self, trace_path: Path) -> AuditReport:
		records: list[dict[str, Any]] = []
		for line_number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), 1):
			if not line.strip():
				continue
			value = json.loads(line)
			if not isinstance(value, dict):
				raise ValueError(f"trace line {line_number} is not a JSON object")
			records.append(value)
		return self.audit_records(records)


def _report_from_verdict(
	verdict: JudgeVerdict,
	*,
	findings: list[Any],
	model: str,
	escalated: bool,
	trace_count: int,
	judge_count: int,
	omitted_count: int,
) -> AuditReport:
	return AuditReport(
		status=AuditStatus.REVIEW if verdict.suspicious else AuditStatus.CLEAN,
		suspicious=verdict.suspicious,
		confidence=verdict.confidence,
		flagged_commands=verdict.flagged_commands,
		reasoning=verdict.reasoning,
		deterministic_findings=findings,
		judge_model=model,
		escalated=escalated,
		trace_command_count=trace_count,
		judge_command_count=judge_count,
		metadata={"omitted_command_count": omitted_count, "score_effect": "none"},
	)
