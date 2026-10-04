"""同步源管理与同步核心：外部知识源（IMA / 有道云笔记）-> AstrBot 知识库。

持久化走插件 KV 存储（AstrBot 官方推荐，随 AstrBot 数据库持久化）：
- kbridge_subs:   同步源列表
- kbridge_index:  每个同步源已同步条目 id -> AstrBot doc_id（增量去重；
                  有道云条目 id 以 yd: 前缀区分，IMA 保持无前缀兼容存量）
"""

import asyncio
import hashlib
import html as html_lib
import uuid
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from astrbot.api import logger

from .ima_client import IMAClient, IMAError, PERMISSION_CODES, _retry_with_backoff
from .youdao_client import YoudaoClient, YoudaoError
from .on_client import OpenNotebookClient, OpenNotebookError
from .memos_client import MemosClient, MemosError
from .github_client import (
    GitHubClient,
    GitHubError,
    IGNORE_SEGMENTS,
    sub_key_to_parsed,
)



KV_SUBS = "kbridge_subs"
KV_INDEX = "kbridge_index"
KV_SCHED_LOGS = "kbridge_sched_logs"
KV_URL2KB_GROUPS = "kbridge_url2kb_groups"
URL2KB_PREFIX = "ur2:"  # url2kb 增量索引前缀（分组内 url id）
MAX_SCHED_LOGS = 100  # 定时同步日志环形上限
DEFAULT_GITHUB_MIRROR = "https://gh.dpik.top/"  # Raw 加速镜像默认值

SUPPORTED_EXT = {"md", "txt", "markdown", "rst", "adoc", "docx", "xlsx", "xls", "pdf", "epub"}
NOTE_MEDIA_TYPES = {11, 12}
TEXT_MEDIA_TYPES = {2, 6}  # 网页 / 微信公众号文章
MAX_ITEM_SAVE = 2000  # 单个同步源单次同步条目上限，防止异常膨胀
# 静默跳过类错误（不计失败）：110020 安全打击/内容违规；210006 笔记已删除
SKIP_CODES = {110020, 210006}
# 有道云条目索引键前缀（与 IMA media_id 区分，避免跨平台 id 撞车）
YDAO_KEY_PREFIX = "yd:"
ON_KEY_PREFIX = "on:"  # Open Notebook 增量索引前缀（source/note id）
MEMOS_KEY_PREFIX = "ms:"  # Memos 增量索引前缀（memo uid）

# 平台显示名（创建知识库时 description 回退使用）
PLATFORM_DISPLAY_NAMES = {
    "ima": "腾讯 ima",
    "youdao": "有道云笔记",
    "github": "Github",
    "url2kb": "url2kb",
    "opennotebook": "Open Notebook",
    "memos": "Memos",
}

# 创建知识库默认图标（可在「平台配置」页面通过 kb_emoji 覆盖）
DEFAULT_KB_EMOJI = "📥"

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t\r\f\v]+")

@dataclass
class Subscription:
    kb_id: str
    kb_name: str
    target_kb: str = ""
    platform: str = "ima"  # 来源平台：ima / youdao / github / url2kb
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
            "platform": self.platform,
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

class _SyncCancelled(Exception):
    """同步被用户取消（已入库部分保留）。"""

@dataclass
class SyncResult:
    kb_id: str
    total: int = 0
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skip_counts: dict[str, int] = field(default_factory=dict)

