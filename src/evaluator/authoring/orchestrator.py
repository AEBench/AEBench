"""Automated case-authoring pipeline."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from evaluator.loader import CaseBundleError, load_case_spec

from .authoring_request import AuthoringRequest
from .backends import AuthoringBackend
from .docker_launch import DEFAULT_WALL_CLOCK_TIMEOUT_S, launch_authoring_container
from .staging import (
	CANDIDATE_CASE,
	FEEDBACK,
	REPORTS,
	build_guidance_dir,
	clone_artifact,
	create_staging_dirs,
)

logger = logging.getLogger(__name__)

_AEBENCH_FEEDBACK_FILENAME = "aebench_validation_feedback.json"

# FIXME: This is hardcoded as three parents up from this file
_AEBENCH_ROOT = Path(__file__).parents[3].resolve()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
	code: str
	severity: str
	path: str
	message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
	ok: bool
	issues: tuple[ValidationIssue, ...]


def validate_candidate_bundle(candidate_case_dir: Path) -> ValidationResult:
	"""Validate candidate case bundle against AEBench structure rules."""
	candidate_case_dir = candidate_case_dir.resolve()
	issues: list[ValidationIssue] = []

	oracle_src = candidate_case_dir / "oracle"
	oracles_src = candidate_case_dir / "oracles"

	if not oracle_src.is_dir() and not oracles_src.is_dir():
		return ValidationResult(
			ok=False,
			issues=(
				ValidationIssue(
					code="missing_oracle_dir",
					severity="error",
					path=str(oracle_src),
					message="oracle/ directory is missing from the candidate bundle",
				),
			),
		)

	actual_oracle_src = oracle_src if oracle_src.is_dir() else oracles_src

	with tempfile.TemporaryDirectory(prefix="aebench_validate_") as tmp_str:
		tmp = Path(tmp_str)

		for name in ("case.toml", "refs"):
			src = candidate_case_dir / name
			if not src.exists():
				continue
			if src.is_dir():
				shutil.copytree(src, tmp / name)
			else:
				shutil.copy2(src, tmp / name)

		shutil.copytree(actual_oracle_src, tmp / "oracles")
		(tmp / "artifact").mkdir()

		try:
			load_case_spec(tmp)
		except CaseBundleError as exc:
			issues.append(
				ValidationIssue(
					code="bundle_structure_error",
					severity="error",
					path=str(candidate_case_dir),
					message=str(exc),
				)
			)

	ok = not any(i.severity == "error" for i in issues)
	return ValidationResult(ok=ok, issues=tuple(issues))


@dataclass(frozen=True, slots=True)
class AuthoringResult:
	ok: bool
	staging_dir: Path
	candidate_dir: Path
	attempts: int
	validation_result: ValidationResult | None


def run_authoring_pipeline(
	request: AuthoringRequest,
	backend: AuthoringBackend,
	*,
	aebench_root: Path | None = None,
	wall_clock_timeout_s: float = DEFAULT_WALL_CLOCK_TIMEOUT_S,
	overwrite_staging: bool = False,
	dry_run: bool = False,
) -> AuthoringResult:
	"""Run full case authoring logic for one artifact."""
	resolved_root = (aebench_root or _AEBENCH_ROOT).resolve()
	staging_root = Path(request.staging_root).expanduser().resolve()

	logger.info("authoring case_id=%s agent=%s", request.case_id, backend.name)

	staging_dir = create_staging_dirs(
		staging_root,
		request.case_id,
		overwrite=overwrite_staging,
	)
	logger.info("staging directory created: %s", staging_dir)

	logger.info("cloning %s @ %s", request.artifact_git_url, request.artifact_git_ref[:12])
	clone_artifact(staging_dir, request.artifact_git_url, request.artifact_git_ref)

	build_guidance_dir(
		staging_dir,
		aebench_root=resolved_root,
		use_evaluator_src=request.use_evaluator_src,
		use_cases_as_examples=request.use_cases_as_examples,
		example_case_ids=list(request.example_case_ids),
	)
	n_examples = len(request.example_case_ids) if request.example_case_ids else "all"
	logger.info("guidance built (examples: %s)", n_examples)

	candidate_dir = staging_dir / CANDIDATE_CASE

	if dry_run:
		logger.info("dry-run: staging prepared, container not launched")
		return AuthoringResult(
			ok=False,
			staging_dir=staging_dir,
			candidate_dir=candidate_dir,
			attempts=0,
			validation_result=None,
		)

	max_attempts = request.native_max_repairs + 1
	validation_result: ValidationResult | None = None
	attempt = 0

	for attempt in range(1, max_attempts + 1):
		aebench_feedback_path: Path | None = None
		if attempt > 1:
			aebench_feedback_path = staging_dir / FEEDBACK / _AEBENCH_FEEDBACK_FILENAME

		logger.info(
			"pipeline attempt %d/%d (agent_max_repairs=%d)",
			attempt,
			max_attempts,
			request.agent_max_repairs,
		)

		launch_authoring_container(
			backend=backend,
			request=request,
			staging_dir=staging_dir,
			attempt=attempt,
			aebench_feedback_path=aebench_feedback_path,
			wall_clock_timeout_s=wall_clock_timeout_s,
		)

		logger.info("running native validation on %s", candidate_dir)
		validation_result = validate_candidate_bundle(candidate_dir)

		reports_dir = staging_dir / REPORTS
		_write_json(
			reports_dir / "aebench_validation_report.json",
			{
				"ok": validation_result.ok,
				"attempt": attempt,
				"issues": [asdict(i) for i in validation_result.issues],
			},
		)

		if validation_result.ok:
			logger.info("native validation passed on attempt %d", attempt)
			break

		n_issues = len(validation_result.issues)
		logger.warning(
			"native validation failed on attempt %d (%d issues)",
			attempt,
			n_issues,
		)
		for issue in validation_result.issues:
			logger.warning(
				"  [%s] %s: %s",
				issue.severity,
				issue.code,
				issue.message,
			)

		if attempt < max_attempts:
			_write_aebench_feedback(
				staging_dir=staging_dir,
				validation_result=validation_result,
				attempt=attempt,
			)
			logger.info(
				"repair %d/%d: feedback written, re-invoking agent",
				attempt,
				request.native_max_repairs,
			)
		else:
			logger.warning("native repair budget exhausted")

	ok = validation_result is not None and validation_result.ok

	if ok:
		logger.info("authoring succeeded for %s", request.case_id)
	else:
		logger.warning("authoring failed for %s", request.case_id)

	return AuthoringResult(
		ok=ok,
		staging_dir=staging_dir,
		candidate_dir=candidate_dir,
		attempts=attempt,
		validation_result=validation_result,
	)


def _write_json(path: Path, data: object) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_aebench_feedback(
	staging_dir: Path,
	validation_result: ValidationResult,
	attempt: int,
) -> None:
	feedback_path = staging_dir / FEEDBACK / _AEBENCH_FEEDBACK_FILENAME
	_write_json(
		feedback_path,
		{
			"source": "aebench_native_validation",
			"attempt": attempt,
			"ok": validation_result.ok,
			"issues": [asdict(i) for i in validation_result.issues],
		},
	)
