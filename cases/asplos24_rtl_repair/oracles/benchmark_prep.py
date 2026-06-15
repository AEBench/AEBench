from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.workspace_path()
        manifest = self._load_manifest(self.ref_path("benchmark_manifest.json"))

        required_directories = self._manifest_string_list(
            manifest,
            "required_directories",
            label="benchmark manifest",
        )
        required_files = self._manifest_string_list(
            manifest,
            "required_files",
            label="benchmark manifest",
        )

        reqs: list[utils.BaseCheck] = []

        for rel_path in required_directories:
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", rel_path).strip("_") or "root"
            reqs.append(
                self.path_check(
                    name=f"dir_{safe_name}",
                    path=repo_root / rel_path,
                    kind=PathKind.DIRECTORY,
                )
            )

        for rel_path in required_files:
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", rel_path).strip("_") or "file"
            reqs.append(
                self.path_check(
                    name=f"file_{safe_name}",
                    path=repo_root / rel_path,
                    kind=PathKind.FILE,
                )
            )

        return tuple(reqs)

    def _load_manifest(self, path) -> dict[str, Any]:
        try:
            text = self.read_text(path)
            data = json.loads(text)
        except OSError as exc:
            raise ValueError(f"failed to read benchmark manifest: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid benchmark manifest: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError("benchmark manifest must contain a JSON object")

        return data

    @staticmethod
    def _manifest_string_list(
        manifest: dict[str, Any],
        key: str,
        *,
        label: str,
    ) -> tuple[str, ...]:
        value = manifest.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"{label} has invalid {key}")

        return tuple(item.strip() for item in value)
