"""订阅管理与同步核心：IMA 知识库 -> AstrBot 知识库。

持久化走插件 KV 存储（AstrBot 官方推荐，随 AstrBot 数据库持久化）：
- kbridge_subs:   订阅列表
- kbridge_index:  每个订阅已同步的 media_id -> AstrBot doc_id（增量去重）
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .ima_client import IMAClient, IMAError, _retry_with_backoff

logger = logging.getLogger("astrbot")

KV_SUBS = "kbridge_subs"
KV_INDEX = "kbridge_index"

SUPPORTED_EXT = {"md", "txt", "markdown", "rst", "adoc", "docx", "xlsx", "xls", "pdf", "epub"}
NOTE_MEDIA_TYPES = {11, 12}
TEXT_MEDIA_TYPES = {2, 6}  # 网页 / 微信公众号文章
MAX_ITEM_SAVE = 2000  # 单个订阅单次同步条目上限，防止异常膨胀

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass
class Subscription:
    kb_id: str
    kb_name: str
    target_kb: str = ""
    enabled: bool = True
    last_sync_at: str = ""
    last_status: str = "pending"  # pending | ok | partial | error
    last_error: str = ""
    synced_count: int = 0
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict:
        return {
            "kb_id": self.kb_id,
            "kb_name": self.kb_name,
            "target_kb": self.target_kb,
            "enabled": self.enabled,
            "last_sync_at": self.last_sync_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "synced_count": self.synced_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Subscription":
        return cls(**{k: d.get(k, v) for k, v in cls.__dataclass_fields__.items() if k in d})


@dataclass
class SyncResult:
    kb_id: str
    total: int = 0
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    return name[:120] or "untitled"


def _html_to_markdown(raw: str) -> str:
    """极简 HTML -> Markdown 文本提取（不引入额外依赖）。"""
    raw = _SCRIPT_RE.sub("", raw)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"</(p|div|h[1-6]|li|tr|section|article)>", "\n", raw, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", raw)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _ext_from_url(url: str, media_type: int) -> str | None:
    """根据 URL 推断文件扩展名；无法判断时返回 None。"""
    if media_type in TEXT_MEDIA_TYPES:
        return "md"
    m = re.search(r"\.([a-zA-Z0-9]+)(?:$|[?#])", url)
    if m:
        ext = m.group(1).lower()
        if ext in SUPPORTED_EXT:
            return ext
    return None


class SyncManager:
    def __init__(self, context, config):
        self.context = context
        self.config = config
        self._client: IMAClient | None = None
        self._index_cache: dict[str, dict[str, Any]] | None = None
        self._sem: asyncio.Semaphore | None = None
        self._sync_lock = asyncio.Lock()
        self._syncing = False

    @property
    def is_syncing(self) -> bool:
        return self._syncing

    # ---------- 基础 ----------

    async def get_client(self) -> IMAClient:
        if self._client is None:
            client_id = (self.config.get("ima_client_id") or "").strip()
            api_key = (self.config.get("ima_api_key") or "").strip()
            if not client_id or not api_key:
                raise IMAError(110030, "未配置 IMA Client ID / API Key（插件配置面板中填写）")
            self._client = IMAClient(client_id, api_key)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    async def reset_client(self) -> None:
        """配置变更后重置 IMA 客户端，使新 Key 生效。"""
        if self._client:
            await self._client.close()
        self._client = None

    async def get_subs(self) -> list[Subscription]:
        raw = await self.context.get_kv_data(KV_SUBS, [])
        return [Subscription.from_dict(d) for d in raw]

    async def _save_subs(self, subs: list[Subscription]) -> None:
        await self.context.put_kv_data(KV_SUBS, [s.to_dict() for s in subs])

    async def _get_index(self) -> dict[str, dict[str, Any]]:
        if self._index_cache is None:
            self._index_cache = await self.context.get_kv_data(KV_INDEX, {}) or {}
        return self._index_cache

    async def _save_index(self) -> None:
        await self.context.put_kv_data(KV_INDEX, self._index_cache)

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, int(self.config.get("max_concurrency", 3))))
        return self._sem

    # ---------- 订阅管理 ----------

    async def add_subscription(
        self, kb_id: str, kb_name: str = "", target_kb: str = ""
    ) -> Subscription:
        subs = await self.get_subs()
        if any(s.kb_id == kb_id for s in subs):
            raise ValueError(f"知识库 {kb_id} 已在订阅列表中")
        sub = Subscription(kb_id=kb_id, kb_name=kb_name, target_kb=target_kb)
        subs.append(sub)
        await self._save_subs(subs)
        return sub

    async def remove_subscription(self, index: int) -> Subscription:
        subs = await self.get_subs()
        if index < 0 or index >= len(subs):
            raise ValueError(f"订阅序号无效: {index}")
        sub = subs.pop(index)
        await self._save_subs(subs)
        index_data = await self._get_index()
        index_data.pop(sub.kb_id, None)
        await self._save_index()
        return sub

    async def resolve_target_kb(self, sub: Subscription):
        """解析/创建目标 AstrBot 知识库，返回 KBHelper 或抛错。"""
        kb_mgr = self.context.kb_manager
        name = sub.target_kb or f"{self.config.get('target_kb_prefix', 'ima-')}{sub.kb_name}"
        kb = await kb_mgr.get_kb_by_name(name)
        if kb:
            return kb, name
        if not self.config.get("auto_create_kb", True):
            raise ValueError(f"目标知识库 {name} 不存在，且未开启自动创建")
        eps = self.context.get_all_embedding_providers()
        if not eps:
            raise ValueError("未配置任何嵌入（Embedding）模型，无法自动创建知识库")
        await kb_mgr.create_kb(name, embedding_provider_id=eps[0].provider_config.get("id") or "default", emoji="📥")
        kb = await kb_mgr.get_kb_by_name(name)
        if kb is None:
            raise ValueError(f"创建知识库失败: {name}")
        return kb, name

    async def _flush_sub(self, sub: Subscription) -> None:
        """把 sub 的最新状态写回订阅列表并落库。"""
        subs = await self.get_subs()
        for s in subs:
            if s.kb_id == sub.kb_id:
                s.synced_count = sub.synced_count
                s.last_sync_at = sub.last_sync_at
                s.last_status = sub.last_status
                s.last_error = sub.last_error
                break
        await self._save_subs(subs)

    # ---------- 同步 ----------

    async def sync_subscription(self, sub: Subscription) -> SyncResult:
        result = SyncResult(kb_id=sub.kb_id)
        try:
            client = await self.get_client()
            kb, kb_name = await self.resolve_target_kb(sub)
            index = await self._get_index()
            kb_index = index.setdefault(sub.kb_id, {})

            # 1. 拉取根目录全部条目（翻页）
            items: list[dict[str, Any]] = []
            cursor = ""
            while True:
                batch, is_end, next_cursor = await _retry_with_backoff(
                    client.list_kb_items(sub.kb_id, cursor=cursor)
                )
                items.extend(batch)
                if is_end or not next_cursor or len(items) >= MAX_ITEM_SAVE:
                    break
                cursor = next_cursor
            result.total = len(items)

            # 2. 逐个条目增量同步（并发受限）
            sem = self._semaphore()

            async def process(item: dict[str, Any]) -> None:
                media_id = str(item.get("media_id") or "")
                if not media_id or media_id in kb_index:
                    result.skipped += 1
                    return
                try:
                    async with sem:
                        await self._process_item(kb, kb_name, sub, media_id, item, kb_index)
                    result.synced += 1
                except IMAError as e:
                    if e.code == 110020:
                        result.skipped += 1  # 安全打击/内容违规，跳过
                    else:
                        result.failed += 1
                        result.errors.append(f"{item.get('title', media_id)}: {e.msg}")
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"同步条目失败 {media_id}")
                    result.failed += 1
                    result.errors.append(f"{item.get('title', media_id)}: {e}")

            await asyncio.gather(*(process(it) for it in items))

            await self._save_index()
            sub.synced_count += result.synced
            sub.last_sync_at = time.strftime("%Y-%m-%d %H:%M:%S")
            sub.last_status = "ok" if result.failed == 0 else "partial"
            sub.last_error = "; ".join(result.errors[:5])
            await self._flush_sub(sub)
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception(f"同步订阅失败 {sub.kb_id}")
            sub.last_status = "error"
            sub.last_error = str(e)
            await self._flush_sub(sub)
            raise

    async def sync_all(self) -> list[SyncResult]:
        """同步全部订阅（防重入：已有同步在跑时直接返回空结果）。"""
        if self._syncing:
            return []
        async with self._sync_lock:
            self._syncing = True
            try:
                results: list[SyncResult] = []
                for sub in await self.get_subs():
                    if not sub.enabled:
                        continue
                    try:
                        results.append(await self.sync_subscription(sub))
                    except Exception as e:  # noqa: BLE001
                        r = SyncResult(kb_id=sub.kb_id)
                        r.failed = 1
                        r.errors.append(str(e))
                        results.append(r)
                return results
            finally:
                self._syncing = False

    async def _process_item(
        self, kb, kb_name: str, sub: Subscription, media_id: str, item: dict, kb_index: dict
    ) -> None:
        client = await self.get_client()
        info = await _retry_with_backoff(client.get_media_info(media_id))
        media_type = int(info.get("media_type") or 0)
        title = str(item.get("title") or media_id)

        # 笔记/AI 会话：暂不支持，跳过并记录
        if media_type in NOTE_MEDIA_TYPES or info.get("notebook_ext_info"):
            kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "note"}
            return

        url_info = info.get("url_info") or {}
        url = url_info.get("url") or ""
        if not url:
            kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "no-url"}
            return

        headers = url_info.get("headers") or {}
        content, content_type, final_url = await self._download(url, headers)

        # 决定落盘类型
        ext = _ext_from_url(final_url or url, media_type)
        if ext is None:
            if content_type.startswith("text/html") or media_type in TEXT_MEDIA_TYPES:
                ext = "md"
            else:
                kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "unsupported"}
                return

        file_name = f"{_sanitize_filename(title)}.{ext}"
        if ext == "md":
            text = _html_to_markdown(content.decode("utf-8", errors="ignore"))
            if not text.strip():
                kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "empty"}
                return
            payload = text.encode("utf-8")
        else:
            payload = content

        doc = await kb.upload_document(
            file_name=file_name,
            file_content=payload,
            file_type=ext,
        )
        kb_index[media_id] = {
            "doc_id": doc.doc_id,
            "title": title,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def _download(
        self, url: str, headers: dict[str, str]
    ) -> tuple[bytes, str, str]:
        """下载 URL 内容，返回 (bytes, content_type, final_url)。"""
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers or None) as resp:
                resp.raise_for_status()
                data = await resp.read()
                if len(data) > 200 * 1024 * 1024:
                    raise ValueError("内容超过 200MB 限制")
                return data, resp.headers.get("Content-Type", ""), str(resp.url)
