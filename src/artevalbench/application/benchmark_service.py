from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..case_runtime.content import resolve_case_dir
from ..case_runtime.loader import load_case_spec
from ..domain.models import (
 CaseRunResult,
 CaseStatus,
 OraclePhaseResult,
 OracleStatus,
 RunOptions,
)
from ..runtime.config import AppContext
from ..runtime.events import EventSink
from ..project_config import ProjectConfigState
from ..run_control import RunControl
from .case_service import CaseRunService


class BenchmarkCaseRecord(BaseModel):
	model_config = ConfigDict(extra="forbid")

	case_id: str
	case_status: CaseStatus
	oracle_status: OracleStatus
	score: int | None = None
	expected_score: int | None = None
	phases: list[OraclePhaseResult] = Field(default_factory=list)
	prepare_duration_ms: int = 0
	prepare_breakdown_ms: dict[str, int] = Field(default_factory=dict)
	runtime_duration_ms: int = 0
	error: str | None = None
	case_output_dir: str
	started_at: datetime
	finished_at: datetime


class BenchmarkRunMetadata(BaseModel):
	model_config = ConfigDict(extra="forbid")

	run_label: str
	model_name: str
	agent_kind: str
	prompt_profile: str
	runtime_mode: str


class BenchmarkSummary(BaseModel):
	model_config = ConfigDict(extra="forbid")

	metadata: BenchmarkRunMetadata
	selected_cases: list[str]
	started_at: datetime
	finished_at: datetime
	total_cases: int
	case_pass_count: int
	case_pass_ratio: float
	total_score: int
	total_expected_score: int
	phase_ratio: float
	status: str


class BenchmarkRunResult(BaseModel):
	model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

	output_dir: str
	case_results_path: str
	summary_path: str
	summary_markdown_path: str
	case_results: list[BenchmarkCaseRecord]
	summary: BenchmarkSummary

	@property
	def all_cases_passed(self) -> bool:
		return (
		 self.summary.total_cases > 0
		 and self.summary.case_pass_count == self.summary.total_cases
		)


class BenchmarkRunService:
	def __init__(self, context: AppContext) -> None:
		self._context = context
		self._case_service = CaseRunService(context)

	def run(
	 self,
	 case_refs: list[str],
	 *,
	 options: RunOptions,
	 output_dir: Path | None,
	 sink: EventSink | None = None,
	 run_control: RunControl | None = None,
	) -> BenchmarkRunResult:
		selected_refs = list(case_refs or self._context.project_state.registry.cases.keys())
		started_at = datetime.now(timezone.utc)
		benchmark_output_dir = (
		 output_dir or (Path(self._context.settings.default_outputs_dir) / "benchmark")
		).resolve()
		benchmark_output_dir.mkdir(parents=True, exist_ok=True)
		control = run_control or RunControl()
		case_results: list[BenchmarkCaseRecord] = []
		prompt_profiles: list[str] = []
		runtime_modes: list[str] = []
		for case_ref in selected_refs:
			if control.stop_requested:
				break
			expected_score: int | None = None
			try:
				case_dir = resolve_case_dir(case_ref, project_state=self._context.project_state)
				case = load_case_spec(case_dir)
				expected_score = case.oracle.expected_score
				result = self._case_service.run(
				 case_dir,
				 save_path=None,
				 options=options,
				 sink=sink,
				 run_control=control,
				)
				runtime_mode = result.runtime_result.runtime.mode.value
				prompt_profile = result.runtime_result.prompt_profile.value
				prompt_profiles.append(prompt_profile)
				runtime_modes.append(runtime_mode)
				case_results.append(
				 benchmark_case_record_from_case_result(
				  result,
				  expected_score=expected_score,
				 )
				)
			except Exception:
				now = datetime.now(timezone.utc)
				case_results.append(
				 BenchmarkCaseRecord(
				  case_id=case_ref,
				  case_status=CaseStatus.ERROR,
				  oracle_status=OracleStatus.ERROR,
				  score=0,
				  expected_score=expected_score,
				  prepare_duration_ms=0,
				  prepare_breakdown_ms={},
				  runtime_duration_ms=0,
				  error=f"benchmark run crashed before case completion; see {case_ref}_error.log",
				  case_output_dir="",
				  started_at=now,
				  finished_at=now,
				 )
				)
				(benchmark_output_dir / f"{case_ref}_error.log").write_text(
				 traceback.format_exc(),
				 encoding="utf-8",
				)
		finished_at = datetime.now(timezone.utc)
		summary = _summarize(
		 case_results,
		 selected_refs,
		 started_at,
		 finished_at,
		 metadata=_metadata_for_benchmark_run(
		  model_name=options.model_name or self._context.settings.default_model,
		  agent_kind=self._context.settings.agent.kind.value,
		  prompt_profile=_default_prompt_profile_for(prompt_profiles, self._context),
		  runtime_mode=_single_value_or_mixed(runtime_modes),
		  run_label=benchmark_output_dir.name,
		 ),
		)
		results_path, summary_path, summary_markdown_path = write_benchmark_outputs(
		 benchmark_output_dir,
		 case_results,
		 summary,
		 project_state=self._context.project_state,
		)
		return BenchmarkRunResult(
		 output_dir=str(benchmark_output_dir),
		 case_results_path=str(results_path),
		 summary_path=str(summary_path),
		 summary_markdown_path=str(summary_markdown_path),
		 case_results=case_results,
		 summary=summary,
		)


