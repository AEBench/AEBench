from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from cases.sosp23_acto.oracles import custom
from cases.sosp23_acto.oracles.experiment_runs import OracleExperimentRuns
from evaluator.oracles.utils import LocalRuntimeCheckExecutor
from models import OracleConfig, OracleInput, OracleRuntimeConfig, RuntimeMode

CASE_ROOT = Path(__file__).parents[2] / "cases" / "sosp23_acto"

TABLE5 = """\
Operator         Undesired State    System Error    Operator Error    Recovery Failure    Total
-------------  -----------------  --------------  ----------------  ------------------  -------
CassOp                         2               0                 0                   2        4
CockroachOp                    3               0                 2                   0        5
KnativeOp                      1               0                 2                   0        3
OCK-RedisOp                    4               1                 3                   1        9
OFC-MongoDBOp                  3               1                 2                   2        8
PCN-MongoDBOp                  4               0                 0                   1        5
RabbitMQOp                     3               0                 0                   0        3
SAH-RedisOp                    2               0                 0                   1        3
TiDBOp                         2               1                 0                   1        4
XtraDBOp                       4               0                 1                   1        6
ZookeeperOp                    4               1                 0                   1        6
Total                         32               4                10                  10       56
"""

TABLE6 = """\
Consequence          # Bugs
-----------------  --------
System failure            5
Reliability issue        15
Security issue            2
Resource issue            9
Operation outage         18
Misconfiguration         15
"""

TABLE7 = """\
Test Oracle                                          # Bugs (Percentage)
---------------------------------------------------  ---------------------
Consistency oracle                                   23 (41.07%)
Differential oracle for normal state transition      25 (44.64%)
Differential oracle for rollback state transition    10 (17.86%)
Regular error check (e.g., exceptions, error codes)  14 (25.00%)
"""

TABLE8 = """\
Operator         # Operations
-------------  --------------
CassOp                    568
CockroachOp               371
KnativeOp                 774
OCK-RedisOp               597
OFC-MongoDBOp             434
PCN-MongoDBOp            1749
RabbitMQOp                394
SAH-RedisOp               718
TiDBOp                    824
XtraDBOp                 1950
ZookeeperOp               740
"""

PUBLISHED_TABLES = {
	"table5": TABLE5,
	"table6": TABLE6,
	"table7": TABLE7,
	"table8": TABLE8,
}


@pytest.mark.parametrize(
	("table_name", "parser", "table_text"),
	(
		("table5", custom.parse_table5, TABLE5),
		("table6", custom.parse_table6, TABLE6),
		("table7", custom.parse_table7, TABLE7),
		("table8", custom.parse_table8, TABLE8),
	),
)
def test_published_tables_match_bundled_references(table_name, parser, table_text) -> None:
	reference_path = CASE_ROOT / "refs" / f"{table_name}.ref.json"
	reference = json.loads(reference_path.read_text(encoding="utf-8"))

	assert parser(table_text) == reference


def test_table5_requires_total_row() -> None:
	with pytest.raises(ValueError, match="missing its Total row"):
		custom.parse_table5(TABLE5.rsplit("\n", 2)[0])


def test_table7_rejects_malformed_percentage() -> None:
	with pytest.raises(ValueError, match="invalid Table 7 bug count and percentage"):
		custom.parse_table7(TABLE7.replace("23 (41.07%)", "23"))


def test_table8_rejects_duplicate_operator() -> None:
	duplicate = TABLE8 + "CassOp                    568\n"

	with pytest.raises(ValueError, match="duplicate Table 8 operator"):
		custom.parse_table8(duplicate)


def test_experiment_oracle_accepts_published_tables(tmp_path: Path) -> None:
	for table_name, table_text in PUBLISHED_TABLES.items():
		(tmp_path / f"{table_name}.txt").write_text(table_text, encoding="utf-8")

	context = OracleInput(
		case_dir=CASE_ROOT,
		artifact_dir=CASE_ROOT / "artifact",
		workspace_dir=tmp_path,
		output_dir=tmp_path / "output",
		oracle_config=OracleConfig(
			runtime=OracleRuntimeConfig(mode=RuntimeMode.LOCAL),
		),
	)
	context.runtime_executor = LocalRuntimeCheckExecutor(default_cwd=tmp_path)
	oracle = OracleExperimentRuns(
		context=context,
		logger=logging.getLogger("test.sosp23_acto.experiment_runs"),
	)

	assert oracle.report().ok
