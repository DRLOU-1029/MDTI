"""麦当劳 MCP · Streamable HTTP 客户端。

协议要点（MCP 2025-06-18 / Streamable HTTP）：
  - 所有消息以 HTTP POST 发到同一个端点
  - 请求头必须带 Accept: application/json, text/event-stream
  - 服务器可能返回 JSON，也可能返回 SSE 流，两种都要处理
  - initialize 之后，服务器可能通过 Mcp-Session-Id 下发会话，后续请求要带上
  - 初始化完成后带 MCP-Protocol-Version 头

零依赖：只用 urllib + json。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"


class McpError(RuntimeError):
    """MCP 调用失败。"""


class McpAuthError(McpError):
    """Token 缺失或被拒绝。"""


class McpClient:
    def __init__(self, token: str, url: str = DEFAULT_URL, timeout: float = 30.0):
        if not token:
            raise McpAuthError(
                "没有 MCP Token：把它填进项目根目录的 .env（MCD_MCP_TOKEN=...），"
                "或设置同名环境变量"
            )
        self.token = token.strip()
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict = {}
        self.last_content_type: str = ""
        self._seq = 0

    # ---------------- 传输层 ----------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, payload: dict, want_id: int | None = None) -> dict | None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=data, headers=self._headers(), method="POST"
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except urllib.error.URLError as exc:
            raise McpError(f"连不上 {self.url}：{exc.reason}") from None

        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id

        content_type = (response.headers.get("Content-Type") or "").lower()
        if content_type:
            self.last_content_type = content_type
        if "text/event-stream" in content_type:
            return self._read_sse(response, want_id)

        raw = response.read().decode("utf-8", "replace").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise McpError(f"服务器返回的不是 JSON：{raw[:200]}") from None

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> McpError:
        body = exc.read().decode("utf-8", "replace")
        detail = " ".join(body.split())[:300]
        if exc.code in (401, 403):
            return McpAuthError(f"Token 被拒绝（HTTP {exc.code}）：{detail}")
        if exc.code == 429:
            return McpError("触发限流（429）：每个 Token 每分钟 600 次，稍等再试")
        return McpError(f"HTTP {exc.code}：{detail}")

    def _read_sse(self, response, want_id: int | None) -> dict | None:
        """从 SSE 流里取出与请求 id 对应的那条 JSON-RPC 响应。"""
        buffer: list[str] = []
        last: dict | None = None
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                buffer.append(line[5:].lstrip())
            elif line == "":
                if not buffer:
                    continue
                chunk = "\n".join(buffer)
                buffer.clear()
                try:
                    message = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                last = message
                if want_id is None or message.get("id") == want_id:
                    return message
        if buffer:
            try:
                last = json.loads("\n".join(buffer))
            except json.JSONDecodeError:
                pass
        return last

    # ---------------- JSON-RPC ----------------

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id()
        payload: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        message = self._post(payload, request_id)
        if message is None:
            raise McpError(f"{method} 没有返回内容")
        if "error" in message:
            error = message.get("error") or {}
            raise McpError(f"{method} 失败：{error.get('code')} {error.get('message')}")
        return message.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload, None)

    # ---------------- MCP 方法 ----------------

    def initialize(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mdti", "version": "1.0.0"},
            },
        )
        self.protocol_version = result.get("protocolVersion") or PROTOCOL_VERSION
        self.server_info = result.get("serverInfo") or {}
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def tool_data(self, name: str, arguments: dict | None = None) -> dict:
        """调用工具并返回业务数据。

        麦当劳 MCP 的返回是 content[] + structuredContent 两层：
        业务数据在 structuredContent 里；没有的话退回拼接 text。
        """
        result = self.call_tool(name, arguments)
        if result.get("isError"):
            raise McpError(f"{name} 返回错误：{self._text_of(result)[:300]}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            # 麦当劳的接口用 success=false 表达业务失败，不能当成正常数据
            if structured.get("success") is False:
                raise McpError(
                    f"{name} 业务失败：{structured.get('code')} {structured.get('message')}"
                )
            if structured:
                return structured
        return {"text": self._text_of(result)}

    @staticmethod
    def _text_of(result: dict) -> str:
        chunks = [
            block.get("text", "")
            for block in (result.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(chunks)
