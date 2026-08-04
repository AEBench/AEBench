from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import PROTOCOLS

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
	return value.replace("/", "_").replace("-", "_").replace(".", "_")


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Check that Kondo's protocol inputs and evaluation drivers are staged."""

	def requirements(self) -> Sequence[BaseCheck]:
		reqs: list[BaseCheck] = [
			self.path_check(
				name="verify_all_script",
				path=self.runtime_path("kondoPrototypes/verify-all"),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="evaluation_script",
				path=self.runtime_path("kondoPrototypes/evaluation/eval.py"),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="sloc_helper",
				path=self.runtime_path("kondoPrototypes/evaluation/file_sloc.py"),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="protocol_manifest",
				path=self.runtime_path("kondoPrototypes/evaluation/protocols.csv"),
				kind=PathKind.FILE,
			),
		]

		for protocol in PROTOCOLS:
			protocol_part = _safe_name_part(protocol)
			for filename in ("hosts.dfy", "types.dfy"):
				reqs.append(
					self.path_check(
						name=f"{protocol_part}_{_safe_name_part(filename)}",
						path=self.runtime_path("kondoPrototypes", protocol, filename),
						kind=PathKind.FILE,
					)
				)

			for variant, filenames in (
				("sync", _REQUIRED_SYNC_FILES),
				("manual", _REQUIRED_MANUAL_FILES),
				("async-kondo", _REQUIRED_ASYNC_KONDO_FILES),
			):
				variant_part = _safe_name_part(variant)
				for filename in filenames:
					reqs.append(
						self.path_check(
							name=f"{protocol_part}_{variant_part}_{_safe_name_part(filename)}",
							path=self.runtime_path("kondoPrototypes", protocol, variant, filename),
							kind=PathKind.FILE,
						)
					)

		return tuple(reqs)
