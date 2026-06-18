"""Tests to help migration/refactoring of py."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evaluator.oracles.oracle_checks_runtime import (
	DockerRuntimeCheckExecutor,
	LocalRuntimeCheckExecutor,
	SessionRuntimeCheckExecutor,
	build_runtime_check_executor,
)
from evaluator.oracles.process import (
	decode_text,
	run_subprocess_capture,
	truncate_text,
)
from evaluator.oracles.reporting import (
	BaseCheck,
	Check,
	CheckOutcome,
	CheckResult,
	build_oracle_report,
	run_checks,
)
from models import RuntimeMode


def _recorded_runtime(
	mode: RuntimeMode | str,
	*,
	saved_image: str | None = None,
	image: str | None = None,
) -> Any:
	return SimpleNamespace(
		runtime=SimpleNamespace(
			mode=mode,
			saved_image=saved_image,
			image=image,
		)
	)


def _oracle_context(
	tmp_path: Path,
	*,
	oracle_mode: RuntimeMode = RuntimeMode.INHERIT,
	oracle_image: str = "",
	include_oracle_runtime: bool = True,
	runtime_result: Any = None,
	runtime_session: Any = None,
	runtime_backend: Any = None,
) -> Any:
	case_dir = tmp_path / "case"
	workspace_dir = tmp_path / "workspace"
	artifact_dir = case_dir / "artifact"
	output_dir = tmp_path / "output"

	for path in (
		case_dir,
		case_dir / "refs",
		workspace_dir,
		artifact_dir,
		output_dir,
	):
		path.mkdir(parents=True, exist_ok=True)

	if include_oracle_runtime:
		oracle_config = SimpleNamespace(
			runtime=SimpleNamespace(
				mode=oracle_mode,
				image=oracle_image,
			)
		)
	else:
		oracle_config = SimpleNamespace()

	return SimpleNamespace(
		case_dir=case_dir,
		workspace_dir=workspace_dir,
		artifact_dir=artifact_dir,
		output_dir=output_dir,
		oracle_config=oracle_config,
		runtime_result=runtime_result,
		runtime_session=runtime_session,
		runtime_backend=runtime_backend,
	)


def _raise_check_error() -> CheckResult:
	raise RuntimeError("check failed unexpectedly")


def _raise_requirements_error() -> list[BaseCheck]:
	raise RuntimeError("requirements unavailable")


def test_decode_text_handles_none_bytes_and_text() -> None:
	assert decode_text(None) == ""
	assert decode_text(b"hello") == "hello"
	assert decode_text("hello") == "hello"
	assert decode_text(b"\xff") == "\ufffd"


def test_truncate_text_returns_short_text_unchanged() -> None:
	assert truncate_text("hello", 10) == "hello"


def test_truncate_text_adds_suffix() -> None:
	assert truncate_text("abcdefgh", 4) == "abcd\n... [output truncated]"


def test_run_subprocess_capture_returns_output_and_returncode(
	tmp_path: Path,
) -> None:
	result = run_subprocess_capture(
		cmd=(
			sys.executable,
			"-c",
			"import sys; "
			"print('stdout text'); "
			"print('stderr text', file=sys.stderr); "
			"raise SystemExit(3)",
		),
		cwd=tmp_path,
		env=None,
		timeout_seconds=5.0,
	)

	assert result.returncode == 3
	assert result.stdout == "stdout text\n"
	assert result.stderr == "stderr text\n"
	assert result.timed_out is False


def test_run_subprocess_capture_uses_cwd_and_environment(
	tmp_path: Path,
) -> None:
	env = os.environ.copy()
	env["AEBENCH_TEST_VALUE"] = "configured"

	result = run_subprocess_capture(
		cmd=(
			sys.executable,
			"-c",
			"import os; print(os.getcwd()); print(os.environ['AEBENCH_TEST_VALUE'])",
		),
		cwd=tmp_path,
		env=env,
		timeout_seconds=5.0,
	)

	assert result.returncode == 0
	assert result.stdout.splitlines() == [
		str(tmp_path),
		"configured",
	]
	assert result.stderr == ""
	assert result.timed_out is False


def test_run_subprocess_capture_streams_chunks(tmp_path: Path) -> None:
	chunks: list[tuple[str, str]] = []

	result = run_subprocess_capture(
		cmd=(
			sys.executable,
			"-c",
			"import sys; print('stdout text'); print('stderr text', file=sys.stderr)",
		),
		cwd=tmp_path,
		env=None,
		timeout_seconds=5.0,
		on_chunk=lambda stream_name, text: chunks.append((stream_name, text)),
	)

	stdout = "".join(text for stream_name, text in chunks if stream_name == "stdout")
	stderr = "".join(text for stream_name, text in chunks if stream_name == "stderr")

	assert result.returncode == 0
	assert stdout == result.stdout
	assert stderr == result.stderr


def test_run_subprocess_capture_truncates_output(tmp_path: Path) -> None:
	result = run_subprocess_capture(
		cmd=(
			sys.executable,
			"-c",
			"import sys; sys.stdout.write('x' * 64)",
		),
		cwd=tmp_path,
		env=None,
		timeout_seconds=5.0,
		capture_limit_chars=8,
	)

	assert result.returncode == 0
	assert result.stdout == "xxxxxxxx\n... [output truncated]"
	assert result.stderr == ""
	assert result.timed_out is False


def test_run_subprocess_capture_rejects_invalid_capture_limit(
	tmp_path: Path,
) -> None:
	with pytest.raises(ValueError, match="capture_limit_chars must be > 0"):
		run_subprocess_capture(
			cmd=(sys.executable, "-c", "print('unused')"),
			cwd=tmp_path,
			env=None,
			timeout_seconds=5.0,
			capture_limit_chars=0,
		)


def test_run_subprocess_capture_marks_timeout(tmp_path: Path) -> None:
	result = run_subprocess_capture(
		cmd=(
			sys.executable,
			"-c",
			"import time; time.sleep(10)",
		),
		cwd=tmp_path,
		env=None,
		timeout_seconds=0.1,
	)

	assert result.returncode is None
	assert result.timed_out is True


def test_run_checks_reports_required_and_optional_failures() -> None:
	checks = (
		Check(
			name="success",
			fn=lambda: CheckResult.success("passed"),
		),
		Check(
			name="required_failure",
			fn=lambda: CheckResult.failure("required failure"),
		),
		Check(
			name="optional_failure",
			optional=True,
			fn=lambda: CheckResult.failure("optional failure"),
		),
	)

	report = run_checks(checks, logger=logging.getLogger(__name__))

	assert report.ok is False
	assert report.passed_count == 1
	assert report.failed_count == 1
	assert [entry.outcome for entry in report.results] == [
		CheckOutcome.PASSED,
		CheckOutcome.FAILED,
		CheckOutcome.WARNING,
	]


def test_optional_failure_does_not_fail_report() -> None:
	checks = (
		Check(
			name="optional_failure",
			optional=True,
			fn=lambda: CheckResult.failure("optional failure"),
		),
	)

	report = run_checks(checks, logger=logging.getLogger(__name__))

	assert report.ok is True
	assert report.passed_count == 0
	assert report.failed_count == 0
	assert report.results[0].outcome == CheckOutcome.WARNING


def test_run_checks_converts_unexpected_exception_to_failure() -> None:
	checks = (
		Check(
			name="unexpected_exception",
			fn=_raise_check_error,
		),
	)

	report = run_checks(checks, logger=logging.getLogger(__name__))

	assert report.ok is False
	assert report.failed_count == 1
	assert report.results[0].outcome == CheckOutcome.FAILED
	assert report.results[0].message == (
		"unexpected error: RuntimeError: check failed unexpectedly"
	)


def test_build_oracle_report_converts_requirement_error_to_failure() -> None:
	report = build_oracle_report(
		logger=logging.getLogger(__name__),
		requirements=_raise_requirements_error,
	)

	assert report.ok is False
	assert report.failed_count == 1
	assert len(report.results) == 1
	assert report.results[0].name == "<requirements>"
	assert report.results[0].outcome == CheckOutcome.FAILED
	assert report.results[0].message == (
		"failed to enumerate requirements: RuntimeError: requirements unavailable"
	)


def test_explicit_local_runtime_uses_local_executor(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.LOCAL,
	)

	executor = build_runtime_check_executor(context)

	assert isinstance(executor, LocalRuntimeCheckExecutor)


def test_missing_oracle_runtime_inherits_active_session(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		include_oracle_runtime=False,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			image="recorded-image:latest",
		),
		runtime_session=object(),
		runtime_backend=object(),
	)

	executor = build_runtime_check_executor(context)

	assert isinstance(executor, SessionRuntimeCheckExecutor)


def test_inherit_without_recorded_runtime_uses_local_executor(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.INHERIT,
		runtime_result=None,
	)

	executor = build_runtime_check_executor(context)

	assert isinstance(executor, LocalRuntimeCheckExecutor)


def test_inherit_recorded_local_runtime_uses_local_executor(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.INHERIT,
		runtime_result=_recorded_runtime(RuntimeMode.LOCAL),
	)

	executor = build_runtime_check_executor(context)

	assert isinstance(executor, LocalRuntimeCheckExecutor)


def test_inherit_docker_runtime_prefers_saved_image(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.INHERIT,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			saved_image="saved-image:latest",
			image="original-image:latest",
		),
	)

	executor = build_runtime_check_executor(context)

	assert isinstance(executor, DockerRuntimeCheckExecutor)
	assert executor._image == "saved-image:latest"


def test_inherit_docker_runtime_falls_back_to_original_image(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.INHERIT,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			saved_image=None,
			image="original-image:latest",
		),
	)

	executor = build_runtime_check_executor(context)

	assert isinstance(executor, DockerRuntimeCheckExecutor)
	assert executor._image == "original-image:latest"


def test_inherit_docker_runtime_without_image_raises(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.INHERIT,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			saved_image=None,
			image=None,
		),
	)

	with pytest.raises(
		RuntimeError,
		match="inherited Docker runtime has no image",
	):
		build_runtime_check_executor(context)


def test_inherit_unsupported_runtime_mode_raises(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		oracle_mode=RuntimeMode.INHERIT,
		runtime_result=_recorded_runtime("unsupported"),
	)

	with pytest.raises(
		RuntimeError,
		match="Cannot build oracle runtime executor",
	):
		build_runtime_check_executor(context)
