from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
	glob,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

# LaTeX commands that wrap a table value, by argument count. The value is the
# last argument, e.g. \colorbox{func}{\makebox[{0.55cm}][r]{9.2k}} -> 9.2k and
# \color{lightgray}{-} -> -. \phantom draws the empty bar of a mini bar chart.
_WRAPPERS = {
	"color": 2,
	"colorbox": 2,
	"footnotesize": 1,
	"makebox": 1,
	"multicolumn": 3,
	"multirow": 3,
	"rotatebox": 2,
	"shortstack": 1,
	"textbf": 1,
	"textcolor": 2,
	"texttt": 1,
}
_DROPPED = {"phantom": 1}

# save_latex escapes these after pandas renders the table.
_ESCAPED = {"#": "#", "%": "%", "&": "&", "_": "_", " ": " ", "\\": " "}

_SUMMARY_LINE = re.compile(r"^(.+?) (\S+): (\d+)$")


def _group(text: str, start: int, open_char: str, close_char: str) -> tuple[str, int]:
	"""Returns the contents of the balanced group at text[start] and the index after it."""
	depth = 0
	for index in range(start, len(text)):
		if text[index] == open_char:
			depth += 1
		elif text[index] == close_char:
			depth -= 1
			if depth == 0:
				return text[start + 1 : index], index + 1
	raise ValueError(f"unbalanced {open_char}{close_char} in {text!r}")


def _skip_spaces(text: str, index: int) -> int:
	while index < len(text) and text[index] == " ":
		index += 1
	return index


def normalize_cell(text: str) -> str:
	"""Reduces a LaTeX table cell to the value it displays."""
	out: list[str] = []
	index = 0
	while index < len(text):
		char = text[index]
		if char in "{}":
			index += 1
			continue
		if char != "\\":
			out.append(char)
			index += 1
			continue

		match = re.match(r"[A-Za-z]+", text[index + 1 :])
		if match is None:
			escaped = text[index + 1 : index + 2]
			out.append(_ESCAPED.get(escaped, "\\" + escaped))
			index += 2
			continue

		name = match.group()
		index += 1 + len(name)
		arity = _WRAPPERS.get(name, _DROPPED.get(name))
		if arity is None:
			# Math symbols and macros such as \Delta or \bccissue are kept verbatim.
			out.append("\\" + name)
			continue

		args: list[str] = []
		while len(args) < arity:
			index = _skip_spaces(text, index)
			if index < len(text) and text[index] == "[":
				_, index = _group(text, index, "[", "]")
			elif index < len(text) and text[index] == "{":
				arg, index = _group(text, index, "{", "}")
				args.append(arg)
			else:
				break
		if name in _WRAPPERS and args:
			out.append(normalize_cell(args[-1]))

	return " ".join("".join(out).split())


def parse_latex_rows(text: str) -> list[list[str]]:
	"""Returns the normalized body rows of a pandas-rendered LaTeX tabular.

	The body starts after the first \\midrule. An empty first cell continues the
	\\multirow label above it, so it is filled from the previous row.
	"""
	lines = text.splitlines()
	try:
		start = next(i for i, line in enumerate(lines) if line.strip() == r"\midrule")
	except StopIteration as exc:
		raise ValueError("no \\midrule found") from exc

	rows: list[list[str]] = []
	for line in lines[start + 1 :]:
		line = line.strip()
		if line in (r"\midrule", r"\hline") or line.startswith(r"\cline"):
			continue
		if line == r"\bottomrule":
			break
		if not line.endswith(r"\\"):
			raise ValueError(f"unterminated table row: {line!r}")
		cells = [normalize_cell(cell) for cell in line[: -len(r"\\")].split("&")]
		if rows and cells and cells[0] == "":
			cells[0] = rows[-1][0]
		rows.append(cells)
	return rows


def _row_key(row: Sequence[str], key_columns: int) -> tuple[str, ...]:
	return tuple(row[:key_columns])


def _describe(key: tuple[str, ...]) -> str:
	return " / ".join(part for part in key if part) or "(blank)"


def _diff_rows(
	observed: Sequence[Sequence[str]],
	expected: Sequence[Sequence[str]],
	key_columns: int,
) -> list[str]:
	"""Lists the differences between two tables, keyed by their label columns."""
	expected_by_key = {_row_key(row, key_columns): row for row in expected}
	observed_by_key = {_row_key(row, key_columns): row for row in observed}

	errors = [
		f"missing row {_describe(key)}" for key in expected_by_key if key not in observed_by_key
	]
	errors += [
		f"unexpected row {_describe(key)}" for key in observed_by_key if key not in expected_by_key
	]
	for key, want in expected_by_key.items():
		got = observed_by_key.get(key)
		if got is None:
			continue
		if len(got) != len(want):
			errors.append(f"row {_describe(key)}: {len(got)} columns, expected {len(want)}")
			continue
		for column, (got_cell, want_cell) in enumerate(zip(got, want)):
			if got_cell != want_cell:
				errors.append(
					f"row {_describe(key)}, column {column + 1}: got {got_cell!r}, "
					f"expected {want_cell!r}"
				)
	if not errors and [_row_key(row, key_columns) for row in observed] != list(expected_by_key):
		errors.append("rows are not in the reference order")
	return errors


