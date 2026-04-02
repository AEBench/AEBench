"""Oracle phase runners, in-process and subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

from ..evaluator.oracles.execution import run_oracle
from ..domain.models import OracleResult, OracleStatus, RunResult


class OracleRunner(Protocol):
    def execute(
        self,
        case_dir: Path,
        *,
        runtime_result: Any,
        output_dir: Path,
        case: Any | None = None,
    ) -> OracleResult: ...


class DirectOracleRunner:

    def execute(
        self,
        case_dir: Path,
        *,
        runtime_result: Any,
        output_dir: Path,
        case: Any | None = None,
    ) -> Any:
        return run_oracle(
            case_dir,
            runtime_result=runtime_result,
            output_dir=output_dir,
            case=case,
        )


class SubprocessOracleRunner:
    """Run oracle phases in a subprocess for import isolation."""

    def execute(
        self,
        case_dir: Path,
        *,
        runtime_result: Any,
        output_dir: Path,
        case: Any | None = None,
    ) -> Any:
        case_root = case_dir.resolve()
        with tempfile.TemporaryDirectory(prefix="ae_oracle_") as tmpdir:
            tmp_root = Path(tmpdir)
            context_path = tmp_root / "context.json"
            result_path = tmp_root / "oracle.json"

            if hasattr(runtime_result, "model_dump"):
                runtime_payload = runtime_result.model_dump()
            else:
                runtime_payload = runtime_result

            context_path.write_text(
                json.dumps(
                    {
                        "case_dir": str(case_root),
                        "output_dir": str(output_dir.resolve()),
                        "runtime_result": runtime_payload,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "artevalbench.runtime.oracle_runner",
                    "--worker",
                    "--case-file",
                    str(context_path),
                    "--result-file",
                    str(result_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return OracleResult(
                    status=OracleStatus.ERROR,
                    score=0,
                    summary="Oracle subprocess failed.",
                    error=(proc.stderr or proc.stdout).strip()
                    or f"oracle subprocess exited with code {proc.returncode}",
                )
            if not result_path.is_file():
                return OracleResult(
                    status=OracleStatus.ERROR,
                    score=0,
                    summary="Oracle subprocess did not write a result file.",
                    error="missing oracle result output",
                )
            return OracleResult.model_validate_json(result_path.read_text(encoding="utf-8"))


def _worker() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run an AEBench oracle worker")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--case-file", required=True)
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.case_file).read_text(encoding="utf-8"))
    runtime_result = RunResult.model_validate(payload["runtime_result"])
    result = run_oracle(
        Path(payload["case_dir"]).resolve(),
        runtime_result=runtime_result,
        output_dir=Path(payload["output_dir"]).resolve(),
    )
    Path(args.result_file).write_text(
        json.dumps(result.model_dump(), indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker())
