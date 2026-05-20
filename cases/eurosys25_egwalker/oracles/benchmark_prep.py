from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, utils  # type: ignore[import-untyped]
from evaluator.oracles.checks import BaseCheck  # type: ignore[import-untyped]


def _dataset_entries(payload: object) -> Iterable[tuple[str, int]]:
    if not isinstance(payload, list):
        raise ValueError("datasets reference must be a JSON array")

    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"entry[{index}] must be an object")

        path = entry.get("filepath")
        size = entry.get("sizeinbytes")

        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"entry[{index}].filepath must be a non-empty string")
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"entry[{index}].filepath must stay inside the workspace")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"entry[{index}].sizeinbytes must be a non-negative int")

        yield path, size


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetManifestCheck(utils.BaseCheck):  # type: ignore[misc]
    workspace_root: Path
    reference_path: Path
    executor: utils.RuntimeCheckExecutor | None = None
    max_items_to_report: int = 10

    def check(self) -> utils.CheckResult:
        try:
            text = utils.check_read_file_text(self.reference_path, executor=self.executor)
            entries = list(_dataset_entries(json.loads(text)))
        except Exception as exc:
            return utils.CheckResult.failure(f"{self.name}: failed to read dataset reference: {exc}")

        missing: list[str] = []
        wrong_size: list[str] = []

        for rel_path, expected_size in entries:
            path = self.workspace_root / rel_path

            if not utils.check_path_is_file(path, executor=self.executor):
                missing.append(rel_path)
                continue

            actual_size = utils.check_file_size(path, executor=self.executor)
            if actual_size != expected_size:
                wrong_size.append(f"{rel_path}: expected {expected_size}, got {actual_size}")

        if not missing and not wrong_size:
            return utils.CheckResult.success()

        lines = [f"{self.name}: dataset files do not match refs"]
        if missing:
            lines.append("missing files:")
            lines.extend(f"- {item}" for item in missing[: self.max_items_to_report])
        if wrong_size:
            lines.append("size mismatches:")
            lines.extend(f"- {item}" for item in wrong_size[: self.max_items_to_report])

        return utils.CheckResult.failure("\n".join(lines))


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):  # type: ignore[misc]
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            DatasetManifestCheck(
                name="dataset_manifest_matches_reference",
                workspace_root=self.workspace_path(),
                reference_path=self.ref_path("datasets.ref.json"),
                executor=self.executor,
            ),
        )