def _summarize(
 case_results: list[BenchmarkCaseRecord],
 selected_refs: list[str],
 started_at: datetime,
 finished_at: datetime,
 *,
 metadata: BenchmarkRunMetadata,
) -> BenchmarkSummary:
	total_cases = len(case_results)
	case_pass_count = sum(1 for result in case_results if result.case_status == CaseStatus.SUCCESS)
	total_score = sum(result.score or 0 for result in case_results)
	total_expected_score = sum(result.expected_score or 0 for result in case_results)
	phase_ratio = (total_score / total_expected_score) if total_expected_score else 0.0
	case_pass_ratio = (case_pass_count / total_cases) if total_cases else 0.0
	status = "success" if total_cases > 0 and case_pass_count == total_cases else "error"
	return BenchmarkSummary(
	 metadata=metadata,
	 selected_cases=list(selected_refs),
	 started_at=started_at,
	 finished_at=finished_at,
	 total_cases=total_cases,
	 case_pass_count=case_pass_count,
	 case_pass_ratio=case_pass_ratio,
	 total_score=total_score,
	 total_expected_score=total_expected_score,
	 phase_ratio=phase_ratio,
	 status=status,
	)


def summarize_case_output_dirs(
 case_output_inputs: Sequence[Path],
 *,
 output_dir: Path,
 project_state: ProjectConfigState,
 model_name: str | None = None,
 agent_kind: str | None = None,
 prompt_profile: str | None = None,
 run_label: str | None = None,
 expected_case_ids: Sequence[str] | None = None,
) -> BenchmarkRunResult:
	discovered_dirs = discover_case_output_dirs(case_output_inputs)
	if not discovered_dirs:
		raise RuntimeError("no case output directories with case_result.json were found")
	case_records_by_id: dict[str, BenchmarkCaseRecord] = {}
	model_names: list[str] = []
	agent_kinds: list[str] = []
	prompt_profiles: list[str] = []
	runtime_modes: list[str] = []
	for case_output_dir in discovered_dirs:
		case_result = _read_case_run_result(case_output_dir)
		model_names.append(case_result.runtime_result.agent.model)
		agent_kinds.append(case_result.runtime_result.agent_kind)
		prompt_profiles.append(case_result.runtime_result.prompt_profile.value)
		runtime_modes.append(case_result.runtime_result.runtime.mode.value)
		record = benchmark_case_record_from_case_result(
		 case_result,
		 expected_score=_expected_score_for_case(case_result.id, project_state=project_state),
		)
		if record.case_id in case_records_by_id:
			raise RuntimeError(
			 f"duplicate case result for {record.case_id}: {case_records_by_id[record.case_id].case_output_dir} and {record.case_output_dir}"
			)
		case_records_by_id[record.case_id] = record
	selected_cases = list(expected_case_ids or case_records_by_id.keys())
	for case_id in selected_cases:
		if case_id not in case_records_by_id:
			case_records_by_id[case_id] = _missing_case_record(case_id, project_state=project_state)
	extra_case_ids = [
	 case_id for case_id in case_records_by_id if case_id not in set(selected_cases)
	]
	ordered_case_results = [case_records_by_id[case_id] for case_id in selected_cases]
	ordered_case_results.extend(case_records_by_id[case_id] for case_id in sorted(extra_case_ids))
	started_at = min(
	 (record.started_at for record in ordered_case_results), default=datetime.now(timezone.utc)
	)
	finished_at = max((record.finished_at for record in ordered_case_results), default=started_at)
	resolved_model_name = model_name or _single_value_or_mixed(model_names)
	resolved_agent_kind = agent_kind or _single_value_or_mixed(agent_kinds)
	resolved_prompt_profile = prompt_profile or _single_value_or_mixed(prompt_profiles)
	summary = _summarize(
	 ordered_case_results,
	 selected_cases or [record.case_id for record in ordered_case_results],
	 started_at,
	 finished_at,
	 metadata=_metadata_for_benchmark_run(
	  model_name=resolved_model_name,
	  agent_kind=resolved_agent_kind,
	  prompt_profile=resolved_prompt_profile,
	  runtime_mode=_single_value_or_mixed(runtime_modes),
	  run_label=run_label or output_dir.name,
	 ),
	)
	output_dir = output_dir.expanduser().resolve()
	output_dir.mkdir(parents=True, exist_ok=True)
	results_path, summary_path, summary_markdown_path = write_benchmark_outputs(
	 output_dir,
	 ordered_case_results,
	 summary,
	 project_state=project_state,
	)
	return BenchmarkRunResult(
	 output_dir=str(output_dir),
	 case_results_path=str(results_path),
	 summary_path=str(summary_path),
	 summary_markdown_path=str(summary_markdown_path),
	 case_results=ordered_case_results,
	 summary=summary,
	)


