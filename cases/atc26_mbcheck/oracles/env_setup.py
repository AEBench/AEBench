from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import HERD7_ENV, SVF_DIR

# mbcheck cannot parse IR from a newer LLVM, so 18 is both floor and ceiling.
_LLVM_MIN = (18, 0, 0)
_LLVM_MAX = (18, 999, 999)
_LLVM_TOOLS = ("clang-18", "opt-18", "llvm-link-18", "llvm-config-18", "ld.lld-18")
_HERD7_VERSION = (7, 58, 0)
_CMAKE_MIN = (3, 16, 0)
_PYTHON_MIN = (3, 10, 0)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm the LLVM 18 toolchain, herd7 7.58 and a built SVF 3.2."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.version_check(
				name=f"{tool.replace('-', '_').replace('.', '_')}_version",
				cmd=(tool, "--version"),
				min_version=_LLVM_MIN,
				max_version=_LLVM_MAX,
			)
			for tool in _LLVM_TOOLS
		]
		checks.extend(
			(
				self.version_check(
					name="cmake_version",
					cmd=("cmake", "--version"),
					min_version=_CMAKE_MIN,
				),
				self.command_check(
					name="ninja_available",
					cmd=("ninja", "--version"),
					timeout_seconds=30.0,
				),
				self.version_check(
					name="python3_version",
					cmd=("python3", "--version"),
					min_version=_PYTHON_MIN,
				),
				self.version_check(
					name="herd7_version",
					cmd=("bash", "-lc", f"{HERD7_ENV}herd7 -version"),
					min_version=_HERD7_VERSION,
					max_version=_HERD7_VERSION,
				),
				# The README lists libSvfCore.a, but SVF 3.2's build.sh produces
				# shared libraries; mbcheck's find_library accepts either.
				self.min_matching_entry_count_check(
					name="svf_core_library",
					directory=self.runtime_path(SVF_DIR, "Release-build/lib"),
					pattern="libSvfCore.*",
				),
				self.min_matching_entry_count_check(
					name="svf_llvm_library",
					directory=self.runtime_path(SVF_DIR, "Release-build/lib"),
					pattern="libSvfLLVM.*",
				),
				self.path_check(
					name="svf_extapi_bitcode",
					path=self.runtime_path(SVF_DIR, "Release-build/lib/extapi.bc"),
					kind=PathKind.FILE,
				),
				# A clone made the README's way keeps its tag; a tarball or a
				# stripped .git does not, so this one only warns.
				self.command_check(
					name="svf_release_tag",
					cmd=("git", "-C", SVF_DIR, "describe", "--tags", "--exact-match"),
					timeout_seconds=30.0,
					signature="SVF-3.2",
					optional=True,
				),
			)
		)
		return tuple(checks)
