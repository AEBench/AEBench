"""Helper methods for running artifact tasks."""

from __future__ import annotations

import json
import os
import re
import subprocess

__all__ = [
    'AGENT_SUMMARY_FALLBACK_MAX',
    'DEFAULT_DOCKER_IMAGE',
    'DEFAULT_MODEL',
    'DEFAULT_TIMEOUT_MS',
    'LOG_OUTPUT_TRUNCATE_BYTES',
    'SUMMARY_BASENAME_TEMPLATE',
    'SUMMARY_INSTRUCTION',
    'Tee',
    'apply_timeout_env',
    'clone_artifact_repo',
    'compute_and_write_summary',
    'docker_image_from_item',
    'env_from_item',
    'get_task',
    'gpu_from_item',
    'has_api_key',
    'interactive_from_item',
    'is_local_env',
    'parse_eval_score',
    'read_task_from_file',
    'resolve_project_path',
    'resolve_timeout_ms',
    'safe_task_id',
    'status_from_exit_code',
    'timeout_env_dict',
    'timeout_ms_from_item',
    'write_task_report',
]

# Default total timeout in milliseconds (96h); used by run_eval and runner.
# Single source: runner.py fallback and runner.sh (345600000) must match when utils is unavailable.
DEFAULT_TIMEOUT_MS = 345_600_000

# Default Docker image and model when not specified.
DEFAULT_DOCKER_IMAGE = 'bastoica/ae-agent-ubuntu24.04:latest'
DEFAULT_MODEL = 'claude-sonnet-4-5-20250929'

# File naming templates for reports and summaries.
SUMMARY_BASENAME_TEMPLATE = 'ae_summary_{safe_id}.md'
SUMMARY_INSTRUCTION = (
    '\n\nAt the end, write a brief summary of what you did and the result to '
    '{basename} in the artifact root (so it can be included in the report).'
)
LOG_OUTPUT_TRUNCATE_BYTES = 50000
AGENT_SUMMARY_FALLBACK_MAX = 8000


def timeout_env_dict(timeout_ms: int) -> dict[str, str]:
    """Return env vars dict for Bash timeout (single source for env and settings file)."""
    return {
        'BASH_MAX_TIMEOUT_MS': str(timeout_ms),
        'BASH_DEFAULT_TIMEOUT_MS': str(timeout_ms),
    }


def apply_timeout_env(timeout_ms: int) -> None:
    """Set BASH_MAX_TIMEOUT_MS and BASH_DEFAULT_TIMEOUT_MS in os.environ."""
    os.environ.update(timeout_env_dict(timeout_ms))


def resolve_timeout_ms(timeout_ms: int | None) -> int:
    """Return timeout_ms if set, else DEFAULT_TIMEOUT_MS. Single place for default."""
    return timeout_ms if timeout_ms is not None else DEFAULT_TIMEOUT_MS


