from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from .common import PROTOCOLS


_AUTOGEN_FILE = "messageInvariantsAutogen.dfy"

_REQUIRED_SYNC_FILES = (
    "applicationProof.dfy",
    "spec.dfy",
    "system.dfy",
    "verify",
)

_REQUIRED_MANUAL_FILES = (
    "applicationProof.dfy",
    "spec.dfy",
    "verify",
)

_REQUIRED_ASYNC_KONDO_FILES = (
    "distributedSystem.dfy",
    "spec.dfy",
    "verify",
)


def _safe_name_part(value: str) -> str:
    return (
        value.replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
    )


def _source_file_checks(
    *,
    protos_dir: Path,
    protocol: str,
    variant: str,
    filenames: Sequence[str],
) -> tuple[utils.BaseCheck, ...]:
    protocol_part = _safe_name_part(protocol)
    variant_part = _safe_name_part(variant)

    return tuple(
        FilesystemPathCheck(
            name=f"{protocol_part}_{variant_part}_{_safe_name_part(filename)}",
            path=protos_dir / protocol / variant / filename,
            path_type=PathType.FILE,
        )
        for filename in filenames
    )


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        protos_dir = self.workspace_path("kondoPrototypes")

        reqs: list[utils.BaseCheck] = [
            FilesystemPathCheck(
                name="verify_all_script",
                path=protos_dir / "verify-all",
                path_type=PathType.FILE,
            ),
        ]

        for protocol in PROTOCOLS:
            reqs.extend(
                _source_file_checks(
                    protos_dir=protos_dir,
                    protocol=protocol,
                    variant="sync",
                    filenames=_REQUIRED_SYNC_FILES,
                )
            )
            reqs.extend(
                _source_file_checks(
                    protos_dir=protos_dir,
                    protocol=protocol,
                    variant="manual",
                    filenames=_REQUIRED_MANUAL_FILES,
                )
            )
            reqs.extend(
                _source_file_checks(
                    protos_dir=protos_dir,
                    protocol=protocol,
                    variant="async-kondo",
                    filenames=_REQUIRED_ASYNC_KONDO_FILES,
                )
            )

            reqs.append(
                FilesystemPathCheck(
                    name=f"{_safe_name_part(protocol)}_async_kondo_autogen",
                    path=protos_dir / protocol / "async-kondo" / _AUTOGEN_FILE,
                    path_type=PathType.FILE,
                )
            )

        return tuple(reqs)