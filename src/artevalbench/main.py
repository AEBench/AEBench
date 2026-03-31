from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from .app import run_app
from .application.benchmark_service import BenchmarkRunResult
from .cache import git_cache_status, prune_git_cache
from .case_runtime.authoring import (
 apply_authoring_answers,
 infer_case_id_default,
 is_interactive_terminal,
 print_authoring_wizard_header,
 prompt_authoring_answers,
 prompt_case_id,
)
from .case_runtime.benchmark import run_benchmark, summarize_case_outputs
from .case_runtime.content import expand_case_dirs, resolve_case_dir
from .case_runtime.loader import CaseBundleError
from .case_runtime.models import CaseRunResult, CaseStatus
from .case_runtime.registry import (
 initialize_case_bundle,
 initialize_user_config,
 initialize_workspace,
)
from .case_runtime.runner import run_case
from .case_runtime.runtime import export_case_dirs
from .case_runtime.scaffold import BundleScaffoldError, scaffold_case_bundle
from .runtime.config import AppContext, resolve_settings
from .log import configure_logging, print_console
from .models import LiveLayoutMode, LiveViewMode, PromptProfile, RunOptions, RuntimeMode, UiMode
from .project_config import ArtifactMode, ProjectConfigState, load_project_config
from .settings import LogLevel

app = typer.Typer(
 context_settings={"help_option_names": ["-h", "--help"]},
 no_args_is_help=True,
 help="ArtEvalBench runtime and case-bundle CLI.",
)
case_app = typer.Typer(
 no_args_is_help=True, help="Run, export, scaffold, and initialize case bundles."
)
cache_app = typer.Typer(no_args_is_help=True, help="Manage the shared git artifact cache.")
runtime_app = typer.Typer(no_args_is_help=True, help="Run low-level JSONL runtime tasks.")
app.add_typer(case_app, name="case")
app.add_typer(cache_app, name="cache")
app.add_typer(runtime_app, name="runtime")


def run_runtime_tasks(
 input_file: str | Path,
 model_name: str | None = None,
 save_path: str | Path | None = None,
 interactive: bool = False,
 prompt_profile: PromptProfile | str | None = None,
 prompt_append: str | None = None,
 cleanup_workspace: bool = False,
 log_level: LogLevel | str | None = None,
) -> int:
	context = _load_context(Path.cwd())
	_configure_logging(context, log_level=log_level)
	resolved_model_name = _resolve_model_name(model_name, context)
	resolved_save_path = _resolve_runtime_save_path(save_path, resolved_model_name, context)
	resolved_save_path.mkdir(parents=True, exist_ok=True)
	return run_app(
	 context=context,
	 input_file=Path(input_file).expanduser().resolve(),
	 save_path=resolved_save_path,
	 options=RunOptions(
	  model_name=resolved_model_name,
	  interactive=interactive,
	  prompt_profile=_normalize_prompt_profile(prompt_profile),
	  prompt_append=prompt_append,
	  cleanup_workspace=cleanup_workspace,
	 ),
	)


def main(
 input_file: str | Path,
 model_name: str | None = None,
 save_path: str | Path | None = None,
 interactive: bool = False,
 prompt_profile: PromptProfile | str | None = None,
 prompt_append: str | None = None,
 cleanup_workspace: bool = False,
 log_level: LogLevel | str | None = None,
) -> int:
	return run_runtime_tasks(
	 input_file=input_file,
	 model_name=model_name,
	 save_path=save_path,
	 interactive=interactive,
	 prompt_profile=prompt_profile,
	 prompt_append=prompt_append,
	 cleanup_workspace=cleanup_workspace,
	 log_level=log_level,
	)


def cli_main() -> None:
	app()


