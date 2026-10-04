"""IMA OpenAPI 客户端（官方协议：HTTP POST + JSON Body，统一响应 {code, msg, data}）。

参考：官方 ima-skills 包（https://app-dl.ima.qq.com/skills/ima-skills-*.zip）
接口 Base Path：/openapi/wiki/v1
"""

import asyncio
from typing import Any

from astrbot.api import logger

import aiohttp



BASE_URL = "https://ima.qq.com"
BASE_PATH = "/openapi/wiki/v1"

# 可重试错误码（指数退避）：110010/110021 业务重试，200001 wiki 频控，20002 notes 频控
RETRYABLE_CODES = {110010, 110021, 200001, 20002}
# 无权限读取（订阅知识库文件/共享笔记需 ima 客户端授权），不可重试：
# 220030 wiki 订阅库文件无权限；210005 非笔记作者；210011 共享知识库笔记无权访问；210034 笔记私有且非作者
PERMISSION_CODES = {220030, 210005, 210011, 210034}
# HTTP 层限流/服务端错误也重试
RETRYABLE_HTTP = {403, 429, 500, 502, 503, 504}
# 官方 skill 版本（随 ima-skills 包更新）
SKILL_VERSION = "1.1.10"

class IMAError(Exception):
    def __init__(self, code: int, msg: str, http_status: int | None = None):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.http_status = http_status

    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES or self.http_status in RETRYABLE_HTTP

class IMAClient:
    def __init__(self, client_id: str, api_key: str, timeout: int = 30):
        self._client_id = client_id
        self._api_key = api_key
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def post(
        self,
        endpoint: str,
        body: dict[str, Any],
        base_path: str = BASE_PATH,
    ) -> dict[str, Any]:
        """调用 IMA OpenAPI。返回 data 字段；code != 0 时抛 IMAError。"""
        url = f"{BASE_URL}{base_path}/{endpoint}"
        headers = {
            "ima-openapi-clientid": self._client_id,
            "ima-openapi-apikey": self._api_key,
            "ima-openapi-ctx": f"skill_version={SKILL_VERSION}",
            "Content-Type": "application/json",
        }
        session = await self._get_session()
        try:
            async with session.post(url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            status = getattr(e, "status", None)
            logger.error(f"IMA API 请求失败: {endpoint}: {e}")
            raise IMAError(-1, f"网络错误: {e}", http_status=status) from e

        code = payload.get("code", -1)
        msg = payload.get("msg", "unknown error")
        if code != 0:
            logger.warning(f"IMA API 返回错误 {endpoint}: code={code} msg={msg}")
            raise IMAError(code, msg)
        return payload.get("data", {}) or {}

    # ---------- 知识库 ----------

    # ---------- 知识库 ----------

    @staticmethod
    def _normalize_kb(item: dict[str, Any]) -> dict[str, Any]:
        """兼容 IMA 返回字段名差异，归一化为统一的 id/name 字段（含嵌套结构）。"""
        if not isinstance(item, dict):
            return item
        item = dict(item)
        for nest_key in ("info", "knowledge_base", "kb"):
            if isinstance(item.get(nest_key), dict) and not item.get("id") and not item.get("name"):
                item = dict(item[nest_key])
                break
        for key in ("id", "knowledge_base_id", "info_id", "kb_id", "media_id"):
            if item.get(key):
                item["id"] = item[key]
                break
        for key in ("name", "knowledge_base_name", "kb_name", "title"):
            if item.get(key):
                item["name"] = item[key]
                break
        return item

    async def search_knowledge_bases(
        self, query: str = "", cursor: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """搜索知识库列表（query 为空返回全部）。条目已归一化为统一的 {id, name}。"""
        items: list[dict[str, Any]] = []
        cur = cursor
        while True:
            data = await self.post(
                "search_knowledge_base",
                {"query": query, "cursor": cur, "limit": min(limit, 20)},
            )
            items.extend(self._normalize_kb(it) for it in (data.get("info_list", []) or []))
            if data.get("is_end") or not data.get("next_cursor"):
                break
            cur = data["next_cursor"]
            if len(items) >= 200:
                break
        return items

    async def list_kb_items(
        self,
        knowledge_base_id: str,
        cursor: str = "",
        limit: int = 50,
        folder_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, str]:
        """浏览知识库内容（一页）。返回 (knowledge_list, is_end, next_cursor)。"""
        body: dict[str, Any] = {
            "knowledge_base_id": knowledge_base_id,
            "cursor": cursor,
            "limit": min(limit, 50),
        }
        if folder_id:
            body["folder_id"] = folder_id
        data = await self.post("get_knowledge_list", body)
        return (
            data.get("knowledge_list", []) or [],
            bool(data.get("is_end")),
            data.get("next_cursor") or "",
        )

    async def get_media_info(self, media_id: str) -> dict[str, Any]:
        """获取媒体信息（url_info / notebook_ext_info）。"""
        return await self.post("get_media_info", {"media_id": media_id})

    async def get_doc_content(self, note_id: str) -> dict[str, Any]:
        """获取笔记纯文本（官方 notes 接口 /openapi/note/v1/get_doc_content）。

        需要笔记作者身份；订阅/共享库中的笔记会返回权限类错误码（210005/210011/210034）。
        """
        return await self.post(
            "get_doc_content",
            {"note_id": note_id, "target_content_format": 0},
            base_path="/openapi/note/v1",
        )

async def _retry_with_backoff(
    fn: Any, retries: int = 3, base_delay: float = 1.0
) -> Any:
    """对可重试的 IMAError 做指数退避重试。

    fn 必须是可重复调用的工厂（lambda），不能传已创建的 coroutine 对象
    （coroutine 只能 await 一次，重试会 RuntimeError）。
    """
    delay = base_delay
    for attempt in range(retries):
        try:
            return await fn()
        except IMAError as e:
            if not e.retryable() or attempt == retries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
