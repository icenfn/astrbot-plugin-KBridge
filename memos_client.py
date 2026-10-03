"""Memos（https://usememos.com/）REST API 客户端（KBridge 同步源方向）。"""
from __future__ import annotations

import aiohttp


class MemosError(Exception):
    """Memos API 错误。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MemosClient:
    """自建 Memos 实例客户端：分页列出备忘录（content 为 Markdown）。"""

    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self._base = (base_url or "").strip().rstrip("/")
        self._token = (token or "").strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        if not self._base:
            raise MemosError("未配置 Memos 实例地址（插件页面「平台配置」中填写）")
        if not self._token:
            raise MemosError("未配置 Memos API Token（Memos 设置 → 我的账户 → 访问令牌）")

    async def _call(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base}/api/v1{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        body = (await resp.text(errors="ignore"))[:300]
                        raise MemosError(f"Memos API {resp.status}: {body}")
                    return await resp.json(content_type=None)
        except MemosError:
            raise
        except Exception as e:  # noqa: BLE001
            raise MemosError(f"Memos API 请求失败: {e}") from e

    async def list_memos(
        self, page_size: int = 100, page_token: str = ""
    ) -> tuple[list[dict], str]:
        """分页列出备忘录（state=NORMAL），返回 (memos, next_page_token)。

        Memos v0.22+ 返回 {"memos": [...], "nextPageToken": "..."}；
        兼容旧版返回数组的响应。
        """
        params: dict = {"pageSize": max(1, min(int(page_size), 1000))}
        if page_token:
            params["pageToken"] = page_token
        data = await self._call("/memos", params=params)
        if isinstance(data, list):
            return data, ""
        memos = data.get("memos") or []
        return memos, str(data.get("nextPageToken") or "")

    async def close(self) -> None:
        pass
