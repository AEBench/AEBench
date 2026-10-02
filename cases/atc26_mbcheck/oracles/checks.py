from __future__ import annotations

import json
import math
import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_read_file_text,
	run_check_process_capture,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

# Exits 3 when the directory is missing, so an absent tree is not read as zero.
_COUNT_FILES_SCRIPT = '[ -d "$1" ] || exit 3; find "$1" -type f -name "$2" | wc -l'


def _read_runtime_text(path: OraclePath, *, label: str, executor: RuntimeCheckExecutor) -> str:
	try:
		return check_read_file_text(path, executor=executor)
	except (OSError, ValueError, RuntimeError) as exc:
		raise ValueError(f"{label}: {exc}") from exc


def _read_runtime_json(
	path: OraclePath, *, label: str, executor: RuntimeCheckExecutor
) -> dict[str, object]:
	text = _read_runtime_text(path, label=label, executor=executor)
	try:
		value = json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError(f"{label}: {path} is not valid JSON: {exc}") from exc
	if not isinstance(value, dict):
		raise ValueError(f"{label}: {path} must hold a JSON object")
	return value


def _load_reference(path: Path) -> dict[str, object]:
	try:
		value = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as exc:
		raise ValueError(f"could not load reference {path}: {exc}") from exc
	if not isinstance(value, dict):
		raise ValueError("reference root must be a JSON object")
	return value


def _ref_section(ref: Mapping[str, object], *keys: str) -> dict[str, object]:
	node: object = ref
	for key in keys:
		if not isinstance(node, dict) or key not in node:
			raise ValueError(f"reference is missing {'.'.join(keys)}")
		node = node[key]
	if not isinstance(node, dict):
		raise ValueError(f"reference {'.'.join(keys)} must be a JSON object")
	return node


def _ref_tolerance(ref: Mapping[str, object]) -> float:
	tolerance = ref.get("count_tolerance")
	if not isinstance(tolerance, (int, float)) or not 0 <= tolerance < 1:
		raise ValueError("reference count_tolerance must be a fraction in [0, 1)")
	return float(tolerance)


def _int_field(data: Mapping[str, object], key: str, *, label: str) -> int:
	value = data.get(key)
	if isinstance(value, bool) or not isinstance(value, int):
		raise ValueError(f"{label}: {key} must be an integer, got {value!r}")
	return value


def _allowed_deviation(expected: int, tolerance: float) -> int:
	# Small counts (Table 9 has modules with 1-3 violations) still get +/-1.
	return max(1, math.floor(expected * tolerance))


def _compare_counts(
	observed: Mapping[str, object],
	expected: Mapping[str, object],
	*,
	tolerance: float,
	label: str,
) -> tuple[list[str], list[str]]:
	"""Returns (mismatches, summaries) for each expected key."""
	mismatches: list[str] = []
	summaries: list[str] = []
	for key, expected_value in expected.items():
		if isinstance(expected_value, bool) or not isinstance(expected_value, int):
			raise ValueError(f"reference {label}.{key} must be an integer")
		actual = observed.get(key)
		if isinstance(actual, bool) or not isinstance(actual, int):
			mismatches.append(f"{key}: missing or non-integer ({actual!r})")
			continue
		allowed = _allowed_deviation(expected_value, tolerance)
		summaries.append(f"{key}={actual:,}")
		if abs(actual - expected_value) > allowed:
			mismatches.append(f"{key}: got {actual:,}, expected {expected_value:,} +/- {allowed:,}")
	return mismatches, summaries


def _normalize_runtime_dir(path: str) -> str:
	return posixpath.normpath(path)


def _ir_lines_problem(observed: int, corpus: Mapping[str, object]) -> str | None:
	# Clang 18 point releases emit a handful of lines more or less over the
	# whole corpus, so the README's line count only holds approximately.
	expected = corpus.get("ir_lines")
	tolerance = corpus.get("ir_lines_tolerance")
	if not isinstance(expected, int) or not isinstance(tolerance, (int, float)):
		return "reference corpus needs integer ir_lines and numeric ir_lines_tolerance"
	allowed = math.floor(expected * tolerance)
	if abs(observed - expected) > allowed:
		return f"ir_lines: got {observed:,}, expected {expected:,} +/- {allowed:,}"
	return None


