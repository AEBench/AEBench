from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import PROTOCOLS

_PROTOCOLS_CSV = "basilisk/evaluation/protocols.csv"
_EVAL_SCRIPT = "basilisk/evaluation/eval.py"
_VERIFY_ALL_SCRIPT = "basilisk/verify_all"


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Check that all 16 protocol inputs and evaluation drivers are staged."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.path_check(
				name="protocol_manifest",
				path=self.runtime_path(_PROTOCOLS_CSV),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="evaluation_script",
				path=self.runtime_path(_EVAL_SCRIPT),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="upstream_verify_all_script",
				path=self.runtime_path(_VERIFY_ALL_SCRIPT),
				kind=PathKind.FILE,
			),
		]

		for protocol in PROTOCOLS:
			safe_name = protocol.replace("-", "_")
			for label, relative_path in (
				("hosts", f"basilisk/{protocol}/hosts.dfy"),
				("distributed_system", f"basilisk/{protocol}/automate_gen2/distributedSystem.dfy"),
				(
					"application_proof",
					f"basilisk/{protocol}/automate_gen2/applicationProofDemo.dfy",
				),
				("verify_driver", f"basilisk/{protocol}/automate_gen2/verify"),
			):
				checks.append(
					self.path_check(
						name=f"{safe_name}_{label}",
						path=self.runtime_path(relative_path),
						kind=PathKind.FILE,
					)
				)

		return tuple(checks)