def has_api_key() -> bool:
    """True if at least one of ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY is set."""
    return bool(os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_FOUNDRY_API_KEY'))


def status_from_exit_code(exit_code: int) -> str:
    """Map process exit code to eval status string. Non-zero (incl. -1 for unknown) → 'error'."""
    return 'success' if exit_code == 0 else 'error'


def is_local_env(env: str) -> bool:
    """True if env denotes local (host) execution rather than Docker."""
    return str(env).strip().lower() == 'local'


def _parse_bool_value(v, default: bool = False) -> bool:
    """Parse a value (bool, str, or other) to bool. Strings 'true', '1', 'yes' → True."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ('true', '1', 'yes')
    return bool(v)


# Default task template when artifact_readme is not specified.
_DEFAULT_TASK_TEMPLATE = (
    'You are an experienced software engineer.'
    ' You are asked to navigate to the {file_path} and follow step-by-step'
    ' instructions to set up, install, compile, and reproduce the results in'
    ' that code repository. You have root access inside a Docker image, which'
    ' means you can directly proceed with executing the steps in the README'
    ' without asking for approval or confirmation. Once you reached the end'
    ' of the README you must exit the Docker image gracefully.'
)


def interactive_from_item(item: dict) -> bool:
    """Whether to enable interactive mode (user can continue giving agent instructions after task completes)."""
    return _parse_bool_value(item.get('interactive', False))


def safe_task_id(task_id: str | None, fallback: str = 'unknown') -> str:
    """Normalize task_id for use in filenames (no spaces, lowercase)."""
    return (task_id or fallback).replace(' ', '_').lower()


def timeout_ms_from_item(item: dict) -> int | None:
    """Parse timeout from task item. Returns ms (int) or None for default.

    Accepts either ``timeout_sec`` (seconds, preferred) or ``timeout_ms``
    (milliseconds). Falls back to the legacy ``timeout`` field, which is
    treated as seconds if < 86_400 (24 hours), otherwise milliseconds.
    """
    if 'timeout_sec' in item:
        v = item['timeout_sec']
        if isinstance(v, (int, float)):
            return int(v * 1000)
        return None
    if 'timeout_ms' in item:
        v = item['timeout_ms']
        if isinstance(v, (int, float)):
            return int(v)
        return None
    v = item.get('timeout', None)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        # Legacy heuristic: 86400 = 24h in seconds; values below are treated as
        # seconds, else as milliseconds (e.g. 345600000 = 96h).
        return int(v * 1000) if v < 86_400 else int(v)
    return None


def env_from_item(item: dict) -> str:
    """Resolve env from task item: 'local' = host, else = docker. Backward compat: run_on_host/docker_env."""
    env = item.get('env', None)
    if env is not None:
        s = str(env).strip().lower()
        return 'local' if s == 'local' else (str(env).strip() or 'docker')
    return 'local' if item.get('run_on_host', False) else 'docker'


def gpu_from_item(item: dict) -> bool:
    """Whether to enable GPU access in Docker. Default False (no host GPU passed to container)."""
    return _parse_bool_value(item.get('gpu', False))


def docker_image_from_item(
    item: dict,
    default: str | None = None,
    *,
    env: str | None = None,
) -> str | None:
    """Resolve Docker image from task item.

    When env is 'local', returns None (no Docker). Otherwise returns, in order:
    item['env'] if it looks like an image name, item['docker_env'], or default.
    If env is provided (e.g. from env_from_item), avoids parsing env twice.
    """
    resolved = (env if env is not None else env_from_item(item)).strip().lower()
    if resolved == 'local':
        return None
    env_val = item.get('env', None)
    if env_val is not None:
        s = str(env_val).strip()
        if s and s.lower() != 'local':
            return s
    return (
        item.get('docker_env', None)
        or item.get('docer_env', None)
        or (default or DEFAULT_DOCKER_IMAGE)
    )


def get_task(file_path: str) -> str:
    """Get agent task from a file path.

    Args:
        file_path: Path to README or task description file (relative to artifact root)

    Returns:
        Task description string for the agent
    """
    return _DEFAULT_TASK_TEMPLATE.format(file_path=file_path)


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
        with open(task_file_path, encoding='utf-8') as f:
            return f.read()
    else:
        return get_task(task_file)


def clone_artifact_repo(artifact_url: str, target_dir: str) -> str:
    """Clone artifact repository from URL into target_dir.

    Args:
        artifact_url: Git clone URL (e.g. https://github.com/org/repo.git)
        target_dir: Absolute path to the directory to clone into (must not exist or be empty).

    Returns:
        target_dir (artifact root path after clone).

    Raises:
        RuntimeError: If git clone fails.
    """
    if os.path.exists(target_dir) and os.listdir(target_dir):
        return target_dir
    if os.path.exists(target_dir):
        os.rmdir(target_dir)
    r = subprocess.run(
        ['git', 'clone', '--depth', '1', artifact_url, target_dir],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f'git clone failed: {r.stderr or r.stdout}')
    return target_dir


def resolve_project_path(item: dict, input_file: str, save_path: str) -> tuple[str | None, str | None]:
    """Resolve artifact project path from task item.

    When both artifact_url and artifact_dir are set, if the local path
    (input_dir/artifact_dir) already exists, it is used and no clone is performed.
    Otherwise the repo is cloned from artifact_url into save_path/workspace/<task_id>.

    Returns:
        (project_path, error_message). If error_message is not None, skip task.
    """
    input_dir = os.path.dirname(os.path.abspath(input_file))
    artifact_dir = item.get('artifact_dir')
    artifact_url = item.get('artifact_url')
    task_id = item.get('artifact_id')
    sid = safe_task_id(task_id)

    if artifact_url:
        candidate = os.path.join(input_dir, artifact_dir) if artifact_dir else None
        if candidate and os.path.isdir(candidate):
            return os.path.abspath(candidate), None
        workspace_dir = os.path.join(save_path, 'workspace', sid)
        os.makedirs(os.path.dirname(workspace_dir), exist_ok=True)
        return clone_artifact_repo(artifact_url, workspace_dir), None
    if not artifact_dir:
        return None, f'Skipping task {task_id}: missing artifact_dir and artifact_url'
    path = os.path.abspath(os.path.join(input_dir, artifact_dir))
    if not os.path.isdir(path):
        return None, f'Project path does not exist: {path}'
    return path, None


class Tee:
    """Write to both an original stream and a log file.

    Implements enough of the TextIO interface to serve as a drop-in
    replacement for sys.stdout / sys.stderr (supports libraries that
    probe encoding, isatty, etc.).
    """

    def __init__(self, stream, log_path: str):
        """Wrap stream and log_path for dual write."""
        self._stream = stream
        self._path = log_path
        self._file = None

    def __enter__(self):
        """Open log file and return self."""
        self._file = open(self._path, 'a', encoding='utf-8')
        return self

    def __exit__(self, *args):
        """Close log file."""
        if self._file:
            self._file.close()

    def write(self, data):
        """Write to both stream and log file."""
        self._stream.write(data)
        if self._file:
            self._file.write(data)
            self._file.flush()

    def flush(self):
        """Flush both stream and log file."""
        self._stream.flush()
        if self._file:
            self._file.flush()

    @property
    def encoding(self) -> str:
        """Return underlying stream encoding or utf-8."""
        return getattr(self._stream, 'encoding', 'utf-8')

    def isatty(self) -> bool:
        """Return whether underlying stream is a TTY."""
        return getattr(self._stream, 'isatty', lambda: False)()

    def fileno(self) -> int:
        """Return underlying stream fileno."""
        return self._stream.fileno()


def write_task_report(
    save_path: str,
    safe_id: str,
    task_id: str,
    result: dict,
    log_path: str,
    agent_summary: str,
) -> None:
    """Write ae_report_<safe_id>.md for a single task."""
    report_path = os.path.join(save_path, f'ae_report_{safe_id}.md')
    saved_image = result.get('saved_image')
    with open(report_path, 'w', encoding='utf-8') as fw:
        fw.write(f'# AE Report: {task_id}\n\n')
        fw.write(f'- **Status**: {result.get("status", "unknown")}\n')
        fw.write(f'- **Timestamp**: {result.get("timestamp", "")}\n')
        fw.write(f'- **Project path**: {result.get("project_path", "")}\n')
        fw.write(f'- **Run on host**: {result.get("run_on_host", False)}\n')
        fw.write(f'- **Log file**: `{log_path}`\n\n')
        if saved_image:
            fw.write('> [!Note]\n')
            fw.write('> ## To check the result\n')
            fw.write('>\n')
            fw.write('> You can run the following command to manually check the result:\n')
            fw.write('>\n')
            fw.write('> ```bash\n')
            fw.write(f'> docker run -it {saved_image} bash\n')
            fw.write('> ```\n')
            fw.write('>\n')
            fw.write(f'> Image: `{saved_image}`\n\n')
        fw.write('## Agent summary\n\n')
        fw.write(agent_summary)
        fw.write('\n')


def parse_eval_score(output) -> int:
    """Parse evaluation score from evaluator script output (string or object with .output).

    - If a line is a single digit (e.g. '4', '0'), use it (prefer last such line).
    - If output contains 'Agent scores: {...}' (Oracle-style evaluator), count ': 1' as passed items.
    - Otherwise return 0.
    """
    s = (getattr(output, 'output', None) or str(output) or '').strip()
    if not s:
        return 0
    lines = s.splitlines()
    for line in reversed(lines):
        t = line.strip()
        if t.isdigit():
            return int(t)
    m = re.search(r'Agent scores:\s*\{[^}]*\}', s)
    if m:
        return m.group(0).count(': 1')
    return 0


def compute_and_write_summary(save_path: str) -> tuple[int, int]:
    """Read result.jsonl, compute total/success, write summary.json.

    total = number of result lines (success + error + skipped). success = status == "success".
    Returns (total_count, success_count).
    """
    result_path = os.path.join(save_path, 'result.jsonl')
    total, success = 0, 0
    if os.path.isfile(result_path):
        with open(result_path, encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.strip())
                    total += 1
                    if row.get('status') == 'success':
                        success += 1
                except json.JSONDecodeError:
                    continue
    rate = success / total if total > 0 else 0.0
    summary = {'total_tasks': total, 'successful_tasks': success, 'success_rate': rate}
    with open(os.path.join(save_path, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=4)
    return total, success
