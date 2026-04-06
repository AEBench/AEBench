"""Evaluator-wide constants."""

from __future__ import annotations


ORACLE_DIRNAME: str = "oracles"

REFS_DIRNAME: str = "refs"


CASE_MANIFEST_FILENAME: str = "case.toml"
ORACLE_RESULT_FILENAME: str = "oracle_result.json"


DEFAULT_ORACLE_CHECK_TIMEOUT: float = 5.0

DEFAULT_ORACLE_BUILD_TIMEOUT: float = 60.0

SUBPROCESS_WAIT_TIMEOUT: float = 5.0
