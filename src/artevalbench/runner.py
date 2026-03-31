from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from contextlib import suppress
from pathlib import Path

import anyio

from .application.session import DriverSession, TaskArtifacts, WorkspacePaths
from .constants import default_timeout_ms
from .display import DisplayEvent, DisplayKind, DisplayPanel
from .domain.models import (
 AgentRequest,
 AgentResult,
 AgentSessionContext,
 PromptContext,
 PromptProfile,
 PromptSpec,
 RunSpec,
 RuntimeMode,
 RuntimeSpec,
)
from .runtime.agent_drivers import AgentDriver, build_agent_driver
from .runtime.bridge import BridgePaths
from .runtime.config import ResolvedAgentConfig, ResolvedSettings
from .runtime.events import EventSink, JsonlEventSink, NullEventSink
from .prompting import build_container_interactive_prompt, build_prompt_bundle
from .reporting import task_paths_for
from .settings import AgentKind, McpClientKind, McpTopology, get_settings


def build_system_prompt(
 task: str,
 *,
 runtime_mode: RuntimeMode = RuntimeMode.DOCKER,
 workspace_path: str = ".",
 timeout_ms: int | None = None,
 interactive: bool = False,
 prompt_profile: PromptProfile = PromptProfile.ARTIFACT_EVAL_V1,
 prompt_append: str | None = None,
) -> str:
	resolved_timeout_ms = timeout_ms if timeout_ms is not None else default_timeout_ms()
	bundle = build_prompt_bundle(
	 PromptContext(
	  task_text=task,
	  workspace_path=workspace_path,
	  runtime_mode=runtime_mode,
	  timeout_ms=resolved_timeout_ms,
	  interactive=interactive,
	  prompt_profile=prompt_profile,
	  prompt_append=prompt_append,
	 )
	)
	return bundle.system_prompt


async def run_agent(
 model_name: str,
 *,
 system_prompt: str,
 initial_prompt: str,
 interactive: bool = False,
 cwd: str | None = None,
 add_dirs: list[str] | None = None,
 use_sdk_sandbox: bool = False,
 driver_kind: str = AgentKind.CLAUDE_SDK.value,
 driver_options: dict[str, object] | None = None,
 sink: EventSink | None = None,
) -> AgentResult:
	request = AgentRequest(
	 model=model_name,
	 system_prompt=system_prompt,
	 initial_prompt=initial_prompt,
	 interactive=interactive,
	 driver_kind=driver_kind,
	 driver_options=dict(driver_options or {}),
	 cwd=cwd,
	 add_dirs=list(add_dirs or []),
	 use_sdk_sandbox=use_sdk_sandbox,
	)
	session = _build_inline_session(request)
	driver = build_agent_driver(kind=driver_kind, options=request.driver_options)
	active_sink = sink or NullEventSink()
	await driver.prepare(session, active_sink)
	try:
		return await driver.execute(request, session, active_sink)
	finally:
		await driver.cleanup(session, active_sink)


def docker_main() -> None:
	raise SystemExit(driver_main())


def driver_main(args: argparse.Namespace | None = None) -> int:
	parsed = args or _parse_args()
	if not parsed.request_file or not parsed.session_file or not parsed.result_file:
		raise SystemExit("runner requires --request-file, --session-file, and --result-file")
	request = AgentRequest.model_validate_json(
	 Path(parsed.request_file).read_text(encoding="utf-8")
	)
	session_context = AgentSessionContext.model_validate_json(
	 Path(parsed.session_file).read_text(encoding="utf-8")
	)
	driver_kind = parsed.driver or request.driver_kind
	settings = _resolved_settings(request, driver_kind=driver_kind)
	session = _build_bridge_session(
	 request=request,
	 session_context=session_context,
	 settings=settings,
	 request_file=Path(parsed.request_file),
	 session_file=Path(parsed.session_file),
	 result_file=Path(parsed.result_file),
	)
	sink = (
	 JsonlEventSink(Path(session_context.event_stream_path))
	 if session_context.event_stream_path
	 else NullEventSink()
	)
	stop_state = _start_stop_monitor(session_context.stop_state_path, sink, session_context.task_id)
	driver = build_agent_driver(kind=driver_kind, options=request.driver_options)
	try:
		result = anyio.run(_run_driver, driver, request, session, sink)
		Path(parsed.result_file).write_text(result.model_dump_json(indent=2), encoding="utf-8")
		return result.exit_code
	finally:
		if stop_state is not None:
			stop_state.stop_event.set()


def container_interactive_system_prompt() -> str:
	return build_container_interactive_prompt()


async def _run_driver(
 driver: AgentDriver,
 request: AgentRequest,
 session: DriverSession,
 sink: EventSink,
) -> AgentResult:
	await driver.prepare(session, sink)
	try:
		return await driver.execute(request, session, sink)
	finally:
		await driver.cleanup(session, sink)


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="ArtEvalBench driver runner")
	parser.add_argument("--driver")
	parser.add_argument("--request-file")
	parser.add_argument("--session-file")
	parser.add_argument("--result-file")
	return parser.parse_args()


