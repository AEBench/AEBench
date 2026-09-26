from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cases.asplos24_loupe.oracles.common import (
	LoupeRunEvidenceCheck,
	LoupeTablesCheck,
	_read_syscall_table,
)
from evaluator.oracles.oracle_checks_runtime import LocalRuntimeCheckExecutor


def _write_table(path: Path, columns: str, width: int, used: set[int]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	lines = [columns]
	for syscall in range(335):
		flags = ["Y" if syscall in used else "N"] + ["N"] * (width - 2)
		lines.append(",".join((str(syscall), *flags)))
	path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_syscall_table_rejects_duplicate(tmp_path: Path) -> None:
	path = tmp_path / "dyn.csv"
	path.write_text("# syscall,used\n0,Y\n0,N\n", encoding="utf-8")
	try:
		_read_syscall_table(path, ("# syscall", "used"))
	except ValueError as exc:
		assert "duplicates syscall 0" in str(exc)
	else:
		raise AssertionError("duplicate syscall row was accepted")


def test_loupe_tables_check_accepts_complete_nontrivial_output(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	result = workspace / "loupedb/aebench-nginx/benchmark-wrk/testhash"
	used = set(range(20)) | {231}
	_write_table(
		result / "data/dyn.csv",
		"# syscall,used,works faked,works stubbed,works both",
		5,
		used,
	)
	lines = (result / "data/dyn.csv").read_text(encoding="utf-8").splitlines()
	lines[2] = "1,Y,Y,N,N"
	(result / "data/dyn.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
	_write_table(result / "data/static_binary.csv", "# syscall,used", 2, set(range(40)) | {231})

	dockerfile = workspace / "examples/E1/Dockerfile.nginx"
	test_script = workspace / "examples/E1/dockerfile_data/nginx-test.sh"
	base = workspace / "docker/Dockerfile.loupe-base"
	for path, text in ((dockerfile, "dockerfile"), (test_script, "test"), (base, "base")):
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text, encoding="utf-8")
	(result / "dockerfile_data").mkdir(parents=True)
	(result / "Dockerfile.aebench-nginx").write_text("dockerfile", encoding="utf-8")
	(result / "dockerfile_data/nginx-test.sh").write_text("test", encoding="utf-8")

	reference = tmp_path / "reference.json"
	reference.write_text(
		json.dumps(
			{
				"dockerfile_md5": "testhash",
				"syscall_min": 0,
				"syscall_max": 334,
				"min_used": 10,
				"min_replaceable": 1,
				"essential_used": [0, 1, 3, 9, 10, 12, 13, 231],
				"input_sha256": {
					"examples/E1/Dockerfile.nginx": hashlib.sha256(b"dockerfile").hexdigest(),
					"examples/E1/dockerfile_data/nginx-test.sh": hashlib.sha256(
						b"test"
					).hexdigest(),
				},
			}
		),
		encoding="utf-8",
	)
	check = LoupeTablesCheck(
		name="tables",
		workspace=workspace,
		reference=reference,
	)
	assert check.check(LocalRuntimeCheckExecutor(default_cwd=workspace)).ok


def test_loupe_tables_check_rejects_incomplete_output(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	result = workspace / "loupedb/aebench-nginx/benchmark-wrk/testhash/data"
	_write_table(result / "dyn.csv", "# syscall,used,works faked,works stubbed,works both", 5, {0})
	(result / "dyn.csv").write_text(
		"\n".join((result / "dyn.csv").read_text(encoding="utf-8").splitlines()[:-1]) + "\n",
		encoding="utf-8",
	)
	_write_table(result / "static_binary.csv", "# syscall,used", 2, {0})
	reference = tmp_path / "reference.json"
	reference.write_text(
		json.dumps(
			{
				"dockerfile_md5": "testhash",
				"syscall_min": 0,
				"syscall_max": 334,
				"min_used": 1,
				"min_replaceable": 1,
				"essential_used": [0],
				"input_sha256": {},
			}
		),
		encoding="utf-8",
	)
	check = LoupeTablesCheck(name="tables", workspace=workspace, reference=reference)
	result_check = check.check(LocalRuntimeCheckExecutor(default_cwd=workspace))
	assert not result_check.ok
	assert "do not cover every syscall" in result_check.message


def test_loupe_run_evidence_requires_both_replicas_and_logs(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	result = workspace / "loupedb/aebench-nginx/benchmark-wrk/testhash"
	result.mkdir(parents=True)
	(result / "cmd.txt").write_text(
		"loupe generate -a aebench-nginx -w wrk -d Dockerfile.nginx\n",
		encoding="utf-8",
	)
	(result / "explore.logs").write_text(
		"**** Logs of replica #0 ****\nok\n**** Logs of replica #1 ****\nok\n",
		encoding="utf-8",
	)
	logs = workspace / "aebench-results"
	logs.mkdir()
	(logs / "experiment.log").write_text("Done! Full analysis last 0:01:00\n", encoding="utf-8")
	(logs / "search.log").write_text(
		"Required:\n[]\nCan be stubbed:\n[]\nCan be faked:\n[]\n",
		encoding="utf-8",
	)
	reference = tmp_path / "reference.json"
	reference.write_text(json.dumps({"dockerfile_md5": "testhash"}), encoding="utf-8")
	check = LoupeRunEvidenceCheck(name="evidence", workspace=workspace, reference=reference)
	assert check.check(LocalRuntimeCheckExecutor(default_cwd=workspace)).ok
