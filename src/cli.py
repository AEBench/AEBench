"""AEBench CLI"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import AppState, resolve_settings
from evaluator.loader import load_case_spec
from log import configure_logging
from models import (
    AgentResult,
    CaseRunResult,
    CaseStatus,
    OracleResult,
    OracleStatus,
    PromptArgs,
    RunOptions,
    RunResult,
    RuntimeMode,
    RuntimeInfo,
    TaskStatus,
)
from project_config import load_project_config
from prompting import build_prompt_bundle
from run_control import RunControl, activate_interrupt_handler
from runtime.backend import get_runtime
from runtime.benchmark_runner import BenchmarkRunner, summarize_case_output_dirs
from runtime.case_runner import CaseRunner
from runtime.cases import expand_case_dirs, export_case_dirs, resolve_case_dir, task_from_case_spec
from runtime.workspace import bundle_refs_path, cleanup_workspace_tree, create_workspace_root
from runtime.oracle_runner import DirectOracleRunner
from runtime.reporting import (
    append_run_result,
    read_agent_summary,
    task_paths_for,
    write_prompt_file,
    write_task_report,
)
from runtime.session import RunSession
from runtime.task_runner import run_tasks_from_jsonl
from sources import prepare_workspace
from task_loader import append_summary_instruction, prepend_case_brief, read_instruction_text
from utils import safe_name


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aebench",
        description="AEBench benchmark, case, and runtime CLI.",
    )
    sub = parser.add_subparsers(dest="command")

    init_p = sub.add_parser("init", help="Initialize workspace and user config.")
    init_p.add_argument("--workspace", type=str, default=None)

    run_p = sub.add_parser("run", help="Run the official benchmark catalog or a subset.")
    run_p.add_argument("case_refs", nargs="*", default=[])
    run_p.add_argument("--output-dir", type=str, default=None)
    run_p.add_argument("--model", type=str, default=None)
    run_p.add_argument("--interactive", action="store_true")
    run_p.add_argument("--cleanup-workspace", action="store_true")

    case_p = sub.add_parser("case", help="Case-bundle workflows.")
    case_sub = case_p.add_subparsers(dest="case_command")

    case_run = case_sub.add_parser("run", help="Run one case bundle.")
    case_run.add_argument("case_ref", type=str)
    case_run.add_argument("--save-path", type=str, default=None)
    case_run.add_argument("--model", type=str, default=None)
    case_run.add_argument("--interactive", action="store_true")
    case_run.add_argument("--cleanup-workspace", action="store_true")
    case_run.add_argument(
        "--manual",
        action="store_true",
        help=(
            "Open an interactive shell inside the case's Docker runtime instead of launching "
            "the agent. When the shell exits, the oracle evaluates the resulting workspace."
        ),
    )

    case_init = case_sub.add_parser("init", help="Initialize a new case bundle.")
    case_init.add_argument("source", nargs="?", default=None)
    case_init.add_argument("--blank", action="store_true")
    case_init.add_argument("--id", dest="case_id", default=None)
    case_init.add_argument("--ref", default=None)
    case_init.add_argument("--target-dir", default=None)
    case_init.add_argument("--no-prompt", action="store_true")

    case_scaffold = case_sub.add_parser("scaffold", help="Deprecated alias for `case init <source>`.")
    case_scaffold.add_argument("source", nargs="?", default=None)
    case_scaffold.add_argument("--id", dest="case_id", default=None)
    case_scaffold.add_argument("--ref", default=None)
    case_scaffold.add_argument("--target-dir", default=None)
    case_scaffold.add_argument("--no-prompt", action="store_true")

    case_export = case_sub.add_parser("export", help="Export one or more cases to JSONL.")
    case_export.add_argument("case_refs", nargs="*", default=[])
    case_export.add_argument("--output", required=True)

    case_summarize = case_sub.add_parser("summarize", help="Summarize existing case outputs.")
    case_summarize.add_argument("case_output_inputs", nargs="+")
    case_summarize.add_argument("--output-dir", required=True)
    case_summarize.add_argument("--run-label", default=None)
    case_summarize.add_argument("--model-name", default=None)
    case_summarize.add_argument("--agent-kind", default=None)
    case_summarize.add_argument("--prompt-profile", default=None)

    case_oracle = case_sub.add_parser("oracle", help="Run oracle evaluation for a case.")
    case_oracle.add_argument("case_ref", type=str)
    case_oracle.add_argument("--output-dir", type=str, default=None)

    case_author = case_sub.add_parser(
        "author",
        help="Auto-author a new case bundle using a registered authoring agent.",
    )
    case_author.add_argument(
        "--agent",
        required=True,
        help="Name of the registered authoring agent backend (e.g. 'benchmate').",
    )
    case_author.add_argument(
        "--config",
        required=True,
        help="Path to a TOML request file describing the artifact and authoring knobs.",
    )
    case_author.add_argument(
        "--dry-run",
        action="store_true",
        help="Set up staging and guidance but do not launch the authoring container.",
    )
    case_author.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing non-empty staging directory.",
    )
    case_author.add_argument(
        "--timeout-hours",
        type=float,
        default=6.0,
        help="Wall-clock timeout in hours for the authoring container (default: 6).",
    )

    runtime_p = sub.add_parser("runtime", help="Low-level runtime workflows.")
    runtime_sub = runtime_p.add_subparsers(dest="runtime_command")

    runtime_run = runtime_sub.add_parser("run", help="Run an explicit JSONL task file.")
    runtime_run.add_argument("--input-file", required=True)
    runtime_run.add_argument("--output-dir", type=str, default=None)
    runtime_run.add_argument("--model", type=str, default=None)
    runtime_run.add_argument("--interactive", action="store_true")
    runtime_run.add_argument("--cleanup-workspace", action="store_true")
    return parser


def cli_main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "init":
        return _init_workspace(args)
    if args.command == "run":
        return _run_benchmark(args)
    if args.command == "case":
        return _handle_case(args)
    if args.command == "runtime":
        return _handle_runtime(args)

    parser.print_help()
    return 1


def _handle_case(args: argparse.Namespace) -> int:
    cmd = getattr(args, "case_command", None)
    if cmd == "run":
        return _case_run(args)
    if cmd == "init":
        return _case_init(args)
    if cmd == "scaffold":
        print("Warning: `case scaffold` is deprecated; use `case init` instead.", file=sys.stderr)
        return _case_init(args)
    if cmd == "export":
        return _case_export(args)
    if cmd == "summarize":
        return _case_summarize(args)
    if cmd == "oracle":
        return _case_oracle(args)
    if cmd == "author":
        return _case_author(args)

    print("usage: aebench case {run,init,scaffold,export,summarize,oracle,author}", file=sys.stderr)
    return 1


def _handle_runtime(args: argparse.Namespace) -> int:
    cmd = getattr(args, "runtime_command", None)
    if cmd == "run":
        return _runtime_run(args)
    print("usage: aebench runtime {run}", file=sys.stderr)
    return 1


def _init_workspace(args: argparse.Namespace) -> int:
    from evaluator.registry import initialize_user_config, initialize_workspace

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd()
    result = initialize_workspace(workspace)
    initialize_user_config()
    print(
        "Initialized workspace. Created: "
        f"{', '.join(str(p) for p in result.created) if result.created else '(none)'}"
    )
    return 0


def _run_benchmark(args: argparse.Namespace) -> int:
    context = _build_context()
    runner = BenchmarkRunner(context)
    control = RunControl()

    with activate_interrupt_handler(control):
        result = runner.run(
            list(args.case_refs or []),
            options=_run_options(args),
            output_dir=Path(args.output_dir).expanduser().resolve() if args.output_dir else None,
            listener=None,
            run_control=control,
        )

    print(f"Benchmark status: {result.summary.status}")
    print(f"Cases: {result.summary.case_pass_count}/{result.summary.total_cases}")
    print(f"Phase score: {result.summary.total_score}/{result.summary.total_expected_score}")
    print(f"Summary: {result.summary_path}")
    return 0 if result.summary.status == "success" else 1


def _case_run(args: argparse.Namespace) -> int:
    if bool(getattr(args, "manual", False)):
        return _case_run_manual(args)

    context = _build_context()
    runner = CaseRunner(context)
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    case = load_case_spec(case_dir)
    control = RunControl()

    with activate_interrupt_handler(control):
        result = runner.run(
            case_dir,
            save_path=Path(args.save_path).expanduser().resolve() if args.save_path else None,
            options=_run_options(args),
            listener=None,
            run_control=control,
        )

    print(f"Case status: {result.status.value}")
    print(f"Oracle status: {result.oracle_result.status.value}")
    print(f"Score: {result.oracle_result.score}/{case.oracle.expected_score}")
    print(f"Output dir: {result.output_dir}")
    return 0 if result.status.value == "success" else 1


def _case_run_manual(args: argparse.Namespace) -> int:
    context = _build_context()
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    case = load_case_spec(case_dir)
    run_spec = task_from_case_spec(case_dir, case, project_state=context.project_state)

    if run_spec.runtime.mode != RuntimeMode.DOCKER:
        raise SystemExit(
            "case run --manual requires runtime.mode='docker'; manual artifact execution "
            "must happen inside the Docker runtime."
        )

    output_dir = _resolve_case_output_dir(
        case_dir,
        case.id,
        context.project_state,
        Path(args.save_path).expanduser().resolve() if args.save_path else None,
    )
    task_id = safe_name(case.id)
    task_paths = task_paths_for(output_dir, task_id)
    task_paths.tool_output_dir.mkdir(parents=True, exist_ok=True)

    control = RunControl()
    workspace_path: Path | None = None
    refs_path: Path | None = None
    summary_path: Path | None = None
    runtime_backend = None
    session: RunSession | None = None
    error_message: str | None = None
    shell_exit_code = 1

    source_prepare_started_at = datetime.now(timezone.utc)
    source_prepare_finished_at = source_prepare_started_at
    runtime_prepare_started_at = source_prepare_started_at
    prepare_finished_at = source_prepare_started_at
    shell_started_at = source_prepare_started_at
    shell_finished_at = source_prepare_started_at
    cleanup_finished_at = source_prepare_started_at

    try:
        workspace_path = create_workspace_root(
            run_spec.id,
            context.settings.tmp_workspace_root,
        )
        workspace_path = prepare_workspace(run_spec, case_dir / "case.toml", workspace_path)
        source_prepare_finished_at = datetime.now(timezone.utc)

        refs_path = bundle_refs_path(case_dir / "case.toml")
        summary_path = workspace_path / f"aebench_summary_{task_id}.md"

        task_text = prepend_case_brief(
            read_instruction_text(workspace_path, run_spec.instructions_path),
            run_spec.case_brief,
        )
        task_text = append_summary_instruction(task_text, summary_path.name)

        prompt = build_prompt_bundle(
            PromptArgs(
                task_text=task_text,
                workspace_path="/repo",
                runtime_mode=RuntimeMode.DOCKER,
                timeout_ms=run_spec.runtime.timeout_ms,
                interactive=True,
                prompt_profile=run_spec.prompt_profile.value,
                prompt_append=run_spec.prompt_append,
                refs_path="/refs" if refs_path else None,
                host_workspace_path=str(workspace_path),
                container_workspace_path="/repo",
            )
        )
        write_prompt_file(task_paths.prompt_path, prompt)

        runtime_backend = get_runtime(
            RuntimeMode.DOCKER,
            image=run_spec.runtime.image or context.settings.default_docker_image,
            gpu=getattr(run_spec.runtime, "gpu", False),
        )
        session = RunSession(
            run_spec=run_spec,
            prompt=prompt,
            settings=context.settings,
            run_control=control,
            host_workspace=workspace_path,
            runtime_workspace="/repo",
            host_refs=refs_path,
            runtime_refs="/refs" if refs_path else None,
            output_dir=output_dir,
            task_paths=task_paths,
            summary_path=summary_path,
            runtime_backend=runtime_backend,
        )

        runtime_prepare_started_at = datetime.now(timezone.utc)
        runtime_backend.prepare(session)
        prepare_finished_at = datetime.now(timezone.utc)

        shell_started_at = prepare_finished_at
        with activate_interrupt_handler(control):
            print(f"Opening Docker shell for case {case.id} ...")
            print(f"Workspace: {workspace_path}")
            print("Run the artifact commands inside the container. Exit the shell when done.")
            shell_exit_code = runtime_backend.open_shell(cwd="/repo")
        shell_finished_at = datetime.now(timezone.utc)

        if shell_exit_code != 0:
            error_message = f"manual shell exited with code {shell_exit_code}"

    except Exception as exc:
        shell_finished_at = datetime.now(timezone.utc)
        error_message = f"{type(exc).__name__}: {exc}"

    finally:
        if session is not None and runtime_backend is not None:
            try:
                runtime_backend.collect_artifacts(session)
            except Exception as exc:
                error_message = error_message or f"collect_artifacts failed: {exc}"
            try:
                runtime_backend.cleanup(session)
            except Exception as exc:
                error_message = error_message or f"runtime cleanup failed: {exc}"
        cleanup_finished_at = datetime.now(timezone.utc)

    interrupted = control.stop_requested or shell_exit_code in {130, 143}
    status = (
        TaskStatus.INTERRUPTED
        if interrupted
        else TaskStatus.SUCCESS if shell_exit_code == 0 and error_message is None else TaskStatus.ERROR
    )

    runtime_result = (
        runtime_backend.runtime_result(session)
        if runtime_backend is not None and session is not None
        else RuntimeInfo(
            mode=RuntimeMode.DOCKER,
            image=run_spec.runtime.image or context.settings.default_docker_image,
            container_id=None,
            saved_image=None,
            container_stopped=True,
        )
    )

    run_result = RunResult(
        id=run_spec.id,
        status=status,
        started_at=shell_started_at,
        finished_at=cleanup_finished_at,
        prepare_duration_ms=_duration_ms(source_prepare_started_at, prepare_finished_at),
        prepare_breakdown_ms={
            f"source_{run_spec.require_source().type.value}_prepare": _duration_ms(
                source_prepare_started_at, source_prepare_finished_at
            ),
            "runtime_docker_prepare": _duration_ms(runtime_prepare_started_at, prepare_finished_at),
        },
        duration_ms=_duration_ms(shell_started_at, shell_finished_at),
        workspace_path=str(workspace_path) if workspace_path is not None else "",
        output_dir=str(output_dir),
        summary_path=str(summary_path) if summary_path is not None else "",
        prompt_profile=run_spec.prompt_profile,
        runtime=runtime_result,
        agent_kind="manual_shell",
        agent=AgentResult(
            model="manual",
            exit_code=shell_exit_code,
            output="manual docker shell session",
            message_count=0,
        ),
        error=("Interrupted by user" if interrupted and not error_message else error_message),
    )

    write_task_report(
        task_paths.report_path,
        run_result,
        read_agent_summary(summary_path or (output_dir / f"aebench_summary_{task_id}.md"), run_result),
    )
    append_run_result(output_dir, run_result)

    if run_result.status == TaskStatus.SUCCESS:
        try:
            oracle_result = DirectOracleRunner().execute(
                case_dir,
                runtime_result=run_result,
                output_dir=output_dir,
                case=case,
            )
        except Exception as exc:
            print(f"Oracle execution failed: {exc}", file=sys.stderr)
            oracle_result = OracleResult(
                status=OracleStatus.ERROR,
                score=0,
                summary="Oracle execution raised an unexpected exception.",
                error=f"{type(exc).__name__}: {exc}",
            )
    elif run_result.status == TaskStatus.INTERRUPTED:
        oracle_result = OracleResult(
            status=OracleStatus.PENDING,
            summary="Manual runtime interrupted; oracle was not executed.",
            error=run_result.error,
        )
    else:
        oracle_result = OracleResult(
            status=OracleStatus.PENDING,
            summary="Manual runtime failed; oracle was not executed.",
            error=run_result.error,
        )

    case_result = CaseRunResult(
        status=_case_status_for(run_result.status, oracle_result.status),
        finished_at=datetime.now(timezone.utc),
        case_dir=str(case_dir),
        artifact_dir=str(case_dir / "artifact"),
        output_dir=str(output_dir),
        case_brief=case.case_brief,
        runtime_result=run_result,
        oracle_result=oracle_result,
    )
    (output_dir / "case_result.json").write_text(case_result.model_dump_json(indent=2), encoding="utf-8")

    if args.cleanup_workspace and workspace_path is not None:
        cleanup_workspace_tree(
            workspace_path,
            preserve=case_result.status != CaseStatus.SUCCESS,
            preserve_failed_workspace=context.settings.preserve_failed_workspace,
        )

    print(f"Case status: {case_result.status.value}")
    print(f"Oracle status: {case_result.oracle_result.status.value}")
    print(f"Score: {case_result.oracle_result.score}/{case.oracle.expected_score}")
    print(f"Output dir: {case_result.output_dir}")
    return 0 if case_result.status == CaseStatus.SUCCESS else 1


def _case_init(args: argparse.Namespace) -> int:
    from evaluator.authoring.authoring import (
        apply_authoring_answers,
        infer_case_id_default,
        is_interactive_terminal,
        prompt_authoring_answers,
        prompt_case_id,
    )
    from evaluator.registry import initialize_case_bundle, initialize_user_config, initialize_workspace
    from evaluator.scaffold import scaffold_case_bundle

    context = _build_context()
    initialize_workspace(context.project_state.root)
    initialize_user_config()

    case_id = args.case_id or infer_case_id_default(args.source)
    if is_interactive_terminal() and not args.no_prompt and not args.case_id:
        case_id = prompt_case_id(default=case_id)

    target_root = Path(args.target_dir).expanduser().resolve() if args.target_dir else None
    if args.blank:
        bundle_dir = initialize_case_bundle(case_id, project_state=context.project_state, target_root=target_root)
    else:
        if not args.source:
            raise SystemExit("case init requires a source unless --blank is used")
        bundle_dir = scaffold_case_bundle(
            args.source,
            case_id,
            project_state=context.project_state,
            target_root=target_root,
            ref=args.ref,
        )

    if is_interactive_terminal() and not args.no_prompt:
        answers = prompt_authoring_answers(bundle_dir)
        apply_authoring_answers(bundle_dir, answers, write_instructions=True)

    print(f"Initialized case bundle: {bundle_dir}")
    return 0


def _case_export(args: argparse.Namespace) -> int:
    context = _build_context()
    case_dirs = expand_case_dirs(list(args.case_refs or []), project_state=context.project_state)
    output = Path(args.output).expanduser().resolve()
    export_case_dirs(case_dirs, output_path=output, project_state=context.project_state)
    print(f"Exported {len(case_dirs)} case(s) to {output}")
    return 0


def _case_summarize(args: argparse.Namespace) -> int:
    context = _build_context()
    result = summarize_case_output_dirs(
        [Path(value) for value in args.case_output_inputs],
        output_dir=Path(args.output_dir).expanduser().resolve(),
        project_state=context.project_state,
        model_name=args.model_name,
        agent_kind=args.agent_kind,
        prompt_profile=args.prompt_profile,
        run_label=args.run_label,
    )
    print(f"Summary status: {result.summary.status}")
    print(f"Summary JSON: {result.summary_path}")
    print(f"Summary Markdown: {result.summary_markdown_path}")
    return 0 if result.summary.status == "success" else 1


def _case_author(args: argparse.Namespace) -> int:
    from evaluator.authoring.authoring_request import load_authoring_request
    from evaluator.authoring.backends import get_backend
    from evaluator.authoring.orchestrator import run_authoring_pipeline

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.is_file():
        print(f"Error: request config not found: {config_path}", file=sys.stderr)
        return 1

    request = load_authoring_request(config_path)

    try:
        backend = get_backend(args.agent)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    timeout_s = float(getattr(args, "timeout_hours", 6.0)) * 3600.0

    try:
        result = run_authoring_pipeline(
            request,
            backend,
            wall_clock_timeout_s=timeout_s,
            overwrite_staging=bool(getattr(args, "overwrite", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    except (FileExistsError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Authoring status: {'success' if result.ok else 'failed'}")
    print(f"Attempts: {result.attempts}")
    print(f"Staging:   {result.staging_dir}")
    print(f"Candidate: {result.candidate_dir}")
    if result.validation_result and not result.validation_result.ok:
        for issue in result.validation_result.issues:
            print(f"  [{issue.severity}] {issue.code}: {issue.message}", file=sys.stderr)
    if result.ok:
        print("Next step: review oracle/ files and promote to cases/")
    return 0 if result.ok else 1


def _case_oracle(args: argparse.Namespace) -> int:
    context = _build_context()
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    case = load_case_spec(case_dir)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else _latest_case_output_dir(context, case.id) or (case_dir / "output").resolve()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_result = _load_runtime_result_for_output_dir(output_dir)
    result = DirectOracleRunner().execute(
        case_dir,
        runtime_result=runtime_result,
        output_dir=output_dir,
        case=case,
    )
    print(f"Oracle status: {result.status.value}")
    print(f"Score: {result.score}/{case.oracle.expected_score}")
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
    return 0 if result.status.value != "error" else 1


def _runtime_run(args: argparse.Namespace) -> int:
    context = _build_context()
    input_file = Path(args.input_file).expanduser().resolve()
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else (Path(context.settings.default_outputs_dir).expanduser().resolve() / "runtime")
    )
    control = RunControl()
    with activate_interrupt_handler(control):
        results = run_tasks_from_jsonl(
            context,
            input_file=input_file,
            output_dir=output_dir,
            options=_run_options(args),
            listener=None,
            run_control=control,
        )
    success = sum(1 for result in results if result.status.value == "success")
    print(f"Completed {len(results)} task(s); success={success}")
    return 0 if success == len(results) else 1


def _build_context() -> AppState:
    state = load_project_config(Path.cwd())
    settings = resolve_settings(state)
    configure_logging(
        log_level=settings.log_level.value,
        log_renderer=settings.log_renderer.value,
    )
    return AppState(project_state=state, settings=settings)


def _run_options(args: argparse.Namespace) -> RunOptions:
    kwargs: dict[str, Any] = {
        "interactive": bool(getattr(args, "interactive", False)),
        "prompt_profile": None,
        "prompt_append": None,
        "cleanup_workspace": bool(getattr(args, "cleanup_workspace", False)),
    }
    model = getattr(args, "model", None)
    if model is not None:
        kwargs["model_name"] = model
    return RunOptions(**kwargs)


def _resolve_case_output_dir(case_dir: Path, case_id: str, project_state, save_path: Path | None) -> Path:
    if save_path is not None:
        output_dir = save_path.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    case_runs_root = (
        project_state.config.resolve_case_runs_dir(project_state.root) / safe_name(case_id)
    ).resolve()
    case_runs_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%f")
    output_dir = (case_runs_root / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _latest_case_output_dir(context: AppState, case_id: str) -> Path | None:
    case_runs_root = (
        context.project_state.config.resolve_case_runs_dir(context.project_state.root) / safe_name(case_id)
    ).resolve()
    if not case_runs_root.is_dir():
        return None
    candidates = sorted(
        (path for path in case_runs_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _load_runtime_result_for_output_dir(output_dir: Path) -> RunResult | None:
    case_result_path = output_dir / "case_result.json"
    if case_result_path.is_file():
        case_result = CaseRunResult.model_validate_json(case_result_path.read_text(encoding="utf-8"))
        return case_result.runtime_result

    result_jsonl = output_dir / "result.jsonl"
    if result_jsonl.is_file():
        lines = [line.strip() for line in result_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            return RunResult.model_validate_json(lines[-1])

    return None


def _case_status_for(runtime_status: TaskStatus, oracle_status: OracleStatus) -> CaseStatus:
    if runtime_status == TaskStatus.INTERRUPTED:
        return CaseStatus.INTERRUPTED
    if runtime_status != TaskStatus.SUCCESS:
        return CaseStatus.ERROR
    if oracle_status == OracleStatus.ERROR:
        return CaseStatus.ERROR
    if oracle_status == OracleStatus.PENDING:
        return CaseStatus.PENDING
    return CaseStatus.SUCCESS


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return int((finished_at - started_at).total_seconds() * 1000)


app = cli_main
main = cli_main

__all__ = ["app", "cli_main", "main"]


if __name__ == "__main__":
    raise SystemExit(cli_main())