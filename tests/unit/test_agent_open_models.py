from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime.agents import load_agent, resolve_secrets


@pytest.mark.parametrize(
	("name", "key", "file_env"),
	[
		("deepseek", "DEEPSEEK_API_KEY", "AEBENCH_DEEPSEEK_API_KEY_FILE"),
		("zcode", "ZAI_CODING_PLAN_KEY", "AEBENCH_ZAI_CODING_PLAN_KEY_FILE"),
	],
)
def test_open_model_credentials(tmp_path: Path, name: str, key: str, file_env: str) -> None:
	agent = load_agent(name)
	token = tmp_path / "key"
	token.write_text("test-value\n")
	assert resolve_secrets(agent, environ={key: "test-value"}) == {key: "test-value"}
	assert resolve_secrets(agent, environ={file_env: str(token)}) == {key: "test-value"}
	assert agent.config.reasoning_effort == "high"
	assert agent.config.trace_format.value == "raw"


@pytest.mark.parametrize(
	("name", "model", "cli", "key"),
	[
		("deepseek", "deepseek-flash", "dsh", "DEEPSEEK_API_KEY"),
		("zcode", "glm-5.3-flash", "zcode", "ZAI_CODING_PLAN_KEY"),
	],
)
def test_open_model_launch(tmp_path: Path, name: str, model: str, cli: str, key: str) -> None:
	if name == "zcode" and shutil.which("node") is None:
		pytest.skip("node is required to write the ZCode configuration")
	state = tmp_path / "state"
	state.mkdir(mode=0o700)
	binary = tmp_path / cli
	binary.write_text(
		"#!/usr/bin/env python3\nimport json,os,sys\n"
		"print(json.dumps({'argv':sys.argv[1:], 'stdin':sys.stdin.read(), "
		"'home':os.environ['HOME'], 'key_retained':'ZAI_CODING_PLAN_KEY' in os.environ, "
		"'dsh_home':os.environ.get('DSH_HOME'), "
		"'umask':os.umask(0)}))\n"
	)
	binary.chmod(0o755)
	agent = load_agent(name)
	env = dict(
		os.environ,
		PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
		AEBENCH_AGENT_DIR=str(agent.directory),
		AEBENCH_STATE_DIR=str(state),
		AEBENCH_MODEL=model,
		AEBENCH_REASONING_EFFORT="high",
		PROMPT="do the task",
	)
	env[key] = "test-value"
	result = subprocess.run(
		["bash", str(agent.directory / "agent.sh")],
		env=env,
		capture_output=True,
		text=True,
		check=True,
		cwd=tmp_path,
	)
	observed = json.loads(result.stdout)
	assert observed["home"] == os.environ["HOME"]
	if name == "deepseek":
		assert observed["stdin"] == "do the task"
		assert observed["dsh_home"] == str(state / ".dsh")
		assert observed["argv"] == [
			"--profile",
			"headless",
			"--patch",
			str(agent.directory / "model.patch.yml"),
			"--json",
			"-",
		]
	else:
		provider = state / "provider.json"
		config = json.loads(provider.read_text())["config"]
		providers = config["providerConfigRules"]["providerRules"]
		assert len(providers) == 1
		assert providers[0]["config"]["access"]["apiKey"] == "test-value"
		assert providers[0]["config"]["api"]["baseUrl"] == "https://api.z.ai/api/anthropic"
		assert config["defaultModelSelection"]["modelId"] == model
		assert config["defaultModelSelection"]["options"]["reasoningLevel"] == "high"
		assert provider.stat().st_mode & 0o777 == 0o600 and not observed["key_retained"]
		assert observed["umask"] == 0o022
		assert observed["argv"][:6] == [
			"--prompt",
			"do the task",
			"--cwd",
			str(tmp_path),
			"--mode",
			"yolo",
		]


@pytest.mark.parametrize("name", ["deepseek", "zcode"])
def test_open_models_reject_other_models(name: str) -> None:
	result = subprocess.run(
		["bash", str(load_agent(name).directory / "agent.sh")],
		env={"AEBENCH_MODEL": "wrong-model"},
		capture_output=True,
		text=True,
	)
	assert result.returncode == 2
