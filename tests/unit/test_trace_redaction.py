from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cli import cli_main


def test_redact_saved_credentials_across_run_and_monitor_files(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
	source = tmp_path / "trial"
	(source / "run").mkdir(parents=True)
	(source / "monitor" / "stream" / "command-1").mkdir(parents=True)
	token = "historical-oauth-value"
	password = 'páss\\word"with spaces'
	(source / "run" / "runner_output.log").write_text(
		"[2026-10-12T17:00:00Z] "
		+ json.dumps(
			{
				"type": "user",
				"message": {
					"content": f"CLAUDE_CODE_OAUTH_TOKEN={token}\nDB_PASSWORD={password}\nPATH=/usr/bin\n"
				},
			}
		)
		+ "\n",
		encoding="utf-8",
	)
	commands = source / "monitor" / "commands.jsonl"
	commands.write_text(
		json.dumps({"argv": ["echo", token, password], "return_code": 0, "complete": True}) + "\n",
		encoding="utf-8",
	)
	stream = source / "monitor" / "stream" / "command-1" / "stderr.log"
	stream.write_bytes(b"\x1b[31mdiagnostic: " + token.encode() + b"\r\n")
	(source / "run" / "case_result.json").write_text('{"score": 4}\n', encoding="utf-8")
	originals = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
	destination = tmp_path / "shared"

	assert cli_main(["trace", "redact", str(source), "--output-dir", str(destination)]) == 0

	for relative, original in originals.items():
		assert (source / relative).read_bytes() == original
		redacted = (destination / relative).read_bytes()
		assert token.encode() not in redacted
		assert password.encode() not in redacted
	assert json.loads((destination / "monitor" / "commands.jsonl").read_text()) == {
		"argv": ["echo", "[REDACTED]", "[REDACTED]"],
		"return_code": 0,
		"complete": True,
	}
	line = (destination / "run" / "runner_output.log").read_text()
	assert line.startswith("[2026-10-12T17:00:00Z] ")
	assert json.loads(line.split("] ", 1)[1])["message"]["content"] == (
		"CLAUDE_CODE_OAUTH_TOKEN=[REDACTED]\nDB_PASSWORD=[REDACTED]\nPATH=/usr/bin\n"
	)
	assert (
		destination / stream.relative_to(source)
	).read_bytes() == b"\x1b[31mdiagnostic: [REDACTED]\r\n"
	assert (destination / "run" / "case_result.json").read_bytes() == originals[
		Path("run/case_result.json")
	]


def test_redact_context_credentials_and_provider_tokens(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	values = ["old-json-credential", "sk-ant-oat01-" + "a" * 40, "ghp_" + "b" * 36]
	(source / "credentials.json").write_text(
		json.dumps({"auth": {"accessToken": values[0]}, "input_tokens": 123}), encoding="utf-8"
	)
	(source / "output.txt").write_text("\n".join(values), encoding="utf-8")

	redact_directory(source, tmp_path / "shared")

	assert json.loads((tmp_path / "shared/credentials.json").read_text()) == {
		"auth": {"accessToken": "[REDACTED]"},
		"input_tokens": 123,
	}
	assert (tmp_path / "shared/output.txt").read_text() == "\n".join(["[REDACTED]"] * 3)


@pytest.mark.parametrize(
	"name,data",
	[
		("payload.bin", b"token=private-value"),
		("payload.log", b"\x00token=private-value"),
		("payload.log", b"\xfftoken=private-value"),
	],
)
def test_redact_refuses_binary_files_without_publishing(
	tmp_path: Path, name: str, data: bytes
) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / name).write_bytes(data)
	with pytest.raises(ValueError, match="binary|non-UTF-8"):
		redact_directory(source, tmp_path / "shared")
	assert (source / name).read_bytes() == data
	assert not (tmp_path / "shared").exists()


def test_redact_keeps_path_and_boolean_values_but_not_numeric_passwords(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	from trace_viewer.redaction import redact_directory

	monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/credentials.json")
	source = tmp_path / "run"
	source.mkdir()
	(source / "env.log").write_text(
		"GOOGLE_APPLICATION_CREDENTIALS=/tmp/credentials.json\n"
		"FEATURE_TOKEN=true\nPASSWORD=12345678\n"
		"ACCESS_TOKEN=https://example.invalid/?sig=private\n",
		encoding="utf-8",
	)
	(source / "output.txt").write_text(
		"/tmp/credentials.json true 12345678 https://example.invalid/?sig=private\n",
		encoding="utf-8",
	)
	redact_directory(source, tmp_path / "shared")
	assert (tmp_path / "shared/output.txt").read_text() == (
		"/tmp/credentials.json true [REDACTED] [REDACTED]\n"
	)


@pytest.mark.parametrize("closing", [")", "]", "}"])
def test_redact_token_before_closing_bracket(tmp_path: Path, closing: str) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "output.log").write_text(
		f"my_method(token=1a2b3c4d{closing}\ntoken=1a2b3c4d{closing}\nagain=1a2b3c4d\n",
		encoding="utf-8",
	)
	redact_directory(source, tmp_path / "shared")
	assert (tmp_path / "shared/output.log").read_text() == (
		f"my_method(token=[REDACTED]{closing}\ntoken=[REDACTED]{closing}\nagain=[REDACTED]\n"
	)


@pytest.mark.parametrize(
	"recorded", [r"quoted\npassword", r"quoted\"password", r"folder\\credential"]
)
def test_redact_escaped_quoted_assignments(tmp_path: Path, recorded: str) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	command = f'export PASSWORD="{recorded}"'
	(source / "stdout.log").write_text(command + "\n", encoding="utf-8")
	(source / "commands.jsonl").write_text(json.dumps({"command": command}), encoding="utf-8")
	(source / "result.json").write_text(
		json.dumps({"output": json.loads(f'"{recorded}"')}), encoding="utf-8"
	)

	redact_directory(source, tmp_path / "shared")

	assert (source / "stdout.log").read_text() == command + "\n"
	assert (tmp_path / "shared/stdout.log").read_text() == 'export PASSWORD="[REDACTED]"\n'
	assert json.loads((tmp_path / "shared/commands.jsonl").read_text()) == {
		"command": 'export PASSWORD="[REDACTED]"'
	}
	assert json.loads((tmp_path / "shared/result.json").read_text()) == {"output": "[REDACTED]"}


@pytest.mark.parametrize("password", ['"quoted"', '{"nested": "private"}'])
def test_redact_secret_values_that_look_like_json(tmp_path: Path, password: str) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "credentials.json").write_text(json.dumps({"password": password}), encoding="utf-8")
	(source / "commands.jsonl").write_text(json.dumps({"argv": [password]}), encoding="utf-8")

	redact_directory(source, tmp_path / "shared")

	assert json.loads((tmp_path / "shared/credentials.json").read_text()) == {
		"password": "[REDACTED]"
	}
	assert json.loads((tmp_path / "shared/commands.jsonl").read_text()) == {"argv": ["[REDACTED]"]}


