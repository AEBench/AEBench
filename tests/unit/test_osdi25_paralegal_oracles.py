from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from cases.osdi25_paralegal.oracles.common import (
	parse_codeql_table,
	parse_smoke_results,
	validate_controller_results,
)

_SMOKE_COLUMNS = (
	"id",
	"experiment",
	"application",
	"expectation",
	"result",
	"pdg_timed_out",
	"analyzer_time",
	"last_self_time",
	"policy_time",
	"pdg_functions",
	"pdg_locs",
	"seen_functions",
	"seen_locs",
	"file_size",
)


def _smoke_csv(*rows: dict[str, object]) -> str:
	output = io.StringIO()
	writer = csv.DictWriter(output, fieldnames=_SMOKE_COLUMNS)
	writer.writeheader()
	for row in rows:
		writer.writerow(row)
	return output.getvalue()


def _smoke_row(run_id: int, outcome: str) -> dict[str, object]:
	return {
		"id": run_id,
		"experiment": "smoke",
		"application": "atomic-data",
		"expectation": outcome,
		"result": outcome,
		"pdg_timed_out": "false",
		"analyzer_time": "1000",
		"last_self_time": "900",
		"policy_time": "100",
		"pdg_functions": "20",
		"pdg_locs": "200",
		"seen_functions": "30",
		"seen_locs": "300",
		"file_size": "4096",
	}


def test_codeql_parser_normalizes_spacing_paths_and_row_order() -> None:
	expected = """\
| db_access | col1 |
+-----------+------+
| call to readLocalSite | file:///home/aec/artifact/codeql-experimentation/cpp/main.cpp:2 |
| call to readLocalSite | file:///home/aec/artifact/codeql-experimentation/cpp/main.cpp:1 |
"""
	observed = """\
| db_access | col1 |
+------+------+
|  call   to readLocalSite  | file:///users/test/work/codeql-experimentation/cpp/main.cpp:1 |
| call to readLocalSite | file:///users/test/work/codeql-experimentation/cpp/main.cpp:2 |
"""

	assert parse_codeql_table(observed) == parse_codeql_table(expected)


@pytest.mark.parametrize(
	("text", "message"),
	[
		("", "empty"),
		("| only_header |", "incomplete"),
		("| header |\nnot-a-separator", "separator"),
		("| left | right |\n+------+------+\n| one |", "columns"),
	],
)
def test_codeql_parser_rejects_empty_malformed_and_incomplete_tables(
	text: str,
	message: str,
) -> None:
	with pytest.raises(ValueError, match=message):
		parse_codeql_table(text)


def test_smoke_parser_accepts_complete_pass_and_fail_runs() -> None:
	results = parse_smoke_results(
		_smoke_csv(
			_smoke_row(0, "pass"),
			_smoke_row(1, "fail"),
		)
	)

	assert len(results.rows) == 2
	assert results.run_ids == frozenset({0, 1})


def test_smoke_parser_rejects_empty_output() -> None:
	with pytest.raises(ValueError, match="empty"):
		parse_smoke_results("")


def test_smoke_parser_rejects_malformed_missing_columns() -> None:
	with pytest.raises(ValueError, match="missing columns"):
		parse_smoke_results("id,experiment\n0,smoke\n")


def test_smoke_parser_rejects_incomplete_expectation_pair() -> None:
	with pytest.raises(ValueError, match="exactly 2 rows"):
		parse_smoke_results(_smoke_csv(_smoke_row(0, "pass")))


def test_smoke_parser_rejects_surplus_rows() -> None:
	with pytest.raises(ValueError, match="exactly 2 rows"):
		parse_smoke_results(
			_smoke_csv(
				_smoke_row(0, "pass"),
				_smoke_row(1, "fail"),
				_smoke_row(2, "pass"),
			)
		)


def test_smoke_parser_rejects_nan_only_measurements() -> None:
	pass_row = _smoke_row(0, "pass")
	fail_row = _smoke_row(1, "fail")
	for column in (
		"analyzer_time",
		"last_self_time",
		"policy_time",
		"pdg_functions",
		"pdg_locs",
		"seen_functions",
		"seen_locs",
		"file_size",
	):
		pass_row[column] = "NaN"
		fail_row[column] = "NaN"

	with pytest.raises(ValueError, match="not finite"):
		parse_smoke_results(_smoke_csv(pass_row, fail_row))


def test_smoke_parser_rejects_result_that_does_not_match_expectation() -> None:
	pass_row = _smoke_row(0, "pass")
	fail_row = _smoke_row(1, "fail")
	fail_row["result"] = "pass"

	with pytest.raises(ValueError, match="does not match expectation"):
		parse_smoke_results(_smoke_csv(pass_row, fail_row))


def test_controller_parser_requires_rows_for_every_smoke_run() -> None:
	controllers = """\
run_id,name,num_nodes,num_edges,unique_locs,unique_functions,analyzed_locs,analyzed_functions
0,commit,12,16,8,4,20,10
"""

	with pytest.raises(ValueError, match="no rows for run ids"):
		validate_controller_results(controllers, expected_run_ids=frozenset({0, 1}))


def test_agent_smoke_config_matches_oracle_reference() -> None:
	case_root = Path(__file__).parents[2] / "cases" / "osdi25_paralegal"

	assert (case_root / "artifact" / "aebench-smoke-config.toml").read_bytes() == (
		case_root / "refs" / "smoke_bench_config.toml"
	).read_bytes()
