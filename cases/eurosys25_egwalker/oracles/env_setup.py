from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

# rustup installs into ~/.cargo/bin, which a plain `docker exec` PATH does not
# include, so the Rust checks add it explicitly. The README names Rust 1.78 for
# the published paper and 1.80 for the current results, and says any recent
# version should work. cargo is released with rustc and shares its version.
RUST_MIN_VERSION = (1, 78, 0)
_CARGO_PATH = 'export PATH="$HOME/.cargo/bin:$PATH"; '


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.version_check(
				name="rustc",
				cmd=("sh", "-c", _CARGO_PATH + "rustc --version"),
				min_version=RUST_MIN_VERSION,
				version_regex=r"rustc (\d+\.\d+(?:\.\d+)?)",
			),
			self.version_check(
				name="cargo",
				cmd=("sh", "-c", _CARGO_PATH + "cargo --version"),
				min_version=RUST_MIN_VERSION,
				version_regex=r"cargo (\d+\.\d+(?:\.\d+)?)",
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
