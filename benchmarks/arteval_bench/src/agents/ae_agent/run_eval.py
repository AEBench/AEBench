"""Orchestration for executing artifact tasks in Docker or on host.

Single entry point: run_eval(env, project_path, task_id, ...).
- env='local'  -> _run_local()      -> runner.run_agent() directly on host
- env != 'local' -> _run_in_docker() -> runner.py executed inside container
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .runner import run_agent
from .utils import (
    DEFAULT_DOCKER_IMAGE,
    apply_timeout_env,
    has_api_key,
    is_local_env,
    parse_eval_score,
    resolve_timeout_ms,
    safe_task_id,
    status_from_exit_code,
    timeout_env_dict,
)

SWEREX_AVAILABLE = False


def _import_swerex():
    """Try importing swerex under both package names (swerex and swe_rex).

    The package was renamed; we support both for backward compatibility.
    Returns (DockerDeploymentConfig, BashAction, CreateBashSessionRequest, UploadRequest)
    or raises ImportError.
    """
    for pkg in ('swerex', 'swe_rex'):
        try:
            mod_docker = __import__(f'{pkg}.deployment.docker', fromlist=['DockerDeploymentConfig'])
            mod_runtime = __import__(
                f'{pkg}.runtime.abstract', fromlist=['BashAction', 'CreateBashSessionRequest', 'UploadRequest']
            )
            return (
                mod_docker.DockerDeploymentConfig,
                mod_runtime.BashAction,
                mod_runtime.CreateBashSessionRequest,
                mod_runtime.UploadRequest,
            )
        except ImportError:
            continue
    raise ImportError("Neither 'swerex' nor 'swe_rex' is installed")


try:
    DockerDeploymentConfig, BashAction, CreateBashSessionRequest, UploadRequest = _import_swerex()
    SWEREX_AVAILABLE = True
except ImportError:
    logging.warning('swerex/swe-rex not available. Docker mode will not work.')


# Progress log every 5 minutes when runner is still running.
_PROGRESS_LOG_INTERVAL_SEC = 300

# Poll interval for checking runner status.
_POLL_INTERVAL_SEC = 10.0


@dataclass
class _RunnerResult:
    """Result from a Docker runner process."""

    exit_code: int
    output: str


def _make_eval_result(
    task_id: str,
    task: str,
    project_path: str,
    agent_output: str,
    status: str,
    run_on_host: bool,
    *,
    container_id: str | None = None,
    saved_image: str | None = None,
    container_stopped: bool = False,
    message_count: int | None = None,
    score: int | None = None,
    test_method: str | None = None,
) -> dict:
    """Build unified eval result dict for both host and Docker modes."""
    result = {
        'task_id': task_id,
        'task': task,
        'project_path': project_path,
        'agent_run_results': agent_output,
        'status': status,
        'run_on_host': run_on_host,
        'container_id': container_id,
        'saved_image': saved_image,
        'container_stopped': container_stopped,
    }
    if message_count is not None:
        result['message_count'] = message_count
    if score is not None:
        result['score'] = score
    if test_method is not None:
        result['test_method'] = test_method
    return result


def make_error_result(
    task_id: str,
    task: str,
    project_path: str,
    error_message: str,
    env: str,
) -> dict:
    """Build result dict for run_eval failure (exception/timeout). Same shape as normal result."""
    return _make_eval_result(
        task_id,
        task,
        project_path,
        error_message,
        'error',
        is_local_env(env),
    )


# ---------------------------------------------------------------------------
# Host mode
# ---------------------------------------------------------------------------


def _check_host_prerequisites() -> bool:
    """Check that docker, python, and API key are available on the host."""
    if not shutil.which('docker'):
        logging.error('Docker is not installed on host.')
        return False
    if subprocess.run(['docker', 'ps'], capture_output=True, timeout=10).returncode != 0:
        logging.error('Docker is not running on host.')
        return False
    if not has_api_key():
        logging.error('Neither ANTHROPIC_API_KEY nor ANTHROPIC_FOUNDRY_API_KEY is set.')
        return False
    return True


def _write_claude_settings(timeout_ms: int):
    """Write ~/.claude/settings.json with timeout configuration."""
    claude_dir = Path.home() / '.claude'
    claude_dir.mkdir(exist_ok=True)
    settings = {'env': timeout_env_dict(timeout_ms)}
    with open(claude_dir / 'settings.json', 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)


async def _run_local(
    project_path,
    task_id,
    task,
    model,
    timeout_ms: int,
    *,
    skip_prereq_check: bool = False,
    interactive: bool = False,
):
    """Run one task on host by delegating to runner.run_agent()."""
    print('=' * 80)
    print('Running task on HOST MACHINE')
    print('=' * 80)

    if not skip_prereq_check and not _check_host_prerequisites():
        raise RuntimeError('Host prerequisites check failed')

    _write_claude_settings(timeout_ms)
    # run_eval() already calls apply_timeout_env() for local; no need to duplicate here.

    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        raise RuntimeError(f'Project path does not exist: {project_path}')

    print(f'Project path: {project_path}')
    print(f'Task ID: {task_id}')
    print(f'Model: {model}')

    agent_result = await run_agent(
        model,
        task,
        env='local',
        artifact_path=project_path,
        timeout_ms=timeout_ms,
        interactive=interactive,
    )

    return _make_eval_result(
        task_id,
        task,
        project_path,
        agent_result['output'],
        status_from_exit_code(agent_result['exit_code']),
        run_on_host=True,
        message_count=agent_result['message_count'],
    )


# ---------------------------------------------------------------------------
# Benchmark flow: run agent then evaluation script (same as claude_sdk in arteval_bench)
# ---------------------------------------------------------------------------

# Default timeout for running the evaluation script (e.g. pytest or oracle script) on host.
_EVAL_SCRIPT_TIMEOUT_SEC = 600


async def _run_agent_then_eval_async(
    project_path: str,
    task_id: str,
    task: str,
    model: str,
    test_method: str | None,
    save_path: str,
    timeout_ms: int | None = None,
    *,
    skip_prereq_check: bool = False,
) -> dict:
    """Run agent on host, then run evaluation script (test_method); return result with score.

    Used by arteval_bench when run_on_host=True and agent is ae_agent. Same flow as claude_sdk:
    agent run → run test_method (e.g. cd project_path && python _agent_eval/main.py) → parse score.
    """
    timeout_ms = resolve_timeout_ms(timeout_ms)
    if not skip_prereq_check and not _check_host_prerequisites():
        raise RuntimeError('Host prerequisites check failed')
    apply_timeout_env(timeout_ms)
    _write_claude_settings(timeout_ms)

    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        raise RuntimeError(f'Project path does not exist: {project_path}')

    # 1. Run agent
    agent_result = await run_agent(
        model,
        task,
        env='local',
        artifact_path=project_path,
        timeout_ms=timeout_ms,
        interactive=False,
    )
    agent_output = agent_result['output']
    agent_status = status_from_exit_code(agent_result['exit_code'])

    # 2. Run evaluation script if provided
    if test_method and test_method.strip():
        try:
            eval_cmd = f'cd {project_path} && {test_method}'
            eval_result = subprocess.run(
                eval_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_EVAL_SCRIPT_TIMEOUT_SEC,
            )
            test_output = (eval_result.stdout or '').strip()
            score = parse_eval_score(test_output)
            status = 'success' if agent_status == 'success' else agent_status
        except subprocess.TimeoutExpired:
            test_output = '(evaluation script timed out)'
            score = 0
            status = 'error'
        except Exception as e:
            test_output = str(e)
            score = 0
            status = f'error: {e}'
    else:
        test_output = ''
        score = 0
        status = agent_status

    return _make_eval_result(
        task_id,
        task,
        project_path,
        agent_output,
        status,
        run_on_host=True,
        score=score,
        test_method=test_method or '',
    )


def run_agent_then_eval(
    project_path: str,
    task_id: str,
    task: str,
    model: str,
    test_method: str | None,
    save_path: str,
    timeout_ms: int | None = None,
    *,
    skip_prereq_check: bool = False,
) -> dict:
    """Synchronous entry: run agent on host then evaluation script; return result with score.

    Called by arteval_bench run_eval_in_env.run_eval_on_host when agent is ae_agent.
    """
    return asyncio.run(
        _run_agent_then_eval_async(
            project_path,
            task_id,
            task,
            model,
            test_method,
            save_path,
            timeout_ms=timeout_ms,
            skip_prereq_check=skip_prereq_check,
        )
    )


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def _validate_agent_path(agent_path: str) -> None:
    """Ensure agent_path exists and has required files."""
    if not agent_path or not os.path.isdir(agent_path):
        raise RuntimeError(f'Agent path does not exist: {agent_path}')
    for name in ('runner.sh', 'runner.py', 'install.sh'):
        if not os.path.isfile(os.path.join(agent_path, name)):
            raise RuntimeError(f'Agent path missing required file: {name}')


def _stdin_is_tty() -> bool:
    """Return True if stdin is a real TTY (required for docker exec -it)."""
    return hasattr(sys.stdin, 'isatty') and sys.stdin.isatty()


def _run_docker_cmd(
    args: list[str],
    *,
    timeout: int = 60,
    on_success_message: str | None = None,
    on_fail_message: str = 'docker command failed',
) -> bool:
    """Run a docker subprocess. Return True if returncode is 0, else False and log."""
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0:
            if on_success_message:
                print(on_success_message)
            return True
        logging.warning('%s: %s', on_fail_message, (r.stderr or r.stdout or '').strip())
        return False
    except subprocess.TimeoutExpired:
        logging.warning('docker command timed out (timeout=%ds)', timeout)
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logging.warning('docker command error: %s', e)
        return False


def _merge_tree(src_dir: str, dst_dir: str, exclude: tuple[str, ...] = ('.venv', '.git', '__pycache__')) -> None:
    """Merge src_dir into dst_dir (copy missing/updated from src into dst)."""
    os.makedirs(dst_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        if name in exclude:
            continue
        src_path = os.path.join(src_dir, name)
        dst_path = os.path.join(dst_dir, name)
        if os.path.isdir(src_path):
            if os.path.isdir(dst_path):
                _merge_tree(src_path, dst_path, exclude)
            elif os.path.exists(dst_path):
                logging.warning('Sync skip (destination not a dir): %s', dst_path)
            else:
                shutil.copytree(src_path, dst_path)
        else:
            try:
                shutil.copy2(src_path, dst_path)
            except OSError as e:
                logging.warning('Sync copy failed %s -> %s: %s', src_path, dst_path, e)


def _sync_workspace(container_id: str, project_path: str) -> None:
    """Copy /repo from container back to host project_path.

    Uses a temp copy plus merge with excludes to avoid overwriting host .venv
    (e.g. when container has .venv/lib64 as a directory and host has it as a
    symlink, which would cause 'cannot overwrite non-directory with directory').
    """
    project_abs = os.path.abspath(project_path)
    if not os.path.isdir(project_abs):
        print(f'WARNING: project_path missing, skipping sync: {project_abs}')
        return

    # Exclude .venv* and .git to avoid overwriting host venv or permission issues
    def _skip_sync(name: str) -> bool:
        return name == '.git' or name == '.venv' or name.startswith('.venv-')

    with tempfile.TemporaryDirectory(prefix='ae_sync_') as tmp:
        dest_tmp = os.path.join(tmp, 'repo')
        if not _run_docker_cmd(
            ['docker', 'cp', f'{container_id}:/repo', dest_tmp],
            timeout=600,
            on_fail_message='docker cp (to temp) failed',
        ):
            return
        # docker cp container:/repo dest_tmp puts repo contents into dest_tmp
        repo_src = dest_tmp
        for name in os.listdir(repo_src):
            if _skip_sync(name):
                continue
            src_path = os.path.join(repo_src, name)
            dst_path = os.path.join(project_abs, name)
            try:
                if os.path.isdir(src_path):
                    if os.path.exists(dst_path):
                        _merge_tree(src_path, dst_path)
                    else:
                        shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)
            except (OSError, shutil.Error) as e:
                logging.warning('Sync item %s failed: %s', name, e)
        print(f'Synced container /repo -> {project_abs}')


def _commit_container(container_id: str, task_id: str) -> str | None:
    """Commit container state as a Docker image. Returns image tag or None."""
    sid = safe_task_id(task_id, fallback='unknown_task')
    image_tag = f'ae-agent-{sid.lower()}:latest'
    if not _run_docker_cmd(
        ['docker', 'commit', container_id, image_tag],
        timeout=600,
        on_fail_message='docker commit failed',
    ):
        return None
    return image_tag


def _stop_container(container_id: str) -> bool:
    """Stop a Docker container. Returns True if stopped successfully."""
    return _run_docker_cmd(
        ['docker', 'stop', container_id],
        timeout=60,
        on_success_message=f'Stopped container {container_id}.',
        on_fail_message='docker stop failed',
    )


def _save_container(
    container_id: str,
    project_path: str,
    task_id: str,
) -> tuple[str | None, bool]:
    """Sync workspace, commit image, and stop container."""
    _sync_workspace(container_id, project_path)
    image_tag = _commit_container(container_id, task_id)
    stopped = _stop_container(container_id)
    return image_tag, stopped


async def _get_container_id(runtime) -> str | None:
    """Get container hostname/ID from inside the container."""
    try:
        cid = (
            await _run_bash(
                runtime,
                'cat /etc/hostname 2>/dev/null || hostname 2>/dev/null || echo ""',
                10.0,
            )
        ).strip()
        return cid if cid and cid != 'unknown' else None
    except (AttributeError, TypeError, ValueError) as e:
        logging.debug('Could not get container ID: %s', e)
        return None


def _shell_escape(s: str) -> str:
    """Escape a string for use inside single-quoted shell arguments."""
    return s.replace("'", "'\"'\"'")


def _build_api_env_dict(timeout_ms: int) -> dict[str, str]:
    """Build env vars dict for API keys, Foundry, and timeouts.

    Single source of truth for _docker_exec_env_args and _setup_container_env.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    foundry_key = os.environ.get('ANTHROPIC_FOUNDRY_API_KEY')
    env = dict(timeout_env_dict(timeout_ms))
    if api_key:
        env['ANTHROPIC_API_KEY'] = api_key
    if foundry_key:
        env['ANTHROPIC_FOUNDRY_API_KEY'] = foundry_key
        if not api_key:
            env['ANTHROPIC_API_KEY'] = foundry_key
    foundry_url = os.environ.get('ANTHROPIC_FOUNDRY_BASE_URL')
    if foundry_url:
        env['ANTHROPIC_FOUNDRY_BASE_URL'] = foundry_url
    if os.environ.get('CLAUDE_CODE_USE_FOUNDRY') == '1':
        env['CLAUDE_CODE_USE_FOUNDRY'] = '1'
    return env


