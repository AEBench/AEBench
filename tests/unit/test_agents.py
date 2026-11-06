from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from runtime.agents import (
	AGENT_DIRS_ENV,
	BUILTIN_AGENTS_DIR,
	AgentConfigError,
	TraceFormat,
	discover_agents,
	load_agent,
	load_agent_dir,
	resolve_files,
	resolve_secrets,
)

_BUILTIN_AGENTS = (
	sorted(path.name for path in BUILTIN_AGENTS_DIR.iterdir() if (path / "agent.sh").is_file())
	if BUILTIN_AGENTS_DIR.is_dir()
	else []
)


def _write_agent(root: Path, name: str, config: str = "", script: str = "true\n") -> Path:
	directory = root / name
	directory.mkdir(parents=True)
	(directory / "agent.sh").write_text(script, encoding="utf-8")
	if config:
		(directory / "agent.toml").write_text(config, encoding="utf-8")
	return directory


# Every agent shipped in agents/ gets these checks without writing a test.


def test_builtin_agents_are_valid() -> None:
	assert discover_agents(environ={}).problems == {}


@pytest.mark.parametrize("name", _BUILTIN_AGENTS)
def test_builtin_agent_is_well_formed(name: str) -> None:
	agent = load_agent(name, environ={})
	assert agent.config.description, "describe the agent in agent.toml"
	assert agent.config.requires, "list the executables agent.sh needs in requires"
	for path in (agent.directory / "agent.sh", agent.install_script):
		if path is not None:
			result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
			assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", _BUILTIN_AGENTS)
def test_builtin_agent_requirements_are_installed_by_some_agent(name: str) -> None:
	installers = "\n".join(
		path.read_text(encoding="utf-8") for path in BUILTIN_AGENTS_DIR.glob("*/install.sh")
	)
	for executable in load_agent(name, environ={}).config.requires:
		assert executable in installers, f"no agents/*/install.sh installs {executable}"


# Discovery


def test_minimal_agent_is_just_a_script(tmp_path: Path) -> None:
	_write_agent(tmp_path, "minimal")
	agent = load_agent("minimal", environ={AGENT_DIRS_ENV: str(tmp_path)})
	assert agent.config.trace_format == TraceFormat.RAW
	assert agent.config.secrets == []
	assert agent.install_script is None


def test_project_and_extra_dirs_are_searched(tmp_path: Path) -> None:
	project = tmp_path / "project"
	_write_agent(project / "agents", "project_agent")
	extra_a = tmp_path / "a"
	extra_b = tmp_path / "b"
	_write_agent(extra_a, "agent_a")
	_write_agent(extra_b, "agent_b")

	catalog = discover_agents(project, environ={AGENT_DIRS_ENV: f"{extra_a}:{extra_b}"})

	assert {"project_agent", "agent_a", "agent_b"} <= set(catalog.agents)
	assert catalog.search_dirs[:3] == [
		extra_a.resolve(),
		extra_b.resolve(),
		(project / "agents").resolve(),
	]


def test_same_name_twice_is_an_error_not_a_shadow(tmp_path: Path) -> None:
	_write_agent(tmp_path / "a", "dup")
	_write_agent(tmp_path / "b", "dup")
	catalog = discover_agents(environ={AGENT_DIRS_ENV: f"{tmp_path / 'a'}:{tmp_path / 'b'}"})
	assert "dup" not in catalog.agents
	assert "defined more than once" in catalog.problems["dup"]
	with pytest.raises(AgentConfigError, match="defined more than once"):
		catalog.get("dup")


def test_broken_agent_does_not_hide_the_others(tmp_path: Path) -> None:
	_write_agent(tmp_path, "broken", 'trace_format = "nope"\n')
	_write_agent(tmp_path, "fine")
	catalog = discover_agents(environ={AGENT_DIRS_ENV: str(tmp_path)})
	assert "trace_format" in catalog.problems["broken"]
	assert "fine" in catalog.agents


def test_unknown_agent_lists_the_available_ones(tmp_path: Path) -> None:
	_write_agent(tmp_path, "known")
	with pytest.raises(AgentConfigError, match="available: .*known"):
		load_agent("does-not-exist", environ={AGENT_DIRS_ENV: str(tmp_path)})


def test_directories_without_agent_files_are_ignored(tmp_path: Path) -> None:
	(tmp_path / "notes").mkdir()
	catalog = discover_agents(environ={AGENT_DIRS_ENV: str(tmp_path)})
	assert "notes" not in catalog.agents and "notes" not in catalog.problems


