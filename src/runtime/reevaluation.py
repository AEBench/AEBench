from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models import (
	CaseConfig,
	OracleReevaluationResult,
	RunResult,
	RuntimeMode,
)

from .oracle_runner import DirectOracleRunner

EVALUATIONS_DIRNAME = "oracle-evaluations"
EVALUATION_RECORD_FILENAME = "evaluation.json"
RESULT_JSONL_FILENAME = "result.jsonl"
CASE_RESULT_FILENAME = "case_result.json"


@dataclass(frozen=True, slots=True)
class CompletedRun:
	run_dir: Path
	runtime_result: RunResult
	workspace_dir: Path


def load_completed_run(run_dir: Path, *, expected_case_id: str) -> CompletedRun:
	root = run_dir.expanduser().resolve(strict=False)
	if not root.is_dir():
		raise ValueError(f"run directory not found: {root}")

	runtime_result = read_run_result(root)
	if runtime_result is None:
		raise ValueError(
			f"completed run metadata not found in {root}; expected "
			f"{RESULT_JSONL_FILENAME} or {CASE_RESULT_FILENAME}"
		)
	if runtime_result.id != expected_case_id:
		raise ValueError(
			f"run case mismatch: found {runtime_result.id!r}, expected {expected_case_id!r}"
		)

	workspace_dir = Path(runtime_result.workspace_path).expanduser().resolve(strict=False)
	if not workspace_dir.is_dir():
		raise ValueError(f"recorded workspace not found: {workspace_dir}")

	_validate_runtime_snapshot(runtime_result)
	return CompletedRun(
		run_dir=root,
		runtime_result=runtime_result,
		workspace_dir=workspace_dir,
	)


def reevaluate_completed_run(
	*,
	case_dir: Path,
	case: CaseConfig,
	run_dir: Path,
	project_root: Path,
) -> tuple[OracleReevaluationResult, Path]:
	completed = load_completed_run(run_dir, expected_case_id=case.id)
	evaluated_at = datetime.now(timezone.utc)
	revision, dirty = _source_state(project_root)
	evaluation_dir = _create_evaluation_dir(
		completed.run_dir,
		evaluated_at=evaluated_at,
		source_revision=revision,
	)

	oracle_result = DirectOracleRunner().execute(
		case_dir,
		runtime_result=completed.runtime_result,
		output_dir=evaluation_dir,
		case=case,
		workspace_dir=completed.workspace_dir,
	)
	record = OracleReevaluationResult(
		case_id=case.id,
		evaluated_at=evaluated_at,
		source_revision=revision,
		source_dirty=dirty,
		run_dir=str(completed.run_dir),
		workspace_dir=str(completed.workspace_dir),
		runtime_snapshot=completed.runtime_result.runtime.saved_image,
		oracle_result=oracle_result,
	)
	(evaluation_dir / EVALUATION_RECORD_FILENAME).write_text(
		record.model_dump_json(indent=2),
		encoding="utf-8",
	)
	return record, evaluation_dir


def read_run_result(run_dir: Path) -> RunResult | None:
	result_jsonl = run_dir / RESULT_JSONL_FILENAME
	if result_jsonl.is_file():
		try:
			lines = [
				line
				for line in result_jsonl.read_text(encoding="utf-8").splitlines()
				if line.strip()
			]
		except OSError as exc:
			raise ValueError(f"failed to read {result_jsonl}: {exc}") from exc
		if lines:
			try:
				return RunResult.model_validate_json(lines[-1])
			except ValueError as exc:
				raise ValueError(f"invalid {result_jsonl}: {exc}") from exc

	case_result_path = run_dir / CASE_RESULT_FILENAME
	if case_result_path.is_file():
		try:
			payload = json.loads(case_result_path.read_text(encoding="utf-8"))
			return RunResult.model_validate(payload["runtime_result"])
		except (KeyError, TypeError) as exc:
			raise ValueError(f"invalid {case_result_path}: missing runtime_result") from exc
		except (OSError, ValueError) as exc:
			raise ValueError(f"invalid {case_result_path}: {exc}") from exc

	return None


def _validate_runtime_snapshot(runtime_result: RunResult) -> None:
	runtime = runtime_result.runtime
	if runtime.mode == RuntimeMode.LOCAL:
		return
	if runtime.mode != RuntimeMode.DOCKER:
		raise ValueError(f"unsupported recorded runtime mode: {runtime.mode.value}")
	if not runtime.saved_image:
		raise ValueError(
			"recorded Docker run has no preserved runtime snapshot; rerun the case with "
			"run.runtime.keep_committed_snapshot = true"
		)
	if shutil.which("docker") is None:
		raise ValueError("Docker is required to use the recorded runtime snapshot")

	try:
		result = subprocess.run(
			["docker", "image", "inspect", runtime.saved_image],
			capture_output=True,
			text=True,
			check=False,
		)
	except OSError as exc:
		raise ValueError(f"failed to inspect runtime snapshot: {exc}") from exc
	if result.returncode != 0:
		raise ValueError(f"recorded runtime snapshot not found: {runtime.saved_image}")


def _source_state(project_root: Path) -> tuple[str | None, bool | None]:
	root = project_root.expanduser().resolve(strict=False)
	revision = _run_git(root, "rev-parse", "HEAD")
	if revision is None:
		return None, None
	status = _run_git(root, "status", "--porcelain", "--untracked-files=no")
	return revision, None if status is None else bool(status)


def _run_git(root: Path, *args: str) -> str | None:
	try:
		result = subprocess.run(
			["git", "-C", str(root), *args],
			capture_output=True,
			text=True,
			check=False,
		)
	except OSError:
		return None
	if result.returncode != 0:
		return None
	return result.stdout.strip()


def _create_evaluation_dir(
	run_dir: Path,
	*,
	evaluated_at: datetime,
	source_revision: str | None,
) -> Path:
	stamp = evaluated_at.astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%fZ")
	revision = source_revision[:12] if source_revision else "unknown"
	path = run_dir / EVALUATIONS_DIRNAME / f"{stamp}_{revision}"
	path.mkdir(parents=True, exist_ok=False)
	return path


__all__ = [
	"CompletedRun",
	"load_completed_run",
	"read_run_result",
	"reevaluate_completed_run",
]
