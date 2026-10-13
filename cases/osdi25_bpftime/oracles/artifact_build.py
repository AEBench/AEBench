from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.reporting import BaseCheck

_BUILD_OUTPUTS = (
	"build/runtime/syscall-server/libbpftime-syscall-server.so",
	"build/runtime/agent/libbpftime-agent.so",
	"build/attach/text_segment_transformer/libbpftime-agent-transformer.so",
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			*(
				self.path_check(
					name=f"built_{path.rsplit('/', 1)[-1]}",
					path=self.runtime_path(path),
					kind=PathKind.FILE,
				)
				for path in _BUILD_OUTPUTS
			),
			self.command_check(
				name="runtime_libraries_load",
				cmd=(
					"python3",
					"-B",
					"-c",
					"import ctypes, sys\n"
					"for path in sys.argv[1:]:\n"
					"    ctypes.CDLL(path)\n"
					"print('bpftime runtime libraries loaded')",
					*_BUILD_OUTPUTS,
				),
				cwd=self.runtime_path(),
				signature="bpftime runtime libraries loaded",
				timeout_seconds=30.0,
			),
			self.command_check(
				name="benchmark_victim_runs",
				cmd=("benchmark/test",),
				cwd=self.runtime_path(),
				signature="Benchmarking __bench_uprobe",
				timeout_seconds=120.0,
			),
		)
