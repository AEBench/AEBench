"""Helper for AE Agent runner and host/Docker orchestration (main.py, run_eval.py)."""

import json
import os
import subprocess

# Default total timeout in milliseconds (48h); used by runner.py and run_eval.
DEFAULT_TIMEOUT_MS = 172_800_000


def interactive_from_item(item: dict) -> bool:
    """Whether to enable interactive mode (user can continue giving agent instructions after task completes)."""
    v = item.get("interactive", False)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def safe_task_id(task_id: str | None, fallback: str = "unknown") -> str:
    """Normalize task_id for use in filenames (no spaces, lowercase)."""
    return (task_id or fallback).replace(" ", "_").lower()


def timeout_ms_from_item(item: dict) -> int | None:
    """Parse timeout from task item. Returns ms (int) or None for default."""
    v = item.get("timeout", None)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v * 1000) if v < 1_000_000 else int(v)
    return None


def env_from_item(item: dict) -> str:
    """Resolve env from task item: 'local' = host, else = docker. Backward compat: run_on_host/docker_env."""
    env = item.get("env", None)
    if env is not None:
        s = str(env).strip().lower()
        return "local" if s == "local" else (str(env).strip() or "docker")
    return "local" if item.get("run_on_host", False) else "docker"


def gpu_from_item(item: dict) -> bool:
    """Whether to enable GPU access in Docker. Default False (no host GPU passed to container)."""
    v = item.get("gpu", False)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def docker_image_from_item(
    item: dict,
    default: str = "bastoica/ae-agent-ubuntu24.04:latest",
) -> str | None:
    """Resolve Docker image from task item. Returns None when env is local."""
    if env_from_item(item) == "local":
        return None
    env = item.get("env", None)
    if env is not None:
        s = str(env).strip()
        if s and s.lower() != "local":
            return s
    return item.get("docker_env", None) or item.get("docer_env", None) or default


def get_task(file_path: str) -> str:
    """Get agent task from a file path.

    Args:
        file_path: Path to README or task description file (relative to artifact root)

    Returns:
        Task description string for the agent
    """
    task = (
        f"You are an experienced software engineer."
        + f" You are asked to navigate to the {file_path} and follow step-by-step"
        + f" instructions to set up, install, compile, and reproduce the results in"
        + f" that code repository. You have root access inside a Docker image, which"
        + f" means you can directly proceed with executing the steps in the README"
        + f" without asking for approval or confirmation. Once you reached the end"
        + f" of the README you must exit the Docker image gracefully."
    )
    return task


def read_task_from_file(artifact_path: str, task_file: str) -> str:
    """Read task description from a file.

    Args:
        artifact_path: Path to artifact root directory
        task_file: Relative path to task file (e.g., README.md)

    Returns:
        Content of the task file as string
    """
    task_file_path = os.path.join(artifact_path, task_file)
    if os.path.exists(task_file_path):
        with open(task_file_path, 'r', encoding='utf-8') as f:
            return f.read()
    else:
        return get_task(task_file)


def clone_artifact_repo(artifact_url: str, target_dir: str) -> str:
    """Clone artifact repository from URL into target_dir."""
    if os.path.exists(target_dir) and os.listdir(target_dir):
        return target_dir
    if os.path.exists(target_dir):
        os.rmdir(target_dir)
    r = subprocess.run(
        ["git", "clone", "--depth", "1", artifact_url, target_dir],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git clone failed: {r.stderr or r.stdout}")
    return target_dir


def resolve_project_path(item: dict, input_file: str, save_path: str) -> tuple[str | None, str | None]:
    """Resolve artifact project path from task item.

    Returns:
        (project_path, error_message). If error_message is not None, skip task.
    """
    input_dir = os.path.dirname(os.path.abspath(input_file))
    artifact_dir = item.get("artifact_dir")
    artifact_url = item.get("artifact_url")
    task_id = item.get("artifact_id")
    sid = safe_task_id(task_id)

    if artifact_url:
        candidate = os.path.join(input_dir, artifact_dir) if artifact_dir else None
        if candidate and os.path.isdir(candidate):
            return os.path.abspath(candidate), None
        workspace_dir = os.path.join(save_path, "workspace", sid)
        os.makedirs(os.path.dirname(workspace_dir), exist_ok=True)
        return clone_artifact_repo(artifact_url, workspace_dir), None
    if not artifact_dir:
        return None, f"Skipping task {task_id}: missing artifact_dir and artifact_url"
    path = os.path.abspath(os.path.join(input_dir, artifact_dir))
    if not os.path.isdir(path):
        return None, f"Project path does not exist: {path}"
    return path, None


class Tee:
    """Write to both original stream and a log file."""

    def __init__(self, stream, log_path: str):
        self._stream = stream
        self._path = log_path
        self._file = None

    def __enter__(self):
        self._file = open(self._path, "a", encoding="utf-8")
        return self

    def __exit__(self, *args):
        if self._file:
            self._file.close()

    def write(self, data):
        self._stream.write(data)
        if self._file:
            self._file.write(data)
            self._file.flush()

    def flush(self):
        self._stream.flush()
        if self._file:
            self._file.flush()


def write_task_report(
    save_path: str,
    safe_id: str,
    task_id: str,
    result: dict,
    log_path: str,
    agent_summary: str,
) -> None:
    """Write ae_report_<safe_id>.md for a single task."""
    report_path = os.path.join(save_path, f"ae_report_{safe_id}.md")
    saved_image = result.get("saved_image")
    with open(report_path, "w", encoding="utf-8") as fw:
        fw.write(f"# AE Report: {task_id}\n\n")
        fw.write(f"- **Status**: {result.get('status', 'unknown')}\n")
        fw.write(f"- **Timestamp**: {result.get('timestamp', '')}\n")
        fw.write(f"- **Project path**: {result.get('project_path', '')}\n")
        fw.write(f"- **Run on host**: {result.get('run_on_host', False)}\n")
        fw.write(f"- **Log file**: `{log_path}`\n\n")
        if saved_image:
            fw.write("> [!Note]\n")
            fw.write("> ## To check the result\n")
            fw.write(">\n")
            fw.write("> You can run the following command to manually check the result:\n")
            fw.write(">\n")
            fw.write("> ```bash\n")
            fw.write(f"> docker run -it {saved_image} bash\n")
            fw.write("> ```\n")
            fw.write(">\n")
            fw.write(f"> Image: `{saved_image}`\n\n")
        fw.write("## Agent summary\n\n")
        fw.write(agent_summary)
        fw.write("\n")


def compute_and_write_summary(save_path: str) -> tuple[int, int]:
    """Read result.jsonl, compute total/success, write summary.json. Returns (total_count, success_count)."""
    result_path = os.path.join(save_path, "result.jsonl")
    total, success = 0, 0
    if os.path.isfile(result_path):
        with open(result_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.strip())
                    total += 1
                    if row.get("status") == "success":
                        success += 1
                except json.JSONDecodeError:
                    continue
    rate = success / total if total > 0 else 0.0
    summary = {"total_tasks": total, "successful_tasks": success, "success_rate": rate}
    with open(os.path.join(save_path, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=4)
    return total, success
