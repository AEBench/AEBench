from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import ICEMU_SOURCE_PATH, LLVM_SOURCE_PATH, README_PATH, RISCV_TOOLCHAIN_PATH


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm the artifact checkout and its build inputs are fully staged."""

	def requirements(self) -> Sequence[BaseCheck]:
		return tuple(
			self.path_check(
				name=name,
				path=self.runtime_path(relative),
				kind=PathKind.FILE,
			)
			for name, relative in (
				("readme", README_PATH),
				("icemu_submodule", ICEMU_SOURCE_PATH),
				("patched_llvm_16_0_2_source", LLVM_SOURCE_PATH),
				("riscv_toolchain", RISCV_TOOLCHAIN_PATH),
			)
		)
