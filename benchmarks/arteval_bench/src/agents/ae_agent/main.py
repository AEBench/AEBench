"""Main entry point for running artifact tasks.

Supports both:
- Run from this directory: env=local (host) or env=docker per task in JSONL.
- Used as in-container runner when benchmark (arteval_bench) uploads this agent to /agent.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime

from .run_eval import make_error_result, run_eval
from .utils import (
    AGENT_SUMMARY_FALLBACK_MAX,
    DEFAULT_MODEL,
    LOG_OUTPUT_TRUNCATE_BYTES,
    SUMMARY_BASENAME_TEMPLATE,
    SUMMARY_INSTRUCTION,
    Tee,
    compute_and_write_summary,
    docker_image_from_item,
    env_from_item,
    get_task,
    gpu_from_item,
    interactive_from_item,
    read_task_from_file,
    resolve_project_path,
    safe_task_id,
    timeout_ms_from_item,
    write_task_report,
)


def _build_task_with_summary(task: str, safe_id: str) -> tuple[str, str]:
    """Append summary instruction to task. Returns (task, summary_basename)."""
    summary_basename = SUMMARY_BASENAME_TEMPLATE.format(safe_id=safe_id)
    full_task = task.rstrip() + SUMMARY_INSTRUCTION.format(basename=summary_basename)
    return full_task, summary_basename


def _persist_result(save_path: str, result: dict, log_path: str) -> None:
    """Write result to result.jsonl and append run output to log."""
    with open(f'{save_path}/result.jsonl', 'a+', encoding='utf-8') as fw:
        fw.write(json.dumps(result, ensure_ascii=False) + '\n')
    with open(log_path, 'a', encoding='utf-8') as lf:
        lf.write(f'\nTask finished at {result["timestamp"]}, status: {result.get("status", "unknown")}\n')
        lf.write('\n--- Agent run output ---\n')
        run_out = str(result.get('agent_run_results', ''))
        lf.write(run_out[:LOG_OUTPUT_TRUNCATE_BYTES])
        if len(run_out) > LOG_OUTPUT_TRUNCATE_BYTES:
            lf.write('\n... (truncated)\n')


def _gather_agent_summary(project_path: str, summary_basename: str, result: dict) -> str:
    """Read agent summary file or fallback to truncated run output."""
    summary_file = os.path.join(project_path, summary_basename)
    if os.path.isfile(summary_file):
        try:
            with open(summary_file, encoding='utf-8') as f:
                return f.read()
        except OSError as e:
            logging.warning('Failed to read summary file %s: %s', summary_file, e)
    fallback = str(result.get('agent_run_results', ''))[:AGENT_SUMMARY_FALLBACK_MAX]
    return fallback or '(No summary captured)'


def _persist_skipped(save_path: str, task_id: str, message: str) -> None:
    """Append one result line for a skipped task so summary total is accurate."""
    result = {
        'task_id': task_id,
        'status': 'skipped',
        'message': message,
        'timestamp': datetime.now().isoformat(),
    }
    with open(f'{save_path}/result.jsonl', 'a+', encoding='utf-8') as fw:
        fw.write(json.dumps(result, ensure_ascii=False) + '\n')


def _run_single_task(
    item: dict,
    model: str,
    agent: str,
    save_path: str,
    input_file: str,
    interactive_default: bool,
) -> None:
    """Process a single JSONL task: parse, run, write results and report."""
    env = env_from_item(item)
    docker_image = docker_image_from_item(item, env=env)
    use_gpu = gpu_from_item(item)
    interactive = interactive_from_item(item) or interactive_default
    task_file = item.get('artifact_readme', None)
    task_id = item.get('artifact_id', None)
    timeout_ms = timeout_ms_from_item(item)
    safe_id = safe_task_id(task_id)

    project_path, path_error = resolve_project_path(item, input_file, save_path)
    if path_error:
        print(path_error)
        _persist_skipped(save_path, task_id or safe_id, path_error)
        return
    print(f'Project path: {project_path}')

    raw_task = read_task_from_file(project_path, task_file) if task_file else get_task('README.md')
    task, summary_basename = _build_task_with_summary(raw_task, safe_id)

    task_file_path = os.path.join(save_path, f'current_task_{safe_id}.txt')
    with open(task_file_path, 'w', encoding='utf-8') as f:
        f.write(task)

    timeout_str = str(timeout_ms) if timeout_ms is not None else 'default'
    print(f'Task {task_id}: env={env}, timeout_ms={timeout_str}, gpu={use_gpu}, interactive={interactive}')

    log_path = os.path.join(save_path, f'ae_log_{safe_id}.log')
    with open(log_path, 'w', encoding='utf-8') as lf:
        lf.write(f'Task {task_id} started at {datetime.now().isoformat()}\n')
        lf.write(f'Project path: {project_path}\n')
        lf.write(f'Env: {env}\n\n')

    # Run task (stdout/stderr teed to log), then persist result and report.
    # Note: For env='local', agent_path is ignored; the in-process runner (this package) is used.
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        with Tee(sys.stdout, log_path) as tee_out:
            with Tee(sys.stderr, log_path) as tee_err:
                sys.stdout, sys.stderr = tee_out, tee_err
                result = run_eval(
                    env=env,
                    docker_image=docker_image,
                    project_path=project_path,
                    task_id=task_id,
                    task=task,
                    task_file_path=task_file_path,
                    model=model,
                    agent_path=agent,
                    save_path=save_path,
                    timeout_ms=timeout_ms,
                    use_gpu=use_gpu,
                    interactive=interactive,
                )
    except Exception as e:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        logging.exception('run_eval failed for task %s: %s', task_id, e)
        result = make_error_result(task_id, task, project_path, str(e), env)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    result['timestamp'] = datetime.now().isoformat()
    result['log_file'] = log_path
    _persist_result(save_path, result, log_path)

    agent_summary = _gather_agent_summary(project_path, summary_basename, result)
    write_task_report(save_path, safe_id, task_id, result, log_path, agent_summary)
    print(f'Task {task_id} completed. Status: {result.get("status", "unknown")}')


def main(input_file, model, agent, save_path, interactive_default: bool = False):
    """Main function for running tasks."""
    if not os.path.isfile(input_file):
        logging.error('Input file not found: %s', input_file)
        sys.exit(1)

    print(f'Using model: {model}, agent: {agent}')

    with open(input_file, encoding='utf-8') as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                print(f'Skipping invalid JSON at line {line_no}: {e}')
                _persist_skipped(save_path, f'line_{line_no}', f'Invalid JSON: {e}')
                continue

            _run_single_task(
                item=item,
                model=model,
                agent=agent,
                save_path=save_path,
                input_file=input_file,
                interactive_default=interactive_default,
            )

    total_count, success_count = compute_and_write_summary(save_path)
    print(f'All tasks completed: {success_count}/{total_count} succeeded.')


@dataclass
class _ResolvedConfig:
    """Resolved CLI configuration ready for main()."""

    input_file: str
    model: str
    agent: str
    save_path: str
    interactive_default: bool


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='AE Agent - Run Claude Agent SDK on artifact tasks')
    parser.add_argument(
        '-i',
        '--input_file',
        help='Input JSONL file with tasks',
        default='./data/benchmark/arteval_tasks.jsonl',
    )
    parser.add_argument('-o', '--save_path', help='Result save path', default=None)
    parser.add_argument(
        '-a',
        '--agent',
        help='Agent name (default: ae-agent)',
        default='ae-agent',
    )
    parser.add_argument(
        '-m',
        '--model_name',
        help='Model Name',
        default=DEFAULT_MODEL,
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Enable interactive mode (continue giving agent instructions after task completes)',
    )
    return parser.parse_args()


def _resolve_paths(args: argparse.Namespace) -> _ResolvedConfig:
    """Resolve paths and agent from parsed args."""
    model_name = args.model_name
    agent = args.agent
    input_file = args.input_file
    save_path = args.save_path

    if save_path is None:
        str_model_name = model_name.replace('/', '_').lower()
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        save_path = os.path.join('./outputs', f'ae_{str_model_name}_ae-agent_{timestamp}')

    # When running from this directory (standalone or as arteval_bench agent), use script dir as agent path
    if agent in ('ae-agent', 'ae_agent', 'claude_sdk'):
        agent = os.path.dirname(os.path.abspath(__file__))

    save_path = os.path.abspath(os.path.expanduser(save_path))
    os.makedirs(save_path, exist_ok=True)

    return _ResolvedConfig(
        input_file=input_file,
        model=model_name,
        agent=agent,
        save_path=save_path,
        interactive_default=getattr(args, 'interactive', False),
    )


def cli_main():
    """CLI entry point."""
    args = _parse_args()
    config = _resolve_paths(args)
    print(f'Input file: {config.input_file}')
    print(f'Save path: {config.save_path}')
    print(f'Agent path: {config.agent}')
    main(
        config.input_file,
        config.model,
        config.agent,
        config.save_path,
        interactive_default=config.interactive_default,
    )


if __name__ == '__main__':
    cli_main()
