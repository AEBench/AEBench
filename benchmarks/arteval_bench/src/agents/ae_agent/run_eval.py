"""Runner for executing artifact tasks in Docker or on host.

Single entry point: run_eval(env, project_path, task_id, task, model, agent_path, save_path, ...).
- env='local' → run on host (internal: _run_local).
- env != 'local' → run in Docker (internal: run_eval_in_env).
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .utils import DEFAULT_TIMEOUT_MS, safe_task_id

# Try to import SWE-ReX (historically called "swerex") for Docker deployment.
SWEREX_AVAILABLE = False

try:
    from swerex.deployment.docker import DockerDeploymentConfig
    from swerex.runtime.abstract import BashAction, Command, CreateBashSessionRequest, UploadRequest
    SWEREX_AVAILABLE = True
except ImportError:
    try:
        from swe_rex.deployment.docker import DockerDeploymentConfig
        from swe_rex.runtime.abstract import BashAction, Command, CreateBashSessionRequest, UploadRequest
        SWEREX_AVAILABLE = True
    except ImportError:
        SWEREX_AVAILABLE = False
        print("WARNING: swerex/swe-rex not available. Docker mode will not work.", file=sys.stderr)


def build_system_prompt(artifact_path: str, task: str) -> str:
    """Build the system prompt for running an artifact task on the host."""
    return f"""You are an experienced software engineer completing an artifact task.

ENVIRONMENT SETUP (HOST MACHINE - NOT DOCKER):
- You are running DIRECTLY on the host machine (NOT inside a Docker container)
- Docker daemon is already running on this host
- When you use Kind to create Kubernetes clusters, they will be created using the host's Docker
- This avoids Docker-in-Docker compatibility issues
- You may need sudo for some operations

ARTIFACT LOCATION:
- The artifact repository is located at: {artifact_path}
- Start by changing to this directory: cd {artifact_path}

YOUR TASK:
{task}

TIMEOUT CONFIGURATION (CRITICAL):
- Long-running commands (builds, tests, Kind cluster creation) are expected
- DO NOT set short timeouts - let commands complete naturally
- Kind cluster creation can take 5-10 minutes
- Full benchmark runs can take hours

IMPORTANT GUIDELINES:
1. First, cd to {artifact_path} and examine the directory structure
2. Follow the README instructions step by step
3. You MUST execute every verification step, test, or command that the README (or referenced docs like TESTBED.md) says is required for evaluation or reproduction. Do NOT skip any such step just because the README mentions that it may take a long time. Long runtimes are expected; run each verification and wait for completion.
4. If you see 'sudo' in instructions, you can use it (or skip if already root)
5. Use the Bash tool to run commands, Read tool to inspect files
6. Work systematically through setup, build, and experiment execution
7. If you encounter errors, debug and resolve them using available tools
8. For Kind clusters, they will work properly since you're on the host (not DinD)"""


def check_prerequisites_on_host() -> bool:
    """Check that required tools (docker, python, API key) are available on the host. Returns True if OK."""
    if not shutil.which("docker"):
        print("ERROR: Docker is not installed on host.", file=sys.stderr)
        return False
    result = subprocess.run(["docker", "ps"], capture_output=True, timeout=10)
    if result.returncode != 0:
        print("ERROR: Docker is not running on host.", file=sys.stderr)
        return False
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY must be set.", file=sys.stderr)
        return False
    return True


def setup_claude_settings_on_host(timeout_ms: int):
    """Set up ~/.claude/settings.json with timeout configuration on host."""
    claude_dir = Path.home() / ".claude"
    settings_file = claude_dir / "settings.json"
    claude_dir.mkdir(exist_ok=True)
    settings = {
        "env": {
            "BASH_MAX_TIMEOUT_MS": str(timeout_ms),
            "BASH_DEFAULT_TIMEOUT_MS": str(timeout_ms),
        }
    }
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)
    print(f"Created {settings_file} with timeout configuration: {timeout_ms} ms.")


