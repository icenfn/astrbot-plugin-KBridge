"""Open Notebook（https://github.com/lfnovo/open-notebook）REST API 客户端（KBridge 同步源方向）。

Open Notebook 是自托管 RAG 服务，REST API 默认运行在 http://localhost:5055（FastAPI），
路径前缀 /api；若启用了 OPEN_NOTEBOOK_PASSWORD，请求头携带 Authorization: Bearer <密码>。

同步方向：列出 notebooks → 取每个 notebook 的 sources（上传的原始文档）或 notes（生成的笔记）
内容，写入 AstrBot 知识库。sources 端点缺失时自动回退 notes（不同版本差异）。
"""
from __future__ import annotations

import aiohttp


class OpenNotebookError(Exception):
    """Open Notebook API 错误。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OpenNotebookClient:
    def __init__(self, base_url: str, password: str = "", timeout: int = 60):
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise OpenNotebookError("未配置 Open Notebook 地址（插件页面「平台配置」中填写）")
        self._base = base.rstrip("/api") if base.endswith("/api") else base
        self._password = (password or "").strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    def _headers(self) -> dict[str, str]:
        if not self._password:
            return {}
        return {"Authorization": f"Bearer {self._password}"}

    async def _call(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{self._base}/api{path}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, headers=self._headers(), params=params) as resp:
                    if resp.status != 200:
                        body = (await resp.text(errors="ignore"))[:300]
                        raise OpenNotebookError(f"Open Notebook API {resp.status}: {body}")
                    return await resp.json(content_type=None)
        except OpenNotebookError:
            raise
        except Exception as e:  # noqa: BLE001
            raise OpenNotebookError(f"Open Notebook API 请求失败: {e}") from e

    async def list_notebooks(self) -> list[dict]:
        """列出全部 notebooks。"""
        data = await self._call("/notebooks")
        if not isinstance(data, list):
            raise OpenNotebookError("Open Notebook 返回的 notebooks 结构无效")
        return data

    async def list_sources(self, notebook_id: str) -> list[dict]:
        """列出 notebook 的 sources（上传的原始文档）。

        不同版本端点差异：优先 /sources?notebook_id=，404 时回退 /notes?notebook_id=。
        """
        try:
            data = await self._call("/sources", params={"notebook_id": notebook_id})
        except OpenNotebookError as e:
            if "404" not in str(e):
                raise
            data = await self._call("/notes", params={"notebook_id": notebook_id})
            return data if isinstance(data, list) else []
        return data if isinstance(data, list) else []

    async def close(self) -> None:
        pass
