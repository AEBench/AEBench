from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from typing import Any

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathKind


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
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

        reqs: list[utils.BaseCheck] = [
            self.path_check(
                name="results_root_exists",
                path=results_root,
                kind=PathKind.DIRECTORY,
            ),
        ]

        for relative_path in result_files:
            clean_relative_path = relative_path.strip()
            result_path = results_root / clean_relative_path
            safe_name = (
                re.sub(r"[^A-Za-z0-9_]+", "_", clean_relative_path).strip("_")
                or "unnamed"
            )

            reqs.append(
                self.path_check(
                    name=f"result_file_exists_{safe_name}",
                    path=result_path,
                    kind=PathKind.FILE,
                )
            )
            reqs.append(
                utils.Check(
                    name=f"result_file_parseable_{safe_name}",
                    fn=lambda path=result_path: self._check_csv_result_file_parseable(path),
                )
            )

        return tuple(reqs)

    def _load_json_object(self, path: str | object, *, label: str) -> dict[str, Any]:
        try:
            text = self.read_text(path)  # type: ignore[arg-type]
            data = json.loads(text)
        except OSError as exc:
            raise ValueError(f"failed to read {label}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid {label}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"{label} must contain a JSON object")

        return data

    def _check_csv_result_file_parseable(self, path) -> utils.CheckResult:
        if not self.is_file(path):
            return utils.CheckResult.failure(f"missing result file: {path}")

        try:
            text = self.read_text(path)
            rows = [
                row
                for row in csv.reader(io.StringIO(text))
                if any(cell.strip() for cell in row)
            ]
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to read result file {path}: {exc}"
            )
        except csv.Error as exc:
            return utils.CheckResult.failure(f"invalid CSV in {path}: {exc}")

        if len(rows) < 2:
            return utils.CheckResult.failure(
                f"expected at least one data row in {path}"
            )

        return utils.CheckResult.success(f"parsed {path}")