def _docker_exec_env_args(timeout_ms: int) -> list[str]:
    """Build -e VAR=value args for docker exec (env vars needed by runner.py)."""
    env = _build_api_env_dict(timeout_ms)
    args = []
    for k, v in env.items():
        args.extend(['-e', f'{k}={v}'])
    return args


async def _upload_task(runtime, task: str, task_file_path: str | None):
    """Upload task description to /agent/current_task.txt inside container."""
    tmpdir = tempfile.mkdtemp(prefix='ae_task_')
    try:
        dest = os.path.join(tmpdir, 'current_task.txt')
        if task_file_path and os.path.isfile(task_file_path):
            shutil.copy2(task_file_path, dest)
        else:
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(task)
        await runtime.upload(UploadRequest(source_path=tmpdir, target_path='/agent_task_file'))
        await _run_bash(
            runtime,
            'cp /agent_task_file/current_task.txt /agent/current_task.txt',
            10.0,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _setup_container_env(runtime, timeout_ms: int):
    """Set timeout and API keys inside the container."""
    env = _build_api_env_dict(timeout_ms)
    parts = [f"export {k}='{_shell_escape(v)}'" for k, v in env.items()]
    await _run_bash(runtime, ' && '.join(parts))

    if not has_api_key():
        logging.warning('No API key found. Runner may fail.')


def _extract_output(res) -> str:
    """Extract output string from swe-rex/bash action result."""
    return str(getattr(res, 'output', '')).strip()


async def _run_bash(runtime, command: str, timeout: float = 10.0) -> str:
    """Run a Bash command in the container session and return its output. Reduces duplication."""
    res = await runtime.run_in_session(BashAction(command=command, timeout=timeout))
    return _extract_output(res)


async def _start_runner_background(runtime, model: str) -> str | None:
    """Start runner.sh in background, return pid or None."""
    await _run_bash(
        runtime,
        'rm -f /agent/runner.live.log && touch /agent/runner.live.log',
        10.0,
    )
    output = await _run_bash(
        runtime,
        (
            f'stdbuf -oL -eL /agent/runner.sh "{model}" /agent/current_task.txt '
            f'> /agent/runner.live.log 2>&1 & '
            f'RUNNER_PID=$!; sleep 1; echo RUNNER_PID=$RUNNER_PID'
        ),
        30.0,
    )
    pid = None
    for line in output.split('\n'):
        if 'RUNNER_PID=' in line:
            pid = line.split('RUNNER_PID=', 1)[1].strip()
            break
    if not pid or not pid.strip().isdigit():
        await asyncio.sleep(2)
        pid = await _run_bash(
            runtime,
            "ps aux | grep '[r]unner.py' | awk '{print $2}' | head -1",
            10.0,
        )
    pid = (pid or '').strip()
    return pid if pid.isdigit() else None


async def _read_runner_log(runtime, elapsed: float, last_log: str) -> str:
    """Read live log and print new content. Returns updated last_log."""
    try:
        cur = await _run_bash(runtime, 'cat /agent/runner.live.log 2>/dev/null || echo ""', 30.0)
        if cur and cur != last_log:
            new = cur[len(last_log) :].strip() if cur.startswith(last_log) else cur
            if new:
                print(f'[log @ {elapsed:.0f}s]\n{new}', flush=True)
            return cur
    except (AttributeError, TypeError, ValueError) as e:
        logging.debug('Log read error: %s', e)
    return last_log


async def _check_runner_exited(runtime, pid: str | None) -> _RunnerResult | None:
    """Check if runner process has exited. Returns _RunnerResult if exited, else None."""
    if pid and pid.isdigit():
        ps_out = await _run_bash(runtime, f'ps -p {pid} >/dev/null 2>&1; echo $?', 10.0)
        if ps_out.strip() != '0':
            code = await _run_bash(runtime, f'wait {pid} 2>/dev/null; echo $?', 30.0)
            ec = int(code.strip()) if code.strip().isdigit() else -1
            return _RunnerResult(exit_code=ec, output=f'exit_code={ec}')
    else:
        # PID was never captured (e.g. RUNNER_PID parse failed); detect exit by process count.
        cnt = await _run_bash(runtime, "ps aux | grep '[r]unner.py' | wc -l", 10.0)
        if not cnt.strip().isdigit() or int(cnt.strip()) == 0:
            return _RunnerResult(exit_code=-1, output='exit_code=unknown')
    return None


async def _handle_runner_timeout(runtime, pid: str | None) -> None:
    """Kill runner and print log tail on timeout."""
    if pid and pid.isdigit():
        try:
            await _run_bash(
                runtime,
                f'kill -TERM {pid} 2>/dev/null || kill -9 {pid} 2>/dev/null || true',
                10.0,
            )
        except (AttributeError, TypeError, ConnectionError) as e:
            logging.debug('Kill runner failed: %s', e)
    try:
        tail_str = await _run_bash(runtime, 'tail -n 200 /agent/runner.live.log', 30.0)
        print(f'Log tail (timeout):\n{tail_str}', flush=True)
    except (AttributeError, TypeError, ValueError) as e:
        logging.debug('Could not read log tail: %s', e)


async def _monitor_runner(runtime, model: str, timeout_s: float) -> _RunnerResult:
    """Start runner.sh in background and poll logs until it finishes or times out."""
    pid = await _start_runner_background(runtime, model)
    print(f'Runner started (pid={pid})', flush=True)

    start = time.monotonic()
    last_log = ''
    last_progress_at = 0.0

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout_s:
            break

        last_log = await _read_runner_log(runtime, elapsed, last_log)
        if elapsed - last_progress_at >= _PROGRESS_LOG_INTERVAL_SEC:
            print(f'[still running @ {elapsed:.0f}s]', flush=True)
            last_progress_at = elapsed

        result = await _check_runner_exited(runtime, pid)
        if result is not None:
            print(f'Runner finished (exit_code={result.exit_code})', flush=True)
            return result

        await asyncio.sleep(_POLL_INTERVAL_SEC)

    await _handle_runner_timeout(runtime, pid)
    raise TimeoutError(f'Runner exceeded timeout {timeout_s}s')


# ---------------------------------------------------------------------------
# Docker mode
# ---------------------------------------------------------------------------


async def _run_interactive_in_container(
    container_id: str,
    task_id: str,
    task: str,
    project_path: str,
    model: str,
    timeout_ms: int,
) -> dict:
    """Run task + interactive in foreground via docker exec -it.

    The same agent session handles both task and follow-up, preserving context.
    """
    print(
        '\n'
        + '=' * 60
        + '\nTask + interactive mode (foreground, context preserved).\n'
        + "Type 'quit' or 'exit' to end the interactive session.\n"
        + '=' * 60,
        flush=True,
    )
    exec_args = [
        'docker',
        'exec',
        '-it',
        *_docker_exec_env_args(timeout_ms),
        container_id,
        'python3',
        '-u',
        '/agent/runner.py',
        model,
        '/agent/current_task.txt',
        '--interactive',
    ]
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            exec_args,
            stdin=sys.__stdin__,
            stdout=sys.__stdout__,
            stderr=sys.__stderr__,
        )
        run_exit_code = proc.returncode
    except (OSError, subprocess.SubprocessError) as e:
        logging.warning('Foreground execution failed for task %s: %s', task_id, e)
        run_exit_code = 1

    return _make_eval_result(
        task_id,
        task,
        project_path,
        f'Interactive session (exit_code={run_exit_code})',
        status_from_exit_code(run_exit_code),
        run_on_host=False,
    )


