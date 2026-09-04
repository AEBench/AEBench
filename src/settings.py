from __future__ import annotations

from enum import Enum


class LogLevel(str, Enum):
	DEBUG = "debug"
	INFO = "info"
	WARNING = "warning"
	ERROR = "error"


class LogRenderer(str, Enum):
	CONSOLE = "console"
	JSON = "json"
