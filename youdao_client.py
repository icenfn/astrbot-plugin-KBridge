"""有道云笔记 MCP SSE 客户端（参考官方 YoudaoNote Skills / youdaonote-cli）。

有道云笔记官方 MCP 服务：https://open.mail.163.com/api/ynote/mcp/sse
认证：请求头 X-API-Key: <API Key>（网易智能开发者平台 https://mopen.163.com 获取）。
协议：老式 SSE 传输 —— GET /sse 建立会话拿 endpoint，POST 到 endpoint 发
JSON-RPC，响应经 SSE 流回推。实现基于标准库 http.client（线程读 SSE 流，
调用方经 asyncio.to_thread 桥接），无第三方依赖。
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import threading
import time

logger = logging.getLogger("astrbot")

MCP_HOST = "open.mail.163.com"
MCP_SSE_PATH = "/api/ynote/mcp/sse"
TOOL_LIST_NOTES = "listNotes"
TOOL_GET_CONTENT = "getNoteTextContent"


class YoudaoError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


class YoudaoClient:
    """有道云笔记 MCP 客户端。调用方须串行调用（MCP 会话单连接）。"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._endpoint: str | None = None
        self._endpoint_event = threading.Event()
        self._responses: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._rid = 0
        self._initialized = False

    # ---------- 连接管理 ----------

    def _connect(self, timeout: int = 60) -> http.client.HTTPSConnection:
        return http.client.HTTPSConnection(MCP_HOST, timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Accept": "text/event-stream"}

    def _sse_loop(self) -> None:
        """阻塞线程：读 SSE 流，捕获 endpoint 与 message 事件。断线自动重连。"""
        while not self._stop.is_set():
            try:
                conn = self._connect()
                conn.request("GET", MCP_SSE_PATH, headers=self._headers())
                resp = conn.getresponse()
                if resp.status != 200:
                    conn.close()
                    time.sleep(2)
                    continue
                event: str | None = None
                while not self._stop.is_set():
                    line = resp.readline()
                    if not line:
                        break
                    s = line.decode("utf-8", "ignore").rstrip("\r\n")
                    if s.startswith("event:"):
                        event = s[6:].strip()
                    elif s.startswith("data:"):
                        payload = s[5:].strip()
                        if event == "endpoint":
                            if payload.startswith("/"):
                                payload = f"https://{MCP_HOST}{payload}"
                            with self._lock:
                                self._endpoint = payload
                            self._endpoint_event.set()
                        else:
                            try:
                                msg = json.loads(payload)
                                rid = msg.get("id")
                                if rid is not None:
                                    with self._lock:
                                        self._responses[rid] = msg
                            except (ValueError, TypeError):
                                pass
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            # 断线：清状态，等下一次调用触发重连
            with self._lock:
                self._endpoint = None
            self._endpoint_event.clear()
            time.sleep(2)

    async def ensure_connected(self) -> None:
        """确保 SSE 会话存活（endpoint 就绪）；未就绪则启动/等待。"""
        if self._reader is None or not self._reader.is_alive():
            self._stop.clear()
            self._reader = threading.Thread(target=self._sse_loop, daemon=True)
            self._reader.start()
        # 线程内已连接的会话：endpoint 就绪即返回
        if self._endpoint_event.is_set() and self._endpoint:
            return
        # 等待首次握手（最多 10s）
        await asyncio.to_thread(self._endpoint_event.wait, 10)
        with self._lock:
            endpoint = self._endpoint
        if not endpoint:
            raise YoudaoError(0, "SSE 握手超时：未收到 MCP endpoint")

    # ---------- MCP 调用 ----------

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid

    def _post(self, body: dict) -> int:
        """阻塞 POST 到 message endpoint（响应经 SSE 流回推，不读 body）。"""
        conn = self._connect(timeout=30)
        try:
            conn.request(
                "POST",
                self._endpoint,
                body=json.dumps(body),
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
            )
            resp = conn.getresponse()
            return resp.status
        finally:
            conn.close()

    async def _call(self, method: str, params: dict, timeout: float = 30.0) -> str:
        """发 JSON-RPC 请求并等待 SSE 回推响应，返回工具结果 text。"""
        await self.ensure_connected()
        await self._ensure_initialized()
        last_err: Exception | None = None
        for attempt in range(3):
            rid = self._next_rid()
            try:
                status = await asyncio.to_thread(
                    self._post,
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "method": method,
                        "params": params,
                    },
                )
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise YoudaoError(0, f"MCP 请求失败: {e}") from e
            if status not in (200, 202):
                # 会话过期（404 session not found）→ 强制重连重试
                if status in (404, 410) and attempt < 2:
                    self._endpoint = None
                    self._endpoint_event.clear()
                    await asyncio.sleep(1.0)
                    continue
                raise YoudaoError(status, f"MCP HTTP {status}")

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with self._lock:
                    msg = self._responses.pop(rid, None)
                if msg is not None:
                    if "error" in msg:
                        err = msg["error"]
                        raise YoudaoError(
                            int(err.get("code") or 0),
                            str(err.get("message") or "MCP 错误"),
                        )
                    result = msg.get("result") or {}
                    text = "\n".join(
                        c.get("text", "")
                        for c in (result.get("content") or [])
                        if c.get("type") == "text"
                    )
                    if result.get("isError"):
                        raise YoudaoError(0, text or "工具调用失败")
                    return text
                await asyncio.sleep(0.05)
            last_err = YoudaoError(0, f"MCP 调用超时: {method}")
            # 超时可能因 SSE 断线，强制重连
            with self._lock:
                self._endpoint = None
            self._endpoint_event.clear()
        raise last_err  # type: ignore[misc]

    # ---------- 业务方法 ----------

    async def _ensure_initialized(self) -> None:
        """MCP 握手：initialize + notifications/initialized（会话级一次性）。"""
        if self._initialized:
            return
        await self._request_raw(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "astrbot-plugin-kbridge", "version": "0.1.0"},
            },
        )
        # 通知无响应，POST 后立即返回
        await asyncio.to_thread(
            self._post,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        self._initialized = True

    async def _request_raw(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        """低层请求：发 JSON-RPC 并等待响应（用于 initialize 等）。"""
        last_err: Exception | None = None
        for attempt in range(3):
            rid = self._next_rid()
            try:
                status = await asyncio.to_thread(
                    self._post,
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "method": method,
                        "params": params,
                    },
                )
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise YoudaoError(0, f"MCP 请求失败: {e}") from e
            if status not in (200, 202):
                if status in (404, 410) and attempt < 2:
                    self._endpoint = None
                    self._endpoint_event.clear()
                    await asyncio.sleep(1.0)
                    continue
                raise YoudaoError(status, f"MCP HTTP {status}")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with self._lock:
                    msg = self._responses.pop(rid, None)
                if msg is not None:
                    if "error" in msg:
                        err = msg["error"]
                        raise YoudaoError(
                            int(err.get("code") or 0),
                            str(err.get("message") or "MCP 错误"),
                        )
                    return msg.get("result") or {}
                await asyncio.sleep(0.05)
            last_err = YoudaoError(0, f"MCP 调用超时: {method}")
            self._endpoint = None
            self._endpoint_event.clear()
        raise last_err  # type: ignore[misc]

    async def list_items(
        self, parent_id: str = "0", last_id: str = ""
    ) -> tuple[list[dict], bool]:
        """列出目录下条目。返回 (entries, has_more)。

        entries 元素: {id, name, version, parentId, dir(bool)}
        """
        args: dict = {"parentId": parent_id}
        if last_id:
            args["lastId"] = last_id
        text = await self._call(
            "tools/call", {"name": TOOL_LIST_NOTES, "arguments": args}
        )
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            raise YoudaoError(0, "listNotes 返回格式异常")
        entries = data.get("entries") or []
        has_more = bool(entries) and len(entries) < int(data.get("totalCount") or 0)
        return entries, has_more

    async def get_note_content(self, file_id: str) -> dict:
        """读取笔记文本。返回 {fileId, content, title, raw}。"""
        text = await self._call(
            "tools/call",
            {"name": TOOL_GET_CONTENT, "arguments": {"fileId": file_id}},
        )
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            raise YoudaoError(0, "getNoteTextContent 返回格式异常")

    async def close(self) -> None:
        self._stop.set()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=2)
        self._reader = None