def test_redact_current_environment_and_explicit_secret_file(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	source = tmp_path / "run"
	source.mkdir()
	monkeypatch.setenv("CUSTOM_API_KEY", "ambient-value")
	secret_file = tmp_path / "private-secrets.txt"
	secret_file.write_text("rotated-value\nrotated-value-longer\n", encoding="utf-8")
	(source / "output.txt").write_text(
		"ambient-value rotated-value-longer rotated-value normal output\n", encoding="utf-8"
	)

	assert (
		cli_main(
			[
				"trace",
				"redact",
				str(source),
				"--output-dir",
				str(tmp_path / "shared"),
				"--secret-file",
				str(secret_file),
			]
		)
		== 0
	)

	assert (tmp_path / "shared/output.txt").read_text() == (
		"[REDACTED] [REDACTED] [REDACTED] normal output\n"
	)
	assert secret_file.read_text() == "rotated-value\nrotated-value-longer\n"


def test_redact_json_escaping_numeric_strings_and_multiline_keys(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	token = "history-token-value"
	private_key = "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----"
	(source / "stdout.log").write_text(
		f"API_TOKEN={token}\nPASSWORD=12345678\nPRIVATE_KEY={private_key}\n", encoding="utf-8"
	)
	(source / "commands.jsonl").write_text(
		'{"argv":["echo","\\u0068istory-token-value","12345678"],"return_code":0}\n'
		+ json.dumps({"text": json.dumps({"output": token})})
		+ "\n",
		encoding="utf-8",
	)

	redact_directory(source, tmp_path / "shared")

	lines = (tmp_path / "shared/commands.jsonl").read_text().splitlines()
	assert json.loads(lines[0]) == {"argv": ["echo", "[REDACTED]", "[REDACTED]"], "return_code": 0}
	assert json.loads(json.loads(lines[1])["text"]) == {"output": "[REDACTED]"}
	assert (tmp_path / "shared/stdout.log").read_text() == (
		"API_TOKEN=[REDACTED]\nPASSWORD=[REDACTED]\nPRIVATE_KEY=[REDACTED]\n"
	)


def test_redact_secret_in_nested_json_string_with_unicode_escape(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "env.log").write_text("API_TOKEN=history-token-value\n", encoding="utf-8")
	(source / "result.jsonl").write_text(
		json.dumps({"text": '{"output":"\\u0068istory-token-value"}'}) + "\n",
		encoding="utf-8",
	)
	redact_directory(source, tmp_path / "shared")
	outer = json.loads((tmp_path / "shared/result.jsonl").read_text())
	assert json.loads(outer["text"]) == {"output": "[REDACTED]"}


def test_redact_does_not_publish_json_with_colliding_redacted_keys(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "env.log").write_text(
		"API_KEY=first-value\nOTHER_API_KEY=second-value\n", encoding="utf-8"
	)
	(source / "results.json").write_text('{"first-value": 1, "second-value": 2}', encoding="utf-8")

	with pytest.raises(ValueError, match="merge JSON keys"):
		redact_directory(source, tmp_path / "shared")

	assert not (tmp_path / "shared").exists()


def test_redact_numeric_stdout_and_json_lines_inside_tool_results(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "env.log").write_text(
		"PASSWORD=12345678\nAPI_TOKEN=history-token-value\n", encoding="utf-8"
	)
	(source / "stdout.log").write_text("12345678\n", encoding="utf-8")
	content = 'first line\n{"output":"\\u0068istory-token-value"}\n{"score":12345678}\n'
	(source / "runner_output.log").write_text(
		json.dumps({"message": {"content": content}}), encoding="utf-8"
	)

	redact_directory(source, tmp_path / "shared")

	assert (tmp_path / "shared/stdout.log").read_text() == "[REDACTED]\n"
	result = json.loads((tmp_path / "shared/runner_output.log").read_text())["message"]["content"]
	assert result == 'first line\n{"output": "[REDACTED]"}\n{"score":12345678}\n'


def test_redact_cli_reports_failure_without_a_partial_copy(
	tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
	assert (
		cli_main(
			["trace", "redact", str(tmp_path / "missing"), "--output-dir", str(tmp_path / "shared")]
		)
		== 1
	)
	assert "Could not redact traces" in capsys.readouterr().err
	assert not (tmp_path / "shared").exists()


def test_redact_refuses_an_unreadable_subtree(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	blocked = source / "monitor"
	blocked.mkdir(parents=True)
	(source / "output.txt").write_text("historical-credential", encoding="utf-8")
	(blocked / "stdout.log").write_text("API_KEY=historical-credential\n", encoding="utf-8")
	scandir = os.scandir

	def deny_monitor(path: str | Path | int) -> object:
		if not isinstance(path, int) and Path(path) == blocked:
			raise PermissionError("monitor is unreadable")
		return scandir(path)

	monkeypatch.setattr(os, "scandir", deny_monitor)
	with pytest.raises(PermissionError, match="monitor is unreadable"):
		redact_directory(source, tmp_path / "shared")

	assert not (tmp_path / "shared").exists()


def test_redact_provider_tokens_in_json_member_names(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	token = "sk-ant-oat01-" + "a" * 40
	(source / "result.json").write_text(
		json.dumps({token: "account@example.invalid"}), encoding="utf-8"
	)

	redact_directory(source, tmp_path / "shared")

	assert json.loads((tmp_path / "shared/result.json").read_text()) == {
		"[REDACTED]": "account@example.invalid"
	}


def test_redact_rejects_duplicate_json_member_names(tmp_path: Path) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "runner_output.log").write_text(
		'{"password":"original-credential","password":"replacement-credential","output":"original-credential"}',
		encoding="utf-8",
	)

	with pytest.raises(ValueError, match="duplicate JSON keys"):
		redact_directory(source, tmp_path / "shared")

	assert not (tmp_path / "shared").exists()


def test_redacted_run_still_exports_to_the_viewer(tmp_path: Path) -> None:
	from trace_viewer import export_trace_site
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	token = "historical-claude-oauth"
	(source / "runner_output.log").write_text(
		json.dumps(
			{
				"type": "user",
				"message": {
					"content": [
						{"type": "tool_result", "content": f"CLAUDE_CODE_OAUTH_TOKEN={token}\n"}
					]
				},
			}
		)
		+ "\n",
		encoding="utf-8",
	)
	(source / "result.jsonl").write_text(
		json.dumps(
			{
				"id": "test-case",
				"status": "success",
				"agent_kind": "claude_non_api",
				"agent": {"model": "claude-opus-5-5", "exit_code": 0},
			}
		)
		+ "\n",
		encoding="utf-8",
	)
	(source / "case_result.json").write_text(
		json.dumps(
			{
				"status": "success",
				"oracle_result": {
					"score": 4,
					"phases": [
						{"phase": name}
						for name in [
							"env_setup",
							"artifact_build",
							"benchmark_prep",
							"experiment_runs",
						]
					],
				},
			}
		),
		encoding="utf-8",
	)
	copy = redact_directory(source, tmp_path / "shared")

	index = export_trace_site(copy, tmp_path / "site")

	assert index.is_file()
	catalog = json.loads((tmp_path / "site/data/index.json").read_text())
	assert catalog["runs"][0]["score"] == 4
	assert catalog["runs"][0]["model"] == "claude-opus-5-5"
	for path in (tmp_path / "site/data").glob("*.json"):
		assert token not in path.read_text()


@pytest.mark.parametrize("destination_name", ["run", "run/shared", "existing"])
def test_redact_refuses_overwrite_and_overlapping_paths(
	tmp_path: Path, destination_name: str
) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "output.txt").write_text("API_KEY=keep-original\n", encoding="utf-8")
	(tmp_path / "existing").mkdir()

	with pytest.raises(ValueError):
		redact_directory(source, tmp_path / destination_name)

	assert (source / "output.txt").read_text() == "API_KEY=keep-original\n"
	assert not (source / "shared").exists()
	assert list((tmp_path / "existing").iterdir()) == []


@pytest.mark.parametrize("entry_kind", ["file_link", "directory_link", "fifo"])
def test_redact_rejects_links_and_special_files_without_publishing(
	tmp_path: Path, entry_kind: str
) -> None:
	from trace_viewer.redaction import redact_directory

	source = tmp_path / "run"
	source.mkdir()
	(source / "output.txt").write_text("API_KEY=private-value\n", encoding="utf-8")
	outside = tmp_path / "outside"
	outside.mkdir()
	(outside / "secret.txt").write_text("outside evidence", encoding="utf-8")
	entry = source / "linked"
	if entry_kind == "fifo":
		os.mkfifo(entry)
	else:
		entry.symlink_to(outside if entry_kind == "directory_link" else outside / "secret.txt")

	with pytest.raises(ValueError):
		redact_directory(source, tmp_path / "shared")

	assert not (tmp_path / "shared").exists()
	assert (source / "output.txt").read_text() == "API_KEY=private-value\n"
	assert (outside / "secret.txt").read_text() == "outside evidence"
