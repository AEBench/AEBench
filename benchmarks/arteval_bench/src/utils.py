"""Re-export get_task for main.py when run from benchmark root (python src/main.py)."""
from core.utils import get_task

__all__ = ["get_task"]
