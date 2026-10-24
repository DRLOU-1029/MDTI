#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳 MCP 命令行：检查连通性、列出工具、调用工具。

  python3 scripts/mcp_cli.py check
  python3 scripts/mcp_cli.py list
  python3 scripts/mcp_cli.py list --json
  python3 scripts/mcp_cli.py call query-meals --args '{"storeCode":"..."}'

Token 从环境变量 MCD_MCP_TOKEN 读取，没有则读项目根目录的 .env。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_client import config  # noqa: E402
from mcp_client.client import McpAuthError, McpClient, McpError  # noqa: E402


def build_client() -> McpClient:
    return McpClient(token=config.token() or "", url=config.url())


def cmd_check(args) -> int:
    client = build_client()
    info = client.initialize()
    server = client.server_info or {}
    print("接入地址 :", client.url)
    print("协议版本 :", client.protocol_version)
    print("服务器   :", server.get("name", "?"), server.get("version", ""))
    print("会话 ID  :", client.session_id or "(无，可能是无状态模式)")
    print("响应类型 :", client.last_content_type or "-")
    capabilities = info.get("capabilities") or {}
    print("能力     :", ", ".join(sorted(capabilities)) or "-")
    return 0


def cmd_list(args) -> int:
    client = build_client()
    client.initialize()
    tools = client.list_tools()

    if args.json:
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        return 0

    print(f"共 {len(tools)} 个工具\n")
    for index, tool in enumerate(tools, start=1):
        print(f"[{index:>2}] {tool.get('name', '?')}")
        title = tool.get("title") or (tool.get("annotations") or {}).get("title")
        if title:
            print(f"     名称: {title}")
        description = " ".join((tool.get("description") or "").split())
        if description:
            print(f"     说明: {description[:150]}")
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        if properties:
            required = set(schema.get("required") or [])
            params = ", ".join(f"{k}*" if k in required else k for k in properties)
            print(f"     参数: {params}")
        print()
    return 0


def cmd_call(args) -> int:
    if not args.tool:
        print("用法: mcp_cli.py call <tool> --args '{...}'", file=sys.stderr)
        return 2
    try:
        arguments = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"--args 不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    client = build_client()
    client.initialize()
    print(json.dumps(client.call_tool(args.tool, arguments), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="麦当劳 MCP 命令行")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("check", help="检查 Token 与连通性")

    p_list = sub.add_parser("list", help="列出全部工具")
    p_list.add_argument("--json", action="store_true", help="输出原始 JSON")

    p_call = sub.add_parser("call", help="调用一个工具")
    p_call.add_argument("tool", nargs="?")
    p_call.add_argument("--args", default="{}", help="参数 JSON")

    args = parser.parse_args()
    handlers = {"check": cmd_check, "list": cmd_list, "call": cmd_call}

    try:
        return handlers[args.action](args)
    except McpAuthError as exc:
        print(f"\n[鉴权失败] {exc}", file=sys.stderr)
        print(f"→ 把 Token 填进 {ROOT / '.env'} 里的 MCD_MCP_TOKEN=", file=sys.stderr)
        return 3
    except McpError as exc:
        print(f"\n[MCP 错误] {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
