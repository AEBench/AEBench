from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import AppState, resolve_settings
from evaluator.loader import load_case_spec
from log import configure_logging
from models import RunOptions
from project_config import load_project_config
from run_control import RunControl, activate_interrupt_handler
from runtime.benchmark_runner import BenchmarkRunner, summarize_case_output_dirs
from runtime.case_runner import CaseRunner
from runtime.cases import expand_case_dirs, export_case_dirs, resolve_case_dir
from runtime.oracle_runner import DirectOracleRunner
from runtime.task_runner import run_tasks_from_jsonl


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aebench", description="AEBench benchmark, case, and runtime CLI.")
    sub = parser.add_subparsers(dest="command")

    init_p = sub.add_parser("init", help="Initialize an AEBench workspace.")
    init_p.add_argument("--workspace", default=None)

    run_p = sub.add_parser("run", help="Run benchmark cases.")
    _add_run_options(run_p)
    run_p.add_argument("case_refs", nargs="*", default=[])
    run_p.add_argument("--output-dir", default=None)
    run_p.add_argument("--skip-incompatible", action="store_true")

    case_p = sub.add_parser("case", help="Case-bundle workflows.")
    case_sub = case_p.add_subparsers(dest="case_command")

    case_run = case_sub.add_parser("run", help="Run one case.")
    _add_run_options(case_run)
    case_run.add_argument("case_ref")
    case_run.add_argument("--save-path", default=None)

    case_init = case_sub.add_parser("init", help="Create a new case bundle.")
    case_init.add_argument("source", nargs="?", default=None)
    case_init.add_argument("--blank", action="store_true")
    case_init.add_argument("--id", dest="case_id", default=None)
    case_init.add_argument("--ref", default=None)
    case_init.add_argument("--target-dir", default=None)

    case_template = case_sub.add_parser("template", help="Write starter oracle templates.")
    case_template.add_argument("case_ref")
    case_template.add_argument("--expected-output", default="results/output.txt")

    case_validate = case_sub.add_parser("validate", help="Validate a case bundle.")
    case_validate.add_argument("case_ref")

    case_export = case_sub.add_parser("export", help="Export cases to JSONL.")
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
    case_oracle.add_argument("case_ref")
    case_oracle.add_argument("--output-dir", default=None)
    case_oracle.add_argument("--workspace-dir", default=None)

    runtime_p = sub.add_parser("runtime", help="Low-level runtime workflows.")
    runtime_sub = runtime_p.add_subparsers(dest="runtime_command")
    runtime_run = runtime_sub.add_parser("run", help="Run an explicit JSONL task file.")
    _add_run_options(runtime_run)
    runtime_run.add_argument("--input-file", required=True)
    runtime_run.add_argument("--output-dir", default=None)
    return parser


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=None)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--cleanup-workspace", action="store_true")
    parser.add_argument("--prompt-profile", default=None)
    parser.add_argument("--prompt-append", default=None)