@runtime_app.command("run")
def runtime_run_command(
 input_file: Annotated[
  Path,
  typer.Option(
   "--input-file",
   "--input_file",
   "-i",
   help="Input JSONL file with task specs",
   exists=True,
   dir_okay=False,
   readable=True,
   resolve_path=True,
  ),
 ],
 save_path: Annotated[
  Path | None,
  typer.Option(
   "--save-path",
   "--save_path",
   "-o",
   help="Directory for logs, reports, and result files",
   file_okay=False,
   resolve_path=True,
  ),
 ] = None,
 model_name: Annotated[
  str | None,
  typer.Option(
   "--model-name",
   "--model_name",
   "-m",
   help="Agent model name. Falls back to artevalbench.toml [agent].default_model or env defaults.",
  ),
 ] = None,
 interactive: Annotated[
  bool,
  typer.Option("--interactive", help="Force interactive mode for all tasks"),
 ] = False,
 prompt_profile: Annotated[
  PromptProfile | None,
  typer.Option("--prompt-profile", help="Override prompt profile for all tasks"),
 ] = None,
 prompt_append: Annotated[
  str | None,
  typer.Option("--prompt-append", help="Append extra prompt rules for all tasks"),
 ] = None,
 cleanup_workspace: Annotated[
  bool,
  typer.Option(
   "--cleanup-workspace/--preserve-workspace",
   help="Delete ephemeral task workspaces after completion. Disabled by default.",
  ),
 ] = False,
 log_level: Annotated[
  LogLevel | None,
  typer.Option("--log-level", help="Override log verbosity for this invocation"),
 ] = None,
) -> None:
	"""Run low-level runtime tasks from a JSONL file."""
	raise typer.Exit(
	 run_runtime_tasks(
	  input_file=input_file,
	  model_name=model_name,
	  save_path=save_path,
	  interactive=interactive,
	  prompt_profile=prompt_profile,
	  prompt_append=prompt_append,
	  cleanup_workspace=cleanup_workspace,
	  log_level=log_level,
	 )
	)


