from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import HERD7_ENV, MBCHECK_BIN

_SELFTEST_PASS = "PASS: mbcheck builds and analyses correctly."

# selftest.sh still passes with "herd7 verdict: skipped" when herd7 is not
# found, so the exit status also requires herd7 to have answered Ok.
_SELFTEST_CMD = (
	f"{HERD7_ENV}"
	'out="$(scripts/selftest.sh 2>&1)"; rc=$?; printf "%s\\n" "$out"; '
	'[ "$rc" -eq 0 ] && printf "%s\\n" "$out" | grep -Eq "herd7 verdict +: Ok"'
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Run the artifact's self-test against the built mbcheck and herd7."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.path_check(
				name="mbcheck_binary",
				path=self.runtime_path(MBCHECK_BIN),
				kind=PathKind.FILE,
			),
			self.command_check(
				name="selftest_with_herd7_ok",
				cmd=("bash", "-lc", _SELFTEST_CMD),
				cwd=self.runtime_path(),
				timeout_seconds=300.0,
				signature=_SELFTEST_PASS,
			),
		)
