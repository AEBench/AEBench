from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, Field

from ..domain.models import (
    AgentResult,
    CaseCardSpec,
    CaseRunResult,
    CaseStatus,
    OracleResult,
    OracleStatus,
    RuntimeMode,
    RuntimeResult as RuntimeInfo,
    RunOptions,
    RunResult,
    TaskStatus,
)
from ..evaluator.loader import load_case_spec
from .case_runner import CaseRunner
from .cases import expand_case_dirs, resolve_case_dir


class BenchmarkSummary(BaseModel):
    run_label: str
    model_name: str
    agent_kind: str
    prompt_profile: str
    runtime_mode: str

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
    output_dir: str
    case_results_path: str
    summary_path: str
    summary_markdown_path: str
    case_results: list[CaseRunResult] = Field(default_factory=list)
    summary: BenchmarkSummary


class BenchmarkRunner:
    def __init__(self, context) -> None:
        self._context = context
        self._case_runner = CaseRunner(context)

    def run(
        self,
        case_refs: list[str],
        *,
        options: RunOptions,
        output_dir: Path | None,
        listener=None,
        run_control=None,
    ) -> BenchmarkRunResult:
        selected_dirs = expand_case_dirs(case_refs, project_state=self._context.project_state)
        started_at = datetime.now(timezone.utc)

        benchmark_output_dir = (
            output_dir or (Path(self._context.settings.default_outputs_dir) / "benchmark")
        ).expanduser().resolve()
        benchmark_output_dir.mkdir(parents=True, exist_ok=True)

        case_results: list[CaseRunResult] = []

        for case_dir in selected_dirs:
            if run_control is not None and run_control.stop_requested:
                break

            case_root = case_dir.resolve()
            case_id = case_root.name
            try:
                case_results.append(
                    self._case_runner.run(
                        case_root,
                        save_path=None,
                        options=options,
                        listener=listener,
                        run_control=run_control,
                    )
                )
            except Exception:
                now = datetime.now(timezone.utc)
                error_path = benchmark_output_dir / f"{case_id}_error.log"
                error_path.write_text(traceback.format_exc(), encoding="utf-8")
                error_msg = f"benchmark run crashed before case completion; see {error_path.name}"
                case_results.append(
                    CaseRunResult(
                        status=CaseStatus.ERROR,
                        finished_at=now,
                        case_dir=str(case_root),
                        artifact_dir="",
                        output_dir="",
                        case_card=CaseCardSpec(
                            core_claim="n/a",
                            acceptable_evidence="n/a",
                            allowed_tolerance="n/a",
                        ),
                        runtime_result=RunResult(
                            id=case_id,
                            status=TaskStatus.ERROR,
                            started_at=now,
                            finished_at=now,
                            prepare_duration_ms=0,
                            prepare_breakdown_ms={},
                            duration_ms=0,
                            workspace_path="",
                            output_dir="",
                            summary_path="",
                            prompt_profile=self._context.settings.default_prompt_profile,
                            runtime=RuntimeInfo(
                                mode=RuntimeMode.LOCAL,
                                image=None,
                                container_id=None,
                                saved_image=None,
                                container_stopped=True,
                            ),
                            agent_kind=self._context.settings.agent.agent_type.value,
                            agent=AgentResult(
                                model=options.model_name or self._context.settings.default_model,
                                exit_code=1,
                            ),
                            error=error_msg,
                        ),
                        oracle_result=OracleResult(
                            status=OracleStatus.ERROR,
                            score=0,
                            summary="benchmark run failed before oracle completion",
                            error=error_msg,
                        ),
                    )
                )

        finished_at = datetime.now(timezone.utc)
        interrupted = bool(run_control is not None and run_control.stop_requested)

        prompt_profiles = [r.runtime_result.prompt_profile.value for r in case_results]
        runtime_modes = [r.runtime_result.runtime.mode.value for r in case_results]
        expected_scores = _load_expected_scores(case_results, project_state=self._context.project_state)

        summary = _summarize(
            case_results,
            [p.name for p in selected_dirs],
            started_at,
            finished_at,
            run_label=benchmark_output_dir.name,
            model_name=options.model_name or self._context.settings.default_model,
            agent_kind=self._context.settings.agent.agent_type.value,
            prompt_profile=_single_value_or_mixed(
                prompt_profiles,
                default=self._context.settings.default_prompt_profile,
            ),
            runtime_mode=_single_value_or_mixed(runtime_modes, default="unknown"),
            expected_scores=expected_scores,
            interrupted=interrupted,
        )
        rp, sp, mp = write_benchmark_outputs(
            benchmark_output_dir,
            case_results,
            summary,
            expected_scores=expected_scores,
        )
        return BenchmarkRunResult(
            output_dir=str(benchmark_output_dir),
            case_results_path=rp,
            summary_path=sp,
            summary_markdown_path=mp,
            case_results=case_results,
            summary=summary,
        )