@app.command("run")
def run_command(
 case_ids: Annotated[
  list[str] | None,
  typer.Argument(
   help="Optional case ids to run. Defaults to all official cases in bundles.json."
  ),
 ] = None,
 output_dir: Annotated[
  Path | None,
  typer.Option(
   "--output-dir",
   help="Directory for benchmark summary files. Per-case outputs still use the standard case runs layout.",
   file_okay=False,
   resolve_path=True,
  ),
 ] = None,
 model_name: Annotated[
  str | None,
  typer.Option("--model-name", "-m", help="Agent model name"),
 ] = None,
 interactive: Annotated[
  bool,
  typer.Option("--interactive", help="Force interactive mode for generated runtime tasks"),
 ] = False,
 prompt_profile: Annotated[
  PromptProfile | None,
  typer.Option("--prompt-profile", help="Override the bundle prompt profile"),
 ] = None,
 prompt_append: Annotated[
  str | None,
  typer.Option("--prompt-append", help="Append extra prompt rules for benchmark runs"),
 ] = None,
 cleanup_workspace: Annotated[
  bool,
  typer.Option(
   "--cleanup-workspace/--preserve-workspace",
   help="Delete ephemeral case workspaces after completion. Disabled by default.",
  ),
 ] = False,
 live_view: Annotated[
  LiveViewMode,
  typer.Option("--live-view", help="Live display mode for benchmark runs"),
 ] = LiveViewMode.AUTO,
 live_layout: Annotated[
  LiveLayoutMode,
  typer.Option("--live-layout", help="Live terminal layout for benchmark runs"),
 ] = LiveLayoutMode.AUTO,
 ui: Annotated[
  UiMode,
  typer.Option("--ui", help="Interactive terminal UI frontend for benchmark runs"),
 ] = UiMode.RICH,
) -> None:
	"""Run the official benchmark bundle catalog and emit benchmark-level summary files."""
	context = _load_context(Path.cwd())
	_configure_logging(context)
	try:
		result = run_benchmark(
		 case_ids or [],
		 model_name=_resolve_model_name(model_name, context),
		 interactive=interactive,
		 prompt_profile=prompt_profile,
		 prompt_append=prompt_append,
		 cleanup_workspace=cleanup_workspace,
		 live_view=live_view,
		 live_layout=live_layout,
		 ui=ui,
		 output_dir=output_dir,
		 project_state=context.project_state,
		)
	except (CaseBundleError, RuntimeError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	except KeyboardInterrupt:
		print_console("[red]Interrupted.[/red]")
		raise typer.Exit(130) from None
	print_console(
	 f"[bold]Benchmark completed.[/bold] "
	 f"phase_ratio={result.summary.phase_ratio:.3f} "
	 f"case_pass_ratio={result.summary.case_pass_ratio:.3f}"
	)
	_print_benchmark_run_details(result)
	raise typer.Exit(0 if result.all_cases_passed else 1)


def _print_benchmark_run_details(result: BenchmarkRunResult) -> None:
	print_console(f"[bold]Output dir:[/bold] {result.output_dir}")
	print_console(f"[bold]Summary JSON:[/bold] {result.summary_path}")
	print_console(f"[bold]Summary Markdown:[/bold] {result.summary_markdown_path}")
	print_console(f"[bold]Results:[/bold] {result.case_results_path}")


@app.command("init")
def init_command(
 force: Annotated[
  bool,
  typer.Option("--force", help="Overwrite the generated global user config file"),
 ] = False,
 local: Annotated[
  bool,
  typer.Option(
   "--local", help="Initialize the current directory as a local workspace instead"
  ),
 ] = False,
) -> None:
	"""Initialize global ArtEvalBench defaults, or bootstrap a local workspace with --local."""
	if local:
		workspace_result = initialize_workspace(Path.cwd(), force=force, include_local_config=True)
		if not workspace_result.created and not workspace_result.updated:
			print_console("[bold]Workspace already initialized.[/bold]")
			return
		for path in workspace_result.created:
			print_console(f"[bold]Created:[/bold] {path}")
		for path in workspace_result.updated:
			print_console(f"[bold]Updated:[/bold] {path}")
		return
	user_result = initialize_user_config(force=force)
	if not user_result.created and not user_result.updated:
		print_console("[bold]Global user config already initialized.[/bold]")
		return
	for path in user_result.created:
		print_console(f"[bold]Created:[/bold] {path}")
	for path in user_result.updated:
		print_console(f"[bold]Updated:[/bold] {path}")
	print_console(f"[bold]Config:[/bold] {user_result.config_path}")


@case_app.command("init")
def case_init_command(
 source: Annotated[
  str | None,
  typer.Argument(
   help="Artifact source: git URL, snapshot path/URL, or local directory. Omit with --blank.",
  ),
 ] = None,
 id: Annotated[
  str | None,
  typer.Option("--id", help="Override the inferred case id"),
 ] = None,
 blank: Annotated[
  bool,
  typer.Option("--blank", help="Create a blank starter bundle instead of importing a source"),
 ] = False,
 config: Annotated[
  Path | None,
  typer.Option(
   "--config",
   help="Explicit workspace config TOML to overlay on top of the discovered workspace config",
   exists=True,
   dir_okay=False,
   readable=True,
   resolve_path=True,
  ),
 ] = None,
 bundles_dir: Annotated[
  Path | None,
  typer.Option(
   "--bundles-dir",
   help="Override the bundles root directory inside the workspace",
   file_okay=False,
   resolve_path=True,
  ),
 ] = None,
 artifact_mode: Annotated[
  ArtifactMode | None,
  typer.Option("--artifact-mode", help="Bundle materialization mode"),
 ] = None,
 ref: Annotated[
  str | None,
  typer.Option("--ref", help="Git branch, tag, or commit to resolve and pin"),
 ] = None,
 runtime_mode: Annotated[
  RuntimeMode,
  typer.Option("--runtime-mode", help="Default runtime mode for the generated case"),
 ] = RuntimeMode.DOCKER,
 image: Annotated[
  str | None,
  typer.Option("--image", help="Default docker image when runtime-mode=docker"),
 ] = None,
 instruction_path: Annotated[
  str,
  typer.Option("--instruction-path", help="Instruction path relative to artifact/"),
 ] = "README.md",
 prompt_profile: Annotated[
  PromptProfile,
  typer.Option("--prompt-profile", help="Prompt profile for the generated case"),
 ] = PromptProfile.ARTIFACT_EVAL_V1,
 prompt: Annotated[
  bool,
  typer.Option(
   "--prompt",
   help="Launch the interactive case authoring wizard after creating the bundle.",
  ),
 ] = False,
 no_prompt: Annotated[
  bool,
  typer.Option(
   "--no-prompt",
   help="Skip the interactive wizard even when running in an interactive terminal.",
  ),
 ] = False,
) -> None:
	"""Create a new case bundle from a source, or use --blank for an empty starter bundle."""
	context = _load_authoring_context(Path.cwd(), config_path=config)
	_configure_logging(context)
	should_prompt = _resolve_authoring_prompt_mode(prompt=prompt, no_prompt=no_prompt)
	resolved_case_id = _resolve_case_init_id(
	 source=source,
	 case_id=id,
	 blank=blank,
	 should_prompt=should_prompt,
	)
	try:
		bundle_dir = _create_case_bundle(
		 source=source,
		 case_id=resolved_case_id,
		 blank=blank,
		 project_state=context.project_state,
		 target_root=bundles_dir,
		 artifact_mode=artifact_mode,
		 ref=ref,
		 runtime_mode=runtime_mode,
		 image=image,
		 instruction_path=instruction_path,
		 prompt_profile=prompt_profile,
		)
		if should_prompt:
			apply_authoring_answers(
			 bundle_dir,
			 prompt_authoring_answers(bundle_dir, show_header=False),
			 write_instructions=blank,
			)
	except (BundleScaffoldError, RuntimeError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	if should_prompt:
		print_console(
		 "[bold]Authoring defaults applied.[/bold] Review case.toml, oracle/*.py, artifact instructions, and refs/ before treating the case as ready."
		)
	print_console(f"[bold]Created bundle:[/bold] {bundle_dir}")


@case_app.command("run")
def case_run_command(
 case_ref: Annotated[
  str,
  typer.Argument(help="Case id or path to a case bundle directory containing case.toml"),
 ],
 config: Annotated[
  Path | None,
  typer.Option(
   "--config",
   help="Explicit workspace config TOML to overlay on top of the discovered workspace config",
   exists=True,
   dir_okay=False,
   readable=True,
   resolve_path=True,
  ),
 ] = None,
 save_path: Annotated[
  Path | None,
  typer.Option(
   "--save-path",
   "-o",
   help="Directory for runtime and oracle outputs",
   file_okay=False,
   resolve_path=True,
  ),
 ] = None,
 model_name: Annotated[
  str | None,
  typer.Option("--model-name", "-m", help="Agent model name"),
 ] = None,
 interactive: Annotated[
  bool,
  typer.Option("--interactive", help="Force interactive mode for the generated runtime task"),
 ] = False,
 prompt_profile: Annotated[
  PromptProfile | None,
  typer.Option("--prompt-profile", help="Override the bundle prompt profile"),
 ] = None,
 prompt_append: Annotated[
  str | None,
  typer.Option("--prompt-append", help="Append extra prompt rules for the bundle run"),
 ] = None,
 cleanup_workspace: Annotated[
  bool,
  typer.Option(
   "--cleanup-workspace/--preserve-workspace",
   help="Delete the ephemeral runtime workspace after the case finishes. Disabled by default.",
  ),
 ] = False,
 live_view: Annotated[
  LiveViewMode,
  typer.Option("--live-view", help="Live display mode for terminal case runs"),
 ] = LiveViewMode.AUTO,
 live_layout: Annotated[
  LiveLayoutMode,
  typer.Option("--live-layout", help="Live terminal layout for case runs"),
 ] = LiveLayoutMode.AUTO,
 ui: Annotated[
  UiMode,
  typer.Option("--ui", help="Interactive terminal UI frontend for case runs"),
 ] = UiMode.RICH,
) -> None:
	"""Run one case bundle end-to-end: runtime first, oracle second."""
	context = _load_context(Path.cwd(), config_path=config)
	_configure_logging(context)
	try:
		case_dir = resolve_case_dir(case_ref, project_state=context.project_state)
		result = run_case(
		 case_dir,
		 model_name=_resolve_model_name(model_name, context),
		 save_path=save_path,
		 interactive=interactive,
		 prompt_profile=prompt_profile,
		 prompt_append=prompt_append,
		 cleanup_workspace=cleanup_workspace,
		 live_view=live_view,
		 live_layout=live_layout,
		 ui=ui,
		 project_state=context.project_state,
		)
	except (CaseBundleError, RuntimeError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	except KeyboardInterrupt:
		print_console("[red]Interrupted.[/red]")
		raise typer.Exit(130) from None
	print_console(f"[bold]Case {result.id} completed.[/bold] Status: {result.status.value}")
	_print_case_run_details(result)
	if result.status == CaseStatus.INTERRUPTED:
		raise typer.Exit(130)
	raise typer.Exit(0 if result.status == CaseStatus.SUCCESS else 1)


@case_app.command("summarize")
def case_summarize_command(
 case_outputs: Annotated[
  list[Path],
  typer.Argument(
   help="Case output directories or parent directories containing case_result.json"
  ),
 ],
 output_dir: Annotated[
  Path,
  typer.Option(
   "--output-dir",
   "-o",
   help="Directory for aggregated benchmark summary outputs",
   file_okay=False,
   resolve_path=True,
  ),
 ],
 run_label: Annotated[
  str | None,
  typer.Option("--run-label", help="Label shown in the aggregated benchmark summary"),
 ] = None,
 expected_case_ids: Annotated[
  list[str] | None,
  typer.Option(
   "--expected-case",
   help="Expected case id. Missing outputs are recorded as benchmark failures.",
  ),
 ] = None,
 model_name: Annotated[
  str | None,
  typer.Option("--model-name", "-m", help="Override the model name stored in the summary"),
 ] = None,
 agent_kind: Annotated[
  str | None,
  typer.Option("--agent-kind", help="Override the agent driver recorded in the summary"),
 ] = None,
 prompt_profile: Annotated[
  PromptProfile | None,
  typer.Option(
   "--prompt-profile",
   help="Override the prompt profile recorded in the summary",
  ),
 ] = None,
 config: Annotated[
  Path | None,
  typer.Option(
   "--config",
   help="Explicit workspace config TOML to overlay on top of the discovered workspace config",
   exists=True,
   dir_okay=False,
   readable=True,
   resolve_path=True,
  ),
 ] = None,
) -> None:
	"""Aggregate one or more case outputs into benchmark-level summary files."""
	context = _load_context(Path.cwd(), config_path=config)
	_configure_logging(context)
	try:
		result = summarize_case_outputs(
		 case_outputs,
		 output_dir=output_dir,
		 model_name=model_name,
		 agent_kind=agent_kind,
		 prompt_profile=prompt_profile.value if prompt_profile is not None else None,
		 run_label=run_label,
		 expected_case_ids=expected_case_ids or [],
		 project_state=context.project_state,
		)
	except RuntimeError as exc:
		raise typer.BadParameter(str(exc)) from exc
	print_console(
	 f"[bold]Benchmark summary created.[/bold] "
	 f"phase_ratio={result.summary.phase_ratio:.3f} "
	 f"case_pass_ratio={result.summary.case_pass_ratio:.3f}"
	)
	_print_benchmark_run_details(result)


@case_app.command("export")
def case_export_command(
 case_inputs: Annotated[
  list[str],
  typer.Argument(
   help="One or more case ids, case bundle paths, parent directories, or globs"
  ),
 ],
 output: Annotated[
  Path,
  typer.Option(
   "--output",
   "-o",
   help="Destination JSONL file for runtime tasks",
   file_okay=True,
   dir_okay=False,
   resolve_path=True,
  ),
 ],
 config: Annotated[
  Path | None,
  typer.Option(
   "--config",
   help="Explicit workspace config TOML to overlay on top of the discovered workspace config",
   exists=True,
   dir_okay=False,
   readable=True,
   resolve_path=True,
  ),
 ] = None,
) -> None:
	"""Export one or more bundles into low-level runtime task JSONL."""
	context = _load_context(Path.cwd(), config_path=config)
	_configure_logging(context)
	resolved = expand_case_dirs(case_inputs, project_state=context.project_state)
	if not resolved:
		raise typer.BadParameter("no case bundles found from the provided inputs")
	export_case_dirs(resolved, output, project_state=context.project_state)
	print_console(f"[bold]Exported[/bold] {len(resolved)} case bundle(s) to {output}")


@case_app.command("scaffold", hidden=True)
def case_scaffold_command(
 source: Annotated[
  str, typer.Argument(help="Artifact source: local dir, git URL, or archive path/URL")
 ],
 case_id: Annotated[
  str | None,
  typer.Option("--id", help="Override the inferred case id"),
 ] = None,
 config: Annotated[
  Path | None,
  typer.Option(
   "--config",
   help="Explicit workspace config TOML to overlay on top of the discovered workspace config",
   exists=True,
   dir_okay=False,
   readable=True,
   resolve_path=True,
  ),
 ] = None,
 bundles_dir: Annotated[
  Path | None,
  typer.Option(
   "--bundles-dir",
   help="Override the bundles root directory",
   file_okay=False,
   resolve_path=True,
  ),
 ] = None,
 artifact_mode: Annotated[
  ArtifactMode | None,
  typer.Option("--artifact-mode", help="Bundle materialization mode"),
 ] = None,
 ref: Annotated[
  str | None,
  typer.Option("--ref", help="Git branch, tag, or commit to resolve and pin"),
 ] = None,
 runtime_mode: Annotated[
  RuntimeMode,
  typer.Option("--runtime-mode", help="Default runtime mode for the generated case"),
 ] = RuntimeMode.DOCKER,
 image: Annotated[
  str | None,
  typer.Option("--image", help="Default docker image when runtime-mode=docker"),
 ] = None,
 instruction_path: Annotated[
  str,
  typer.Option("--instruction-path", help="Instruction path relative to artifact/"),
 ] = "README.md",
 prompt_profile: Annotated[
  PromptProfile,
  typer.Option("--prompt-profile", help="Prompt profile for the generated case"),
 ] = PromptProfile.ARTIFACT_EVAL_V1,
 prompt: Annotated[
  bool,
  typer.Option(
   "--prompt",
   help="Launch the interactive case authoring wizard after scaffolding the bundle.",
  ),
 ] = False,
 no_prompt: Annotated[
  bool,
  typer.Option(
   "--no-prompt",
   help="Skip the interactive wizard even when running in an interactive terminal.",
  ),
 ] = False,
) -> None:
	"""Create a minimal case bundle from a raw artifact source."""
	context = _load_authoring_context(Path.cwd(), config_path=config)
	_configure_logging(context)
	should_prompt = _resolve_authoring_prompt_mode(prompt=prompt, no_prompt=no_prompt)
	resolved_case_id = _resolve_case_init_id(
	 source=source,
	 case_id=case_id,
	 blank=False,
	 should_prompt=should_prompt,
	)
	try:
		print_console(
		 "[yellow]`artevalbench case scaffold` is deprecated; use `artevalbench case init <source>` instead.[/yellow]"
		)
		bundle_dir = _create_case_bundle(
		 source=source,
		 case_id=resolved_case_id,
		 blank=False,
		 project_state=context.project_state,
		 target_root=bundles_dir,
		 artifact_mode=artifact_mode,
		 ref=ref,
		 runtime_mode=runtime_mode,
		 image=image,
		 instruction_path=instruction_path,
		 prompt_profile=prompt_profile,
		)
		if should_prompt:
			apply_authoring_answers(
			 bundle_dir,
			 prompt_authoring_answers(bundle_dir, show_header=False),
			 write_instructions=False,
			)
	except BundleScaffoldError as exc:
		raise typer.BadParameter(str(exc)) from exc
	if should_prompt:
		print_console(
		 "[bold]Authoring defaults applied.[/bold] Review case.toml, oracle/*.py, artifact instructions, and refs/ before treating the case as ready."
		)
	print_console(f"[bold]Created bundle:[/bold] {bundle_dir}")


@cache_app.command("status")
def cache_status_command() -> None:
	"""Show shared git cache size and recent entries."""
	context = _load_context(Path.cwd())
	_configure_logging(context)
	status = git_cache_status(context.project_state)
	print_console(f"[bold]Cache root:[/bold] {status.root}")
	print_console(
	 f"[bold]Usage:[/bold] {_human_bytes(status.total_size_bytes)} / {_human_bytes(status.max_size_bytes)}"
	)
	print_console(f"[bold]Entries:[/bold] {status.entry_count}")
	for entry in status.entries[:10]:
		print_console(
		 f"- {entry.repo_key}@{entry.resolved_ref[:12]} "
		 f"({_human_bytes(entry.size_bytes)}, last used {entry.last_accessed.isoformat()})"
		)


def _resolve_authoring_prompt_mode(*, prompt: bool, no_prompt: bool) -> bool:
	if prompt and no_prompt:
		raise typer.BadParameter("use at most one of --prompt or --no-prompt")
	if prompt:
		if not is_interactive_terminal():
			raise typer.BadParameter("--prompt requires an interactive terminal")
		return True
	if no_prompt:
		return False
	return is_interactive_terminal()


def _load_authoring_context(start: Path, *, config_path: Path | None = None) -> AppContext:
	context = _load_context(start, config_path=config_path)
	initialize_workspace(context.project_state.root, include_local_config=False)
	return _load_context(context.project_state.root, config_path=config_path)


def _resolve_case_init_id(
 *,
 source: str | None,
 case_id: str | None,
 blank: bool,
 should_prompt: bool,
) -> str:
	if blank and source is not None:
		raise typer.BadParameter("do not provide a source when using --blank")
	if not blank and source is None:
		raise typer.BadParameter("source is required unless you pass --blank")
	if blank and not should_prompt and (case_id is None or not case_id.strip()):
		raise typer.BadParameter("--blank requires --id when not prompting")
	default_case_id = infer_case_id_default(None if blank else source, explicit_id=case_id)
	if should_prompt:
		print_authoring_wizard_header(case_id_hint=default_case_id)
		return prompt_case_id(default=default_case_id)
	return default_case_id


def _create_case_bundle(
 *,
 source: str | None,
 case_id: str,
 blank: bool,
 project_state: ProjectConfigState,
 target_root: Path | None,
 artifact_mode: ArtifactMode | None,
 ref: str | None,
 runtime_mode: RuntimeMode,
 image: str | None,
 instruction_path: str,
 prompt_profile: PromptProfile,
) -> Path:
	if blank:
		return initialize_case_bundle(
		 case_id,
		 project_state=project_state,
		 target_root=target_root,
		)
	if source is None:
		raise typer.BadParameter("source is required unless you pass --blank")
	return scaffold_case_bundle(
	 source,
	 case_id,
	 project_state=project_state,
	 target_root=target_root,
	 artifact_mode=artifact_mode,
	 ref=ref,
	 runtime_mode=runtime_mode,
	 image=image,
	 instruction_path=instruction_path,
	 prompt_profile=prompt_profile,
	)


@cache_app.command("prune")
def cache_prune_command() -> None:
	"""Prune shared git cache entries using the configured size cap and LRU policy."""
	context = _load_context(Path.cwd())
	_configure_logging(context)
	result = prune_git_cache(context.project_state)
	print_console(f"[bold]Cache root:[/bold] {result.root}")
	print_console(
	 f"[bold]Pruned:[/bold] {_human_bytes(result.before_size_bytes - result.after_size_bytes)} "
	 f"across {len(result.removed_entries)} entries"
	)
	print_console(
	 f"[bold]Remaining:[/bold] {_human_bytes(result.after_size_bytes)} / {_human_bytes(result.max_size_bytes)}"
	)


def _load_context(start: Path, *, config_path: Path | None = None) -> AppContext:
	project_state = load_project_config(start, config_path=config_path)
	return AppContext(project_state=project_state, settings=resolve_settings(project_state))


def _normalize_prompt_profile(value: PromptProfile | str | None) -> PromptProfile | None:
	if value is None or isinstance(value, PromptProfile):
		return value
	return PromptProfile(value)


def _resolve_runtime_save_path(
 save_path: str | Path | None,
 model_name: str,
 context: AppContext,
) -> Path:
	if save_path is not None:
		return Path(save_path).expanduser().resolve()
	model_fragment = model_name.replace("/", "_").lower()
	timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
	return (
	 Path(context.settings.default_outputs_dir) / f"artevalbench_{model_fragment}_{timestamp}"
	).resolve()


def _resolve_model_name(model_name: str | None, context: AppContext) -> str:
	return model_name or context.settings.default_model


def _configure_logging(
 context: AppContext,
 *,
 log_level: LogLevel | str | None = None,
) -> None:
	configure_logging(
	 log_level=_resolve_log_level(log_level, context),
	 log_renderer=context.settings.log_renderer,
	)


def _print_case_run_details(result: CaseRunResult) -> None:
	print_console(f"[bold]Output dir:[/bold] {result.output_dir}")
	if result.status == CaseStatus.SUCCESS:
		return
	runtime_error = result.runtime_result.error
	oracle_error = result.oracle_result.error
	if runtime_error:
		print_console("[bold red]Runtime error:[/bold red]")
		print_console(runtime_error)
	if oracle_error and oracle_error != runtime_error:
		print_console("[bold red]Oracle error:[/bold red]")
		print_console(oracle_error)
	failed_phases = [
	 phase
	 for phase in result.oracle_result.phases
	 if getattr(phase.status, "value", str(phase.status)) == "error"
	]
	if failed_phases:
		print_console("[bold red]Failed phases:[/bold red]")
		for phase in failed_phases:
			print_console(f"- {phase.phase}: {phase.summary}")
			if phase.error:
				print_console(f"  {phase.error}")
	print_console(f"[bold]Task log:[/bold] {result.runtime_result.log_path}")
	if result.runtime_result.infra_log_path:
		print_console(f"[bold]Infra log:[/bold] {result.runtime_result.infra_log_path}")
	if result.runtime_result.runner_log_path:
		print_console(f"[bold]Runner log:[/bold] {result.runtime_result.runner_log_path}")


def _resolve_log_level(
 log_level: LogLevel | str | None,
 context: AppContext,
) -> LogLevel:
	if isinstance(log_level, str):
		return LogLevel(log_level)
	if log_level is not None:
		return log_level
	return context.settings.log_level


def _human_bytes(size_bytes: int) -> str:
	units = ["B", "KB", "MB", "GB", "TB"]
	value = float(size_bytes)
	for unit in units:
		if value < 1024 or unit == units[-1]:
			if unit == "B":
				return f"{int(value)} {unit}"
			return f"{value:.1f} {unit}"
		value /= 1024
	return f"{size_bytes} B"


if __name__ == "__main__":
	cli_main()