def _build_inline_session(request: AgentRequest) -> DriverSession:
	settings = _resolved_settings(request, driver_kind=request.driver_kind)
	workspace_path = Path(request.cwd or Path.cwd()).resolve()
	host_refs = _path_or_none(request.add_dirs[1]) if len(request.add_dirs) > 1 else None
	task_paths = task_paths_for(workspace_path, "inline")
	bridge_dir = (workspace_path / ".artevalbench-driver" / "inline").resolve()
	bridge_dir.mkdir(parents=True, exist_ok=True)
	bridge_paths = BridgePaths(
	 host_dir=bridge_dir,
	 runtime_dir=str(bridge_dir),
	 request_host=bridge_dir / "request.json",
	 request_runtime=str(bridge_dir / "request.json"),
	 session_host=bridge_dir / "session.json",
	 session_runtime=str(bridge_dir / "session.json"),
	 result_host=bridge_dir / "result.json",
	 result_runtime=str(bridge_dir / "result.json"),
	 event_host=bridge_dir / "events.jsonl",
	 event_runtime=str(bridge_dir / "events.jsonl"),
	 mcp_config_host=bridge_dir / "mcp-config.json",
	 mcp_config_runtime=str(bridge_dir / "mcp-config.json"),
	)
	run_spec = RunSpec(
	 id="inline-run",
	 runtime=RuntimeSpec(
	  mode=RuntimeMode.LOCAL,
	  timeout_ms=request.timeout_ms or settings.default_timeout_ms,
	  interactive=request.interactive,
	 ),
	 prompt=PromptSpec(profile=PromptProfile(settings.default_prompt_profile)),
	)
	return DriverSession(
	 run_spec=run_spec,
	 workspace=WorkspacePaths(
	  host_workspace=workspace_path,
	  runtime_workspace=str(workspace_path),
	  host_refs=host_refs,
	  runtime_refs=str(host_refs) if host_refs is not None else None,
	 ),
	 artifacts=TaskArtifacts(
	  output_dir=workspace_path,
	  task_paths=task_paths,
	  summary_path=workspace_path / "artevalbench_summary_inline-run.md",
	  bridge_paths=bridge_paths,
	 ),
	 prompt=build_prompt_bundle(
	  PromptContext(
	   task_text=request.initial_prompt,
	   workspace_path=str(workspace_path),
	   runtime_mode=RuntimeMode.LOCAL,
	   timeout_ms=request.timeout_ms or settings.default_timeout_ms,
	   interactive=request.interactive,
	   prompt_profile=PromptProfile(settings.default_prompt_profile),
	  )
	 ),
	 settings=settings,
	 run_control=None,
	)


def _build_bridge_session(
 *,
 request: AgentRequest,
 session_context: AgentSessionContext,
 settings: ResolvedSettings,
 request_file: Path,
 session_file: Path,
 result_file: Path,
) -> DriverSession:
	bridge_dir = request_file.resolve().parent
	event_file = (
	 Path(session_context.event_stream_path).resolve()
	 if session_context.event_stream_path
	 else bridge_dir / "events.jsonl"
	)
	workspace_path = Path(session_context.workspace_path).resolve()
	host_refs = _path_or_none(session_context.refs_path)
	run_spec = RunSpec(
	 id=session_context.task_id,
	 runtime=RuntimeSpec(
	  mode=RuntimeMode.LOCAL,
	  timeout_ms=session_context.timeout_ms,
	  interactive=request.interactive,
	 ),
	 prompt=PromptSpec(profile=session_context.prompt_profile),
	)
	return DriverSession(
	 run_spec=run_spec,
	 workspace=WorkspacePaths(
	  host_workspace=workspace_path,
	  runtime_workspace=str(workspace_path),
	  host_refs=host_refs,
	  runtime_refs=str(host_refs) if host_refs is not None else None,
	 ),
	 artifacts=TaskArtifacts(
	  output_dir=Path(session_context.output_dir).resolve(),
	  task_paths=task_paths_for(
	   Path(session_context.output_dir).resolve(), session_context.task_id
	  ),
	  summary_path=Path(session_context.summary_path).resolve(),
	  bridge_paths=BridgePaths(
	   host_dir=bridge_dir,
	   runtime_dir=str(bridge_dir),
	   request_host=request_file.resolve(),
	   request_runtime=str(request_file.resolve()),
	   session_host=session_file.resolve(),
	   session_runtime=str(session_file.resolve()),
	   result_host=result_file.resolve(),
	   result_runtime=str(result_file.resolve()),
	   event_host=event_file,
	   event_runtime=str(event_file),
	   mcp_config_host=bridge_dir / "mcp-config.json",
	   mcp_config_runtime=str(bridge_dir / "mcp-config.json"),
	  ),
	 ),
	 prompt=build_prompt_bundle(
	  PromptContext(
	   task_text=request.initial_prompt,
	   workspace_path=str(workspace_path),
	   runtime_mode=RuntimeMode.LOCAL,
	   timeout_ms=session_context.timeout_ms,
	   interactive=request.interactive,
	   prompt_profile=session_context.prompt_profile,
	  )
	 ),
	 settings=settings,
	 run_control=None,
	)