def discover_case_output_dirs(case_output_inputs: Sequence[Path]) -> list[Path]:
	discovered: list[Path] = []
	seen: set[Path] = set()
	for raw_path in case_output_inputs:
		path = raw_path.expanduser().resolve()
		candidates: list[Path] = []
		if path.is_file() and path.name == "case_result.json":
			candidates = [path.parent]
		elif path.is_dir():
			if (path / "case_result.json").is_file():
				candidates = [path]
			else:
				candidates = sorted(result.parent for result in path.glob("**/case_result.json"))
		for candidate in candidates:
			if candidate not in seen:
				discovered.append(candidate)
				seen.add(candidate)
	return discovered


def benchmark_case_record_from_case_result(
 case_result: CaseRunResult,
 *,
 expected_score: int | None,
) -> BenchmarkCaseRecord:
	return BenchmarkCaseRecord(
	 case_id=case_result.id,
	 case_status=case_result.status,
	 oracle_status=case_result.oracle_result.status,
	 score=case_result.oracle_result.score,
	 expected_score=expected_score,
	 phases=list(case_result.oracle_result.phases),
	 prepare_duration_ms=case_result.runtime_result.prepare_duration_ms,
	 prepare_breakdown_ms=dict(case_result.runtime_result.prepare_breakdown_ms),
	 runtime_duration_ms=case_result.runtime_result.duration_ms,
	 error=case_result.oracle_result.error or case_result.runtime_result.error,
	 case_output_dir=case_result.output_dir,
	 started_at=case_result.started_at,
	 finished_at=case_result.finished_at,
	)


def write_benchmark_outputs(
 benchmark_output_dir: Path,
 case_results: Sequence[BenchmarkCaseRecord],
 summary: BenchmarkSummary,
 *,
 project_state: ProjectConfigState,
) -> tuple[Path, Path, Path]:
	results_path = benchmark_output_dir / "benchmark_results.jsonl"
	summary_path = benchmark_output_dir / "benchmark_summary.json"
	summary_markdown_path = benchmark_output_dir / "benchmark_summary.md"
	with results_path.open("w", encoding="utf-8") as handle:
		for record in case_results:
			handle.write(record.model_dump_json())
			handle.write("\n")
	summary_path.write_text(
	 json.dumps(summary.model_dump(mode="json"), indent=2),
	 encoding="utf-8",
	)
	summary_markdown_path.write_text(
	 render_benchmark_summary_markdown(
	  summary,
	  case_results,
	  project_state=project_state,
	 ),
	 encoding="utf-8",
	)
	return results_path, summary_path, summary_markdown_path


