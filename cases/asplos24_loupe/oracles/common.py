from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult


def _sha256(executor: RuntimeCheckExecutor, path: Path) -> str:
	result = executor.run_process_capture(
		cmd=(
			"python3",
			"-c",
			"import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
			str(executor.resolve_path(path)),
		),
		cwd=None,
		env=None,
		timeout_seconds=30.0,
	)
	if result.returncode != 0:
		raise ValueError(result.stderr.strip() or f"cannot hash {path}")
	return result.stdout.strip()


def _load_reference(path: Path) -> dict[str, Any]:
	data = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise ValueError("Loupe reference must be a JSON object")
	return data


def _git_head(executor: RuntimeCheckExecutor, path: Path) -> str:
	result = executor.run_process_capture(
		cmd=("git", "rev-parse", "HEAD"),
		cwd=path,
		env=None,
		timeout_seconds=30.0,
	)
	if result.returncode != 0:
		raise ValueError(result.stderr.strip() or f"cannot read Git HEAD for {path}")
	return result.stdout.strip()


def _result_dir(workspace: Path, dockerfile_md5: str) -> Path:
	return workspace / "loupedb" / "aebench-nginx" / "benchmark-wrk" / dockerfile_md5


def _read_syscall_table(
	executor: RuntimeCheckExecutor, path: Path, expected_columns: tuple[str, ...]
) -> dict[int, tuple[str, ...]]:
	with io.StringIO(executor.read_file_text(path)) as stream:
		reader = csv.reader(stream)
		try:
			header = tuple(column.strip() for column in next(reader))
		except StopIteration as exc:
			raise ValueError(f"{path} is empty") from exc
		if header != expected_columns:
			raise ValueError(f"{path} header is {header!r}, expected {expected_columns!r}")

		rows: dict[int, tuple[str, ...]] = {}
		for line_number, raw in enumerate(reader, start=2):
			row = tuple(value.strip() for value in raw)
			if len(row) != len(expected_columns):
				raise ValueError(
					f"{path}:{line_number} has {len(row)} columns, expected {len(expected_columns)}"
				)
			try:
				syscall = int(row[0])
			except ValueError as exc:
				raise ValueError(f"{path}:{line_number} has invalid syscall {row[0]!r}") from exc
			if syscall in rows:
				raise ValueError(f"{path}:{line_number} duplicates syscall {syscall}")
			if any(value not in {"Y", "N"} for value in row[1:]):
				raise ValueError(f"{path}:{line_number} contains a flag other than Y/N")
			rows[syscall] = row[1:]
	return rows


def _search_syscalls(text: str) -> tuple[set[int], set[int]]:
	groups: list[set[int]] = []
	for label in ("Required", "Can be stubbed", "Can be faked", "Can be both stubbed or faked"):
		match = re.search(rf"(?m)^{re.escape(label)}:\s*\n(\[[^\n]*\])", text)
		if match is None:
			raise ValueError(f"search log is missing {label} values")
		values = json.loads(match.group(1))
		if not isinstance(values, list) or any(type(value) is not int for value in values):
			raise ValueError(f"search log has invalid {label} values")
		group = set(values)
		if len(group) != len(values):
			raise ValueError(f"search log duplicates {label} values")
		groups.append(group)
	if sum(map(len, groups)) != len(set.union(*groups)):
		raise ValueError("search log places a syscall in multiple categories")
	return groups[0], set.union(*groups[1:])


@dataclass(frozen=True, slots=True, kw_only=True)
class PinnedInputsCheck(BaseCheck):
	workspace: Path
	reference: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			ref = _load_reference(self.reference)
			if _git_head(executor, self.workspace) != ref["loupe_commit"]:
				return CheckResult.failure("Loupe checkout is not at the pinned artifact commit")
			for relative, expected in ref["input_sha256"].items():
				path = self.workspace / relative
				if not executor.path_is_file(path):
					return CheckResult.failure(f"missing pinned input: {relative}")
				if _sha256(executor, path) != expected:
					return CheckResult.failure(f"pinned input was modified: {relative}")
		except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
			return CheckResult.failure(f"failed to verify pinned inputs: {exc}")
		return CheckResult.success("Loupe commit and four experiment inputs match the reference")


@dataclass(frozen=True, slots=True, kw_only=True)
class PinnedDatabaseCheck(BaseCheck):
	workspace: Path
	reference: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			ref = _load_reference(self.reference)
			actual = _git_head(executor, self.workspace / "loupedb")
		except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
			return CheckResult.failure(f"failed to verify loupedb checkout: {exc}")
		if actual != ref["loupedb_commit"]:
			return CheckResult.failure(
				f"loupedb HEAD is {actual}, expected {ref['loupedb_commit']}"
			)
		return CheckResult.success(f"loupedb is pinned at {actual}")