def _resolved_settings(request: AgentRequest, *, driver_kind: str) -> ResolvedSettings:
	app_settings = get_settings()
	options = dict(request.driver_options)
	kind = AgentKind(driver_kind)
	return ResolvedSettings(
	 default_timeout_ms=app_settings.default_timeout_ms,
	 default_docker_image=app_settings.default_docker_image,
	 default_model=app_settings.default_model,
	 default_prompt_profile=app_settings.default_prompt_profile,
	 default_outputs_dir=app_settings.default_outputs_dir,
	 ephemeral_workspace_root=Path(app_settings.ephemeral_workspace_root).expanduser().resolve(),
	 preserve_failed_workspace=app_settings.preserve_failed_workspace,
	 log_level=app_settings.log_level,
	 log_renderer=app_settings.log_renderer,
	 agent=ResolvedAgentConfig(
	  kind=kind,
	  max_buffer_size=_int_option(
	   options, "max_buffer_size", app_settings.agent_max_buffer_size
	  ),
	  claude_sdk_base_url=_str_option(
	   options, "claude_sdk_base_url", app_settings.anthropic_base_url
	  ),
	  api_key=app_settings.anthropic_api_key,
	  python_target=_str_option(options, "python_target", app_settings.agent_python_target),
	  cli_argv=_list_option(options, "cli_argv", app_settings.cli_argv()),
	  cli_env=_dict_option(options, "cli_env", app_settings.cli_env()),
	  cli_shim_shells=_bool_option(
	   options, "cli_shim_shells", app_settings.agent_cli_shim_shells
	  ),
	  cli_expose_container_shell=_bool_option(
	   options,
	   "cli_expose_container_shell",
	   app_settings.agent_cli_expose_container_shell,
	  ),
	  cli_expose_host_shell=_bool_option(
	   options, "cli_expose_host_shell", app_settings.agent_cli_expose_host_shell
	  ),
	  remote_base_url=_str_option(
	   options, "remote_base_url", app_settings.agent_remote_base_url
	  ),
	  remote_auth=_str_option(options, "remote_auth", app_settings.agent_remote_auth),
	  remote_protocol=_str_option(
	   options, "remote_protocol", app_settings.agent_remote_protocol
	  )
	  or "http",
	  remote_headers=_dict_option(
	   options, "remote_headers", app_settings.agent_remote_headers or {}
	  ),
	  mcp_client=McpClientKind(
	   _str_option(
	    options,
	    "mcp_client",
	    (app_settings.agent_mcp_client or McpClientKind.CLAUDE_CODE).value,
	   )
	   or McpClientKind.CLAUDE_CODE.value
	  ),
	  mcp_argv=_list_option(options, "mcp_argv", app_settings.mcp_argv()),
	  mcp_env=_dict_option(options, "mcp_env", app_settings.mcp_env()),
	  mcp_topology=McpTopology(
	   _str_option(options, "mcp_topology", app_settings.agent_mcp_topology.value)
	   or app_settings.agent_mcp_topology.value
	  ),
	 ),
	)


def _path_or_none(value: str | None) -> Path | None:
	if not value:
		return None
	return Path(value).resolve()


def _str_option(options: dict[str, object], key: str, default: str | None) -> str | None:
	value = options.get(key)
	return value if isinstance(value, str) else default


def _int_option(options: dict[str, object], key: str, default: int) -> int:
	value = options.get(key)
	return value if isinstance(value, int) else default


def _bool_option(options: dict[str, object], key: str, default: bool) -> bool:
	value = options.get(key)
	return value if isinstance(value, bool) else default


def _list_option(options: dict[str, object], key: str, default: list[str]) -> list[str]:
	value = options.get(key)
	if isinstance(value, list):
		return [item for item in value if isinstance(item, str)]
	return list(default)


def _dict_option(
 options: dict[str, object],
 key: str,
 default: dict[str, str],
) -> dict[str, str]:
	value = options.get(key)
	if isinstance(value, dict):
		return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
	return dict(default)


class _StopMonitorState:
	def __init__(self, stop_event: threading.Event) -> None:
		self.stop_event = stop_event


def _start_stop_monitor(
 stop_path_raw: str | None,
 sink: EventSink,
 task_id: str,
) -> _StopMonitorState | None:
	if not stop_path_raw:
		return None
	stop_path = Path(stop_path_raw)
	stop_event = threading.Event()

	def _watch() -> None:
		while not stop_event.wait(0.1):
			if not stop_path.exists():
				continue
			sink.emit(
			 DisplayEvent(
			  case_id=task_id,
			  kind=DisplayKind.STATUS.value,
			  panel=DisplayPanel.INFRA.value,
			  text="Runner stop requested; terminating runner process group",
			  is_error=True,
			 )
			)
			time.sleep(0.05)
			with suppress(Exception):
				os.killpg(os.getpgrp(), signal.SIGTERM)
			return

	thread = threading.Thread(
	 target=_watch,
	 name="artevalbench-runner-stop-monitor",
	 daemon=True,
	)
	thread.start()
	return _StopMonitorState(stop_event)


if __name__ == "__main__":
	docker_main()