def render_benchmark_summary_markdown(
 summary: BenchmarkSummary,
 case_results: Sequence[BenchmarkCaseRecord],
 *,
 project_state: ProjectConfigState,
) -> str:
	metadata = summary.metadata
	claim_by_case_id = _resolve_case_claims(case_results, project_state=project_state)
	lines = [
	 "# Benchmark Summary",
	 "",
	 f"- Run label: `{metadata.run_label}`",
	 f"- Status: `{summary.status}`",
	 f"- Model: `{metadata.model_name}`",
	 f"- Agent driver: `{metadata.agent_kind}`",
	 f"- Prompt profile: `{metadata.prompt_profile}`",
	 f"- Runtime mode: `{metadata.runtime_mode}`",
	 f"- Started: `{summary.started_at.isoformat()}`",
	 f"- Finished: `{summary.finished_at.isoformat()}`",
	 f"- Selected cases: `{summary.total_cases}`",
	 f"- Case pass ratio: `{summary.case_pass_count}/{summary.total_cases}` (`{summary.case_pass_ratio:.3f}`)",
	 f"- Phase score: `{summary.total_score}/{summary.total_expected_score}` (`{summary.phase_ratio:.3f}`)",
	 "",
	 "## Cases",
	 "",
	 "| Case | Claim | Case status | Oracle | Score | Output dir |",
	 "| --- | --- | --- | --- | --- | --- |",
	]
	for record in case_results:
		score_text = (
		 f"{record.score}/{record.expected_score}"
		 if record.score is not None and record.expected_score is not None
		 else "n/a"
		)
		claim_text = _markdown_table_cell(claim_by_case_id.get(record.case_id, "n/a"))
		lines.append(
		 f"| `{record.case_id}` | {claim_text} | `{record.case_status.value}` | `{record.oracle_status.value}` | `{score_text}` | `{record.case_output_dir or 'n/a'}` |"
		)
	failures = [record for record in case_results if record.case_status != CaseStatus.SUCCESS]
	if failures:
		lines.extend(["", "## Failures", ""])
		for record in failures:
			detail = _failure_detail(record)
			lines.append(f"- `{record.case_id}`: {detail}")
	return "\n".join(lines) + "\n"


def _metadata_for_benchmark_run(
 *,
 model_name: str,
 agent_kind: str,
 prompt_profile: str,
 runtime_mode: str,
 run_label: str,
) -> BenchmarkRunMetadata:
	return BenchmarkRunMetadata(
	 run_label=run_label,
	 model_name=model_name,
	 agent_kind=agent_kind,
	 prompt_profile=prompt_profile,
	 runtime_mode=runtime_mode,
	)


def _default_prompt_profile_for(prompt_profiles: Sequence[str], context: AppContext) -> str:
	value = _single_value_or_mixed(prompt_profiles)
	return value if value != "unknown" else context.settings.default_prompt_profile


def _single_value_or_mixed(values: Sequence[str]) -> str:
	items = [value for value in values if value]
	if not items:
		return "unknown"
	non_unknown = [value for value in items if value != "unknown"]
	if non_unknown:
		items = non_unknown
	first = items[0]
	return first if all(item == first for item in items) else "mixed"


def _read_case_run_result(case_output_dir: Path) -> CaseRunResult:
	case_result_path = case_output_dir / "case_result.json"
	if not case_result_path.is_file():
		raise RuntimeError(f"missing case_result.json under {case_output_dir}")
	return CaseRunResult.model_validate_json(case_result_path.read_text(encoding="utf-8"))


def _expected_score_for_case(case_id: str, *, project_state: ProjectConfigState) -> int | None:
	try:
		case_dir = resolve_case_dir(case_id, project_state=project_state)
	except Exception:
		return None
	try:
		return load_case_spec(case_dir).oracle.expected_score
	except Exception:
		return None


def _missing_case_record(case_id: str, *, project_state: ProjectConfigState) -> BenchmarkCaseRecord:
	now = datetime.now(timezone.utc)
	return BenchmarkCaseRecord(
	 case_id=case_id,
	 case_status=CaseStatus.ERROR,
	 oracle_status=OracleStatus.ERROR,
	 score=0,
	 expected_score=_expected_score_for_case(case_id, project_state=project_state),
	 phases=[],
	 prepare_duration_ms=0,
	 prepare_breakdown_ms={},
	 runtime_duration_ms=0,
	 error="missing case output",
	 case_output_dir="",
	 started_at=now,
	 finished_at=now,
	)


def _resolve_case_claims(
 case_results: Sequence[BenchmarkCaseRecord],
 *,
 project_state: ProjectConfigState,
) -> dict[str, str]:
	claims: dict[str, str] = {}
	for record in case_results:
		claims[record.case_id] = _claim_for_case_id(record.case_id, project_state=project_state)
	return claims


def _claim_for_case_id(case_id: str, *, project_state: ProjectConfigState) -> str:
	try:
		case_dir = resolve_case_dir(case_id, project_state=project_state)
		case = load_case_spec(case_dir)
	except Exception:
		return "n/a"
	return _compact_text(case.case_card.core_claim, max_length=96)


def _failure_detail(record: BenchmarkCaseRecord) -> str:
	if record.error:
		return _compact_text(record.error)
	for phase in record.phases:
		if phase.status == OracleStatus.ERROR:
			return _compact_text(phase.error or phase.summary or "oracle phase failed")
	return "case failed"


def _compact_text(value: str, *, max_length: int = 240) -> str:
	line = " ".join(part.strip() for part in value.splitlines() if part.strip())
	if len(line) <= max_length:
		return line
	return line[: max_length - 3] + "..."


def _markdown_table_cell(value: str) -> str:
	return value.replace("|", "\\|")
