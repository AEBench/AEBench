from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind  # type: ignore[import-untyped]
from evaluator.oracles.checks import BaseCheck  # type: ignore[import-untyped]


# rustup installs into ~/.cargo/bin, which a plain `docker exec` PATH does not
# include, so the Rust checks add it explicitly. 1.80.0 is the toolchain the
# paper used; the artifact builds on it.
RUST_MIN_VERSION = "1.80.0"
_CARGO_PATH = 'export PATH="$HOME/.cargo/bin:$PATH"; '


def _min_version_cmd(tool: str, minimum: str) -> str:
    return (
        _CARGO_PATH
        + f'v=$({tool} --version | sed -E "s/^[^0-9]*([0-9]+(\\.[0-9]+)*).*/\\1/") && '
        + f'[ "$(printf "%s\\n%s\\n" "$v" {minimum} | sort -V | head -n1)" = {minimum} ]'
    )


class OracleEnvSetup(CaseOracleEnvSetupBase):  # type: ignore[misc]
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            self.command_check(
                name="rustc",
                cmd=_min_version_cmd("rustc", RUST_MIN_VERSION),
                timeout_seconds=60.0,
                use_shell=True,
            ),
            self.command_check(
                name="cargo",
                cmd=_min_version_cmd("cargo", "1.0.0"),
                timeout_seconds=60.0,
                use_shell=True,
            ),
            self.version_check(
                name="node",
                cmd=("node", "--version"),
                min_version=(0, 0, 0),
            ),
            self.version_check(
                name="make",
                cmd=("make", "--version"),
                min_version=(0, 0, 0),
                optional=True,
            ),
            self.path_check(
                name="repo_root_exists",
                path=self.workspace_path(),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="datasets_ref_exists",
                path=self.ref_path("datasets.ref.json"),
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="timings_ref_exists",
                path=self.ref_path("timings.ref.json"),
                kind=PathKind.FILE,
            ),
        )
