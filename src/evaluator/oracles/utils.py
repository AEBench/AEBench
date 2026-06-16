"""Shared oracle infrastructure: checks, executors, subprocess helpers, and reporting."""

from __future__ import annotations

import abc
import codecs
import dataclasses
import enum
import locale
import logging
import os
import pathlib
import selectors
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any, Protocol, cast, runtime_checkable

from models import OracleInput, RuntimeMode
from runtime.backend import BenchRuntime, validate_env_var_name


from .process import (
	DEFAULT_MAX_CAPTURE_CHARS,
	ProcResult,
	decode_text,
	run_subprocess_capture,
	truncate_text,
)

from .executors import (
	DockerRuntimeCheckExecutor,
	LocalRuntimeCheckExecutor,
	PathLike,
	RuntimeCheckExecutor,
	SessionRuntimeCheckExecutor,
	UnavailableRuntimeCheckExecutor,
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

DEFAULT_MAX_TRUNCATED_MESSAGE_CHARS = 2_048