def _compare_cells(
	observed: Sequence[Sequence[str]],
	expected: Sequence[Sequence[str]],
	key_columns: int,
) -> tuple[int, int, list[str]]:
	"""Counts the reference value cells the observed table reproduces.

	Rows are matched by their label columns, so a missing row counts all of its
	cells as wrong. Rows the reference lacks are reported but not counted.
	"""
	observed_by_key = {_row_key(row, key_columns): row for row in observed}
	expected_keys = {_row_key(row, key_columns) for row in expected}
	matched = total = 0
	errors: list[str] = []
	for want in expected:
		key = _row_key(want, key_columns)
		values = want[key_columns:]
		total += len(values)
		got = observed_by_key.get(key)
		if got is None:
			errors.append(f"missing row {_describe(key)}")
			continue
		got_values = list(got[key_columns:])
		for column, want_cell in enumerate(values):
			got_cell = got_values[column] if column < len(got_values) else None
			if got_cell == want_cell:
				matched += 1
			else:
				errors.append(
					f"row {_describe(key)}, column {key_columns + column + 1}: got {got_cell!r}, "
					f"expected {want_cell!r}"
				)
	errors += [
		f"unexpected row {_describe(key)}" for key in observed_by_key if key not in expected_keys
	]
	return matched, total, errors


def _read_table(path: OraclePath, executor: RuntimeCheckExecutor) -> list[list[str]]:
	return parse_latex_rows(check_read_file_text(path, encoding="utf-8", executor=executor))


def _first_lines(errors: Sequence[str], limit: int = 10) -> str:
	shown = "; ".join(errors[:limit])
	if len(errors) > limit:
		shown += f"; ... and {len(errors) - limit} more"
	return shown


@dataclass(frozen=True, slots=True, kw_only=True)
class LatexTableCheck(BaseCheck):
	"""Pass iff enough cells of a generated LaTeX table match the reference.

	Cells are compared as the rounded strings the notebooks print (e.g. 9.2k),
	after their LaTeX formatting is removed. Counts can shift with the toolchain
	that builds the dataset or the eBPF programs (e.g. a clean build of the
	pinned BCC gives readahead no struct dependencies), so a fraction of the
	value cells may differ.
	"""

	path: OraclePath
	rows: Sequence[Sequence[str]]
	key_columns: int
	min_match_fraction: float

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		filename = PurePosixPath(str(self.path)).name
		try:
			observed = _read_table(self.path, executor)
		except (OSError, UnicodeError, ValueError) as exc:
			return CheckResult.failure(f"failed to parse {self.path}: {exc}")

		matched, total, errors = _compare_cells(observed, self.rows, self.key_columns)
		fraction = matched / total if total else 0.0
		summary = (
			f"{filename}: {matched}/{total} cells match ({fraction:.1%}, "
			f"threshold {self.min_match_fraction:.0%})"
		)
		if errors:
			summary += f"; differences: {_first_lines(errors)}"
		if fraction < self.min_match_fraction:
			return CheckResult.failure(summary)
		return CheckResult.success(summary)


def parse_summary(text: str) -> dict[str, int]:
	"""Parses a DepSurf Summary.txt into {"<Kind> <Issue>": count}.

	DepSurf omits zero counts, so a missing key means zero.
	"""
	counts: dict[str, int] = {}
	for line in text.splitlines():
		match = _SUMMARY_LINE.match(line.strip())
		if match is not None:
			counts[f"{match.group(1)} {match.group(2)}"] = int(match.group(3))
	return counts


def _read_summary(path: OraclePath, executor: RuntimeCheckExecutor) -> dict[str, int]:
	counts = parse_summary(check_read_file_text(path, encoding="utf-8", executor=executor))
	if not counts:
		raise ValueError(f"no counts in {path}")
	return counts


# Formatting copied from the notebooks that render the tables (format_val and
# format_percent in 35_src and 39_config), so recomputed cells match exactly.
def format_config_count(count: int) -> str:
	if count > 1000:
		return f"{count / 1000:.1f}k"
	if count == 0:
		return "-"
	return str(count)


def format_src_count(kind: str, count: int) -> str:
	if kind == "Function":
		return f"{count / 1000:.0f}k"
	if kind == "Struct":
		return f"{count / 1000:.1f}k"
	return str(count)


def format_src_percent(count: int, old: int) -> str:
	if count == 0:
		return "-"
	pct = count / old * 100
	if pct < 0.5:
		return f"{pct:.1f}"
	return f"{pct:.0f}"


