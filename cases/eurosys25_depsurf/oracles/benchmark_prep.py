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

        manifest = self._load_json_object(
            self.ref_path("dataset_manifest.json"),
            label="dataset manifest",
        )

        dataset_root_value = manifest.get("dataset_root")
        subdirs = manifest.get("subdirs")
        basenames = manifest.get("basenames")

        if not isinstance(dataset_root_value, str) or not dataset_root_value.strip():
            raise ValueError("dataset manifest has invalid dataset_root")

        if not isinstance(subdirs, list) or not all(
            isinstance(value, str) and value.strip() for value in subdirs
        ):
            raise ValueError("dataset manifest has invalid subdirs")

        if not isinstance(basenames, list) or not all(
            isinstance(value, str) and value.strip() for value in basenames
        ):
            raise ValueError("dataset manifest has invalid basenames")

        clean_subdirs = tuple(value.strip() for value in subdirs)
        expected_basenames = tuple(value.strip() for value in basenames)
        dataset_root = repo_root / dataset_root_value.strip()

        reqs: list[utils.BaseCheck] = [
            self.path_check(
                name="dataset_root_exists",
                path=dataset_root,
                kind=PathKind.DIRECTORY,
            ),
        ]

        for subdir in clean_subdirs:
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", subdir).strip("_") or "unnamed"

            reqs.append(
                self.path_check(
                    name=f"dataset_subdir_exists_{safe_name}",
                    path=dataset_root / subdir,
                    kind=PathKind.DIRECTORY,
                )
            )

        reqs.append(
            utils.Check(
                name="dataset_basenames_present",
                fn=lambda: self._check_dataset_basenames_present(
                    dataset_root=dataset_root,
                    subdirs=clean_subdirs,
                    expected_basenames=expected_basenames,
                ),
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

    def _check_dataset_basenames_present(
        self,
        *,
        dataset_root,
        subdirs: Sequence[str],
        expected_basenames: Sequence[str],
    ) -> utils.CheckResult:
        expected = set(expected_basenames)
        missing: list[str] = []

        for subdir in subdirs:
            subdir_path = dataset_root / subdir

            if not self.is_dir(subdir_path):
                continue

            try:
                present = {
                    path.stem
                    for path in subdir_path.iterdir()
                    if path.is_file() and not path.name.startswith(".")
                }
            except OSError as exc:
                return utils.CheckResult.failure(
                    f"failed to read dataset directory {subdir_path}: {exc}"
                )

            absent = sorted(expected - present)
            if absent:
                missing.append(f"{subdir_path}: missing {', '.join(absent[:5])}")

        if missing:
            return utils.CheckResult.failure("; ".join(missing))

        return utils.CheckResult.success("dataset basenames match the manifest")