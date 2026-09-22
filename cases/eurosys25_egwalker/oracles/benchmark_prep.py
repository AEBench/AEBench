from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult


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
class DatasetManifestCheck(BaseCheck):
    workspace_root: Path
    reference_path: Path
    max_items_to_report: int = 10

    def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
        try:
            text = executor.read_file_text(self.reference_path)
            entries = list(_dataset_entries(json.loads(text)))
        except Exception as exc:
            return CheckResult.failure(f"{self.name}: failed to read dataset reference: {exc}")

        missing: list[str] = []
        wrong_size: list[str] = []

        for rel_path, expected_size in entries:
            path = self.workspace_root / rel_path

            if not executor.path_is_file(path):
                missing.append(rel_path)
                continue

            resolved = str(executor.resolve_path(path))
            result = executor.run_process_capture(
                cmd=("wc", "-c", resolved),
                cwd=None,
                env=None,
                timeout_seconds=10.0,
            )
            if result.returncode != 0:
                wrong_size.append(f"{rel_path}: failed to read size")
                continue
            try:
                actual_size = int(result.stdout.split()[0])
            except (ValueError, IndexError):
                wrong_size.append(f"{rel_path}: failed to parse size")
                continue
            if actual_size != expected_size:
                wrong_size.append(f"{rel_path}: expected {expected_size}, got {actual_size}")

        if not missing and not wrong_size:
            return CheckResult.success()

        lines = [f"{self.name}: dataset files do not match refs"]
        if missing:
            lines.append("missing files:")
            lines.extend(f"- {item}" for item in missing[: self.max_items_to_report])
        if wrong_size:
            lines.append("size mismatches:")
            lines.extend(f"- {item}" for item in wrong_size[: self.max_items_to_report])

        return CheckResult.failure("\n".join(lines))


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            DatasetManifestCheck(
                name="dataset_manifest_matches_reference",
                workspace_root=self.workspace_path(),
                reference_path=self.ref_path("datasets.ref.json"),
            ),
        )
