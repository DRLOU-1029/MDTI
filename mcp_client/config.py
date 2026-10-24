"""读取 MCP 配置：环境变量优先，其次项目根目录的 .env。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
DEFAULT_URL = "https://mcp.mcd.cn"


def load_env(path: Path | None = None) -> dict[str, str]:
    """.env 是一个极简 KEY=VALUE 文件。"""
    target = path or ENV_FILE
    values: dict[str, str] = {}
    if not target.exists():
        return values
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    return load_env().get(name) or default


def token() -> str | None:
    return get("MCD_MCP_TOKEN")


def url() -> str:
    return get("MCD_MCP_URL", DEFAULT_URL) or DEFAULT_URL
