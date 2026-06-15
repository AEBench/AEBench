from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathKind

_STATUS_PATTERN = re.compile(r'^status\s*=\s*"(?P<status>[^"]+)"\s*$', re.MULTILINE)


def _parse_custom_status(text: str) -> str | None:
    in_custom = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[custom]":
            in_custom = True
            continue
        if in_custom and stripped.startswith("["):
            break
        if not in_custom:
            continue
        match = _STATUS_PATTERN.match(stripped)
        if match is not None:
            return match.group("status")
    return None


def _load_reference(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("default experiment reference must be a JSON object")
    return data


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        reference = _load_reference(self.ref_path("default_experiment.ref.json"))
        experiment_dir = str(reference.get("experiment_dir", "")).strip()
        if not experiment_dir:
            raise ValueError("default experiment reference missing experiment_dir")

        status_counts = reference.get("status_counts")
        if not isinstance(status_counts, dict):
            raise ValueError("default experiment reference missing status_counts")

        expected_counts: dict[str, int] = {}
        for status, count in status_counts.items():
            if not isinstance(status, str) or not status.strip():
                raise ValueError("default experiment reference has invalid status name")
            if not isinstance(count, int) or count < 0:
                raise ValueError(
                    f"default experiment reference has invalid count for {status!r}"
                )
            expected_counts[status.strip()] = count

        max_timeout = reference.get("max_timeout")
        if not isinstance(max_timeout, int) or max_timeout < 0:
            raise ValueError("default experiment reference missing max_timeout")

        min_result_tomls = reference.get("min_result_tomls")
        if not isinstance(min_result_tomls, int) or min_result_tomls <= 0:
            raise ValueError("default experiment reference missing min_result_tomls")

        experiment_root = self.workspace_path(experiment_dir)

        return (
            self.path_check(
                name="experiment_dir_exists",
                path=experiment_root,
                kind=PathKind.DIRECTORY,
            ),
            utils.Check(
                name="default_experiment_status_counts",
                fn=lambda: self._check_experiment_status_counts(
                    experiment_root=experiment_root,
                    expected_counts=expected_counts,
                    max_timeout=max_timeout,
                    min_result_tomls=min_result_tomls,
                ),
            ),
        )

    def _check_experiment_status_counts(
        self,
        *,
        experiment_root: Path,
        expected_counts: dict[str, int],
        max_timeout: int,
        min_result_tomls: int,
    ) -> utils.CheckResult:
        if not self.is_dir(experiment_root):
            return utils.CheckResult.failure(
                f"experiment directory missing: {experiment_root}"
            )

        try:
            result_files = sorted(experiment_root.rglob("result.toml"))
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to scan experiment directory {experiment_root}: {exc}"
            )

        if len(result_files) < min_result_tomls:
            return utils.CheckResult.failure(
                f"found {len(result_files)} result.toml file(s) in {experiment_root}, "
                f"expected at least {min_result_tomls}"
            )

        observed = Counter()
        unreadable: list[str] = []

        for result_path in result_files:
            try:
                text = self.read_text(result_path)
            except OSError as exc:
                unreadable.append(f"{result_path}: {exc}")
                continue

            status = _parse_custom_status(text)
            if status is None:
                unreadable.append(f"{result_path}: missing [custom].status")
                continue

            observed[status] += 1

        if unreadable:
            preview = "; ".join(unreadable[:3])
            suffix = f" (+{len(unreadable) - 3} more)" if len(unreadable) > 3 else ""
            return utils.CheckResult.failure(
                f"could not parse status from result.toml files: {preview}{suffix}"
            )

        mismatches: list[str] = []

        for status, expected in sorted(expected_counts.items()):
            actual = observed.get(status, 0)
            if actual != expected:
                mismatches.append(f"{status}: observed {actual}, expected {expected}")

        timeout_count = observed.get("timeout", 0)
        if timeout_count > max_timeout:
            mismatches.append(
                f"timeout: observed {timeout_count}, expected at most {max_timeout}"
            )

        if mismatches:
            observed_summary = ", ".join(
                f"{status}={count}" for status, count in sorted(observed.items())
            )
            return utils.CheckResult.failure(
                "default experiment status counts do not match reference: "
                + "; ".join(mismatches)
                + f"; observed={{{observed_summary}}}"
            )

        return utils.CheckResult.success(
            f"matched default experiment status counts across {len(result_files)} result.toml files"
        )