def summarize_case_output_dirs(
    case_output_inputs: Sequence[Path],
    *,
    output_dir: Path,
    project_state,
    model_name: str | None = None,
    agent_kind: str | None = None,
    prompt_profile: str | None = None,
    run_label: str | None = None,
    expected_case_ids: Sequence[str] | None = None,
) -> BenchmarkRunResult:
    discovered_dirs = discover_case_output_dirs(case_output_inputs)
    if not discovered_dirs:
        raise RuntimeError("no case output directories with case_result.json were found")

    case_results_by_id: dict[str, CaseRunResult] = {}
    for case_output_dir in discovered_dirs:
        path = case_output_dir / "case_result.json"
        if not path.is_file():
            raise RuntimeError(f"missing case_result.json under {case_output_dir}")
        cr = CaseRunResult.model_validate_json(path.read_text(encoding="utf-8"))
        case_results_by_id[cr.id] = cr

    ordered_ids = list(expected_case_ids or case_results_by_id.keys())
    ordered_results = [case_results_by_id[case_id] for case_id in ordered_ids if case_id in case_results_by_id]
    ordered_results.extend(
        case_result
        for case_id, case_result in sorted(case_results_by_id.items())
        if case_id not in set(ordered_ids)
    )

    started_at = min((r.started_at for r in ordered_results), default=datetime.now(timezone.utc))
    finished_at = max((r.finished_at for r in ordered_results), default=started_at)

    model_names = [r.runtime_result.agent.model for r in ordered_results]
    agent_kinds = [r.runtime_result.agent_kind for r in ordered_results]
    prompt_profiles = [r.runtime_result.prompt_profile.value for r in ordered_results]
    runtime_modes = [r.runtime_result.runtime.mode.value for r in ordered_results]

    expected_scores = _load_expected_scores(ordered_results, project_state=project_state)
    summary = _summarize(
        ordered_results,
        ordered_ids or [r.id for r in ordered_results],
        started_at,
        finished_at,
        run_label=run_label or output_dir.name,
        model_name=model_name or _single_value_or_mixed(model_names, default="unknown"),
        agent_kind=agent_kind or _single_value_or_mixed(agent_kinds, default="unknown"),
        prompt_profile=prompt_profile or _single_value_or_mixed(prompt_profiles, default="unknown"),
        runtime_mode=_single_value_or_mixed(runtime_modes, default="unknown"),
        expected_scores=expected_scores,
        interrupted=False,
    )
    out = output_dir.expanduser().resolve()
    rp, sp, mp = write_benchmark_outputs(out, ordered_results, summary, expected_scores=expected_scores)
    return BenchmarkRunResult(
        output_dir=str(out),
        case_results_path=rp,
        summary_path=sp,
        summary_markdown_path=mp,
        case_results=ordered_results,
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


def write_benchmark_outputs(
    benchmark_output_dir: Path,
    case_results: Sequence[CaseRunResult],
    summary: BenchmarkSummary,
    *,
    expected_scores: dict[str, int | None],
) -> tuple[str, str, str]:
    benchmark_output_dir.mkdir(parents=True, exist_ok=True)

    rp = benchmark_output_dir / "benchmark_results.jsonl"
    sp = benchmark_output_dir / "benchmark_summary.json"
    mp = benchmark_output_dir / "benchmark_summary.md"

    with rp.open("w", encoding="utf-8") as f:
        for cr in case_results:
            f.write(cr.model_dump_json())
            f.write("\n")

    sp.write_text(json.dumps(summary.model_dump(), indent=2), encoding="utf-8")
    mp.write_text(
        render_benchmark_summary_markdown(summary, case_results, expected_scores=expected_scores),
        encoding="utf-8",
    )
    return str(rp), str(sp), str(mp)


def render_benchmark_summary_markdown(
    summary: BenchmarkSummary,
    case_results: Sequence[CaseRunResult],
    *,
    expected_scores: dict[str, int | None],
) -> str:
    lines = [
        "# Benchmark Summary",
        "",
        f"- Run label: `{summary.run_label}`",
        f"- Status: `{summary.status}`",
        f"- Model: `{summary.model_name}`",
        f"- Agent driver: `{summary.agent_kind}`",
        f"- Prompt profile: `{summary.prompt_profile}`",
        f"- Runtime mode: `{summary.runtime_mode}`",
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
    for case_result in case_results:
        expected_score = expected_scores.get(case_result.id)
        score_text = (
            f"{case_result.oracle_result.score}/{expected_score}"
            if case_result.oracle_result.score is not None and expected_score is not None
            else "n/a"
        )
        claim = _compact_text(case_result.case_card.core_claim, max_length=96).replace("|", "\\|")
        lines.append(
            f"| `{case_result.id}` | {claim} | "
            f"`{case_result.status.value}` | `{case_result.oracle_result.status.value}` | "
            f"`{score_text}` | `{case_result.output_dir or 'n/a'}` |"
        )

    failures = [result for result in case_results if result.status != CaseStatus.SUCCESS]
    if failures:
        lines.extend(["", "## Failures", ""])
        for result in failures:
            lines.append(f"- `{result.id}`: {_failure_detail(result)}")
    return "\n".join(lines) + "\n"


def _summarize(
    case_results: list[CaseRunResult],
    selected_refs: list[str],
    started_at: datetime,
    finished_at: datetime,
    *,
    run_label: str,
    model_name: str,
    agent_kind: str,
    prompt_profile: str,
    runtime_mode: str,
    expected_scores: dict[str, int | None],
    interrupted: bool,
) -> BenchmarkSummary:
    total_cases = len(case_results)
    case_pass_count = sum(1 for result in case_results if result.status == CaseStatus.SUCCESS)
    total_score = sum(result.oracle_result.score or 0 for result in case_results)
    total_expected_score = sum(expected_scores.get(result.id) or 0 for result in case_results)
    phase_ratio = (total_score / total_expected_score) if total_expected_score else 0.0
    case_pass_ratio = (case_pass_count / total_cases) if total_cases else 0.0

    if interrupted:
        status = "interrupted"
    else:
        status = "success" if case_pass_count == total_cases else "error"

    return BenchmarkSummary(
        run_label=run_label,
        model_name=model_name,
        agent_kind=agent_kind,
        prompt_profile=prompt_profile,
        runtime_mode=runtime_mode,
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


def _load_expected_scores(
    case_results: Sequence[CaseRunResult], *, project_state
) -> dict[str, int | None]:
    scores: dict[str, int | None] = {}
    for r in case_results:
        try:
            scores[r.id] = load_case_spec(resolve_case_dir(r.id, project_state=project_state)).oracle.expected_score
        except Exception:
            scores[r.id] = None
    return scores


def _failure_detail(case_result: CaseRunResult) -> str:
    error = case_result.oracle_result.error or case_result.runtime_result.error
    if error:
        return _compact_text(error)
    for phase in case_result.oracle_result.phases:
        if phase.status == OracleStatus.ERROR:
            return _compact_text(phase.error or phase.summary or "oracle phase failed")
    return "case failed"


def _compact_text(value: str, *, max_length: int = 240) -> str:
    line = " ".join(part.strip() for part in value.splitlines() if part.strip())
    if len(line) <= max_length:
        return line
    return line[: max_length - 3] + "..."


def _single_value_or_mixed(values: Sequence[str], *, default: str) -> str:
    items = [value for value in values if value]
    if not items:
        return default
    first = items[0]
    return first if all(item == first for item in items) else "mixed"