@dataclass(frozen=True, slots=True, kw_only=True)
class LoupeTablesCheck(BaseCheck):
	workspace: Path
	reference: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			ref = _load_reference(self.reference)
			result = _result_dir(self.workspace, str(ref["dockerfile_md5"]))
			dynamic = _read_syscall_table(
				executor,
				result / "data" / "dyn.csv",
				("# syscall", "used", "works faked", "works stubbed", "works both"),
			)
			static = _read_syscall_table(
				executor,
				result / "data" / "static_binary.csv",
				("# syscall", "used"),
			)
			expected_syscalls = set(range(int(ref["syscall_min"]), int(ref["syscall_max"]) + 1))
			if set(dynamic) != expected_syscalls or set(static) != expected_syscalls:
				return CheckResult.failure("dynamic/static tables do not cover every syscall 0-334")

			used = {number for number, flags in dynamic.items() if flags[0] == "Y"}
			replaceable = {number for number, flags in dynamic.items() if "Y" in flags[1:]}
			if any(sum(flag == "Y" for flag in flags[1:]) > 1 for flags in dynamic.values()):
				return CheckResult.failure(
					"dynamic table has overlapping fake/stub/both categories"
				)
			if any(number not in used for number in replaceable):
				return CheckResult.failure(
					"dynamic table classifies an unused syscall as replaceable"
				)
			if len(used) < int(ref["min_used"]):
				return CheckResult.failure(f"only {len(used)} syscalls were observed as used")
			if len(replaceable) < int(ref["min_replaceable"]):
				return CheckResult.failure("no used syscall tolerated stubbing or faking")
			if not used - replaceable:
				return CheckResult.failure("no used syscall required an implementation")
			missing_essential = set(ref["essential_used"]) - used
			if missing_essential:
				return CheckResult.failure(
					f"essential Nginx process syscalls not observed: {sorted(missing_essential)}"
				)
			static_used = sum(flags[0] == "Y" for flags in static.values())
			if static_used < len(used):
				return CheckResult.failure(
					f"static table reports {static_used} used calls, fewer than dynamic {len(used)}"
				)

			copied_inputs = {
				result / "Dockerfile.aebench-nginx": ref["input_sha256"][
					"examples/E1/Dockerfile.nginx"
				],
				result / "dockerfile_data" / "nginx-test.sh": ref["input_sha256"][
					"examples/E1/dockerfile_data/nginx-test.sh"
				],
			}
			for path, expected in copied_inputs.items():
				if not executor.path_is_file(path) or _sha256(executor, path) != expected:
					return CheckResult.failure(
						f"generated result has wrong copied input: {path.name}"
					)
		except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
			return CheckResult.failure(f"failed to validate Loupe result tables: {exc}")

		return CheckResult.success(
			f"complete syscall tables: {len(used)} dynamic used, "
			f"{len(replaceable)} replaceable, {static_used} static used"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class LoupeRunEvidenceCheck(BaseCheck):
	workspace: Path
	reference: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			ref = _load_reference(self.reference)
			result = _result_dir(self.workspace, str(ref["dockerfile_md5"]))
			paths = {
				"command": result / "cmd.txt",
				"replica logs": result / "explore.logs",
				"experiment log": self.workspace / "aebench-results" / "experiment.log",
				"search log": self.workspace / "aebench-results" / "search.log",
			}
			contents: dict[str, str] = {}
			for label, path in paths.items():
				if not executor.path_is_file(path):
					return CheckResult.failure(f"missing or empty {label}: {path}")
				contents[label] = executor.read_file_text(path)
				if not contents[label]:
					return CheckResult.failure(f"missing or empty {label}: {path}")

			command = contents["command"]
			for token in ("generate", "-a aebench-nginx", "-w wrk", "Dockerfile.nginx"):
				if token not in command:
					return CheckResult.failure(f"cmd.txt is missing {token!r}")
			replica_logs = contents["replica logs"]
			if "[E] " in replica_logs:
				return CheckResult.failure("replica logs contain a Loupe error")
			for replica in ("replica #0", "replica #1"):
				if replica not in replica_logs:
					return CheckResult.failure(f"replica logs are missing {replica}")
			if "Done! Full analysis last" not in contents["experiment log"]:
				return CheckResult.failure(
					"experiment log lacks Loupe's successful completion marker"
				)
			dynamic = _read_syscall_table(
				executor,
				result / "data" / "dyn.csv",
				("# syscall", "used", "works faked", "works stubbed", "works both"),
			)
			used = {number for number, flags in dynamic.items() if flags[0] == "Y"}
			replaceable = {number for number, flags in dynamic.items() if "Y" in flags[1:]}
			required_in_search, replaceable_in_search = _search_syscalls(contents["search log"])
			if required_in_search != used - replaceable or replaceable_in_search != replaceable:
				return CheckResult.failure("search log syscall values disagree with dyn.csv")
		except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
			return CheckResult.failure(f"failed to validate Loupe execution evidence: {exc}")
		return CheckResult.success(
			"command, two replica logs, completion log, and query output are complete"
		)
