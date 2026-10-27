from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from evaluator.oracles import reporting
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath

_MARKER = "__AEBENCH_VALUE__"


def shell_env_var(executor: RuntimeCheckExecutor, name: str) -> str | None:
	"""Reads an environment variable as the agent's shells would see it.

	The README exports WASABI_ROOT_DIR and JAVA_HOME in a shell, and agents
	usually persist them in ~/.bashrc, which only interactive shells read.
	"""
	for flags in ("-ic", "-lc"):
		try:
			proc = executor.run_process_capture(
				cmd=("bash", flags, f'printf "{_MARKER}%s" "${{{name}}}"'),
				cwd=None,
				env=None,
				timeout_seconds=10.0,
			)
		except (OSError, RuntimeError):
			continue
		_, found, value = proc.stdout.rpartition(_MARKER)
		if proc.returncode == 0 and found and value.strip():
			return value.strip()
	return executor.read_env_var(name) or None


def eval_roots(executor: RuntimeCheckExecutor, workspace_dir: Path) -> list[RuntimePath]:
	"""Returns the directories that may hold benchmarks/ and results/, in priority order.

	run.py and display_bug_results.py use $WASABI_ROOT_DIR/.., and the README
	clones WASABI into ~/sosp24-ae/wasabi, so neither lives inside the artifact.
	"""
	workspace = PurePosixPath(str(executor.resolve_path(workspace_dir)))
	roots = [workspace.parent]
	if wasabi_root := shell_env_var(executor, "WASABI_ROOT_DIR"):
		# Keep ".." unresolved: the OS resolves it after following symlinks, as run.py does.
		roots.insert(0, PurePosixPath(wasabi_root) / "..")
	if home := executor.read_env_var("HOME"):
		roots.append(PurePosixPath(home) / "sosp24-ae")
	roots.append(workspace)
	return [RuntimePath(root) for root in dict.fromkeys(roots)]


def find_eval_dir(
	executor: RuntimeCheckExecutor, workspace_dir: Path, name: str
) -> tuple[RuntimePath | None, list[RuntimePath]]:
	"""Returns the first ``<root>/<name>`` directory, and every candidate searched."""
	candidates = [RuntimePath(root.value / name) for root in eval_roots(executor, workspace_dir)]
	found = next((path for path in candidates if executor.path_is_dir(path)), None)
	return found, candidates


def missing_dir_check(name: str, candidates: list[RuntimePath]) -> reporting.Check:
	searched = ", ".join(str(path) for path in candidates)
	return reporting.Check(
		name=name,
		fn=lambda _executor: reporting.CheckResult.failure(
			f"directory not found; searched {searched}"
		),
	)


def run_in_runtime(
	executor: RuntimeCheckExecutor,
	fn: Callable[..., Any],
	*args: str,
	timeout_seconds: float,
) -> Any:
	"""Calls ``fn(*args)`` in the runtime's python3 and returns its JSON-decoded result."""
	script = (
		f"{inspect.getsource(fn)}\n"
		"import json, sys\n"
		f"print(json.dumps({fn.__name__}(*sys.argv[1:])))"
	)
	proc = executor.run_process_capture(
		cmd=("python3", "-c", script, *args),
		cwd=None,
		env=None,
		timeout_seconds=timeout_seconds,
	)
	if proc.timed_out:
		raise RuntimeError(f"timed out after {timeout_seconds:g}s")
	if proc.returncode != 0:
		raise RuntimeError(proc.stderr.strip() or f"exit code {proc.returncode}")
	return json.loads(proc.stdout)