async def _run_local(
    project_path, task_id, task, model, agent_path, save_path, timeout_ms: int, *,
    skip_prereq_check: bool = False, interactive: bool = False
):
    """Internal: run one task on the host (no Docker). Used by run_eval when env='local'."""
    print("=" * 80)
    print("Running task directly on HOST MACHINE (not in Docker)")
    print("=" * 80)

    if not skip_prereq_check and not check_prerequisites_on_host():
        raise RuntimeError("Host prerequisites check failed (docker, ANTHROPIC_API_KEY)")

    setup_claude_settings_on_host(timeout_ms)

    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        raise RuntimeError(f"Project path does not exist: {project_path}")

    print(f"Project path: {project_path}")
    print(f"Task ID: {task_id}")
    print(f"Model: {model}")

    try:
        from claude_agent_sdk import ClaudeAgentOptions
        if interactive:
            from claude_agent_sdk import ClaudeSDKClient
        else:
            from claude_agent_sdk import query
    except ImportError as e:
        raise RuntimeError(f"claude_agent_sdk not installed: {e}. Install with: pip install claude-agent-sdk")

    system_prompt = build_system_prompt(project_path, task)
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Write", "Bash"],
        setting_sources=["user"],
    )

    os.environ['BASH_MAX_TIMEOUT_MS'] = str(timeout_ms)
    os.environ['BASH_DEFAULT_TIMEOUT_MS'] = str(timeout_ms)

    message_count = 0
    run_results_output = ""

    if interactive:
        print("Starting Claude Agent SDK (Host Mode, Interactive)...")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                f"Please start the artifact task. Begin by changing to the artifact directory at {project_path} and examining its contents."
            )
            async for message in client.receive_response():
                message_count += 1
                if message_count % 10 == 0:
                    print(f"[Progress] Processed {message_count} messages...")
                msg_str = str(message)
                print(msg_str)
                if 'ResultMessage' in msg_str or 'TextBlock' in msg_str:
                    run_results_output = msg_str

            print(f"Claude Agent SDK execution completed. Total messages: {message_count}")
            print("\n" + "=" * 60)
            print("Interactive mode - Type your follow-up instructions (or 'quit'/'exit' to end).")
            print("=" * 60 + "\n")

            while True:
                try:
                    user_input = input("\n>>> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting interactive mode.", flush=True)
                    break
                if not user_input:
                    continue
                if user_input.lower() in ('quit', 'exit', 'q'):
                    print("Exiting interactive mode.", flush=True)
                    break

                await client.query(user_input)
                async for msg in client.receive_response():
                    msg_str = str(msg)
                    print(msg_str)
                    if 'ResultMessage' in msg_str or 'TextBlock' in msg_str:
                        run_results_output = msg_str
    else:
        print("Starting Claude Agent SDK (Host Mode)...")
        try:
            async for message in query(
                prompt=f"Please start the artifact task. Begin by changing to the artifact directory at {project_path} and examining its contents.",
                options=options
            ):
                message_count += 1
                if message_count % 10 == 0:
                    print(f"[Progress] Processed {message_count} messages...")
                msg_str = str(message)
                print(msg_str)
                if 'ResultMessage' in msg_str or 'TextBlock' in msg_str:
                    run_results_output = msg_str

            print(f"Claude Agent SDK execution completed. Total messages: {message_count}")

        except Exception as e:
            print(f"ERROR: Claude Agent SDK execution failed: {e}")
            import traceback
            traceback.print_exc()
            run_results_output = f"Error: {e}"

    result = {
        'task_id': task_id,
        'task': task,
        'project_path': project_path,
        'agent_run_results': run_results_output,
        'message_count': message_count,
        'status': 'success' if message_count > 0 else 'error',
        'run_on_host': True,
        'container_id': None,
        'saved_image': None,
        'container_stopped': False,
    }

    return result


