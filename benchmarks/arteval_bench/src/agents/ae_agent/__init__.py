"""AE Agent - A tool for running Claude Agent SDK on artifact evaluation tasks.

Output files (under save_path):
- ae_report_<artifact_id>.md: Per-artifact report with status and agent summary
- ae_log_<artifact_id>.log: Per-artifact execution log
- result.jsonl: Per-task results (one JSON per line)
- summary.json: Overall statistics
"""

from .main import cli_main, main
from .run_eval import run_agent_then_eval, run_eval
from .runner import build_system_prompt, run_agent
from .utils import parse_eval_score

__all__ = [
    'build_system_prompt',
    'cli_main',
    'main',
    'parse_eval_score',
    'run_agent',
    'run_agent_then_eval',
    'run_eval',
]
