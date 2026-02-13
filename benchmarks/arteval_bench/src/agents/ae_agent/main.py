"""Main entry point for running artifact tasks (host or Docker).

Supports both:
- Run from this directory: env=local (host) or env=docker per task in JSONL.
- Used as in-container runner when benchmark uploads this agent to /agent.
"""

import argparse
import json
import os
import sys
from datetime import datetime

from .run_eval import run_eval
from .utils import (
    compute_and_write_summary,
    docker_image_from_item,
    env_from_item,
    get_task,
    gpu_from_item,
    interactive_from_item,
    read_task_from_file,
    resolve_project_path,
    safe_task_id,
    Tee,
    timeout_ms_from_item,
    write_task_report,
)


def main(input_file, model, agent, save_path, interactive_default: bool = False):
    """Main function for running tasks."""
    print(f'Using model: {model}, agent: {agent}')

    with open(input_file) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                print(f'Skipping invalid JSON line: {line}')
                continue

            env = env_from_item(item)
            docker_image = docker_image_from_item(item)
            use_gpu = gpu_from_item(item)
            interactive = interactive_from_item(item) or interactive_default
            task_file = item.get("artifact_readme", None)
            task_id = item.get("artifact_id", None)
            timeout_ms = timeout_ms_from_item(item)
            safe_id = safe_task_id(task_id)

            project_path, path_error = resolve_project_path(item, input_file, save_path)
            if path_error:
                print(path_error)
                continue
            print(f"Project path: {project_path}")

            task = read_task_from_file(project_path, task_file) if task_file else get_task("README.md")
            summary_basename = f'ae_summary_{safe_id}.md'
            task = task.rstrip() + f"\n\nAt the end, write a brief summary of what you did and the result to {summary_basename} in the artifact root (so it can be included in the report)."

            task_file_path = os.path.join(save_path, f'current_task_{safe_id}.txt')
            with open(task_file_path, 'w', encoding='utf-8') as f:
                f.write(task)

            print(f"Task {task_id}: env={env}, timeout_ms={timeout_ms if timeout_ms is not None else 'default'}, gpu={use_gpu}, interactive={interactive}")

            log_path = os.path.join(save_path, f'ae_log_{safe_id}.log')
            with open(log_path, 'w', encoding='utf-8') as lf:
                lf.write(f"Task {task_id} started at {datetime.now().isoformat()}\n")
                lf.write(f"Project path: {project_path}\n")
                lf.write(f"Env: {env}\n\n")
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
                            model=model,
                            agent_path=agent,
                            save_path=save_path,
                            timeout_ms=timeout_ms,
                            use_gpu=use_gpu,
                            task_file_path=task_file_path,
                            interactive=interactive,
                        )
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

            result["timestamp"] = datetime.now().isoformat()
            result["log_file"] = log_path
            with open(f"{save_path}/result.jsonl", "a+", encoding="utf-8") as fw:
                fw.write(json.dumps(result, ensure_ascii=False) + "\n")
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\nTask finished at {result['timestamp']}, status: {result.get('status', 'unknown')}\n")
                lf.write("\n--- Agent run output ---\n")
                run_out = str(result.get("agent_run_results", ""))
                lf.write(run_out[:50000])
                if len(run_out) > 50000:
                    lf.write("\n... (truncated)\n")

            summary_file = os.path.join(project_path, summary_basename)
            agent_summary = ""
            if os.path.isfile(summary_file):
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        agent_summary = f.read()
                except Exception:
                    pass
            if not agent_summary:
                agent_summary = (str(result.get("agent_run_results", ""))[:8000] or "(No summary captured)")
            write_task_report(save_path, safe_id, task_id, result, log_path, agent_summary)
            print(f"Task {task_id} completed. Status: {result.get('status', 'unknown')}")

    total_count, success_count = compute_and_write_summary(save_path)
    print(f"All tasks completed: {success_count}/{total_count} succeeded.")


def cli_main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='AE Agent - Run Claude Agent SDK on artifact tasks (host or Docker)')
    parser.add_argument('-i', '--input_file', help='Input JSONL file with tasks', default='./data/benchmark/arteval_tasks.jsonl')
    parser.add_argument('-o', '--save_path', help='Result save path', default=None)
    parser.add_argument('-a', '--agent', help='Agent name (default: ae_agent)', default='ae_agent')
    parser.add_argument('-m', '--model_name', help='Model Name', default='claude-sonnet-4-5-20250929')
    parser.add_argument('--interactive', action='store_true', help='Enable interactive mode (continue giving agent instructions after task completes)')
    args = parser.parse_args()
    model_name = args.model_name
    agent = args.agent
    input_file = args.input_file
    save_path = args.save_path
    if save_path is None:
        str_model_name = model_name.replace('/', '_').lower()
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        save_path = os.path.join('./outputs', f'ae_{str_model_name}_ae_agent_{timestamp}')
    # When running from this directory, use it as agent path
    if agent in ('ae-agent', 'ae_agent', 'claude_sdk'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        agent = script_dir
    save_path = os.path.abspath(os.path.expanduser(save_path))
    os.makedirs(save_path, exist_ok=True)
    interactive_default = getattr(args, 'interactive', False)
    print(f"Input file: {input_file}")
    print(f"Save path: {save_path}")
    print(f"Agent path: {agent}")
    main(input_file, model_name, agent, save_path, interactive_default=interactive_default)


if __name__ == '__main__':
    cli_main()