_CONFIG_KINDS = {
	"Config": "Config",
	"Func": "Function",
	"Struct": "Struct",
	"Tracept": "Tracepoint",
	"Native Syscall": "Syscall",
}
_CONFIG_ISSUES = {"#": "New", "$+$": "Added", "$-$": "Removed", r"$\Delta$": "Changed"}
_SRC_KINDS = ("Function", "Struct", "Tracepoint")
_SRC_ISSUES = ("Added", "Removed", "Changed")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigTableProvenanceCheck(BaseCheck):
	"""Pass iff Table 5 (cfg.tex) agrees with the per-pair diff summaries.

	Each count is recomputed from output/config/<group>/<pair>/Summary.txt with
	the notebook's own rounding, so the table must come from this run's diffs.
	"""

	path: OraclePath
	summaries: Sequence[OraclePath]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			rows = _read_table(self.path, executor)
			columns = [_read_summary(path, executor) for path in self.summaries]
		except (OSError, UnicodeError, ValueError) as exc:
			return CheckResult.failure(f"failed to read Table 5 inputs: {exc}")

		errors: list[str] = []
		compared = 0
		for row in rows:
			kind = _CONFIG_KINDS.get(row[0])
			issue = _CONFIG_ISSUES.get(row[1])
			if kind is None or issue is None:
				continue  # The Register row is a fixed label, not a count.
			cells = row[2:]
			if len(cells) != len(columns) + 1:
				errors.append(f"row {row[0]} / {row[1]}: {len(cells)} value columns")
				continue
			# The default column shows the base kernel, i.e. the "Old" side of every pair.
			default = (
				format_config_count(columns[0].get(f"{kind} Old", 0)) if issue == "New" else "-"
			)
			expected = [default] + [
				format_config_count(counts.get(f"{kind} {issue}", 0)) for counts in columns
			]
			for column, (got, want) in enumerate(zip(cells, expected)):
				compared += 1
				if got != want:
					errors.append(
						f"row {row[0]} / {row[1]}, column {column + 3}: {got!r} != {want!r}"
					)

		if errors:
			return CheckResult.failure(
				f"cfg.tex disagrees with Summary.txt: {_first_lines(errors)}"
			)
		if compared == 0:
			return CheckResult.failure("cfg.tex has no count rows")
		return CheckResult.success(f"{compared} cfg.tex cells match the config diff summaries")


@dataclass(frozen=True, slots=True, kw_only=True)
class SrcTableProvenanceCheck(BaseCheck):
	"""Pass iff Table 3 (src.tex) agrees with the per-pair diff summaries.

	groups maps each row group label to its version pairs, as
	(version-pair directory, Summary.txt path), in table order.
	"""

	path: OraclePath
	groups: Mapping[str, Sequence[tuple[str, OraclePath]]]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			observed = _read_table(self.path, executor)
			expected: list[list[str]] = []
			for group, pairs in self.groups.items():
				for index, (pair, path) in enumerate(pairs):
					counts = _read_summary(path, executor)
					old, new = pair.split("_")
					row = [group, old]
					for kind in _SRC_KINDS:
						base = counts.get(f"{kind} Old", 0)
						row.append(format_src_count(kind, base))
						row += [
							format_src_percent(counts.get(f"{kind} {issue}", 0), base)
							for issue in _SRC_ISSUES
						]
					expected.append(row)
					if index == len(pairs) - 1:
						# The newest version gets a row of its own with only its size.
						last = [group, new]
						for kind in _SRC_KINDS:
							last += [
								format_src_count(kind, counts.get(f"{kind} New", 0)),
								"",
								"",
								"",
							]
						expected.append(last)
		except (OSError, UnicodeError, ValueError) as exc:
			return CheckResult.failure(f"failed to read Table 3 inputs: {exc}")

		errors = _diff_rows(observed, expected, key_columns=2)
		if errors:
			return CheckResult.failure(
				f"src.tex disagrees with Summary.txt: {_first_lines(errors)}"
			)
		return CheckResult.success(
			f"all {len(expected)} src.tex rows match the source diff summaries"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramReportsCheck(BaseCheck):
	"""Pass iff output/report has one report per analyzed program and Table 7 lists only those."""

	report_dir: OraclePath
	programs: Sequence[str]
	table_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			found = {path.stem for path in glob(self.report_dir, "*.log", executor=executor)}
			rows = _read_table(self.table_path, executor)
		except (OSError, UnicodeError, ValueError) as exc:
			return CheckResult.failure(f"failed to read program reports: {exc}")

		errors: list[str] = []
		missing = sorted(set(self.programs) - found)
		if missing:
			errors.append(f"missing reports: {', '.join(missing)}")
		unreported = sorted({row[0].lower() for row in rows} - found)
		if unreported:
			errors.append(f"bcc.tex rows without a report: {', '.join(unreported)}")

		if errors:
			return CheckResult.failure("; ".join(errors))
		return CheckResult.success(
			f"{len(self.programs)} program reports present, covering all {len(rows)} bcc.tex rows"
		)
