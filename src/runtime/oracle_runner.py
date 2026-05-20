"""Thin bridge between runtime and evaluator oracle execution."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

from evaluator.oracles.execution import run_oracle
from models import OracleResult, OracleStatus, RunResult


class OracleRunner(Protocol):
	def execute(
		self,
		case_dir: Path,
		*,
		runtime_result: Any,
		output_dir: Path,
		case: Any | None = None,
		workspace_dir: Path | None = None,
		runtime_session: Any | None = None,
		runtime_backend: Any | None = None,
	) -> OracleResult: ...


class DirectOracleRunner:
	def execute(
		self,
		case_dir: Path,
		*,
		runtime_result: Any,
		output_dir: Path,
		case: Any | None = None,
		workspace_dir: Path | None = None,
		runtime_session: Any | None = None,
		runtime_backend: Any | None = None,
	) -> OracleResult:
		return run_oracle(
			case_dir,
			runtime_result=runtime_result,
			output_dir=output_dir,
			case=case,
			workspace_dir=workspace_dir,
			runtime_session=runtime_session,
			runtime_backend=runtime_backend,
		)


class SubprocessOracleRunner:
	"""Run oracles in a subprocess.

	This runner cannot pass live runtime/session objects. Use DirectOracleRunner
	for container-aware checks that need the running runtime backend.
	"""

	def execute(
		self,
		case_dir: Path,
		*,
		runtime_result: Any,
		output_dir: Path,
		case: Any | None = None,
		workspace_dir: Path | None = None,
		runtime_session: Any | None = None,
		runtime_backend: Any | None = None,
	) -> OracleResult:
		_ = case, runtime_session, runtime_backend
		case_root = case_dir.resolve()

		with tempfile.TemporaryDirectory(prefix="ae_oracle_") as tmpdir:
			tmp_root = Path(tmpdir)
			context_path = tmp_root / "context.json"
			result_path = tmp_root / "oracle.json"
			_write_worker_context(
				context_path,
				case_root=case_root,
				output_dir=output_dir,
				runtime_result=runtime_result,
				workspace_dir=workspace_dir,
			)
			proc = subprocess.run(
				_worker_command(context_path, result_path),
				capture_output=True,
				text=True,
				check=False,
			)
			if proc.returncode != 0:
				return OracleResult(
					status=OracleStatus.ERROR,
					score=0,
					summary="Oracle subprocess failed.",
					error=(proc.stderr or proc.stdout).strip(),
				)
			if not result_path.is_file():
				return OracleResult(
					status=OracleStatus.ERROR,
					score=0,
					summary="Oracle subprocess did not write a result file.",
					error="missing oracle result output",
				)
			return OracleResult.model_validate_json(result_path.read_text(encoding="utf-8"))


def _write_worker_context(
	path: Path,
	*,
	case_root: Path,
	output_dir: Path,
	runtime_result: Any,
	workspace_dir: Path | None,
) -> None:
	runtime_payload = (
		runtime_result.model_dump(mode="json")
		if hasattr(runtime_result, "model_dump")
		else runtime_result
	)
	payload = {
		"case_dir": str(case_root),
		"output_dir": str(output_dir.resolve()),
		"runtime_result": runtime_payload,
		"workspace_dir": None if workspace_dir is None else str(workspace_dir.resolve()),
	}
	path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _worker_command(context_path: Path, result_path: Path) -> list[str]:
	return [
		sys.executable,
		"-m",
		"runtime.oracle_runner",
		"--worker",
		"--case-file",
		str(context_path),
		"--result-file",
		str(result_path),
	]


def _worker() -> int:
	import argparse

	parser = argparse.ArgumentParser(description="Run an AEBench oracle worker")
	parser.add_argument("--worker", action="store_true")
	parser.add_argument("--case-file", required=True)
	parser.add_argument("--result-file", required=True)
	args = parser.parse_args()

	payload = json.loads(Path(args.case_file).read_text(encoding="utf-8"))
	result = run_oracle(
		Path(payload["case_dir"]).resolve(),
		runtime_result=RunResult.model_validate(payload["runtime_result"]),
		output_dir=Path(payload["output_dir"]).resolve(),
		workspace_dir=(
			None
			if payload.get("workspace_dir") is None
			else Path(payload["workspace_dir"]).resolve()
		),
	)
	Path(args.result_file).write_text(
		json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(_worker())
