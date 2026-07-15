from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.oracle_checks_runtime import (
	RuntimeCheckExecutor,
	check_path_is_file,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import PROTOCOLS

_VERIFY_LOG = "verify-all.log"
_COMMON_AUTOGEN_FILES = (
	"applicationProofDraftAutogen.dfy",
	"messageInvariantsAutogen.dfy",
	"monotonicityInvariantsAutogen.dfy",
)
_OWNERSHIP_PROTOCOLS = frozenset({"distributedLock", "shardedKv", "shardedKvBatched", "lockServer"})
_SUMMARY_RE = re.compile(
	r"Dafny program verifier finished with\s+(\d+)\s+verified,\s+(\d+)\s+errors?"
	r"(?:,\s+(\d+)\s+time outs?)?"
)

_VERIFY_SECTIONS = (
	"Verifying Client-Server (manual)",
	"Verifying Client-Server (sync)",
	"Verifying Client-Server (kondo)",
	"Verifying Ring Leader Election (manual)",
	"Verifying Ring Leader Election (sync)",
	"Verifying Ring Leader Election (kondo)",
	"Verifying Simplified Leader Election (manual)",
	"Verifying Simplified Leader Election (sync)",
	"Verifying Simplified Leader Election (kondo)",
	"Verifying Two-Phase Commit (manual)",
	"Verifying Two-Phase Commit (sync)",
	"Verifying Two-Phase Commit (kondo)",
	"Verifying Paxos (manual)",
	"Verifying Paxos (sync)",
	"Verifying Paxos (kondo)",
	"Verifying Flexible Paxos (sync)",
	"Verifying Flexible Paxos (kondo)",
	"Verifying DistributedLock (manual)",
	"Verifying DistributedLock (sync)",
	"Verifying DistributedLock (kondo)",
	"Verifying ShardedKV (manual)",
	"Verifying ShardedKV (sync)",
	"Verifying ShardedKV (kondo)",
	"Verifying ShardedKV-Batched (manual)",
	"Verifying ShardedKV-Batched (sync)",
	"Verifying ShardedKV-Batched (kondo)",
	"Verifying Lock Server (manual)",
	"Verifying Lock Server (sync)",
	"Verifying Lock Server (kondo)",
)

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


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifyAllLogCheck(BaseCheck):
	path: Path
	executor: RuntimeCheckExecutor | None = dataclasses.field(
		default=None, repr=False, compare=False
	)

	def check(self) -> CheckResult:
		if not check_path_is_file(self.path, executor=self.executor):
			return CheckResult.failure(f"verify-all log not found: {self.path}")

		try:
			text = check_read_file_text(self.path, executor=self.executor)
		except (OSError, RuntimeError) as exc:
			return CheckResult.failure(f"failed to read verify-all log: {exc}")

		lines = text.splitlines()
		positions: list[int] = []
		for section in _VERIFY_SECTIONS:
			matches = [index for index, line in enumerate(lines) if line.strip() == section]
			if len(matches) != 1:
				return CheckResult.failure(
					f"expected one {section!r} section in verify-all log, found {len(matches)}"
				)
			positions.append(matches[0])

		if positions != sorted(positions):
			return CheckResult.failure("verify-all sections are not in the expected order")

		summary_count = 0
		for index, section in enumerate(_VERIFY_SECTIONS):
			start = positions[index] + 1
			end = positions[index + 1] if index + 1 < len(positions) else len(lines)
			section_text = "\n".join(lines[start:end])
			summaries = _SUMMARY_RE.findall(section_text)
			if not summaries:
				return CheckResult.failure(f"no Dafny verification summary found for {section}")
			if "timed out" in section_text.lower():
				return CheckResult.failure(f"Dafny verification timed out for {section}")

			failed = [
				(verified, errors, timeouts or "0")
				for verified, errors, timeouts in summaries
				if int(errors) != 0 or int(timeouts or 0) != 0
			]
			if failed:
				return CheckResult.failure(
					f"Dafny verification reported errors or timeouts for {section}: {failed}"
				)
			if sum(int(verified) for verified, _errors, _timeouts in summaries) == 0:
				return CheckResult.failure(f"Dafny verified no obligations for {section}")
			summary_count += len(summaries)

		return CheckResult.success(
			f"all {len(_VERIFY_SECTIONS)} verify-all sections passed "
			f"({summary_count} Dafny summaries, 0 errors, no timeouts)"
		)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		protos_dir = self.workspace_path("kondoPrototypes")

		reqs: list[BaseCheck] = [
			self.path_check(
				name="verify_all_script",
				path=protos_dir / "verify-all",
				kind=PathKind.FILE,
			),
			VerifyAllLogCheck(
				name="verify_all_succeeded",
				path=protos_dir / _VERIFY_LOG,
				executor=self.executor,
			),
		]

		for protocol in PROTOCOLS:
			protocol_part = _safe_name_part(protocol)

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
							path=protos_dir / protocol / variant / filename,
							kind=PathKind.FILE,
						)
					)

			autogen_files = list(_COMMON_AUTOGEN_FILES)
			if protocol in _OWNERSHIP_PROTOCOLS:
				autogen_files.append("ownershipInvariantsAutogen.dfy")

			for filename in autogen_files:
				reqs.append(
					self.path_check(
						name=f"{protocol_part}_{_safe_name_part(filename)}",
						path=protos_dir / protocol / "async-kondo" / filename,
						kind=PathKind.FILE,
					)
				)

		return tuple(reqs)
