"""麦当劳 MCP 接入层（零依赖，只用标准库）。"""

from .client import DEFAULT_URL, McpAuthError, McpClient, McpError
from . import config

__all__ = ["DEFAULT_URL", "McpAuthError", "McpClient", "McpError", "config"]