def _save_container_as_image(container_id: str, project_path: str, task_id: str) -> tuple[str | None, bool]:
    """Save Docker container as image (docker cp, commit, stop). Returns (saved_image_tag or None, container_stopped)."""
    project_path_abs = os.path.abspath(project_path)
    if os.path.isdir(project_path_abs):
        try:
            cp_proc = subprocess.run(
                ["docker", "cp", f"{container_id}:/repo/.", project_path_abs],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if cp_proc.returncode == 0:
                print(f"Synced container /repo to host workspace: {project_path_abs}")
            else:
                print(
                    f"WARNING: docker cp failed (container {container_id} -> {project_path_abs}): "
                    f"{cp_proc.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            print(f"WARNING: docker cp timed out copying /repo from container {container_id}")
        except Exception as e:
            print(f"WARNING: Exception during docker cp from container {container_id}: {e}")
    else:
        print(f"WARNING: project_path does not exist, skipping workspace sync: {project_path_abs}")

    sid = safe_task_id(task_id, fallback="unknown_task")
    saved_image = f"ae-agent-{sid.lower()}:latest"
    try:
        commit_proc = subprocess.run(
            ["docker", "commit", container_id, saved_image],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if commit_proc.returncode == 0:
            print(f"Saved container {container_id} as image '{saved_image}'.")
        else:
            print(
                f"WARNING: docker commit failed for container {container_id}: "
                f"{commit_proc.stderr.strip()}"
            )
            saved_image = None
    except Exception as e:
        print(f"WARNING: Exception during docker commit for container {container_id}: {e}")
        saved_image = None

    container_stopped = False
    try:
        stop_proc = subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if stop_proc.returncode == 0:
            print(f"Stopped container {container_id}.")
            container_stopped = True
        else:
            print(
                f"WARNING: docker stop failed for container {container_id}: "
                f"{stop_proc.stderr.strip()}"
            )
    except Exception as e:
        print(f"WARNING: Exception during docker stop for container {container_id}: {e}")

    return (saved_image, container_stopped)


def _validate_agent_path(agent_path: str) -> None:
    """Ensure agent_path exists and has required files. Raises RuntimeError if invalid."""
    if not agent_path or not os.path.isdir(agent_path):
        raise RuntimeError(f"Agent path does not exist or is not a directory: {agent_path}")
    required = ["runner.sh", "runner.py", "install.sh"]
    for f in required:
        p = os.path.join(agent_path, f)
        if not os.path.isfile(p):
            raise RuntimeError(f"Agent path missing required file: {f} (expected at {p})")


async def run_eval_in_env(
    deployment, project_path, task_id, task, model, agent_path, save_path,
    task_file_path: str | None = None, interactive: bool = False
):
    """Run task in Docker container."""
    if not SWEREX_AVAILABLE:
        raise RuntimeError("swerex is not available. Cannot run in Docker mode.")

    _validate_agent_path(agent_path)

    await deployment.start()
    runtime = deployment.runtime

    timeout_ms_env = os.environ.get("BASH_MAX_TIMEOUT_MS")
    try:
        timeout_s = float(timeout_ms_env) / 1000.0 if timeout_ms_env else (DEFAULT_TIMEOUT_MS / 1000.0)
    except (ValueError, TypeError):
        timeout_s = DEFAULT_TIMEOUT_MS / 1000.0

    if hasattr(runtime, "_config"):
        print(f"Current RemoteRuntime timeout: {runtime._config.timeout}s")
        runtime._config.timeout = timeout_s
        print(f"Overriding RemoteRuntime timeout to {timeout_s}s based on BASH_MAX_TIMEOUT_MS")

    await runtime.create_session(CreateBashSessionRequest())

    print('Uploading project files...')
    await runtime.upload(
        UploadRequest(
            source_path=project_path,
            target_path='/repo',
        )
    )
    print('Project files uploaded.')

    is_ae_agent = 'ae_agent' in str(agent_path) or str(agent_path).endswith('claude_sdk')

    await runtime.run_in_session(BashAction(command='cd /repo'))
    pwd_result = await runtime.run_in_session(BashAction(command='pwd'))
    print(f'Current directory: {pwd_result}')
    ls_result = await runtime.run_in_session(BashAction(command='ls'))
    print(f'Current directory contents: {ls_result}')

    print('Uploading agent runner script...')
    await runtime.upload(
        UploadRequest(
            source_path=agent_path,
            target_path='/agent',
        )
    )
    print('Agent runner script uploaded.')

    print('Setup the agent running environment...')
    await runtime.run_in_session(BashAction(command='chmod +x /agent/runner.sh /agent/install.sh 2>/dev/null; /agent/install.sh'))

    if task_file_path and os.path.isfile(task_file_path):
        tmpdir = tempfile.mkdtemp(prefix='ae_agent_task_')
        try:
            dest = os.path.join(tmpdir, 'current_task.txt')
            shutil.copy2(task_file_path, dest)
            await runtime.upload(UploadRequest(source_path=tmpdir, target_path='/agent_task_file'))
            await runtime.run_in_session(BashAction(command='cp /agent_task_file/current_task.txt /agent/current_task.txt', timeout=10.0))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        tmpdir = tempfile.mkdtemp(prefix='ae_agent_task_')
        try:
            task_file_host = os.path.join(tmpdir, 'current_task.txt')
            with open(task_file_host, 'w', encoding='utf-8') as f:
                f.write(task)
            await runtime.upload(UploadRequest(source_path=tmpdir, target_path='/agent_task_file'))
            await runtime.run_in_session(BashAction(command='cp /agent_task_file/current_task.txt /agent/current_task.txt', timeout=10.0))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if timeout_ms_env:
        set_timeout_cmd = (
            f"export BASH_MAX_TIMEOUT_MS='{timeout_ms_env}' && "
            f"export BASH_DEFAULT_TIMEOUT_MS='{timeout_ms_env}'"
        )
        print(f"Setting BASH_MAX_TIMEOUT_MS/BASH_DEFAULT_TIMEOUT_MS in container to {timeout_ms_env} ms...")
        await runtime.run_in_session(BashAction(command=set_timeout_cmd))

    if is_ae_agent:
        parts = []
        anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
        foundry_api_key = os.environ.get('ANTHROPIC_FOUNDRY_API_KEY')
        if anthropic_api_key:
            escaped_key = anthropic_api_key.replace("'", "'\"'\"'")
            parts.append(f"export ANTHROPIC_API_KEY='{escaped_key}'")
        if foundry_api_key:
            escaped_foundry_key = foundry_api_key.replace("'", "'\"'\"'")
            parts.append(f"export ANTHROPIC_FOUNDRY_API_KEY='{escaped_foundry_key}'")
            if not anthropic_api_key:
                parts.append(f"export ANTHROPIC_API_KEY='{escaped_foundry_key}'")
        foundry_base = os.environ.get('ANTHROPIC_FOUNDRY_BASE_URL')
        if foundry_base:
            escaped_url = foundry_base.replace("'", "'\"'\"'")
            parts.append(f"export ANTHROPIC_FOUNDRY_BASE_URL='{escaped_url}'")
        if os.environ.get('CLAUDE_CODE_USE_FOUNDRY') == '1':
            parts.append("export CLAUDE_CODE_USE_FOUNDRY=1")
        if parts:
            set_env_cmd = " && ".join(parts)
            print('Setting Anthropic/Foundry API key and env in container...')
            await runtime.run_in_session(BashAction(command=set_env_cmd))
        if not anthropic_api_key and not foundry_api_key:
            print('WARNING: Neither ANTHROPIC_API_KEY nor ANTHROPIC_FOUNDRY_API_KEY found on host. Runner may fail.')

    print('Running runner script...')
    runner_timeout = timeout_s if is_ae_agent else min(timeout_s, 1200.0)

    container_id_early = None
    try:
        container_id_res = await runtime.run_in_session(
            BashAction(
                command='cat /etc/hostname 2>/dev/null || hostname 2>/dev/null || echo "unknown"',
                timeout=10.0,
            )
        )
        container_id_early = str(getattr(container_id_res, "output", "")).strip()
        if container_id_early == "unknown":
            container_id_early = None
    except Exception as e:
        print(f"WARNING: Failed to get container id early (will retry after runner): {e}")

    try:
        if is_ae_agent:
            await runtime.run_in_session(BashAction(command='rm -f /agent/runner.live.log && touch /agent/runner.live.log', timeout=10.0))

            start_cmd = (
                'stdbuf -oL -eL /agent/runner.sh "' + model + '" /agent/current_task.txt > /agent/runner.live.log 2>&1 & '
                'RUNNER_PID=$!; '
                'sleep 1; '
                'echo RUNNER_PID=$RUNNER_PID'
            )
            start_res = await runtime.run_in_session(BashAction(command=start_cmd, timeout=30.0))
            start_output = str(getattr(start_res, "output", "")).strip()

            pid = None
            for line in start_output.split('\n'):
                if 'RUNNER_PID=' in line:
                    pid = line.split('RUNNER_PID=', 1)[1].strip()
                    break

            if not pid or not pid.isdigit():
                await asyncio.sleep(2)
                ps_res = await runtime.run_in_session(
                    BashAction(command="ps aux | grep '[r]unner.py' | awk '{print $2}' | head -1", timeout=10.0)
                )
                pid = str(getattr(ps_res, "output", "")).strip()

            print(f'ae-agent runner started with pid: {pid}')
            await asyncio.sleep(2)

            elapsed = 0.0
            poll_interval = 10.0
            run_results = None
            last_log_content = ""

            while elapsed < runner_timeout:
                try:
                    log_res = await runtime.run_in_session(
                        BashAction(command='cat /agent/runner.live.log 2>/dev/null || echo ""', timeout=30.0)
                    )
                    current_log_content = str(getattr(log_res, "output", "")).strip()

                    if current_log_content and current_log_content != last_log_content:
                        if last_log_content and current_log_content.startswith(last_log_content):
                            new_content = current_log_content[len(last_log_content):].strip()
                            if new_content:
                                print(f'[ae-agent live log @ {elapsed:.0f}s ({elapsed/60:.1f} min)]\n{new_content}')
                        else:
                            print(f'[ae-agent live log @ {elapsed:.0f}s ({elapsed/60:.1f} min)]\n{current_log_content}')
                        last_log_content = current_log_content
                    elif elapsed % 300 == 0 and elapsed > 0:
                        print(f'[ae-agent still running @ {elapsed:.0f}s ({elapsed/60:.1f} min), no new output]')
                except Exception as e:
                    print(f'Failed to read ae-agent live log: {e}')

                if pid and pid.isdigit():
                    ps_res = await runtime.run_in_session(
                        BashAction(command=f'ps -p {pid} >/dev/null 2>&1; echo $?', timeout=10.0)
                    )
                    ps_code = str(getattr(ps_res, "output", "")).strip()
                    if ps_code != "0":
                        wait_res = await runtime.run_in_session(
                            BashAction(command=f'wait {pid} 2>/dev/null; echo $?', timeout=30.0)
                        )
                        exit_code_str = str(getattr(wait_res, "output", "")).strip()
                        class MockResult:
                            def __init__(self, code):
                                self.exit_code = int(code) if code.isdigit() else 0
                                self.output = f'exit_code={self.exit_code}'
                        run_results = MockResult(exit_code_str)
                        print(f'ae-agent runner finished with exit code: {run_results.exit_code}')
                        break
                else:
                    ps_res = await runtime.run_in_session(
                        BashAction(command="ps aux | grep '[r]unner.py' | wc -l", timeout=10.0)
                    )
                    proc_count = str(getattr(ps_res, "output", "")).strip()
                    if proc_count == "0" or not proc_count.isdigit() or int(proc_count) == 0:
                        print('ae-agent runner process not found, assuming finished')
                        class MockResult:
                            def __init__(self):
                                self.exit_code = 0
                                self.output = 'exit_code=0'
                        run_results = MockResult()
                        break

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            if run_results is None:
                if pid and pid.isdigit():
                    try:
                        await runtime.run_in_session(BashAction(command=f'kill -TERM {pid} 2>/dev/null || kill -9 {pid} 2>/dev/null || true', timeout=10.0))
                    except Exception:
                        pass
                try:
                    tail_log = await runtime.run_in_session(
                        BashAction(command='tail -n 200 /agent/runner.live.log', timeout=30.0)
                    )
                    print(f'ae-agent live log tail (on timeout):\n{tail_log}')
                except Exception as e:
                    print(f'Failed to read ae-agent live log after timeout: {e}')
                raise TimeoutError(f'ae-agent runner exceeded timeout {runner_timeout}s')

        else:
            runner_cmd = '/agent/runner.sh "' + model + '" /agent/current_task.txt'
            run_results = await runtime.run_in_session(BashAction(command=runner_cmd, timeout=runner_timeout))

        print(f"agent's run results: {run_results}")
        print('Runner script finished.')

        result = {
            'task_id': task_id,
            'task': task,
            'project_path': project_path,
            'agent_run_results': run_results.output if hasattr(run_results, 'output') else str(run_results),
            'status': 'success' if (hasattr(run_results, 'exit_code') and run_results.exit_code == 0) else 'error',
            'run_on_host': False,
        }

        container_id = container_id_early
        if not container_id or container_id == "unknown":
            try:
                container_id_res = await runtime.run_in_session(
                    BashAction(
                        command='cat /etc/hostname 2>/dev/null || hostname 2>/dev/null || echo "unknown"',
                        timeout=10.0,
                    )
                )
                container_id = str(getattr(container_id_res, "output", "")).strip()
            except Exception as e:
                print(f"WARNING: Failed to get container id from inside container: {e}")

        saved_image = None
        container_stopped = False

        if interactive and container_id and container_id != "unknown":
            print("\n" + "=" * 60)
            print("Interactive mode - Attaching to container. Type instructions (or 'quit'/'exit' to end).")
            print("=" * 60 + "\n")
            try:
                proc = subprocess.run(
                    ["docker", "exec", "-it", container_id, "python3", "/agent/interactive_runner.py", model],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                if proc.returncode != 0:
                    print(f"Interactive session exited with code {proc.returncode}", file=sys.stderr)
            except Exception as e:
                print(f"WARNING: Interactive mode failed: {e}", file=sys.stderr)

        if container_id and container_id != "unknown":
            print(f"Preparing to save Docker container {container_id} as an image and stop it...")
            saved_image, container_stopped = _save_container_as_image(container_id, project_path, task_id)

        try:
            await deployment.stop()
        except Exception as e:
            print(f"WARNING: Failed to stop deployment cleanly: {e}")

        result['container_id'] = container_id
        result['saved_image'] = saved_image
        result['container_stopped'] = container_stopped

        return result

    except Exception as e:
        print(f"Task ended with error: {e}")
        result = {
            'task_id': task_id,
            'task': task,
            'project_path': project_path,
            'agent_run_results': str(e),
            'status': 'error',
            'run_on_host': False,
            'container_id': None,
            'saved_image': None,
            'container_stopped': False,
        }
        if container_id_early and container_id_early != "unknown":
            print("Attempting to save container as image (abnormal exit path)...")
            try:
                saved_img, stopped = _save_container_as_image(container_id_early, project_path, task_id)
                result['container_id'] = container_id_early
                result['saved_image'] = saved_img
                result['container_stopped'] = stopped
            except Exception as save_e:
                print(f"WARNING: Failed to save image on abnormal exit: {save_e}")
        try:
            await deployment.stop()
        except Exception as stop_e:
            print(f"WARNING: Failed to stop deployment cleanly: {stop_e}")
        return result


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
):
    """Run task in the given environment: local (host) or docker.

    Single entry point for one-task execution. Call this from main.

    Args:
        env: 'local' = run on host; otherwise run in Docker (value can be image name).
        project_path: Path to the artifact project
        task_id: Task identifier
        task: Task description (used when task_file_path is None)
        model: Model name
        agent_path: Path to agent scripts
        save_path: Path to save results
        docker_image: Docker image (used when env != 'local'); default if None.
        timeout_ms: Optional total timeout in milliseconds for this task
        skip_prereq_check: If True (host only), skip docker/API-key check before running
        use_gpu: If True (Docker only), pass host GPU into container via --gpus all
        task_file_path: If set, upload this file as task (avoids passing large string)
        interactive: If True, after task completes user can continue giving agent instructions
    """
    if timeout_ms is None:
        timeout_ms = DEFAULT_TIMEOUT_MS
    os.environ["BASH_MAX_TIMEOUT_MS"] = str(timeout_ms)
    os.environ["BASH_DEFAULT_TIMEOUT_MS"] = str(timeout_ms)

    if str(env).strip().lower() == "local":
        print(f"Task {task_id} configured to run on HOST (env=local, timeout_ms={timeout_ms}, interactive={interactive})")
        return asyncio.run(
            _run_local(
                project_path, task_id, task, model, agent_path, save_path, timeout_ms,
                skip_prereq_check=skip_prereq_check, interactive=interactive,
            )
        )

    if not SWEREX_AVAILABLE:
        raise RuntimeError(
            "SWE-ReX (swerex) is not available. Install swe-rex for Docker mode."
        )
    image = docker_image or 'bastoica/ae-agent-ubuntu24.04:latest'
    docker_args = [
        '--privileged',
        '--cgroupns=host',
        '-e', 'KIND_EXPERIMENTAL_CONTAINERD_SNAPSHOTTER=native',
    ]
    if use_gpu:
        docker_args.extend(['--gpus', 'all'])
    config = DockerDeploymentConfig(
        image=image,
        startup_timeout=1200.0,
        docker_args=docker_args,
    )
    deployment_obj = config.get_deployment()
    gpu_note = " (GPU enabled)" if use_gpu else ""
    interactive_note = " (interactive)" if interactive else ""
    print(f"Task {task_id} configured to run in DOCKER (image={image}, timeout_ms={timeout_ms}){gpu_note}{interactive_note}")
    return asyncio.run(
        run_eval_in_env(
            deployment_obj, project_path, task_id, task, model, agent_path, save_path,
            task_file_path=task_file_path, interactive=interactive,
        )
    )