def _count_files(directory: str, pattern: str, *, executor: RuntimeCheckExecutor) -> int:
	proc = run_check_process_capture(
		cmd=("sh", "-c", _COUNT_FILES_SCRIPT, "sh", directory, pattern),
		cwd=None,
		env=None,
		timeout_seconds=600.0,
		executor=executor,
	)
	if proc.timed_out:
		raise ValueError(f"counting {pattern} under {directory} timed out")
	if proc.returncode == 3:
		raise ValueError(f"directory not found: {directory}")
	if proc.returncode != 0:
		raise ValueError(
			f"counting {pattern} under {directory} failed (rc={proc.returncode}): "
			f"{proc.stderr.strip()}"
		)
	try:
		return int(proc.stdout.strip())
	except ValueError as exc:
		raise ValueError(f"unexpected file count output {proc.stdout!r}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class CorpusManifestCheck(BaseCheck):
	"""The corpus MANIFEST written by build-kernel-ir.sh matches the README."""

	manifest_path: OraclePath
	reference_path: Path
	kernel_dir: str

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			text = _read_runtime_text(self.manifest_path, label="MANIFEST", executor=executor)
			corpus = _ref_section(_load_reference(self.reference_path), "corpus")
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		fields: dict[str, str] = {}
		for line in text.splitlines():
			if line.startswith("#") or "=" not in line:
				continue
			key, value = line.split("=", 1)
			fields[key.strip()] = value.strip()

		problems: list[str] = []
		for key in ("modules", "ir_lines"):
			raw = fields.get(key)
			if raw is None or not raw.isdigit():
				problems.append(f"{key}: missing or non-integer ({raw!r})")
			elif key == "ir_lines":
				if problem := _ir_lines_problem(int(raw), corpus):
					problems.append(problem)
			elif int(raw) != corpus.get(key):
				problems.append(f"{key}: got {int(raw):,}, expected {corpus.get(key):,}")

		kernel_source = fields.get("kernel_source")
		if kernel_source is None or _normalize_runtime_dir(kernel_source) != self.kernel_dir:
			problems.append(f"kernel_source: got {kernel_source!r}, expected {self.kernel_dir!r}")
		clang = fields.get("clang", "")
		if posixpath.basename(clang) != "clang-18":
			problems.append(f"clang: got {clang!r}, expected clang-18")

		if problems:
			return CheckResult.failure("corpus MANIFEST mismatch: " + "; ".join(problems))
		return CheckResult.success(
			message=(
				f"MANIFEST reports {fields['modules']} modules and {int(fields['ir_lines']):,} "
				f"IR lines from {kernel_source} with {clang}"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class FileCountCheck(BaseCheck):
	"""Exactly ``expected`` files named ``pattern`` exist under ``directory``."""

	directory: str
	pattern: str
	expected: int

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			count = _count_files(self.directory, self.pattern, executor=executor)
		except ValueError as exc:
			return CheckResult.failure(str(exc))
		if count != self.expected:
			return CheckResult.failure(
				f"found {count} {self.pattern} files under {self.directory}, "
				f"expected {self.expected}"
			)
		return CheckResult.success(message=f"{count} {self.pattern} files under {self.directory}")


@dataclass(frozen=True, slots=True, kw_only=True)
class SweepProvenanceCheck(BaseCheck):
	"""The sweep JSON describes a full pass over the pinned v6.19 corpus."""

	sweep_path: OraclePath
	reference_path: Path
	corpus_dir: str

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sweep = _read_runtime_json(self.sweep_path, label="sweep", executor=executor)
			corpus = _ref_section(_load_reference(self.reference_path), "corpus")
			modules = _int_field(sweep, "ir_modules", label="sweep")
			lines = _int_field(sweep, "ir_lines", label="sweep")
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		testdir = sweep.get("testdir")
		problems: list[str] = []
		if not isinstance(testdir, str) or _normalize_runtime_dir(testdir) != self.corpus_dir:
			problems.append(f"testdir: got {testdir!r}, expected {self.corpus_dir!r}")
		if modules != corpus["modules"]:
			problems.append(f"ir_modules: got {modules:,}, expected {corpus['modules']:,}")
		if problem := _ir_lines_problem(lines, corpus):
			problems.append(problem)
		if problems:
			return CheckResult.failure(
				"sweep did not cover the v6.19 corpus: " + "; ".join(problems)
			)
		return CheckResult.success(
			message=f"sweep covered {modules:,} modules / {lines:,} IR lines of {testdir}"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class SweepCountsCheck(BaseCheck):
	"""Top-level sweep counts (Table 4) fall within the reference tolerance."""

	sweep_path: OraclePath
	reference_path: Path
	section: str

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sweep = _read_runtime_json(self.sweep_path, label="sweep", executor=executor)
			ref = _load_reference(self.reference_path)
			expected = _ref_section(ref, self.section)
			mismatches, summaries = _compare_counts(
				sweep, expected, tolerance=_ref_tolerance(ref), label=self.section
			)
		except ValueError as exc:
			return CheckResult.failure(str(exc))
		if mismatches:
			return CheckResult.failure(f"{self.section} mismatches: " + "; ".join(mismatches))
		return CheckResult.success(
			message=f"{self.section} within tolerance: " + ", ".join(summaries)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class RankedCountsCheck(BaseCheck):
	"""A top-N table (Tables 6 and 7): counts within tolerance, ranked as listed."""

	sweep_path: OraclePath
	reference_path: Path
	sweep_key: str
	ref_keys: Sequence[str]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		label = ".".join(self.ref_keys)
		try:
			sweep = _read_runtime_json(self.sweep_path, label="sweep", executor=executor)
			ref = _load_reference(self.reference_path)
			expected = _ref_section(ref, *self.ref_keys)
			observed = sweep.get(self.sweep_key)
			if not isinstance(observed, dict):
				raise ValueError(f"sweep {self.sweep_key} must be a JSON object")
			mismatches, summaries = _compare_counts(
				observed, expected, tolerance=_ref_tolerance(ref), label=label
			)
		except ValueError as exc:
			return CheckResult.failure(str(exc))
		if mismatches:
			return CheckResult.failure(f"{label} mismatches: " + "; ".join(mismatches))

		ranked = list(expected)
		for higher, lower in zip(ranked, ranked[1:]):
			if observed[higher] <= observed[lower]:
				return CheckResult.failure(
					f"{label} ranking broken: {higher} ({observed[higher]:,}) "
					f"does not exceed {lower} ({observed[lower]:,})"
				)
		return CheckResult.success(
			message=f"{label} ranked as in the paper: " + ", ".join(summaries)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeBreakdownCheck(BaseCheck):
	"""Table 5: every paper stage is timed and the transpiler and herd7 dominate.

	Absolute times, shares and peak RSS depend on the machine, so only the
	shape of the breakdown is compared.
	"""

	sweep_path: OraclePath
	reference_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sweep = _read_runtime_json(self.sweep_path, label="sweep", executor=executor)
			table5 = _ref_section(_load_reference(self.reference_path), "table5")
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		timing = sweep.get("timing")
		shares = timing.get("shares") if isinstance(timing, dict) else None
		if not isinstance(timing, dict) or not isinstance(shares, dict):
			return CheckResult.failure("sweep timing.shares is missing")

		stages = table5.get("stages")
		dominant = table5.get("dominant_stages")
		if not isinstance(stages, list) or not isinstance(dominant, list):
			return CheckResult.failure("reference table5 needs stages and dominant_stages lists")

		missing = [
			stage
			for stage in stages
			if not isinstance(shares.get(stage), (int, float)) or shares[stage] < 0
		]
		if missing:
			return CheckResult.failure(f"stages without a timing share: {missing}")

		total = sum(v for v in shares.values() if isinstance(v, (int, float)))
		if abs(total - 100.0) > 1.0:
			return CheckResult.failure(f"timing shares sum to {total:.2f}%, not 100%")

		by_share = sorted(stages, key=lambda stage: shares[stage], reverse=True)
		top = by_share[: len(dominant)]
		if set(top) != set(dominant):
			return CheckResult.failure(
				f"most expensive stages are {top}, expected {dominant}: "
				+ ", ".join(f"{s}={shares[s]:.2f}%" for s in by_share)
			)

		peak_rss = timing.get("peak_rss_gb")
		wall = timing.get("wall_total_s")
		if not isinstance(peak_rss, (int, float)) or peak_rss <= 0:
			return CheckResult.failure(f"peak_rss_gb not recorded: {peak_rss!r}")
		if not isinstance(wall, (int, float)) or wall <= 0:
			return CheckResult.failure(f"wall_total_s not recorded: {wall!r}")

		return CheckResult.success(
			message=(
				", ".join(f"{s}={shares[s]:.2f}%" for s in by_share)
				+ f"; wall {wall / 60:.1f} min, peak RSS {peak_rss:.2f} GB"
			)
		)


def _module_name(module: str) -> str:
	return module[:-3] if module.endswith(".ll") else module


@dataclass(frozen=True, slots=True, kw_only=True)
class ViolationsByModuleCheck(BaseCheck):
	"""Table 9: herd7 violations land in the README's modules in its proportions."""

	sweep_path: OraclePath
	reference_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sweep = _read_runtime_json(self.sweep_path, label="sweep", executor=executor)
			ref = _load_reference(self.reference_path)
			expected = _ref_section(ref, "table9")
			tolerance = _ref_tolerance(ref)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		violations = sweep.get("violations")
		if not isinstance(violations, list):
			return CheckResult.failure("sweep violations must be a list")

		observed: dict[str, int] = {}
		for entry in violations:
			if not isinstance(entry, dict) or not isinstance(entry.get("module"), str):
				return CheckResult.failure(f"malformed violation entry: {entry!r}")
			module = _module_name(entry["module"])
			observed[module] = observed.get(module, 0) + 1

		if set(observed) != set(expected):
			missing = sorted(set(expected) - set(observed))
			extra = sorted(set(observed) - set(expected))
			return CheckResult.failure(
				f"violating modules differ from the README: missing={missing}, unexpected={extra}"
			)

		try:
			mismatches, summaries = _compare_counts(
				observed, expected, tolerance=tolerance, label="table9"
			)
			total_expected = sum(int(v) for v in expected.values())
		except (ValueError, TypeError) as exc:
			return CheckResult.failure(str(exc))
		total = sum(observed.values())
		allowed = _allowed_deviation(total_expected, tolerance)
		if abs(total - total_expected) > allowed:
			mismatches.append(f"total: got {total}, expected {total_expected} +/- {allowed}")
		if mismatches:
			return CheckResult.failure("table9 mismatches: " + "; ".join(mismatches))
		return CheckResult.success(
			message=f"{total} violations across {len(observed)} modules: " + ", ".join(summaries)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class LitmusOnDiskCheck(BaseCheck):
	"""The sweep's litmus tests, and every violating one, exist on disk."""

	sweep_path: OraclePath
	litmus_dir: str

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sweep = _read_runtime_json(self.sweep_path, label="sweep", executor=executor)
			reported = _int_field(sweep, "litmus", label="sweep")
			on_disk = _count_files(self.litmus_dir, "*.litmus", executor=executor)
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		# A re-run with --force reuses the directory, so leftovers may add to the
		# count; every test the sweep counted must still be there.
		if on_disk < reported:
			return CheckResult.failure(
				f"sweep reports {reported:,} litmus tests but only {on_disk:,} are in {self.litmus_dir}"
			)

		violations = sweep.get("violations")
		if not isinstance(violations, list):
			return CheckResult.failure("sweep violations must be a list")
		missing: list[str] = []
		for entry in violations:
			if not isinstance(entry, dict):
				return CheckResult.failure(f"malformed violation entry: {entry!r}")
			module, litmus = entry.get("module"), entry.get("litmus")
			if not isinstance(module, str) or not isinstance(litmus, str):
				return CheckResult.failure(f"malformed violation entry: {entry!r}")
			# run_kernel_noLTO.py names each module's directory after its
			# corpus path with '/' replaced by '__'.
			subdir = _module_name(module).replace("/", "__")
			path = RuntimePath.from_parts(self.litmus_dir, subdir, litmus)
			if not executor.path_is_file(path):
				missing.append(str(path))
		if missing:
			return CheckResult.failure(
				f"{len(missing)} violating litmus files missing, e.g. {missing[:3]}"
			)
		return CheckResult.success(
			message=(
				f"{on_disk:,} litmus files on disk for {reported:,} reported; "
				f"all {len(violations)} violating tests present"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class MicrobenchmarkCheck(BaseCheck):
	"""Table 8: all 23 correctly synchronised cases are true negatives."""

	table8_path: OraclePath
	reference_path: Path
	cases: Sequence[str]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			data = _read_runtime_json(self.table8_path, label="table8", executor=executor)
			expected = _ref_section(_load_reference(self.reference_path), "table8")
			for key in ("total", "tn", "fp"):
				observed_value = _int_field(data, key, label="table8")
				if observed_value != expected[key]:
					raise ValueError(f"{key}: got {observed_value}, expected {expected[key]}")
		except ValueError as exc:
			return CheckResult.failure(str(exc))

		cases = data.get("cases")
		if not isinstance(cases, list):
			return CheckResult.failure("table8 cases must be a list")
		by_name: dict[str, dict[str, object]] = {}
		for case in cases:
			if not isinstance(case, dict) or not isinstance(case.get("case"), str):
				return CheckResult.failure(f"malformed table8 case: {case!r}")
			by_name[case["case"]] = case

		if set(by_name) != set(self.cases):
			missing = sorted(set(self.cases) - set(by_name))
			extra = sorted(set(by_name) - set(self.cases))
			return CheckResult.failure(f"case set mismatch: missing={missing}, unexpected={extra}")

		bad = [
			f"{name}: verdict={case.get('verdict')!r} no={case.get('no')!r} "
			f"err={case.get('err')!r} rc={case.get('tool_rc')!r}"
			for name, case in sorted(by_name.items())
			if case.get("verdict") != "TN"
			or case.get("no") != 0
			or case.get("err") != 0
			or case.get("tool_rc") != 0
		]
		if bad:
			return CheckResult.failure("cases not cleanly TN: " + "; ".join(bad))

		litmus = sum(int(case.get("litmus") or 0) for case in by_name.values())
		return CheckResult.success(
			message=f"{len(by_name)} TN, 0 FP; {litmus} litmus tests, all answered Ok by herd7"
		)
