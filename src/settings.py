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


class AgentType(str, Enum):
    CLAUDE_SDK = "claude_sdk"
    MOCK = "mock"
    PYTHON = "python"
    CLI = "cli"
    REMOTE = "remote"
    MCP_CLIENT = "mcp_client"


class McpClientKind(str, Enum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    CUSTOM = "custom"


class McpMode(str, Enum):
    MCP_LOCAL = "mcp_local"
    MCP_HOST_BRIDGE = "mcp_host_bridge"
