from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .checks import CorpusManifestCheck, FileCountCheck
from .consts import CORPUS_DIR, KERNEL_DIR, RESULTS_REF, SUITE_IR_DIR, SYNC_CASES

_KERNEL_VERSION = "6.19.0"
_CORPUS_MODULES = 3160

_KERNEL_VERSION_CMD = (
	f'v="$(make -s -C {KERNEL_DIR} kernelversion)" || exit 1; '
	f'echo "kernelversion=$v"; [ "$v" = "{_KERNEL_VERSION}" ]'
)
# The README's configuration: arm64 defconfig compiled with LLVM=-18.
_KERNEL_CONFIG_CMD = (
	f"cfg={KERNEL_DIR}/.config; "
	'grep -x "CONFIG_ARM64=y" "$cfg" && grep -x "CONFIG_CC_IS_CLANG=y" "$cfg" '
	'&& grep -E "^CONFIG_CLANG_VERSION=18[0-9]{4}$" "$cfg"'
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Check the built v6.19 kernel, its IR corpus and the Table 8 suite IR."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.command_check(
				name="kernel_tree_is_v6_19",
				cmd=("bash", "-c", _KERNEL_VERSION_CMD),
				timeout_seconds=120.0,
			),
			self.command_check(
				name="kernel_configured_arm64_clang18",
				cmd=("bash", "-c", _KERNEL_CONFIG_CMD),
				timeout_seconds=30.0,
			),
			self.path_check(
				name="kernel_vmlinux_built",
				path=self.runtime_path(KERNEL_DIR, "vmlinux"),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="kernel_image_built",
				path=self.runtime_path(KERNEL_DIR, "arch/arm64/boot/Image"),
				kind=PathKind.FILE,
			),
			CorpusManifestCheck(
				name="ir_corpus_manifest",
				manifest_path=self.runtime_path(CORPUS_DIR, "MANIFEST"),
				reference_path=self.ref_path(RESULTS_REF),
				kernel_dir=KERNEL_DIR,
			),
			FileCountCheck(
				name="ir_corpus_module_files",
				directory=CORPUS_DIR,
				pattern="*.ll",
				expected=_CORPUS_MODULES,
			),
		]
		# Compiled by `scripts/run.sh table8` with the kernel's own flags.
		checks.extend(
			self.path_check(
				name=f"suite_ir_{case}",
				path=self.runtime_path(SUITE_IR_DIR, f"{case}.ll"),
				kind=PathKind.FILE,
			)
			for case in SYNC_CASES
		)
		return tuple(checks)
