from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from typing import Any

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.oracle_checks_runtime import OraclePath, RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = self.workspace_path()

        manifest = self._load_json_object(
            self.ref_path("results_manifest.json"),
            label="results manifest",
        )

        result_files = manifest.get("result_files")
        if not isinstance(result_files, list) or not all(
            isinstance(value, str) and value.strip() for value in result_files
        ):
            raise ValueError("results manifest has invalid result_files")

        results_root = repo_root / "results"

        reqs: list[BaseCheck] = [
            self.path_check(
                name="results_root_exists",
                path=results_root,
                kind=PathKind.DIRECTORY,
            ),
        ]

        for relative_path in result_files:
            clean_relative_path = relative_path.strip()
            result_path = results_root / clean_relative_path
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", clean_relative_path).strip("_") or "unnamed"

            reqs.append(
                self.path_check(
                    name=f"result_file_exists_{safe_name}",
                    path=result_path,
                    kind=PathKind.FILE,
                )
            )
            reqs.append(
                Check(
                    name=f"result_file_parseable_{safe_name}",
                    fn=lambda executor, path=result_path: self._check_csv_result_file_parseable(
                        path, executor=executor
                    ),
                )
            )

        return tuple(reqs)

    def _load_json_object(self, path: OraclePath, *, label: str) -> dict[str, Any]:
        try:
            text = self.read_text(path)
            data = json.loads(text)
        except OSError as exc:
            raise ValueError(f"failed to read {label}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid {label}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"{label} must contain a JSON object")

        return data

    def _check_csv_result_file_parseable(
        self, path, *, executor: RuntimeCheckExecutor
    ) -> CheckResult:
        if not executor.path_is_file(path):
            return CheckResult.failure(f"missing result file: {path}")

        try:
            text = executor.read_file_text(path)
            rows = [
                row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)
            ]
        except OSError as exc:
            return CheckResult.failure(f"failed to read result file {path}: {exc}")
        except csv.Error as exc:
            return CheckResult.failure(f"invalid CSV in {path}: {exc}")

        if len(rows) < 2:
            return CheckResult.failure(f"expected at least one data row in {path}")

        return CheckResult.success(f"parsed {path}")
