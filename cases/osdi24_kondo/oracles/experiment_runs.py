from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from .common import (
    PROTOCOLS,
    load_json_file,
    load_sloc_csv,
)

_SLOC_TOLERANCE = 5
_SLOC_FIELDS = ("sync_spec", "manual_proof", "sync_proof")


@dataclass(frozen=True, slots=True, kw_only=True)
class SlocExactMatchCheck(utils.BaseCheck):
    """Fail if SLOC values differ from reference beyond tolerance."""

    sloc_csv_path: Path
    reference_path: Path
    tolerance: int = _SLOC_TOLERANCE

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
        if not self.sloc_csv_path.is_file():
            return utils.CheckResult.failure(f"sloc.csv not found: {self.sloc_csv_path}")

        try:
            observed_data = load_sloc_csv(self.sloc_csv_path)
        except (ValueError, KeyError) as exc:
            return utils.CheckResult.failure(f"failed to parse sloc.csv: {exc}")

        try:
            ref_obj = load_json_file(self.reference_path, label="sloc reference")
        except ValueError as exc:
            return utils.CheckResult.failure(str(exc))

        if not isinstance(ref_obj, dict):
            return utils.CheckResult.failure("sloc reference: expected a JSON object")

        missing_protocols: list[str] = []
        mismatches: list[str] = []
        matched = 0

        for protocol in PROTOCOLS:
            ref_entry = ref_obj.get(protocol)
            if not isinstance(ref_entry, dict):
                continue

            obs_entry = observed_data.get(protocol)
            if obs_entry is None:
                missing_protocols.append(protocol)
                continue

            for field in _SLOC_FIELDS:
                ref_val = ref_entry.get(field)
                obs_val = obs_entry.get(field)
                if ref_val is None:
                    continue
                if obs_val is None:
                    mismatches.append(f"{protocol}.{field}: missing in sloc.csv")
                    continue

                if abs(int(obs_val) - int(ref_val)) > self.tolerance:
                    mismatches.append(
                        f"{protocol}.{field}: got {obs_val}, expected {ref_val} "
                        f"(+/- {self.tolerance})"
                    )
                else:
                    matched += 1

        if missing_protocols:
            return utils.CheckResult.failure(
                f"sloc.csv missing protocols: {', '.join(missing_protocols)}"
            )

        if mismatches:
            shown = mismatches[:10]
            more = f"\n... ({len(mismatches) - 10} more)" if len(mismatches) > 10 else ""
            return utils.CheckResult.failure(
                f"{len(mismatches)} SLOC mismatch(es):\n"
                + "\n".join(f"- {m}" for m in shown) + more
            )

        return utils.CheckResult.success(
            message=f"all {matched} SLOC values match reference (tolerance +/- {self.tolerance})"
        )


class OracleExperimentRuns(CaseOracleExperimentRunsBase):

    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.paths.workspace_dir

        kondo_protos = repo_root / "kondoPrototypes"
        sloc_csv = kondo_protos / "evaluation" / "sloc.csv"
        sloc_ref = self.ref_path("sloc.ref.json")

        reqs: list[utils.BaseCheck] = [
            FilesystemPathCheck(
                name="sloc_csv_exists",
                path=sloc_csv,
                path_type=PathType.FILE,
            ),
            SlocExactMatchCheck(
                name="sloc_values_match",
                sloc_csv_path=sloc_csv,
                reference_path=sloc_ref,
            ),
        ]

        for protocol in PROTOCOLS:
            reqs.append(
                FilesystemPathCheck(
                    name=f"sync_proof_{protocol}",
                    path=kondo_protos / protocol / "sync" / "applicationProof.dfy",
                    path_type=PathType.FILE,
                )
            )
            reqs.append(
                FilesystemPathCheck(
                    name=f"manual_proof_{protocol}",
                    path=kondo_protos / protocol / "manual" / "applicationProof.dfy",
                    path_type=PathType.FILE,
                )
            )

        return tuple(reqs)
