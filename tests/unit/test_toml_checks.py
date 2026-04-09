"""Tests for declarative TOML oracle check support."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from evaluator.oracles.benchmark_prep_checks import BenchmarkCommandCheck
from evaluator.oracles.discovery import (
	ARTIFACT_BUILD,
	ENV_SETUP,
	EXPERIMENT_RUNS,
	DiscoveredPhase,
	merge_toml_and_python_phases,
)
from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	EnvironmentVariableCheck,
	FilesystemPathCheck,
)
from evaluator.oracles.toml_checks import (
	DotAccessDict,
	ExpressionCheck,
	_build_template_vars,
	_load_data_file,
	_resolve,
	build_checks,
	discover_toml_phases,
)
from evaluator.oracles.utils import (
	DockerRuntimeCheckExecutor,
	ProcResult,
	RuntimeCheckExecutor,
	SessionRuntimeCheckExecutor,
	build_runtime_check_executor,
)
from models import (
	AgentResult,
	CommandCheckDecl,
	EnvVarCheckDecl,
	ExprCheckDecl,
	OracleConfig,
	OracleInput,
	PathCheckDecl,
	PhaseChecksConfig,
	PromptProfile,
	RunResult,
	RuntimeInfo,
	RuntimeMode,
	TaskStatus,
	VersionCheckDecl,
)
from runtime.backend import BenchRuntime, DockerRuntime
from runtime.session import RunSession


def _ctx(tmp_path: Path) -> OracleInput:
	return OracleInput(
		case_dir=tmp_path / "case",
		artifact_dir=tmp_path / "artifact",
		workspace_dir=tmp_path / "workspace",
		output_dir=tmp_path / "output",
	)


def _vars(tmp_path: Path) -> dict[str, str]:
	return _build_template_vars(_ctx(tmp_path))


def _fake_run_session(
	*,
	task_id: str = "docker_case",
	keep_committed_snapshot: bool = False,
	snapshot_timeout_seconds: float = 60.0,
) -> RunSession:
	return cast(
		RunSession,
		SimpleNamespace(
			task_id=task_id,
			run_spec=SimpleNamespace(
				runtime=SimpleNamespace(
					snapshot_timeout_seconds=snapshot_timeout_seconds,
					keep_committed_snapshot=keep_committed_snapshot,
				)
			),
		),
	)


class FakeRuntimeExecutor(RuntimeCheckExecutor):
	path_separator = ":"

	def __init__(self) -> None:
		self.calls: list[tuple[str, object]] = []

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		self.calls.append(("resolve_executable", executable))
		return executable

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		self.calls.append(("read_env_var", name))
		return None

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = 16_384,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: object | None = None,
	) -> ProcResult:
		self.calls.append(("run_process_capture", cmd))
		return ProcResult(returncode=0, stdout="", stderr="", timed_out=False)

	def close(self) -> None:
		self.calls.append(("close", None))


class _FakeRuntimeBackend:
	path_separator = ":"

	def __init__(self) -> None:
		self.calls: list[tuple[str, object]] = []

	def resolve_executable(
		self,
		executable: str,
		*,
		cwd: str | None = None,
		env: dict[str, str] | None = None,
	) -> str | None:
		self.calls.append(
			("resolve_executable", {"executable": executable, "cwd": cwd, "env": env})
		)
		return executable

	def read_env_var(
		self,
		name: str,
		*,
		cwd: str | None = None,
		env: dict[str, str] | None = None,
	) -> str | None:
		self.calls.append(("read_env_var", {"name": name, "cwd": cwd, "env": env}))
		return None

	def run_process(
		self,
		cmd: list[str],
		*,
		cwd: str | None = None,
		env: dict[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float | None = None,
	) -> subprocess.CompletedProcess[str]:
		self.calls.append(
			(
				"run_process",
				{
					"cmd": cmd,
					"cwd": cwd,
					"env": env,
					"stdin_text": stdin_text,
					"timeout": timeout,
				},
			)
		)
		return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")


class _FakeSession:
	def __init__(self, workspace_dir: Path, refs_dir: Path | None = None) -> None:
		self._workspace_dir = workspace_dir.resolve()
		self._refs_dir = None if refs_dir is None else refs_dir.resolve()

	def translate_host_path(self, path: Path | None) -> str | None:
		target = (path or self._workspace_dir).resolve()
		if target == self._workspace_dir:
			return "/repo"
		try:
			relative = target.relative_to(self._workspace_dir)
			return f"/repo/{relative.as_posix()}"
		except ValueError:
			pass
		if self._refs_dir is not None:
			if target == self._refs_dir:
				return "/refs"
			try:
				relative = target.relative_to(self._refs_dir)
				return f"/refs/{relative.as_posix()}"
			except ValueError:
				pass
		return str(target)


def test_build_template_vars_keys(tmp_path: Path) -> None:
	variables = _vars(tmp_path)
	assert set(variables.keys()) == {
		"case_dir",
		"artifact_dir",
		"workspace_dir",
		"output_dir",
		"refs_dir",
	}


def test_resolve_simple(tmp_path: Path) -> None:
	variables = _vars(tmp_path)
	result = _resolve("{workspace_dir}/foo.txt", variables)
	assert result.endswith("/foo.txt")
	assert "workspace" in result


def test_resolve_unknown_variable() -> None:
	with pytest.raises(ValueError, match="unknown template variable"):
		_resolve("{nonexistent}/foo", {})


def test_resolve_no_template() -> None:
	assert _resolve("/absolute/path", {}) == "/absolute/path"


def test_version_check_decl_requires_3_elements() -> None:
	with pytest.raises(Exception, match="exactly 3 elements"):
		VersionCheckDecl(name="bad", cmd=["python3"], required_version=[3, 10])


def test_version_check_decl_rejects_negative() -> None:
	with pytest.raises(Exception, match="non-negative"):
		VersionCheckDecl(name="bad", cmd=["python3"], required_version=[3, -1, 0])


def test_oracle_config_has_toml_checks_false() -> None:
	config = OracleConfig()
	assert not config.has_toml_checks


def test_oracle_config_has_toml_checks_true() -> None:
	config = OracleConfig(
		env_setup=PhaseChecksConfig(
			checks=[
				PathCheckDecl(name="test", path="/tmp/test"),
			]
		)
	)
	assert config.has_toml_checks


def test_oracle_config_extra_forbid() -> None:
	with pytest.raises(Exception):
		PathCheckDecl(name="test", path="/tmp", unknown_field="bad")  # type: ignore[call-arg]


def test_build_path_check(tmp_path: Path) -> None:
	decl = PathCheckDecl(name="foo", path="{workspace_dir}/foo", path_type="file")
	checks = build_checks([decl], _vars(tmp_path))
	assert len(checks) == 1
	check = checks[0]
	assert isinstance(check, FilesystemPathCheck)
	assert check.name == "foo"
	assert str(check.path).endswith("/foo")


def test_build_version_check(tmp_path: Path) -> None:
	decl = VersionCheckDecl(
		name="python", cmd=["python3", "--version"], required_version=[3, 10, 0]
	)
	checks = build_checks([decl], _vars(tmp_path))
	assert len(checks) == 1
	check = checks[0]
	assert isinstance(check, DependencyVersionCheck)
	assert check.required_version == (3, 10, 0)


def test_build_env_var_check(tmp_path: Path) -> None:
	decl = EnvVarCheckDecl(name="home", env_var="HOME", expected="/home/user")
	checks = build_checks([decl], _vars(tmp_path))
	assert len(checks) == 1
	assert isinstance(checks[0], EnvironmentVariableCheck)


def test_build_command_check(tmp_path: Path) -> None:
	decl = CommandCheckDecl(name="echo", cmd=["echo", "hello"], cwd="{workspace_dir}")
	checks = build_checks([decl], _vars(tmp_path))
	assert len(checks) == 1
	assert isinstance(checks[0], BenchmarkCommandCheck)


def test_build_checks_propagates_runtime_executor(tmp_path: Path) -> None:
	executor = FakeRuntimeExecutor()
	ctx = _ctx(tmp_path)
	ctx.runtime_executor = executor
	checks = build_checks(
		[
			VersionCheckDecl(
				name="python",
				cmd=["python3", "--version"],
				required_version=[3, 10, 0],
			),
			EnvVarCheckDecl(name="home", env_var="HOME", expected="/home/user"),
			CommandCheckDecl(name="echo", cmd=["echo", "hello"], cwd="{workspace_dir}"),
		],
		_build_template_vars(ctx),
		executor=ctx.runtime_executor,
	)
	assert isinstance(checks[0], DependencyVersionCheck)
	assert checks[0].executor is executor
	assert isinstance(checks[1], EnvironmentVariableCheck)
	assert checks[1].executor is executor
	assert isinstance(checks[2], BenchmarkCommandCheck)
	assert checks[2].executor is executor


def test_build_expr_check(tmp_path: Path) -> None:
	decl = ExprCheckDecl(name="simple", expr="1 + 1 == 2", observed="{workspace_dir}/data.json")
	checks = build_checks([decl], _vars(tmp_path))
	assert len(checks) == 1
	assert isinstance(checks[0], ExpressionCheck)


def test_build_check_optional_flag(tmp_path: Path) -> None:
	decl = PathCheckDecl(name="opt", path="/tmp/x", optional=True)
	checks = build_checks([decl], _vars(tmp_path))
	assert checks[0].optional is True


# DotAccessDict


def test_dot_access_dict_simple() -> None:
	d = DotAccessDict({"a": 1, "b": "hello"})
	assert d.a == 1
	assert d.b == "hello"


def test_dot_access_dict_nested() -> None:
	d = DotAccessDict({"results": {"throughput": [1.0, 2.0, 3.0]}})
	assert d.results.throughput == [1.0, 2.0, 3.0]


def test_dot_access_dict_missing_key() -> None:
	d = DotAccessDict({"a": 1})
	with pytest.raises(AttributeError, match="no field"):
		_ = d.missing


def test_build_runtime_check_executor_uses_live_runtime_backend(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	workspace_dir = ctx.workspace_dir
	workspace_dir.mkdir(parents=True)
	backend = _FakeRuntimeBackend()
	ctx.runtime_session = _FakeSession(workspace_dir)
	ctx.runtime_backend = backend
	executor = build_runtime_check_executor(ctx)
	assert isinstance(executor, SessionRuntimeCheckExecutor)
	result = executor.run_process_capture(
		cmd=("python3", "--version"),
		cwd=workspace_dir / "subdir",
		env=None,
		timeout_seconds=5.0,
	)
	assert result.returncode == 0
	assert backend.calls[0][0] == "run_process"
	payload = backend.calls[0][1]
	assert isinstance(payload, dict)
	assert payload["cwd"] == "/repo/subdir"


def test_build_runtime_check_executor_prefers_saved_image(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	now = datetime.now(timezone.utc)
	ctx.runtime_result = RunResult(
		id="docker_case",
		status=TaskStatus.SUCCESS,
		started_at=now,
		finished_at=now,
		duration_ms=0,
		workspace_path=str(ctx.workspace_dir),
		output_dir=str(ctx.output_dir),
		summary_path=str(ctx.output_dir / "summary.txt"),
		prompt_profile=PromptProfile.ARTIFACT_EVAL_V1,
		runtime=RuntimeInfo(
			mode=RuntimeMode.DOCKER,
			image="ghcr.io/example/base:latest",
			container_id="abc123",
			saved_image="aebench-oracle-snapshots:test",
			container_stopped=False,
		),
		agent_kind="mock",
		agent=AgentResult(model="mock", exit_code=0),
	)
	ctx.runtime_session = _FakeSession(ctx.workspace_dir)
	ctx.runtime_backend = _FakeRuntimeBackend()
	executor = build_runtime_check_executor(ctx)
	assert isinstance(executor, DockerRuntimeCheckExecutor)


def test_session_runtime_check_executor_rejects_string_cmd_without_shell(tmp_path: Path) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	executor = SessionRuntimeCheckExecutor(
		session=_FakeSession(workspace_dir),
		runtime_backend=cast(BenchRuntime, _FakeRuntimeBackend()),
	)
	with pytest.raises(TypeError, match="sequence cmd"):
		executor.run_process_capture(
			cmd="python3 --version",
			cwd=workspace_dir,
			env=None,
			timeout_seconds=5.0,
			use_shell=False,
		)


def test_docker_runtime_check_executor_rejects_sequence_cmd_with_shell(tmp_path: Path) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	executor = DockerRuntimeCheckExecutor(
		default_cwd=workspace_dir,
		workspace_dir=workspace_dir,
		refs_dir=None,
		image="example:latest",
	)
	with pytest.raises(TypeError, match="string cmd"):
		executor.run_process_capture(
			cmd=("python3", "--version"),
			cwd=workspace_dir,
			env=None,
			timeout_seconds=5.0,
			use_shell=True,
		)


def test_docker_runtime_check_executor_read_env_var_uses_printenv(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	executor = DockerRuntimeCheckExecutor(
		default_cwd=workspace_dir,
		workspace_dir=workspace_dir,
		refs_dir=None,
		image="example:latest",
	)
	seen: dict[str, object] = {}

	def _fake_docker_exec(
		*,
		cmd: list[str],
		cwd: Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
	) -> subprocess.CompletedProcess[str]:
		seen["cmd"] = cmd
		seen["cwd"] = cwd
		seen["env"] = dict(env or {})
		seen["timeout_seconds"] = timeout_seconds
		return subprocess.CompletedProcess(cmd, 0, stdout="3.11.9\n", stderr="")

	monkeypatch.setattr(executor, "_docker_exec", _fake_docker_exec)
	value = executor.read_env_var("PYTHON_VERSION", env={"FOO": "bar"})
	assert value == "3.11.9"
	assert seen["cmd"] == ["printenv", "PYTHON_VERSION"]
	assert seen["env"] == {"FOO": "bar"}


def test_docker_runtime_check_executor_read_env_var_rejects_invalid_name(
	tmp_path: Path,
) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	executor = DockerRuntimeCheckExecutor(
		default_cwd=workspace_dir,
		workspace_dir=workspace_dir,
		refs_dir=None,
		image="example:latest",
	)
	with pytest.raises(ValueError, match="invalid environment variable name"):
		executor.read_env_var("BAD-NAME")


def test_docker_runtime_snapshot_uses_configured_timeout(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	docker_runtime = DockerRuntime()
	docker_runtime.container_id = "container-123"
	calls: list[dict[str, Any]] = []

	def _fake_run(
		cmd: list[str],
		*,
		capture_output: bool,
		text: bool,
		timeout: float | None = None,
		check: bool,
	) -> subprocess.CompletedProcess[str]:
		calls.append({"cmd": cmd, "timeout": timeout})
		return subprocess.CompletedProcess(cmd, 0, stdout="image-id\n", stderr="")

	monkeypatch.setattr(subprocess, "run", _fake_run)
	session = _fake_run_session(snapshot_timeout_seconds=12.5)
	saved_image = docker_runtime.snapshot(session)
	assert saved_image is not None
	assert calls[0]["cmd"][:2] == ["docker", "commit"]
	assert calls[0]["timeout"] == 12.5


def test_docker_runtime_cleanup_removes_snapshot_by_default(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	docker_runtime = DockerRuntime()
	docker_runtime.container_id = "container-123"
	docker_runtime.container_name = "container-name"
	docker_runtime.saved_image = "aebench-oracle-snapshots:test"
	commands: list[list[str]] = []

	def _fake_run(
		cmd: list[str],
		*,
		capture_output: bool,
		text: bool,
		check: bool,
	) -> subprocess.CompletedProcess[str]:
		commands.append(cmd)
		return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

	monkeypatch.setattr(subprocess, "run", _fake_run)
	session = _fake_run_session(keep_committed_snapshot=False)
	docker_runtime.cleanup(session)
	assert ["docker", "rm", "-f", "container-123"] in commands
	assert ["docker", "rmi", "-f", "aebench-oracle-snapshots:test"] in commands
	assert docker_runtime.saved_image is None


def test_docker_runtime_cleanup_keeps_snapshot_when_requested(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	workspace_dir = tmp_path / "workspace"
	workspace_dir.mkdir(parents=True)
	docker_runtime = DockerRuntime()
	docker_runtime.container_id = "container-123"
	docker_runtime.saved_image = "aebench-oracle-snapshots:test"
	commands: list[list[str]] = []

	def _fake_run(
		cmd: list[str],
		*,
		capture_output: bool,
		text: bool,
		check: bool,
	) -> subprocess.CompletedProcess[str]:
		commands.append(cmd)
		return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

	monkeypatch.setattr(subprocess, "run", _fake_run)
	session = _fake_run_session(keep_committed_snapshot=True)
	docker_runtime.cleanup(session)
	assert ["docker", "rm", "-f", "container-123"] in commands
	assert all(cmd[:2] != ["docker", "rmi"] for cmd in commands)
	assert docker_runtime.saved_image == "aebench-oracle-snapshots:test"


def test_load_json_dict(tmp_path: Path) -> None:
	p = tmp_path / "data.json"
	p.write_text(json.dumps({"throughput": [10.0, 20.0], "latency": 5.0}))
	result = _load_data_file(p)
	assert result.throughput == [10.0, 20.0]
	assert result.latency == 5.0


def test_load_json_non_dict(tmp_path: Path) -> None:
	p = tmp_path / "data.json"
	p.write_text(json.dumps([1, 2, 3]))
	result = _load_data_file(p)
	assert result._root == [1, 2, 3]


def test_load_csv(tmp_path: Path) -> None:
	p = tmp_path / "data.csv"
	p.write_text("throughput,latency\n10.0,1.0\n20.0,2.0\n")
	result = _load_data_file(p)
	assert result.throughput == [10.0, 20.0]
	assert result.latency == [1.0, 2.0]


def test_load_unsupported_format(tmp_path: Path) -> None:
	p = tmp_path / "data.xml"
	p.write_text("<data/>")
	with pytest.raises(ValueError, match="unsupported"):
		_load_data_file(p)


def test_expr_check_pass(tmp_path: Path) -> None:
	p = tmp_path / "data.json"
	p.write_text(json.dumps({"values": [1.0, 2.0, 3.0]}))
	check = ExpressionCheck(name="avg", expr="avg(obs.values) == 2.0", observed_path=p)
	result = check.check()
	assert result.ok


def test_expr_check_fail(tmp_path: Path) -> None:
	p = tmp_path / "data.json"
	p.write_text(json.dumps({"values": [1.0, 2.0, 3.0]}))
	check = ExpressionCheck(name="avg", expr="avg(obs.values) > 100", observed_path=p)
	result = check.check()
	assert not result.ok
	assert "False" in result.message


def test_expr_check_with_reference(tmp_path: Path) -> None:
	obs_path = tmp_path / "observed.json"
	ref_path = tmp_path / "reference.json"
	obs_path.write_text(json.dumps({"throughput": [9.0, 10.0, 11.0]}))
	ref_path.write_text(json.dumps({"throughput": [10.0, 10.0, 10.0]}))
	check = ExpressionCheck(
		name="compare",
		expr="avg(obs.throughput) >= 0.95 * avg(ref.throughput)",
		observed_path=obs_path,
		reference_path=ref_path,
	)
	result = check.check()
	assert result.ok


def test_expr_check_supports_item_access_for_dunder_keys(tmp_path: Path) -> None:
	obs_path = tmp_path / "observed.json"
	ref_path = tmp_path / "reference.json"
	obs_path.write_text(
		json.dumps(
			{
				"kernel_uprobe": {"__bench_uprobe": {"avg": 11.0}},
				"userspace_uprobe": {"__bench_uprobe": {"avg": 1.0}},
			}
		)
	)
	ref_path.write_text(json.dumps({"ops": {"__bench_uprobe": 13.48}}))
	check = ExpressionCheck(
		name="dunder_keys",
		expr=('obs.kernel_uprobe["__bench_uprobe"].avg >= ref.ops["__bench_uprobe"] * 0.8'),
		observed_path=obs_path,
		reference_path=ref_path,
	)
	result = check.check()
	assert result.ok


def test_expr_check_missing_file() -> None:
	check = ExpressionCheck(name="missing", expr="True", observed_path=Path("/nonexistent.json"))
	result = check.check()
	assert not result.ok
	assert "failed to load" in result.message


def test_expr_check_non_bool_result(tmp_path: Path) -> None:
	p = tmp_path / "data.json"
	p.write_text(json.dumps({"x": 42}))
	check = ExpressionCheck(name="num", expr="obs.x + 1", observed_path=p)
	result = check.check()
	assert not result.ok
	assert "must evaluate to bool" in result.message


def test_expr_check_syntax_error() -> None:
	check = ExpressionCheck(name="bad", expr="if True then False")
	result = check.check()
	assert not result.ok
	assert "evaluation failed" in result.message


def test_expr_check_no_files() -> None:
	check = ExpressionCheck(name="literal", expr="1 + 1 == 2")
	result = check.check()
	assert result.ok


def test_expr_check_builtin_functions(tmp_path: Path) -> None:
	p = tmp_path / "data.json"
	p.write_text(json.dumps({"vals": [3.0, 1.0, 2.0]}))
	for fn_expr, expected in [
		("sum(obs.vals) == 6.0", True),
		("min(obs.vals) == 1.0", True),
		("max(obs.vals) == 3.0", True),
		("len(obs.vals) == 3", True),
		("median(obs.vals) == 2.0", True),
		("count(obs.vals) == 3", True),
	]:
		check = ExpressionCheck(name="fn", expr=fn_expr, observed_path=p)
		result = check.check()
		assert result.ok == expected, f"Failed: {fn_expr}"


def test_discover_toml_phases_single(tmp_path: Path) -> None:
	config = OracleConfig(
		env_setup=PhaseChecksConfig(
			checks=[
				PathCheckDecl(name="test", path="/tmp/test"),
			]
		)
	)
	phases = discover_toml_phases(config, _ctx(tmp_path))
	assert len(phases) == 1
	assert phases[0].key == ENV_SETUP
	assert phases[0].qualname == "<toml:env_setup>"


def test_discover_toml_phases_multiple(tmp_path: Path) -> None:
	config = OracleConfig(
		env_setup=PhaseChecksConfig(
			checks=[
				PathCheckDecl(name="a", path="/tmp/a"),
			]
		),
		artifact_build=PhaseChecksConfig(
			checks=[
				CommandCheckDecl(name="b", cmd=["echo", "hi"]),
			]
		),
	)
	phases = discover_toml_phases(config, _ctx(tmp_path))
	assert len(phases) == 2
	assert [p.key for p in phases] == [ENV_SETUP, ARTIFACT_BUILD]


def test_discover_toml_phases_empty(tmp_path: Path) -> None:
	config = OracleConfig()
	phases = discover_toml_phases(config, _ctx(tmp_path))
	assert len(phases) == 0


def test_discover_toml_phases_requirements_callable(tmp_path: Path) -> None:
	config = OracleConfig(
		env_setup=PhaseChecksConfig(
			checks=[
				PathCheckDecl(name="test", path="{workspace_dir}/foo"),
			]
		)
	)
	phases = discover_toml_phases(config, _ctx(tmp_path))
	ctx = _ctx(tmp_path)
	checks = phases[0].requirements(ctx)
	assert len(checks) == 1
	assert isinstance(checks[0], FilesystemPathCheck)


def _stub_phase(key: tuple[str, ...], qualname: str = "stub") -> DiscoveredPhase:
	from evaluator.oracles.discovery import _PHASE_PRIORITIES

	return DiscoveredPhase(
		key=key,
		priority=_PHASE_PRIORITIES[key],
		requirements=lambda context: [],
		qualname=qualname,
	)


def test_merge_python_only() -> None:
	py = [_stub_phase(ENV_SETUP, "py_env")]
	result = merge_toml_and_python_phases(py, [])
	assert len(result) == 1
	assert result[0].qualname == "py_env"


def test_merge_toml_only() -> None:
	toml = [_stub_phase(ARTIFACT_BUILD, "toml_build")]
	result = merge_toml_and_python_phases([], toml)
	assert len(result) == 1
	assert result[0].qualname == "toml_build"


def test_merge_disjoint() -> None:
	py = [_stub_phase(ENV_SETUP, "py_env")]
	toml = [_stub_phase(ARTIFACT_BUILD, "toml_build")]
	result = merge_toml_and_python_phases(py, toml)
	assert len(result) == 2
	assert [p.key for p in result] == [ENV_SETUP, ARTIFACT_BUILD]


def test_merge_same_phase_chains(tmp_path: Path) -> None:
	from evaluator.oracles.discovery import _PHASE_PRIORITIES
	from evaluator.oracles.env_setup_checks import FilesystemPathCheck

	def py_req(context: OracleInput) -> list[FilesystemPathCheck]:
		return [FilesystemPathCheck(name="py_check", path=Path("/py"))]

	def toml_req(context: OracleInput) -> list[FilesystemPathCheck]:
		return [FilesystemPathCheck(name="toml_check", path=Path("/toml"))]

	py_phase = DiscoveredPhase(
		key=ENV_SETUP,
		priority=_PHASE_PRIORITIES[ENV_SETUP],
		requirements=py_req,
		qualname="py_env",
	)
	toml_phase = DiscoveredPhase(
		key=ENV_SETUP,
		priority=_PHASE_PRIORITIES[ENV_SETUP],
		requirements=toml_req,
		qualname="toml_env",
	)

	result = merge_toml_and_python_phases([py_phase], [toml_phase])
	assert len(result) == 1
	assert "py_env" in result[0].qualname
	assert "toml_env" in result[0].qualname

	ctx = _ctx(tmp_path)
	checks = result[0].requirements(ctx)
	assert len(checks) == 2
	assert checks[0].name == "py_check"
	assert checks[1].name == "toml_check"


def test_merge_preserves_priority_order() -> None:
	py = [_stub_phase(EXPERIMENT_RUNS, "py_exp")]
	toml = [_stub_phase(ENV_SETUP, "toml_env")]
	result = merge_toml_and_python_phases(py, toml)
	assert [p.key for p in result] == [ENV_SETUP, EXPERIMENT_RUNS]


def test_oracle_config_parses_toml_checks() -> None:
	data = {
		"phases": ["env_setup"],
		"env_setup": {
			"checks": [
				{"type": "path", "name": "test", "path": "/tmp/test"},
				{
					"type": "version",
					"name": "py",
					"cmd": ["python3", "--version"],
					"required_version": [3, 10, 0],
				},
			]
		},
	}
	config = OracleConfig.model_validate(data)
	assert len(config.env_setup.checks) == 2
	assert config.has_toml_checks


def test_oracle_config_parses_expr_check() -> None:
	data = {
		"experiment_runs": {
			"checks": [
				{
					"type": "expr",
					"name": "throughput",
					"expr": "avg(obs.throughput) >= 0.95",
					"observed": "{workspace_dir}/results.json",
				}
			]
		},
	}
	config = OracleConfig.model_validate(data)
	assert len(config.experiment_runs.checks) == 1
	check = config.experiment_runs.checks[0]
	assert isinstance(check, ExprCheckDecl)
	assert check.expr == "avg(obs.throughput) >= 0.95"
