from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

TableData = dict[str, Any]

_TABLE5_HEADERS = (
	"Operator",
	"Undesired State",
	"System Error",
	"Operator Error",
	"Recovery Failure",
	"Total",
)
_TABLE6_HEADERS = ("Consequence", "# Bugs")
_TABLE7_HEADERS = ("Test Oracle", "# Bugs (Percentage)")
_TABLE8_HEADERS = ("Operator", "# Operations")


def _parse_rows(text: str, *, headers: Sequence[str]) -> list[tuple[str, ...]]:
	lines = [line.rstrip() for line in text.splitlines() if line.strip()]
	if len(lines) < 3:
		raise ValueError("table must contain a header, separator, and at least one row")

	observed_headers = tuple(re.split(r"\s{2,}", lines[0].strip()))
	if observed_headers != tuple(headers):
		raise ValueError(f"unexpected table headers: {observed_headers!r}")
	if re.fullmatch(r"-+(?:\s{2,}-+)+", lines[1].strip()) is None:
		raise ValueError("table header separator is missing or malformed")

	rows: list[tuple[str, ...]] = []
	for line_number, line in enumerate(lines[2:], start=3):
		row = tuple(re.split(r"\s{2,}", line.strip()))
		if len(row) != len(headers):
			raise ValueError(f"row {line_number} has {len(row)} columns; expected {len(headers)}")
		rows.append(row)

	return rows


def _parse_nonnegative_int(value: str, *, label: str) -> int:
	try:
		parsed = int(value)
	except ValueError as exc:
		raise ValueError(f"{label} must be an integer, got {value!r}") from exc
	if parsed < 0:
		raise ValueError(f"{label} must be nonnegative, got {parsed}")
	return parsed


def _require_unique(name: str, seen: set[str], *, label: str) -> None:
	if name in seen:
		raise ValueError(f"duplicate {label}: {name!r}")
	seen.add(name)


def parse_table5(text: str) -> TableData:
	rows = _parse_rows(text, headers=_TABLE5_HEADERS)
	operators: list[dict[str, int | str]] = []
	totals: dict[str, int] | None = None
	seen: set[str] = set()

	for row in rows:
		name = row[0]
		values = [_parse_nonnegative_int(value, label=f"Table 5 {name}") for value in row[1:]]
		undesired_state, system_error, operator_error, recovery_failure, total = values
		if total != undesired_state + system_error + operator_error + recovery_failure:
			raise ValueError(f"Table 5 total does not match category counts for {name!r}")

		if name == "Total":
			if totals is not None:
				raise ValueError("Table 5 contains more than one Total row")
			totals = {
				"undesired_state": undesired_state,
				"system_error": system_error,
				"operator_error": operator_error,
				"recovery_failure": recovery_failure,
				"total_all": total,
			}
			continue

		_require_unique(name, seen, label="Table 5 operator")
		operators.append(
			{
				"operator": name,
				"undesired_state": undesired_state,
				"system_error": system_error,
				"operator_error": operator_error,
				"recovery_failure": recovery_failure,
				"total": total,
			}
		)

	if not operators:
		raise ValueError("Table 5 contains no operator rows")
	if totals is None:
		raise ValueError("Table 5 is missing its Total row")

	computed_totals = {
		"undesired_state": sum(int(row["undesired_state"]) for row in operators),
		"system_error": sum(int(row["system_error"]) for row in operators),
		"operator_error": sum(int(row["operator_error"]) for row in operators),
		"recovery_failure": sum(int(row["recovery_failure"]) for row in operators),
		"total_all": sum(int(row["total"]) for row in operators),
	}
	if totals != computed_totals:
		raise ValueError("Table 5 Total row does not match the operator rows")

	operators.sort(key=lambda row: str(row["operator"]))
	return {"operators": operators, "totals": totals}


def parse_table6(text: str) -> TableData:
	rows = _parse_rows(text, headers=_TABLE6_HEADERS)
	symptoms: list[dict[str, int | str]] = []
	seen: set[str] = set()

	for symptom, bugs_text in rows:
		_require_unique(symptom, seen, label="Table 6 consequence")
		symptoms.append(
			{
				"symptom": symptom,
				"bugs": _parse_nonnegative_int(bugs_text, label=f"Table 6 {symptom}"),
			}
		)

	symptoms.sort(key=lambda row: str(row["symptom"]))
	return {"symptoms": symptoms}


def parse_table7(text: str) -> TableData:
	rows = _parse_rows(text, headers=_TABLE7_HEADERS)
	test_oracles: list[dict[str, float | int | str]] = []
	seen: set[str] = set()
	value_pattern = re.compile(r"^(\d+)\s+\((\d+(?:\.\d+)?)%\)$")

	for test_oracle, value_text in rows:
		_require_unique(test_oracle, seen, label="Table 7 test oracle")
		match = value_pattern.fullmatch(value_text)
		if match is None:
			raise ValueError(f"invalid Table 7 bug count and percentage: {value_text!r}")
		test_oracles.append(
			{
				"test_oracle": test_oracle,
				"bugs": int(match.group(1)),
				"percentage": float(match.group(2)),
			}
		)

	test_oracles.sort(key=lambda row: str(row["test_oracle"]))
	return {"test_oracles": test_oracles}


def parse_table8(text: str) -> TableData:
	rows = _parse_rows(text, headers=_TABLE8_HEADERS)
	operators: list[dict[str, int | str]] = []
	seen: set[str] = set()

	for operator, operations_text in rows:
		_require_unique(operator, seen, label="Table 8 operator")
		operators.append(
			{
				"operator": operator,
				"operations": _parse_nonnegative_int(operations_text, label=f"Table 8 {operator}"),
			}
		)

	operators.sort(key=lambda row: str(row["operator"]))
	return {"operators": operators}