def cli_main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return {
            "init": _init_workspace,
            "run": _run_benchmark,
            "case": _handle_case,
            "runtime": _handle_runtime,
        }[args.command](args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


def _handle_case(args: argparse.Namespace) -> int:
    handler = {
        "run": _case_run,
        "init": _case_init,
        "template": _case_template,
        "validate": _case_validate,
        "export": _case_export,
        "summarize": _case_summarize,
        "oracle": _case_oracle,
    }.get(getattr(args, "case_command", None))
    if handler is None:
        print("usage: aebench case {run,init,template,validate,export,summarize,oracle}", file=sys.stderr)
        return 1
    return handler(args)


def _handle_runtime(args: argparse.Namespace) -> int:
    if getattr(args, "runtime_command", None) == "run":
        return _runtime_run(args)
    print("usage: aebench runtime {run}", file=sys.stderr)
    return 1


def _init_workspace(args: argparse.Namespace) -> int:
    from evaluator.registry import initialize_user_config, initialize_workspace

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd()
    result = initialize_workspace(workspace)
    initialize_user_config()
    created = ", ".join(str(path) for path in result.created) if result.created else "none"
    print(f"Initialized workspace: {workspace}")
    print(f"Created: {created}")
    return 0


def _run_benchmark(args: argparse.Namespace) -> int:
    context = _build_context()
    control = RunControl()
    with activate_interrupt_handler(control):
        result = BenchmarkRunner(context).run(
            list(args.case_refs or []),
            options=_run_options(args),
            output_dir=_optional_path(args.output_dir),
            listener=None,
            run_control=control,
        )
    print(f"Benchmark status: {result.summary.status}")
    print(f"Cases: {result.summary.case_pass_count}/{result.summary.total_cases}")
    print(f"Phase score: {result.summary.total_score}/{result.summary.total_expected_score}")
    print(f"Summary: {result.summary_path}")
    return 0 if result.summary.status == "success" else 1


def _case_run(args: argparse.Namespace) -> int:
    context = _build_context()
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    case = load_case_spec(case_dir)
    control = RunControl()
    with activate_interrupt_handler(control):
        result = CaseRunner(context).run(
            case_dir,
            save_path=_optional_path(args.save_path),
            options=_run_options(args),
            listener=None,
            run_control=control,
        )
    print(f"Case status: {result.status.value}")
    print(f"Oracle status: {result.oracle_result.status.value}")
    print(f"Score: {result.oracle_result.score}/{case.oracle.expected_score}")
    print(f"Output dir: {result.output_dir}")
    return 0 if result.status.value == "success" else 1


def _case_init(args: argparse.Namespace) -> int:
    from evaluator.registry import initialize_case_bundle, initialize_user_config, initialize_workspace
    from evaluator.template import create_case_from_source, infer_case_id

    context = _build_context()
    initialize_workspace(context.project_state.root)
    initialize_user_config()
    case_id = args.case_id or (infer_case_id(args.source) if args.source else None)
    if not case_id:
        raise SystemExit("case init requires --id when no source is provided")
    target_root = _optional_path(args.target_dir)
    if args.blank:
        bundle_dir = initialize_case_bundle(case_id, project_state=context.project_state, target_root=target_root)
    else:
        if not args.source:
            raise SystemExit("case init requires a source unless --blank is used")
        bundle_dir = create_case_from_source(args.source, case_id, project_state=context.project_state, target_root=target_root, ref=args.ref)
    print(f"Initialized case bundle: {bundle_dir}")
    return 0


def _case_template(args: argparse.Namespace) -> int:
    from evaluator.authoring.template import write_case_template

    context = _build_context()
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    write_case_template(case_dir, expected_output_path=args.expected_output)
    print(f"Wrote oracle templates: {case_dir / 'oracles'}")
    return 0


def _case_validate(args: argparse.Namespace) -> int:
    from evaluator.authoring.validate import validate_case_bundle

    context = _build_context()
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    result = validate_case_bundle(case_dir)
    if result.ok:
        print(f"Case bundle is valid: {case_dir}")
        return 0
    print(f"Case bundle is invalid: {case_dir}", file=sys.stderr)
    for issue in result.issues:
        print(f"[{issue.severity}] {issue.code}: {issue.message}", file=sys.stderr)
    return 1


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


def _case_oracle(args: argparse.Namespace) -> int:
    from models import RunResult
    from runtime.benchmark_runner import discover_case_output_dirs

    context = _build_context()
    case_dir = resolve_case_dir(args.case_ref, project_state=context.project_state)
    case = load_case_spec(case_dir)
    output_dir = _optional_path(args.output_dir)
    if output_dir is None:
        candidates = discover_case_output_dirs([context.project_state.config.resolve_case_runs_dir(context.project_state.root) / case.id])
        output_dir = candidates[-1] if candidates else case_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_result = None
    result_jsonl = output_dir / "result.jsonl"
    if result_jsonl.is_file():
        lines = [line for line in result_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            runtime_result = RunResult.model_validate_json(lines[-1])

    result = DirectOracleRunner().execute(
        case_dir,
        runtime_result=runtime_result,
        output_dir=output_dir,
        case=case,
        workspace_dir=_optional_path(args.workspace_dir),
    )
    print(f"Oracle status: {result.status.value}")
    print(f"Score: {result.score}/{case.oracle.expected_score}")
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
    return 0 if result.status.value != "error" else 1


def _runtime_run(args: argparse.Namespace) -> int:
    context = _build_context()
    output_dir = _optional_path(args.output_dir) or (Path(context.settings.default_outputs_dir).expanduser().resolve() / "runtime")
    control = RunControl()
    with activate_interrupt_handler(control):
        results = run_tasks_from_jsonl(
            context,
            input_file=Path(args.input_file).expanduser().resolve(),
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
    configure_logging(log_level=settings.log_level.value, log_renderer=settings.log_renderer.value)
    return AppState(project_state=state, settings=settings)


def _run_options(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        interactive=bool(getattr(args, "interactive", False)),
        model_name=getattr(args, "model", None),
        prompt_profile=getattr(args, "prompt_profile", None),
        prompt_append=getattr(args, "prompt_append", None),
        cleanup_workspace=bool(getattr(args, "cleanup_workspace", False)),
        skip_incompatible=bool(getattr(args, "skip_incompatible", False)),
    )


def _optional_path(value: str | None) -> Path | None:
    return Path(value).expanduser().resolve() if value else None


app = cli_main
main = cli_main

__all__ = ["app", "cli_main", "main"]


if __name__ == "__main__":
    raise SystemExit(cli_main())