def test_config_without_script_is_reported(tmp_path: Path) -> None:
	directory = tmp_path / "noscript"
	directory.mkdir()
	(directory / "agent.toml").write_text('description = "x"\n', encoding="utf-8")
	catalog = discover_agents(environ={AGENT_DIRS_ENV: str(tmp_path)})
	assert "has no agent.sh" in catalog.problems["noscript"]


def test_invalid_agent_name_is_reported(tmp_path: Path) -> None:
	_write_agent(tmp_path, "Bad Name")
	catalog = discover_agents(environ={AGENT_DIRS_ENV: str(tmp_path)})
	assert any("names must match" in problem for problem in catalog.problems.values())


# agent.toml validation


@pytest.mark.parametrize(
	("config", "message"),
	[
		("unknown_key = 1\n", "unknown_key"),
		('trace_format = "html"\n', "trace_format"),
		('[[secrets]]\nname = "PATH"\n', "set by AEBench"),
		('[[secrets]]\nname = "AEBENCH_MODEL"\n', "set by AEBench"),
		('[[secrets]]\nname = "BAD-NAME"\n', "invalid environment variable"),
		('[[files]]\ntarget = "../escape"\nsources = ["~/x"]\n', "relative path"),
		('[[files]]\ntarget = "/abs"\nsources = ["~/x"]\n', "relative path"),
		('[[files]]\ntarget = "x"\nsources = []\n', "sources"),
		('trace_converter = "missing.py"\n', "does not exist"),
		('trace_converter = "../x.py"\n', "inside the agent directory"),
		("description = \n", "invalid TOML"),
	],
)
def test_config_errors_name_the_problem(tmp_path: Path, config: str, message: str) -> None:
	directory = _write_agent(tmp_path, "agent", config)
	with pytest.raises(AgentConfigError, match=message) as caught:
		load_agent_dir(directory)
	assert str(directory / "agent.toml") in str(caught.value) or "agent.sh" in str(caught.value)


# Credentials


def test_secret_reads_the_first_set_variable(tmp_path: Path) -> None:
	agent = load_agent_dir(
		_write_agent(tmp_path, "a", '[[secrets]]\nname = "KEY"\nfrom_env = ["FIRST", "SECOND"]\n')
	)
	assert resolve_secrets(agent, environ={"FIRST": "", "SECOND": "two"}) == {"KEY": "two"}
	assert resolve_secrets(agent, environ={"FIRST": "one", "SECOND": "two"}) == {"KEY": "one"}


def test_secret_falls_back_to_a_file(tmp_path: Path) -> None:
	agent = load_agent_dir(
		_write_agent(tmp_path, "a", '[[secrets]]\nname = "KEY"\nfrom_file_env = "KEY_FILE"\n')
	)
	token = tmp_path / "token"
	token.write_text("  from-file \n", encoding="utf-8")
	assert resolve_secrets(agent, environ={"KEY_FILE": str(token)}) == {"KEY": "from-file"}
	with pytest.raises(AgentConfigError, match="cannot read KEY"):
		resolve_secrets(agent, environ={"KEY_FILE": str(tmp_path / "missing")})


def test_missing_secrets_are_all_reported_with_hints(tmp_path: Path) -> None:
	agent = load_agent_dir(
		_write_agent(
			tmp_path,
			"a",
			'[[secrets]]\nname = "A"\nhint = "export A"\n[[secrets]]\nname = "B"\n',
		)
	)
	with pytest.raises(AgentConfigError, match="missing A: export A; B"):
		resolve_secrets(agent, environ={})


def test_file_uses_the_first_candidate_whose_variables_are_set(tmp_path: Path) -> None:
	agent = load_agent_dir(
		_write_agent(
			tmp_path,
			"a",
			'[[files]]\ntarget = "cfg/auth.json"\n'
			'sources = ["${OVERRIDE}", "${HOME_DIR}/auth.json"]\nhint = "log in"\n',
		)
	)
	home = tmp_path / "home"
	home.mkdir()
	(home / "auth.json").write_text("{}", encoding="utf-8")

	assert resolve_files(agent, environ={"HOME_DIR": str(home)}) == [
		(home / "auth.json", "cfg/auth.json")
	]
	# A set override is used even when it is wrong, rather than silently skipped.
	with pytest.raises(AgentConfigError, match="nope.json.*log in"):
		resolve_files(
			agent, environ={"OVERRIDE": str(tmp_path / "nope.json"), "HOME_DIR": str(home)}
		)
	with pytest.raises(AgentConfigError, match="looked for"):
		resolve_files(agent, environ={})