def _content_hash(payload) -> str:
    """内容指纹（md5 前 16 位）。payload 支持 str/bytes。"""
    raw = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:16]


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
    """同步源管理与同步核心。star 为插件实例（提供 KV 存储与专属 logger）。"""

    def __init__(self, star, context, config):
        self.star = star
        self.context = context
        self.config = config
        self.logger = getattr(star, "logger", logger)
        self._client: IMAClient | None = None
        self._youdao: YoudaoClient | None = None
        self._github: GitHubClient | None = None
        self._on: OpenNotebookClient | None = None
        self._memos: MemosClient | None = None
        self._index_cache: dict[str, dict[str, Any]] | None = None
        self._sem: asyncio.Semaphore | None = None
        self._upload_sem: asyncio.Semaphore | None = None  # 全局入库串行锁（防并发 embedding OOM）
        self._cancel = False
        self._cancel_kbs: set[str] = set()  # 按同步源取消（多源并行下互不影响）
        self._active: dict[str, tuple[Subscription, Any]] = {}  # 进行中的同步源 kb_id -> (sub, result)

    @property
    def is_syncing(self) -> bool:
        return bool(self._active)

    def _sub_cancelled(self, kb_id: str) -> bool:
        """该同步源是否被请求取消（全局取消或单源取消）。"""
        return self._cancel or kb_id in self._cancel_kbs

    def request_cancel(self, kb_id: str | None = None) -> None:
        """取消同步。kb_id 为空时取消全部进行中的同步源。"""
        if kb_id is None:
            self._cancel = True
        else:
            self._cancel_kbs.add(kb_id)

    @property
    def current_progress(self) -> list[dict]:
        """所有进行中同步源的进度（多源并行），无同步时返回空列表。"""
        return [
            {
                "kb_id": kb_id,
                "name": sub.kb_name,
                "total": r.total,
                "synced": r.synced,
                "skipped": r.skipped,
                "failed": r.failed,
            }
            for kb_id, (sub, r) in self._active.items()
        ]

    # ---------- 基础 ----------

    def platform_enabled(self) -> dict:
        """读取平台启用/禁用状态（默认全部启用）。"""
        pe = self.config.get("platform_enabled") or {}
        if not isinstance(pe, dict):
            return {"ima": True, "youdao": True, "github": True}
        return {
            "ima": bool(pe.get("ima", True)),
            "youdao": bool(pe.get("youdao", True)),
            "github": bool(pe.get("github", True)),
            "url2kb": bool(pe.get("url2kb", True)),
            "opennotebook": bool(pe.get("opennotebook", True)),
            "memos": bool(pe.get("memos", True)),
        }

    def _check_platform_enabled(self, platform: str) -> None:
        if not self.platform_enabled().get(platform or "ima", True):
            raise ValueError(f"平台 {platform} 已禁用，请先在「平台配置」中启用")

    async def get_client(self, platform: str = "ima"):
        """按平台返回客户端实例（懒加载，凭据缺失时抛错）。"""
        platform = platform or "ima"
        if platform == "youdao":
            if self._youdao is None:
                api_key = (self.config.get("youdao_api_key") or "").strip()
                if not api_key:
                    raise YoudaoError(0, "未配置有道云笔记 API Key（插件页面「平台配置」中填写）")
                self._youdao = YoudaoClient(api_key)
            return self._youdao
        if platform == "github":
            if self._github is None:
                self._github = GitHubClient(
                    (self.config.get("github_token") or "").strip(),
                    raw_mirror=(self.config.get("github_raw_mirror") or "").strip()
                    or DEFAULT_GITHUB_MIRROR,
                )
            return self._github
        if platform == "opennotebook":
            if self._on is None:
                self._on = OpenNotebookClient(
                    (self.config.get("open_notebook_url") or "").strip()
                    or "http://localhost:5055",
                    (self.config.get("open_notebook_password") or "").strip(),
                )
            return self._on
        if platform == "memos":
            if self._memos is None:
                self._memos = MemosClient(
                    (self.config.get("memos_url") or "").strip(),
                    (self.config.get("memos_token") or "").strip(),
                )
            return self._memos
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
        if self._youdao:
            await self._youdao.close()
        if self._github:
            await self._github.close()
        if self._on:
            await self._on.close()
        if self._memos:
            await self._memos.close()

    async def reset_client(self, platform: str = "") -> None:
        """配置变更后重置客户端，使新 Key 生效。platform 为空则全部重置。"""
        if platform in ("", "ima") and self._client:
            await self._client.close()
            self._client = None
        if platform in ("", "youdao") and self._youdao:
            await self._youdao.close()
            self._youdao = None
        if platform in ("", "github") and self._github:
            await self._github.close()
            self._github = None
        if platform in ("", "opennotebook") and self._on:
            await self._on.close()
            self._on = None
        if platform in ("", "memos") and self._memos:
            await self._memos.close()
            self._memos = None

    async def get_subs(self) -> list[Subscription]:
        raw = await self.star.get_kv_data(KV_SUBS, [])
        return [Subscription.from_dict(d) for d in raw]

    async def _save_subs(self, subs: list[Subscription]) -> None:
        await self.star.put_kv_data(KV_SUBS, [s.to_dict() for s in subs])

    async def _get_index(self) -> dict[str, dict[str, Any]]:
        if self._index_cache is None:
            self._index_cache = await self.star.get_kv_data(KV_INDEX, {}) or {}
        return self._index_cache

    async def _save_index(self) -> None:
        await self.star.put_kv_data(KV_INDEX, self._index_cache)

    async def _count_index(self, kb_id: str) -> int:
        """该同步源当前已入库的文档数（增量索引条目数）。"""
        index = await self._get_index()
        return len(index.get(kb_id) or {})

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, int(self.config.get("max_concurrency", 3))))
        return self._sem

    def _upload_semaphore(self) -> asyncio.Semaphore:
        """全局入库串行锁：所有同步源的 upload_document 统一串行。

        upload_document 会做切片 + embedding 向量化，若多个同步源/多个条目
        并发入库，本地 embedding 模型或 API 请求会瞬间打爆内存/显存（OOM）。
        下载仍可并发，仅入库严格串行。
        """
        if self._upload_sem is None:
            self._upload_sem = asyncio.Semaphore(1)
        return self._upload_sem

    # ---------- 同步源管理 ----------

    async def get_sub_by_kb_id(self, kb_id: str, platform: str = "ima") -> Subscription | None:
        """按平台 + 知识库 ID 查找已存在的同步源。"""
        subs = await self.get_subs()
        return next(
            (s for s in subs if s.kb_id == kb_id and s.platform == (platform or "ima")),
            None,
        )

    async def add_subscription(
        self, kb_id: str, kb_name: str = "", target_kb: str = "", platform: str = "ima"
    ) -> Subscription:
        platform = platform or "ima"
        subs = await self.get_subs()
        if any(s.kb_id == kb_id and s.platform == platform for s in subs):
            raise ValueError(f"知识库 {kb_id} 已在 {platform} 同步源列表中")
        sub = Subscription(kb_id=kb_id, kb_name=kb_name, target_kb=target_kb, platform=platform)
        subs.append(sub)
        await self._save_subs(subs)
        return sub

    async def remove_subscription(self, index: int) -> Subscription:
        subs = await self.get_subs()
        if index < 0 or index >= len(subs):
            raise ValueError(f"同步源序号无效: {index}")
        sub = subs.pop(index)
        await self._save_subs(subs)
        index_data = await self._get_index()
        index_data.pop(sub.kb_id, None)
        await self._save_index()
        self.logger.info(f"[KBridge] 删除同步源: {sub.kb_name} ({sub.kb_id})")
        return sub

    async def remove_subscription_by_key(self, kb_id: str, platform: str = "ima") -> Subscription:
        """按 kb_id + platform 删除同步源（页面删除按钮使用，避免列表顺序错位）。"""
        subs = await self.get_subs()
        for i, s in enumerate(subs):
            if s.kb_id == kb_id and s.platform == (platform or "ima"):
                sub = subs.pop(i)
                await self._save_subs(subs)
                index_data = await self._get_index()
                index_data.pop(sub.kb_id, None)
                await self._save_index()
                self.logger.info(f"[KBridge] 删除同步源: {sub.kb_name} ({sub.kb_id})")
                return sub
        raise ValueError("同步源不存在")

    async def set_sub_enabled(
        self, kb_id: str, platform: str = "ima", enabled: bool = True
    ) -> Subscription:
        """开关同步源的定时同步（enabled）。"""
        subs = await self.get_subs()
        for s in subs:
            if s.kb_id == kb_id and s.platform == (platform or "ima"):
                s.enabled = bool(enabled)
                await self._save_subs(subs)
                self.logger.info(f"[KBridge] 同步源定时开关: {s.kb_name} -> {s.enabled}")
                return s
        raise ValueError("同步源不存在")

    async def ensure_opennotebook_subs(self) -> list[dict[str, Any]]:
        """Open Notebook（beta）：自动补全 notebooks 同步源并清理残留，返回列表。"""
        client = await self.get_client("opennotebook")
        items = await _retry_with_backoff(lambda: client.list_notebooks())
        subs = await self.get_subs()
        existing = {(s.platform, s.kb_id) for s in subs}
        valid: set[str] = set()
        changed = False
        out: list[dict[str, Any]] = []
        for it in items:
            nid = str(it.get("id") or "")
            name = str(it.get("name") or nid)
            if not nid:
                continue
            valid.add(nid)
            out.append({"id": nid, "name": name})
            if ("opennotebook", nid) not in existing:
                subs.append(
                    Subscription(
                        kb_id=nid, kb_name=name, target_kb=name, platform="opennotebook"
                    )
                )
                existing.add(("opennotebook", nid))
                changed = True
        stale = [s for s in subs if s.platform == "opennotebook" and s.kb_id not in valid]
        if stale:
            subs = [s for s in subs if s.platform != "opennotebook" or s.kb_id in valid]
            changed = True
        if changed:
            await self._save_subs(subs)
        return out

    async def ensure_memos_sub(self) -> Subscription:
        """Memos（beta）：单库同步源，目标库名与配置（memos_target_kb）保持同步。"""
        target = (self.config.get("memos_target_kb") or "").strip() or "Memos"
        subs = await self.get_subs()
        sub = next((s for s in subs if s.platform == "memos"), None)
        if sub is None:
            sub = Subscription(
                kb_id="memos", kb_name=target, target_kb=target, platform="memos"
            )
            subs.append(sub)
            await self._save_subs(subs)
        elif sub.target_kb != target:
            sub.target_kb = target
            sub.kb_name = target
            await self._save_subs(subs)
        return sub

    async def add_schedule_log(self, level: str, message: str) -> None:
        """写入定时同步日志（环形，最多 MAX_SCHED_LOGS 条，新在前）。"""
        logs = await self.star.get_kv_data(KV_SCHED_LOGS, []) or []
        logs.insert(
            0,
            {
                "t": time.strftime("%Y-%m-%d %H:%M:%S"),
                "level": level,
                "msg": message,
            },
        )
        await self.star.put_kv_data(KV_SCHED_LOGS, logs[:MAX_SCHED_LOGS])

    async def get_schedule_logs(self) -> list[dict]:
        return await self.star.get_kv_data(KV_SCHED_LOGS, []) or []

    async def clear_schedule_logs(self) -> None:
        """清空定时同步日志。"""
        await self.star.put_kv_data(KV_SCHED_LOGS, [])

    async def ensure_ima_subs(self) -> list[dict[str, Any]]:
        """确保 IMA 自建知识库均有对应同步源（页面直接展示，无需手动拉取）。

        返回归一化的知识库列表（含 id/name），供页面展示。
        """
        client = await self.get_client("ima")
        items = await _retry_with_backoff(lambda: client.search_knowledge_bases())
        # 只展示自建库：订阅/共享文件无法经 OpenAPI 读取（220030），拉取必然失败
        items = [it for it in items if it.get("base_type") != "我加入的订阅知识库"]
        subs = await self.get_subs()
        existing = {(s.platform, s.kb_id) for s in subs}
        added = False
        for it in items:
            kb_id = str(it.get("id") or "")
            if not kb_id or ("ima", kb_id) in existing:
                continue
            subs.append(
                Subscription(kb_id=kb_id, kb_name=it.get("name") or kb_id, platform="ima")
            )
            existing.add(("ima", kb_id))
            added = True
        if added:
            await self._save_subs(subs)
            self.logger.info(f"[KBridge] 自动补全 IMA 同步源（无需手动拉取）")
        return items

    async def ensure_youdao_sub(self) -> Subscription:
        """确保有道云单库同步源存在，目标库名与配置（youdao_target_kb）保持同步。"""
        subs = await self.get_subs()
        sub = next((s for s in subs if s.platform == "youdao"), None)
        target = (self.config.get("youdao_target_kb") or "").strip() or "YoudaoNote"
        if sub is None:
            sub = Subscription(
                kb_id="0", kb_name=target, target_kb=target, platform="youdao"
            )
            subs.append(sub)
            await self._save_subs(subs)
        elif sub.target_kb != target or sub.kb_name != target:
            sub.target_kb = target
            sub.kb_name = target
            await self._save_subs(subs)
        return sub

    # ---------- url2kb：网页分组 -> AstrBot 知识库 ----------

    async def get_url2kb_groups(self) -> list[dict[str, Any]]:
        """读取 url2kb 分组列表：[{id, name, note, urls:[{id,title,url}]}]。"""
        raw = await self.star.get_kv_data(KV_URL2KB_GROUPS, []) or []
        return raw if isinstance(raw, list) else []

    async def save_url2kb_groups(self, groups: list[dict[str, Any]]) -> None:
        await self.star.put_kv_data(KV_URL2KB_GROUPS, groups)

    async def url2kb_add_group(self, name: str, note: str = "") -> dict[str, Any]:
        groups = await self.get_url2kb_groups()
        g = {
            "id": str(uuid.uuid4()),
            "name": (name or "").strip(),
            "note": (note or "").strip(),
            "urls": [],
        }
        groups.append(g)
        await self.save_url2kb_groups(groups)
        await self.ensure_url2kb_subs()
        return g

    async def url2kb_update_group(self, gid: str, name: str, note: str) -> bool:
        groups = await self.get_url2kb_groups()
        for g in groups:
            if str(g.get("id")) == gid:
                if name is not None:
                    g["name"] = (name or "").strip()
                if note is not None:
                    g["note"] = (note or "").strip()
                await self.save_url2kb_groups(groups)
                await self.ensure_url2kb_subs()
                return True
        return False

    async def url2kb_remove_group(self, gid: str) -> bool:
        groups = await self.get_url2kb_groups()
        new = [g for g in groups if str(g.get("id")) != gid]
        if len(new) == len(groups):
            return False
        await self.save_url2kb_groups(new)
        await self.ensure_url2kb_subs()
        return True

    async def url2kb_add_url(self, gid: str, url: str) -> tuple[bool, str]:
        """添加 URL 到分组；自动识别网页标题（识别失败返回 '' 由调用方回退 URL）。"""
        url = (url or "").strip()
        if not url:
            return False, ""
        groups = await self.get_url2kb_groups()
        for g in groups:
            if str(g.get("id")) != gid:
                continue
            title = ""
            try:
                title = await self.url2kb_detect_title(url)
            except Exception:  # noqa: BLE001
                title = ""
            g.setdefault("urls", []).append(
                {"id": str(uuid.uuid4()), "title": title or "", "url": url}
            )
            await self.save_url2kb_groups(groups)
            return True, title
        return False, ""

    async def url2kb_update_url(self, gid: str, uid: str, title: str, url: str) -> bool:
        """编辑 URL 的标题与地址。"""
        groups = await self.get_url2kb_groups()
        url = (url or "").strip()
        if not url:
            return False
        for g in groups:
            if str(g.get("id")) != gid:
                continue
            for u in g.get("urls") or []:
                if str(u.get("id")) != uid:
                    continue
                u["title"] = (title or "").strip()
                u["url"] = url
                await self.save_url2kb_groups(groups)
                return True
            return False
        return False

    async def url2kb_remove_url(self, gid: str, uid: str) -> bool:
        groups = await self.get_url2kb_groups()
        for g in groups:
            if str(g.get("id")) != gid:
                continue
            urls = [u for u in g.get("urls") or [] if str(u.get("id")) != uid]
            if len(urls) == len(g.get("urls") or []):
                return False
            g["urls"] = urls
            await self.save_url2kb_groups(groups)
            return True
        return False

    async def url2kb_detect_title(self, url: str) -> str:
        """抓取网页 <title> 识别标题（失败返回空字符串）。

        只读取响应前 128KB（<title> 位于 <head> 头部），避免整页下载
        拖慢「添加 URL」；超时 10s，慢/被墙站点快速回退，页面不卡。
        """
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:
                if resp.status != 200:
                    return ""
                raw = await resp.content.read(128 * 1024)
                html = raw.decode("utf-8", errors="ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.S)
        if not m:
            return ""
        title = _html_to_markdown(m.group(1)).strip()
        return title[:80]

    async def ensure_url2kb_subs(self) -> list[dict[str, Any]]:
        """url2kb 分组即同步源：按分组自动补全/同步/清理订阅（分组名 = AstrBot 知识库名）。"""
        groups = await self.get_url2kb_groups()
        subs = await self.get_subs()
        existing = {(s.platform, s.kb_id): s for s in subs}
        valid: set[str] = set()
        changed = False
        for g in groups:
            gid = str(g.get("id") or "")
            if not gid:
                continue
            valid.add(gid)
            name = str(g.get("name") or "").strip() or "url2kb"
            key = ("url2kb", gid)
            if key not in existing:
                subs.append(
                    Subscription(
                        kb_id=gid, kb_name=name, target_kb=name, platform="url2kb"
                    )
                )
                changed = True
            else:
                s = existing[key]
                if s.target_kb != name or s.kb_name != name:
                    s.target_kb = name
                    s.kb_name = name
                    changed = True
        # 分组已删除的残留订阅一并清理
        stale = [s for s in subs if s.platform == "url2kb" and s.kb_id not in valid]
        if stale:
            subs = [s for s in subs if not (s.platform == "url2kb" and s.kb_id not in valid)]
            changed = True
        if changed:
            await self._save_subs(subs)
        return groups

    async def resolve_target_kb(self, sub: Subscription):
        """解析/创建目标 AstrBot 知识库，返回 (KBHelper, name, recreated)。

        有道云目标库名取配置 youdao_target_kb（默认 YoudaoNote）；
        ima 与源同名；github 取仓库名。
        创建时自动选择可用的 Embedding 与 Rerank（重排序）Provider。
        recreated=True 表示本次新建（原知识库被删除），旧增量索引全部失效。
        """
        kb_mgr = self.context.kb_manager
        name = self._target_kb_name(sub)
        kb = await kb_mgr.get_kb_by_name(name)
        if kb:
            return kb, name, False
        # 始终自动创建目标知识库（无 auto_create_kb 开关），并默认绑定重排序模型
        embedding_provider_id = await self._pick_embedding_provider_id()
        rerank_provider_id = await self._pick_rerank_provider_id()
        await kb_mgr.create_kb(
            name,
            description=await self._kb_description(sub),
            embedding_provider_id=embedding_provider_id,
            rerank_provider_id=rerank_provider_id,
            emoji=self._kb_emoji(),
        )
        kb = await kb_mgr.get_kb_by_name(name)
        if kb is None:
            raise ValueError(f"创建知识库失败: {name}")
        if rerank_provider_id:
            self.logger.info(
                f"[KBridge] 知识库 {name} 已重建并绑定重排序模型: {rerank_provider_id}"
            )
        return kb, name, True

    def _target_kb_name(self, sub: Subscription) -> str:
        """该同步源的目标 AstrBot 知识库名（与 resolve_target_kb 一致）。"""
        if sub.platform == "youdao":
            return (
                sub.target_kb
                or (self.config.get("youdao_target_kb") or "").strip()
                or "YoudaoNote"
            )
        if sub.platform == "github":
            return sub.target_kb or self._github_repo_name(sub.kb_id)
        return sub.target_kb or sub.kb_name

    def _kb_emoji(self) -> str:
        """创建知识库时使用的图标，可在「平台配置」页面通过 kb_emoji 配置。"""
        return (str(self.config.get("kb_emoji") or "").strip()) or DEFAULT_KB_EMOJI

    async def _kb_description(self, sub: Subscription) -> str:
        """创建知识库时写入的描述：优先使用从来源获取到的描述，取不到则回退平台名称。"""
        desc = ""
        if sub.platform == "github":
            try:
                client = await self.get_client("github")
                get_desc = getattr(client, "get_repo_description", None)
                if get_desc is not None:
                    desc = (await get_desc(sub_key_to_parsed(sub.kb_id)) or "").strip()
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"[KBridge] 获取 GitHub 仓库描述失败：{e}")
        return desc or PLATFORM_DISPLAY_NAMES.get(sub.platform, "") or sub.platform

    async def delete_local(self, sub: Subscription) -> dict:
        """删除该同步源已同步到 AstrBot 的本地知识库数据（订阅与源保留）。

        删除目标知识库（幂等：不存在则跳过），并清空该同步源的增量索引，
        列表「已同步 N」随之归零，下次同步全量重建。
        """
        name = self._target_kb_name(sub)
        kb_mgr = self.context.kb_manager
        deleted = False
        kb = await kb_mgr.get_kb_by_name(name)
        if kb is not None:
            del_kb = getattr(kb_mgr, "delete_kb", None)
            if del_kb is None:
                raise ValueError("当前 AstrBot 版本不支持删除知识库（KBManager.delete_kb）")
            # delete_kb 需要 kb_id（UUID），传入知识库名会查不到实例导致静默失败
            kb_id = getattr(getattr(kb, "kb", None), "kb_id", None) or getattr(
                kb, "kb_id", None
            )
            if not kb_id:
                raise ValueError(f"无法解析知识库「{name}」的 id，删除失败")
            ok = await del_kb(kb_id)
            deleted = ok if isinstance(ok, bool) else True
            if not deleted:
                raise ValueError(f"删除知识库「{name}」失败")
        index = await self._get_index()
        if sub.kb_id in index:
            del index[sub.kb_id]
            await self._save_index()
        # 同步源列表状态归零（已同步数/时间/状态）
        sub.synced_count = 0
        sub.last_sync_at = ""
        sub.last_status = "deleted"
        sub.last_error = ""
        await self._flush_sub(sub)
        return {"name": name, "deleted": deleted}

    @staticmethod
    def _github_repo_name(kb_id: str) -> str:
        """从 github 同步源 kb_id（gh:owner/repo@branch:path）提取仓库名。"""
        try:
            body = kb_id.split(":", 1)[1]
            return body.split("/", 1)[1].split("@", 1)[0]
        except (IndexError, ValueError):
            return kb_id

    async def _pick_embedding_provider_id(self) -> str:
        """返回通过 ProviderManager 校验、确实可用的 Embedding Provider id。

        get_all_embedding_providers() 返回的实例列表可能包含已失效的残留实例
        （reload/terminate 后 embedding_provider_insts 未清理），不能直接取
        第一个的 config id；必须经 inst_map（get_provider_by_id）逐一验证。
        """
        eps = self.context.get_all_embedding_providers()
        if not eps:
            raise ValueError(
                "未配置任何嵌入（Embedding）模型，无法自动创建知识库："
                "请先在 AstrBot「平台设置」中启用并配置嵌入模型（如 OpenAI Embedding）"
            )
        prov_mgr = getattr(self.context, "provider_manager", None)
        for p in eps:
            cfg = p.provider_config if isinstance(p.provider_config, dict) else {}
            pid = cfg.get("id")
            if not pid:
                continue
            try:
                got = await prov_mgr.get_provider_by_id(pid) if prov_mgr else None
            except Exception:  # noqa: BLE001
                got = None
            if got is not None:
                return pid
        raise ValueError(
            "没有可用的 Embedding Provider：请先在 AstrBot「平台设置」中启用并配置"
            "嵌入模型（如 OpenAI Embedding），或检查 Provider 是否已生效"
        )

    async def _pick_rerank_provider_id(self) -> str | None:
        """返回可用的 Rerank（重排序）Provider id；未配置时返回 None（跳过重排序）。

        枚举 ProviderManager.rerank_provider_insts，并经 get_provider_by_id 校验
        实例确实可用（与 embedding 的选择逻辑一致）。
        """
        prov_mgr = getattr(self.context, "provider_manager", None)
        if prov_mgr is None:
            return None
        insts = getattr(prov_mgr, "rerank_provider_insts", None) or []
        for p in insts:
            cfg = p.provider_config if isinstance(p.provider_config, dict) else {}
            pid = cfg.get("id")
            if not pid:
                continue
            try:
                got = await prov_mgr.get_provider_by_id(pid)
            except Exception:  # noqa: BLE001
                got = None
            if got is not None:
                return pid
        return None

    async def _flush_sub(self, sub: Subscription) -> None:
        """把 sub 的最新状态写回同步源列表并落库。"""
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
        self._check_platform_enabled(sub.platform)  # 平台已禁用时直接拒绝同步
        if sub.kb_id in self._active:
            # 同源防重入：已在同步时直接返回当前进度（不重复启动）
            return self._active[sub.kb_id][1]
        self._active[sub.kb_id] = (sub, result)
        self._cancel_kbs.discard(sub.kb_id)
        self.logger.info(f"[KBridge] 开始同步: {sub.kb_name} ({sub.kb_id}) [{sub.platform}]")
        try:
            if sub.platform == "youdao":
                await self._sync_youdao(sub, result)
            elif sub.platform == "github":
                await self._sync_github(sub, result)
            elif sub.platform == "url2kb":
                await self._sync_url2kb(sub, result)
            elif sub.platform == "opennotebook":
                await self._sync_opennotebook(sub, result)
            elif sub.platform == "memos":
                await self._sync_memos(sub, result)
            else:
                await self._sync_ima(sub, result)
            sub.synced_count = await self._count_index(sub.kb_id)
            sub.last_sync_at = time.strftime("%Y-%m-%d %H:%M:%S")
            # 仅真实失败（failed>0）视为 partial；跳过（笔记/无权限/空内容等）不算失败
            if result.failed > 0:
                sub.last_status = "partial"
            else:
                sub.last_status = "ok"
            sub.last_error = "; ".join(result.errors[:5])
            if result.skip_counts and not sub.last_error:
                sub.last_error = "跳过原因: " + ", ".join(
                    f"{k}×{v}" for k, v in sorted(result.skip_counts.items())
                )
            await self._flush_sub(sub)
            skip_detail = ""
            if result.skip_counts:
                skip_detail = " | 跳过原因: " + ", ".join(
                    f"{k}×{v}" for k, v in sorted(result.skip_counts.items())
                )
            self.logger.info(
                f"[KBridge] 同步完成 {sub.kb_name}: 共 {result.total} 条, "
                f"新增 {result.synced}, 跳过 {result.skipped}, 失败 {result.failed}"
                f"{skip_detail}"
            )
            return result
        except _SyncCancelled:
            # 用户取消：已入库部分保留（index 已保存），标记取消状态
            await self._save_index()
            sub.synced_count = await self._count_index(sub.kb_id)
            sub.last_status = "cancelled"
            sub.last_error = "已取消（已完成部分保留）"
            await self._flush_sub(sub)
            self.logger.info(f"[KBridge] 同步已取消: {sub.kb_name}（已完成 {result.synced} 条）")
            return result
        except Exception as e:  # noqa: BLE001
            # 不向上抛出：返回带 error 状态的 result，页面/定时日志可正常展示
            self.logger.exception(f"同步失败 {sub.kb_id}")
            sub.last_status = "error"
            sub.last_error = str(e)
            if result.total == 0 and result.failed == 0:
                result.failed = 1
                result.errors.append(str(e))
            await self._flush_sub(sub)
            return result
        finally:
            self._active.pop(sub.kb_id, None)
            self._cancel_kbs.discard(sub.kb_id)

    async def _sync_ima(self, sub: Subscription, result: SyncResult) -> None:
        """IMA 同步：递归遍历知识库（含文件夹）-> 逐个条目增量处理。"""
        client = await self.get_client("ima")
        kb, kb_name, recreated = await self.resolve_target_kb(sub)
        index = await self._get_index()
        kb_index = index.setdefault(sub.kb_id, {})
        if recreated:
            kb_index.clear()  # 知识库刚重建，旧索引失效，全量重同步

        # 1. 递归收集全部叶子条目（media_type=99 的文件夹递归进入，目录本身不入库）
        items: list[dict[str, Any]] = []

        async def walk(folder_id: str | None, depth: int = 0) -> None:
            if depth > 8 or len(items) >= MAX_ITEM_SAVE:
                return
            cursor = ""
            while True:
                batch, is_end, next_cursor = await _retry_with_backoff(
                    lambda: client.list_kb_items(
                        sub.kb_id, cursor=cursor, folder_id=folder_id
                    )
                )
                for it in batch:
                    if len(items) >= MAX_ITEM_SAVE:
                        return
                    media_id = str(it.get("media_id") or "")
                    if it.get("media_type") == 99 or media_id.startswith("folder_"):
                        await walk(media_id, depth + 1)
                    else:
                        items.append(it)
                if is_end or not next_cursor or len(items) >= MAX_ITEM_SAVE:
                    break
                cursor = next_cursor

        await walk(None)
        result.total = len(items)

        # 2. 逐个条目增量同步（并发受限）
        sem = self._semaphore()
        perm_warned = False  # 权限类错误只提示一次

        async def process(item: dict[str, Any]) -> None:
            nonlocal perm_warned
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            media_id = str(item.get("media_id") or "")
            if not media_id or media_id in kb_index:
                result.skipped += 1
                return
            try:
                async with sem:
                    if self._sub_cancelled(sub.kb_id):
                        raise _SyncCancelled()
                    status = await self._process_ima_item(
                        kb, kb_name, sub, media_id, item, kb_index
                    )
                if self._sub_cancelled(sub.kb_id):
                    raise _SyncCancelled()
                if status == "ok":
                    result.synced += 1
                else:
                    result.skipped += 1
                    result.skip_counts[status] = result.skip_counts.get(status, 0) + 1
            except _SyncCancelled:
                raise
            except IMAError as e:
                if e.code in SKIP_CODES:
                    result.skipped += 1  # 安全打击/内容违规/笔记已删除，跳过
                elif e.code in PERMISSION_CODES:
                    # 订阅库文件/共享笔记无权限（需 ima 客户端授权），记一次说明后跳过
                    result.skipped += 1
                    if not perm_warned:
                        perm_warned = True
                        result.errors.append(
                            f"IMA 无权限读取（code={e.code}）："
                            "请在 ima 客户端授权，或改用你自己创建的知识库"
                        )
                else:
                    result.failed += 1
                    result.errors.append(f"{item.get('title', media_id)}: {e.msg}")
            except Exception as e:  # noqa: BLE001
                self.logger.exception(f"同步条目失败 {media_id}")
                result.failed += 1
                result.errors.append(f"{item.get('title', media_id)}: {e}")

        ret = await asyncio.gather(*(process(it) for it in items), return_exceptions=True)
        if any(isinstance(x, _SyncCancelled) for x in ret):
            raise _SyncCancelled()
        await self._save_index()

    async def _sync_youdao(self, sub: Subscription, result: SyncResult) -> None:
        """有道云同步：递归列出文件夹下全部笔记 -> 读取文本内容入库。

        订阅源 kb_id 为有道云目录 ID（"0" = 根目录，或 listNotes 返回的
        dir=true 条目 id）。笔记内容统一转为 Markdown 写入 AstrBot 知识库。
        """
        client = await self.get_client("youdao")
        kb, kb_name, recreated = await self.resolve_target_kb(sub)
        index = await self._get_index()
        kb_index = index.setdefault(sub.kb_id, {})
        if recreated:
            kb_index.clear()  # 知识库刚重建，旧索引失效，全量重同步

        # 1. 递归收集全部笔记（防循环引用）
        notes: list[dict[str, Any]] = []

        async def walk(folder_id: str, depth: int = 0) -> None:
            if depth > 8 or len(notes) >= MAX_ITEM_SAVE:
                return
            last_id = ""
            while True:
                entries, has_more = await _retry_with_backoff(
                    lambda: client.list_items(folder_id, last_id=last_id)
                )
                for e in entries:
                    if len(notes) >= MAX_ITEM_SAVE:
                        break
                    if e.get("dir"):
                        await walk(str(e.get("id") or ""), depth + 1)
                    else:
                        notes.append(e)
                if not has_more or not entries:
                    break
                last_id = str(entries[-1].get("id") or "")
        await walk(sub.kb_id or "0")
        result.total = len(notes)

        # 2. 逐个笔记读取并入库（MCP 会话单连接，串行调用）
        async def process(note: dict[str, Any]) -> None:
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            file_id = str(note.get("id") or "")
            if not file_id:
                result.skipped += 1
                return
            key = f"{YDAO_KEY_PREFIX}{file_id}"
            entry = kb_index.get(key)
            title = str(note.get("name") or file_id)
            try:
                # 覆盖语义：每次同步读取内容与指纹比对，源内容更新则删旧传新
                data = await client.get_note_content(file_id)
                content = str(data.get("content") or "").strip()
                if not content:
                    await self._maybe_delete_old(kb, entry)
                    kb_index[key] = {
                        "doc_id": "", "title": title, "skipped": "empty",
                        "hash": _content_hash(content),
                    }
                    result.skipped += 1
                    result.skip_counts["empty"] = result.skip_counts.get("empty", 0) + 1
                    return
                # 去扩展名（name 形如 "xxx.note"），统一存 .md
                stem = re.sub(r"\.[a-zA-Z0-9]+$", "", title).strip() or "untitled"
                await self._upsert_doc(
                    kb, kb_index, key, title,
                    f"{_sanitize_filename(stem)}.md",
                    content.encode("utf-8"), "md", result, sub.kb_id,
                )
            except _SyncCancelled:
                raise
            except YoudaoError as e:
                # 笔记删除/权限类错误跳过，其余计失败
                if e.code in (404, 403, 401):
                    result.skipped += 1
                    result.skip_counts["denied"] = result.skip_counts.get("denied", 0) + 1
                else:
                    result.failed += 1
                    result.errors.append(f"{title}: {e.msg}")
            except Exception as e:  # noqa: BLE001
                self.logger.exception(f"有道云同步条目失败 {file_id}")
                result.failed += 1
                result.errors.append(f"{title}: {e}")

        for note in notes:
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            await process(note)
        await self._save_index()

    async def _sync_github(self, sub: Subscription, result: SyncResult) -> None:
        """GitHub 仓库同步：Git Trees API 遍历 -> 过滤支持格式 -> raw 下载入库。

        kb_id 为规范化键（gh:owner/repo@branch:path）；tree 模式仅同步指定子目录。
        """
        client = await self.get_client("github")
        kb, kb_name, recreated = await self.resolve_target_kb(sub)
        index = await self._get_index()
        kb_index = index.setdefault(sub.kb_id, {})
        if recreated:
            kb_index.clear()  # 知识库刚重建，旧索引失效，全量重同步
        # 直接从同步源键还原 parsed，避免 URL 重建歧义（kb_id 含 @/:/占位符）
        parsed = sub_key_to_parsed(sub.kb_id)

        # 1. 获取完整文件树，过滤支持格式与忽略目录
        blobs = await client.get_tree(parsed)
        prefix = (parsed.get("path") or "").strip("/")
        files: list[dict[str, str]] = []
        for b in blobs:
            path = b.get("path") or ""
            if prefix and not (path == prefix or path.startswith(prefix + "/")):
                continue
            segs = path.split("/")
            if any(seg in IGNORE_SEGMENTS for seg in segs[:-1]):
                continue
            relpath = path[len(prefix) + 1 :] if prefix else path
            ext = relpath.rsplit(".", 1)[-1].lower() if "." in relpath else ""
            if ext not in SUPPORTED_EXT:
                continue
            files.append({"path": relpath, "size": b.get("size") or 0, "sha": b.get("sha") or ""})
        if len(files) > MAX_ITEM_SAVE:
            files = files[:MAX_ITEM_SAVE]
        result.total = len(files)

        # 2. 逐个文件增量下载入库（并发受限）
        sem = self._semaphore()

        async def process(f: dict[str, str]) -> None:
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            relpath = f["path"]
            sha = str(f.get("sha") or "")
            entry = kb_index.get(relpath)
            # 内容指纹（Git blob sha）一致即无需重新下载/入库
            if sha and entry and entry.get("hash") == sha and entry.get("doc_id"):
                result.skipped += 1
                return
            try:
                async with sem:
                    if self._sub_cancelled(sub.kb_id):
                        raise _SyncCancelled()
                    data, ext = await client.fetch_raw(parsed, relpath)
                if self._sub_cancelled(sub.kb_id):
                    raise _SyncCancelled()
                if not data.strip():
                    # 内容被清空：删除旧文档（若有），索引记为 empty
                    await self._maybe_delete_old(kb, entry)
                    kb_index[relpath] = {
                        "doc_id": "", "title": relpath, "skipped": "empty", "hash": sha,
                    }
                    result.skipped += 1
                    result.skip_counts["empty"] = result.skip_counts.get("empty", 0) + 1
                    return
                file_name = f"{_sanitize_filename(relpath)}.{ext}"
                await self._upsert_doc(
                    kb, kb_index, relpath, relpath, file_name, data, ext,
                    result, sub.kb_id, content_hash=sha,
                )
            except _SyncCancelled:
                raise
            except GitHubError as e:
                result.failed += 1
                result.errors.append(f"{relpath}: {e.msg}")
                if len(result.errors) <= 5:  # 避免刷屏，最多打印前 5 条
                    self.logger.warning(f"[KBridge] GitHub 条目失败 {relpath}: {e.msg}")
            except Exception as e:  # noqa: BLE001
                self.logger.exception(f"GitHub 同步条目失败 {relpath}")
                result.failed += 1
                result.errors.append(f"{relpath}: {e}")

        # 分块并发（每批 GH_BATCH），避免一次性挂起上千个 task 挤占内存
        GH_BATCH = 60
        for i in range(0, len(files), GH_BATCH):
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            batch = files[i : i + GH_BATCH]
            ret = await asyncio.gather(*(process(f) for f in batch), return_exceptions=True)
            if any(isinstance(x, _SyncCancelled) for x in ret):
                raise _SyncCancelled()
        await self._save_index()

    async def _sync_opennotebook(self, sub: Subscription, result: SyncResult) -> None:
        """Open Notebook（beta）同步：取 notebook 的 sources/notes 内容入库。

        目标 AstrBot 知识库名 = notebook 名（自动创建）；增量索引 key=on:<id>。
        """
        client = await self.get_client("opennotebook")
        kb, kb_name, recreated = await self.resolve_target_kb(sub)
        index = await self._get_index()
        kb_index = index.setdefault(sub.kb_id, {})
        if recreated:
            kb_index.clear()  # 知识库刚重建，旧索引失效，全量重同步
        sources = await _retry_with_backoff(lambda: client.list_sources(sub.kb_id))
        result.total = len(sources)

        async def process(src: dict[str, Any]) -> None:
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            sid = str(src.get("id") or "")
            if not sid:
                result.skipped += 1
                return
            key = f"{ON_KEY_PREFIX}{sid}"
            entry = kb_index.get(key)
            title = str(src.get("name") or src.get("title") or sid)[:120]
            content = str(src.get("content") or src.get("text") or "").strip()
            if not content:
                await self._maybe_delete_old(kb, entry)
                kb_index[key] = {
                    "doc_id": "", "title": title, "skipped": "empty", "hash": _content_hash(content),
                }
                result.skipped += 1
                return
            await self._upsert_doc(
                kb, kb_index, key, title, f"{title}.md", content, "md", result, sub.kb_id,
            )

        async with self._semaphore():
            await asyncio.gather(*(process(src) for src in sources))

    async def _sync_memos(self, sub: Subscription, result: SyncResult) -> None:
        """Memos（beta）同步：分页拉取全部备忘录（Markdown content）入库。

        目标 AstrBot 知识库名 = memos_target_kb（默认 Memos）；增量 key=ms:<uid>。
        """
        client = await self.get_client("memos")
        kb, kb_name, recreated = await self.resolve_target_kb(sub)
        index = await self._get_index()
        kb_index = index.setdefault(sub.kb_id, {})
        if recreated:
            kb_index.clear()
        memos: list[dict[str, Any]] = []
        page_token = ""
        while True:
            items, page_token = await _retry_with_backoff(
                lambda: client.list_memos(100, page_token)
            )
            memos.extend(items)
            if not page_token or len(memos) >= MAX_ITEM_SAVE:
                break
        result.total = len(memos)

        async def process(m: dict[str, Any]) -> None:
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            uid = str(m.get("uid") or m.get("id") or "")
            if not uid:
                result.skipped += 1
                return
            key = f"{MEMOS_KEY_PREFIX}{uid}"
            entry = kb_index.get(key)
            content = str(m.get("content") or "").strip()
            if not content:
                await self._maybe_delete_old(kb, entry)
                kb_index[key] = {
                    "doc_id": "", "title": uid, "skipped": "empty", "hash": _content_hash(content),
                }
                result.skipped += 1
                return
            title = content.splitlines()[0][:40] or uid
            await self._upsert_doc(
                kb, kb_index, key, title, f"{title}.md", content, "md", result, sub.kb_id,
            )

        async with self._semaphore():
            await asyncio.gather(*(process(m) for m in memos))

    async def _sync_url2kb(self, sub: Subscription, result: SyncResult) -> None:
        """url2kb 同步：分组内全部 URL 抓取网页 -> Markdown 入库。

        分组名即 AstrBot 知识库名；增量索引 key 为 ur2:{url_id}。
        """
        kb, kb_name, recreated = await self.resolve_target_kb(sub)
        index = await self._get_index()
        kb_index = index.setdefault(sub.kb_id, {})
        if recreated:
            kb_index.clear()  # 知识库刚重建，旧索引失效，全量重同步
        groups = await self.get_url2kb_groups()
        group = next((g for g in groups if str(g.get("id")) == sub.kb_id), None)
        urls = list((group or {}).get("urls") or [])
        result.total = len(urls)

        for u in urls:
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
            uid = str(u.get("id") or "")
            key = f"{URL2KB_PREFIX}{uid}"
            if not uid:
                result.skipped += 1
                continue
            entry = kb_index.get(key)
            url = str(u.get("url") or "").strip()
            title = str(u.get("title") or "").strip() or url
            if not url:
                result.skipped += 1
                continue
            try:
                # 覆盖语义：每次同步重新抓取并比对指纹，网页内容更新则删旧传新
                text = await self._fetch_web_md(url)
                if not text.strip():
                    await self._maybe_delete_old(kb, entry)
                    kb_index[key] = {
                        "doc_id": "", "title": title, "skipped": "empty",
                        "hash": _content_hash(text),
                    }
                    result.skipped += 1
                    result.skip_counts["empty"] = result.skip_counts.get("empty", 0) + 1
                    continue
                await self._upsert_doc(
                    kb, kb_index, key, title,
                    f"{_sanitize_filename(title)}.md",
                    text.encode("utf-8"), "md", result, sub.kb_id,
                )
            except _SyncCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"[KBridge] url2kb 条目失败 {title}: {e}")
                result.failed += 1
                result.errors.append(f"{title}: {e}")
        await self._save_index()

    async def _fetch_web_md(self, url: str) -> str:
        """抓取网页正文并转为纯文本 Markdown。"""
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:
                resp.raise_for_status()
                raw = await resp.read()
                if len(raw) > 5 * 1024 * 1024:
                    raise ValueError("网页超过 5MB 限制")
                return _html_to_markdown(raw.decode("utf-8", errors="ignore"))

    async def sync_all(self) -> list[SyncResult]:
        """并行同步全部开启的同步源（多源同时同步；同源已在同步时自动去重）。

        下载/处理并发各自受限（_semaphore），入库统一走全局串行锁（_upload_semaphore），
        避免多源并行导致 embedding 并发 OOM。
        """
        subs = [s for s in await self.get_subs() if s.enabled]
        self._cancel = False
        results = await asyncio.gather(
            *(self.sync_subscription(sub) for sub in subs), return_exceptions=True
        )
        out: list[SyncResult] = []
        for r in results:
            if isinstance(r, BaseException):
                out.append(SyncResult(kb_id="", failed=1, errors=[str(r)]))
            else:
                out.append(r)
        return out

    async def _maybe_delete_old(self, kb, entry: dict | None) -> None:
        """删除旧文档（覆盖语义：内容变化时先删旧再传新）。知识库不存在/无删除接口时静默跳过。"""
        old_doc = (entry or {}).get("doc_id") or ""
        if not old_doc:
            return
        del_fn = getattr(kb, "delete_document", None)
        if del_fn is None:
            return
        try:
            await del_fn(old_doc)
        except Exception:  # noqa: BLE001 旧文档可能已被删除/向量库不一致
            pass

    async def _upsert_doc(
        self, kb, kb_index: dict, key: str, title: str,
        file_name: str, payload, ext: str, result, kb_id: str,
        content_hash: str | None = None,
    ) -> None:
        """通用增量入库（支持新旧覆盖）：

        - 新条目：上传并记索引（含内容指纹 hash）
        - 已存在且 hash 相同：跳过（不重复入库）
        - 已存在但 hash 不同（源内容已更新，或旧索引无 hash 的存量条目）：
          删除旧文档后重新上传（覆盖），保证 AstrBot 知识库内容与源一致
        upload 统一走全局串行锁（防 embedding 并发 OOM）。
        """
        if self._sub_cancelled(kb_id):
            raise _SyncCancelled()
        h = content_hash or _content_hash(payload)
        entry = kb_index.get(key)
        if entry and entry.get("hash") == h and entry.get("doc_id"):
            result.skipped += 1
            return
        await self._maybe_delete_old(kb, entry)
        async with self._upload_semaphore():
            if self._sub_cancelled(kb_id):
                raise _SyncCancelled()
            doc = await kb.upload_document(
                file_name=file_name, file_content=payload, file_type=ext
            )
        kb_index[key] = {
            "doc_id": doc.doc_id,
            "title": title,
            "hash": h,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        result.synced += 1

    async def _process_ima_item(
        self, kb, kb_name: str, sub: Subscription, media_id: str, item: dict, kb_index: dict
    ) -> str:
        """处理单条媒体。返回状态：ok（已入库）/ 跳过原因字符串。

        注意：跳过分支必须返回原因，不得静默返回——调用方据此统计 synced/skipped。
        """
        client = await self.get_client()
        # 节流：IMA get_media_info 频控极严（实测单次即可触发 200001 频率超限），
        # 必须低频调用，降低触发 403/200001 概率
        await asyncio.sleep(1.0)
        info = await _retry_with_backoff(lambda: client.get_media_info(media_id))
        media_type = int(info.get("media_type") or 0)
        title = str(item.get("title") or media_id)

        # 笔记/AI 会话：笔记（11）走官方 notes 接口读纯文本入库；无 notebook_id 的跳过
        if media_type in NOTE_MEDIA_TYPES or info.get("notebook_ext_info"):
            note_id = (info.get("notebook_ext_info") or {}).get("notebook_id") or ""
            if not note_id:
                kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "note"}
                return "note"
            # 节流：notes 接口同样受频控
            await asyncio.sleep(1.0)
            note_data = await _retry_with_backoff(lambda: client.get_doc_content(note_id))
            content = str(note_data.get("content") or "").strip()
            if not content:
                kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "empty"}
                return "empty"
            file_name = f"{_sanitize_filename(title)}.md"
            async with self._upload_semaphore():  # 全局串行入库，防 OOM
                if self._sub_cancelled(sub.kb_id):
                    raise _SyncCancelled()
                doc = await kb.upload_document(
                    file_name=file_name,
                    file_content=content.encode("utf-8"),
                    file_type="md",
                )
            kb_index[media_id] = {
                "doc_id": doc.doc_id,
                "title": title,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            return "ok"

        url_info = info.get("url_info") or {}
        url = url_info.get("url") or ""
        if not url:
            kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "no-url"}
            return "no-url"

        headers = url_info.get("headers") or {}
        content, content_type, final_url = await self._download(url, headers)

        # 决定落盘类型
        ext = _ext_from_url(final_url or url, media_type)
        if ext is None:
            if content_type.startswith("text/html") or media_type in TEXT_MEDIA_TYPES:
                ext = "md"
            else:
                kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "unsupported"}
                return "unsupported"

        file_name = f"{_sanitize_filename(title)}.{ext}"
        if ext == "md":
            text = _html_to_markdown(content.decode("utf-8", errors="ignore"))
            if not text.strip():
                kb_index[media_id] = {"doc_id": "", "title": title, "skipped": "empty"}
                return "empty"
            payload = text.encode("utf-8")
        else:
            payload = content

        async with self._upload_semaphore():  # 全局串行入库，防 OOM
            if self._sub_cancelled(sub.kb_id):
                raise _SyncCancelled()
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
        return "ok"

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
