from __future__ import annotations

import pytest

from cli import _build_parser


def test_case_oracle_accepts_run_dir() -> None:
	args = _build_parser().parse_args(
		["case", "oracle", "fixture_case", "--run-dir", "/tmp/completed-run"]
	)

	assert args.run_dir == "/tmp/completed-run"
	assert args.output_dir is None


def test_case_oracle_rejects_run_dir_with_output_dir() -> None:
	with pytest.raises(SystemExit):
		_build_parser().parse_args(
			[
				"case",
				"oracle",
				"fixture_case",
				"--run-dir",
				"/tmp/completed-run",
				"--output-dir",
				"/tmp/output",
			]
		)
