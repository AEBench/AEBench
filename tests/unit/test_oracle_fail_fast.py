from __future__ import annotations

from cli import _build_parser, _run_options


def test_oracle_fail_fast_defaults_off() -> None:
	args = _build_parser().parse_args(["case", "run", "some_case"])

	assert _run_options(args).oracle_fail_fast is False


def test_oracle_fail_fast_flag_sets_run_option() -> None:
	args = _build_parser().parse_args(["case", "run", "--oracle-fail-fast", "some_case"])

	assert _run_options(args).oracle_fail_fast is True


def test_case_oracle_accepts_oracle_fail_fast() -> None:
	args = _build_parser().parse_args(["case", "oracle", "--oracle-fail-fast", "some_case"])

	assert args.oracle_fail_fast is True