async def _run_in_docker(  # noqa: C901
    deployment,
    project_path,
    task_id,
    task,
    model,
    agent_path,
    _save_path: str,
    timeout_ms: int,
    *,
    task_file_path: str | None = None,
    interactive: bool = False,
) -> dict:
    """Run task inside a Docker container.

    _save_path: Unused in Docker path (results are returned to main.py which writes reports).
    Kept for a consistent run_eval() → _run_in_docker() API.
    """
    if not SWEREX_AVAILABLE:
        raise RuntimeError('swerex is not available.')

    _validate_agent_path(agent_path)
    await deployment.start()
    runtime = deployment.runtime

    timeout_s = timeout_ms / 1000.0
    # swe-rex doesn't expose a public API for session-level timeout;
    # override the internal config as a workaround.
    if hasattr(runtime, '_config'):
        runtime._config.timeout = timeout_s

    await runtime.create_session(CreateBashSessionRequest())

    print('Uploading project files...', flush=True)
    await runtime.upload(UploadRequest(source_path=project_path, target_path='/repo'))
    await _run_bash(runtime, 'cd /repo')

    print('Uploading agent scripts...', flush=True)
    await runtime.upload(UploadRequest(source_path=agent_path, target_path='/agent'))
    await _run_bash(
        runtime,
        'chmod +x /agent/runner.sh /agent/install.sh 2>/dev/null; /agent/install.sh',
        120.0,  # install.sh may run pip install; allow up to 2 minutes
    )

    await _upload_task(runtime, task, task_file_path)
    await _setup_container_env(runtime, timeout_ms)

    container_id = await _get_container_id(runtime)
    result = None

    try:
        # Prefer foreground interactive when container_id is available and stdin is a TTY.
        if interactive and container_id and _stdin_is_tty():
            result = await _run_interactive_in_container(container_id, task_id, task, project_path, model, timeout_ms)
        else:
            if interactive and not _stdin_is_tty():
                print(
                    'WARNING: Interactive mode requires a terminal (TTY). Running task in non-interactive mode.',
                    flush=True,
                )
            elif interactive and not container_id:
                print(
                    'WARNING: Cannot get container ID; falling back to non-interactive mode.',
                    flush=True,
                )
            # Background run: start runner, poll logs, then return result.
            run_results = await _monitor_runner(runtime, model, timeout_s)
            print(f'Runner result: {run_results}', flush=True)
            result = _make_eval_result(
                task_id,
                task,
                project_path,
                run_results.output,
                status_from_exit_code(run_results.exit_code),
                run_on_host=False,
            )
    except Exception as e:
        logging.error('Task %s error: %s', task_id, e, exc_info=True)
        result = _make_eval_result(
            task_id,
            task,
            project_path,
            str(e),
            'error',
            run_on_host=False,
        )
    finally:
        if not container_id:
            container_id = await _get_container_id(runtime)

        saved_image, stopped = None, False
        if container_id:
            try:
                saved_image, stopped = _save_container(container_id, project_path, task_id)
            except (OSError, subprocess.SubprocessError) as e:
                logging.warning('Save container failed: %s', e)

        try:
            await deployment.stop()
        except Exception as e:
            # Container may already be stopped; deployment.close() can fail with
            # ClientConnectorError when the remote service port is gone.
            logging.warning('deployment.stop() failed for task %s: %s', task_id, e)

        if result is None:
            # Exception occurred before any result was set (e.g. before try body ran
            # or a BaseException was raised). Ensure we always have a dict for update/return.
            result = _make_eval_result(
                task_id,
                task,
                project_path,
                'Execution interrupted or failed before result was set.',
                'error',
                run_on_host=False,
            )
        result.update(
            container_id=container_id,
            saved_image=saved_image,
            container_stopped=stopped,
        )

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_eval(
    env: str,
    project_path: str,
    task_id: str,
    task: str,
    model: str,
    agent_path: str,
    save_path: str,
    docker_image: str | None = None,
    timeout_ms: int | None = None,
    *,
    skip_prereq_check: bool = False,
    use_gpu: bool = False,
    task_file_path: str | None = None,
    interactive: bool = False,
) -> dict:
    """Run task in the given environment (local host or Docker).

    Single entry point — called from main.py for each JSONL task.
    """
    timeout_ms = resolve_timeout_ms(timeout_ms)
    if is_local_env(env):
        apply_timeout_env(timeout_ms)  # Docker mode uses container env only; no host env.
        print(f'Task {task_id}: HOST (timeout={timeout_ms}ms, interactive={interactive})')
        return asyncio.run(
            _run_local(
                project_path,
                task_id,
                task,
                model,
                timeout_ms,
                skip_prereq_check=skip_prereq_check,
                interactive=interactive,
            )
        )

    if not SWEREX_AVAILABLE:
        raise RuntimeError('SWE-ReX not available. Install swe-rex for Docker mode.')

    image = docker_image or DEFAULT_DOCKER_IMAGE
    docker_args = [
        '--privileged',
        '--cgroupns=host',
        '-e',
        'KIND_EXPERIMENTAL_CONTAINERD_SNAPSHOTTER=native',
    ]
    if use_gpu:
        docker_args.extend(['--gpus', 'all'])

    config = DockerDeploymentConfig(
        image=image,
        startup_timeout=1200.0,
        docker_args=docker_args,
    )
    deployment = config.get_deployment()

    gpu_note = ' (GPU)' if use_gpu else ''
    print(f'Task {task_id}: DOCKER (image={image}, timeout={timeout_ms}ms){gpu_note}')
    return asyncio.run(
        _run_in_docker(
            deployment,
            project_path,
            task_id,
            task,
            model,
            agent_path,
            save_path,
            timeout_ms,
            task_file_path=task_file_path,
            interactive=interactive,
        )
    )
