"""Shared oracle infrastructure: checks, executors, subprocess helpers, and reporting."""

from __future__ import annotations

DEFAULT_MAX_TRUNCATED_MESSAGE_CHARS = 2_048


from .reporting import (
	BaseCheck,
	Check,
	CheckEntry,
	CheckOutcome,
	CheckResult,
	Checkable,
	OracleReport,
	build_oracle_report,
	log_oracle_report,
	run_checks,
)

from .process import (
	DEFAULT_MAX_CAPTURE_CHARS,
	ProcResult,
	decode_text,
	run_subprocess_capture,
	stream_subprocess,
	truncate_text,
)

from .oracle_checks_runtime import (
	DockerRuntimeCheckExecutor,
	LocalRuntimeCheckExecutor,
	PathLike,
	RuntimeCheckExecutor,
	SessionRuntimeCheckExecutor,
	build_path_mounts,
	build_runtime_check_executor,
	check_path_exists,
	check_path_is_dir,
	check_path_is_file,
	check_read_file_text,
	get_check_path_separator,
	path_from_user_input,
	read_check_env_var,
	resolve_check_executable,
	run_check_process_capture,
)