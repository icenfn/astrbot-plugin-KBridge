"""GitHub Repository 客户端（Git Trees API + raw 下载，参考官方 REST API）。

支持：
- 整个仓库：https://github.com/{owner}/{repo}（默认分支）
- 指定分支/子目录（tree 模式）：https://github.com/{owner}/{repo}/tree/{branch}[/{path}]
认证：可选 GitHub Token（Authorization: Bearer），公开仓库可匿名（限 60 次/小时）。
"""

import asyncio
import re
from typing import Any

import aiohttp

GITHUB_API = "https://api.github.com"
RAW_HOST = "https://raw.githubusercontent.com"

# 与 sync_manager.SUPPORTED_EXT 对齐（AstrBot 可解析的格式）
SUPPORTED_EXT = {"md", "txt", "markdown", "rst", "adoc", "docx", "xlsx", "xls", "pdf", "epub"}
# 树遍历时忽略的路径片段（避免拉入依赖/构建产物）
IGNORE_SEGMENTS = {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__", ".idea", ".vscode"}

class GitHubError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.msg = msg
        self.status = status

def parse_github_url(url: str) -> dict[str, str]:
    """解析 GitHub URL 为 {owner, repo, branch, path}。

    - https://github.com/owner/repo           -> 全仓库（默认分支）
    - https://github.com/owner/repo/tree/br   -> 指定分支根目录
    - https://github.com/owner/repo/tree/br/dir/sub -> 指定分支子目录
    """
    url = (url or "").strip()
    # 兼容带协议/子域/尾斜杠/查询参数
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)(?:/([^?#]*))?$", url)
    if not m:
        raise GitHubError(f"无法解析 GitHub 仓库地址: {url}")
    owner, repo, rest = m.group(1), m.group(2), (m.group(3) or "")
    if not owner or not repo:
        raise GitHubError(f"无法解析 GitHub 仓库地址: {url}")
    branch, path = "", ""
    if rest:
        segs = rest.split("/")
        if segs[0] == "tree":
            if len(segs) > 1:
                branch = segs[1]
            path = "/".join(segs[2:]) if len(segs) > 2 else ""
        elif segs[0] in ("blob", "commit", "releases", "issues", "pull"):
            raise GitHubError("仅支持仓库整体或 tree 路径（如 /tree/master/docs）")
        else:
            # 兼容 github.com/owner/repo/tree 缺省之外的其他路径，视为未知
            raise GitHubError(f"不支持的 GitHub 路径: /{rest}")
    return {"owner": owner, "repo": repo, "branch": branch, "path": path}

def sub_key(parsed: dict[str, str]) -> str:
    """规范化同步源唯一键（存入 Subscription.kb_id）。"""
    branch = parsed.get("branch") or "default"
    path = parsed.get("path") or ""
    return f"gh:{parsed['owner']}/{parsed['repo']}@{branch}:{path or '/'}"

def sub_key_to_parsed(key: str) -> dict[str, str]:
    """从同步源键（gh:owner/repo@branch:path）还原 {owner, repo, branch, path}。

    与 sub_key 互为逆运算；同步时直接使用，避免从键重建 URL 产生歧义
    （键中的 '@'、':' 不是合法 URL 路径分隔符）。
    """
    body = key.split(":", 1)[1] if ":" in key else key
    owner_repo, _, rest = body.partition("@")
    owner, _, repo = owner_repo.partition("/")
    branch, _, path = rest.partition(":")
    if branch == "default":
        branch = ""  # default 为占位符，表示需查询仓库默认分支
    return {"owner": owner, "repo": repo, "branch": branch, "path": path.strip("/")}

def display_name(parsed: dict[str, str]) -> str:
    """页面/列表显示名。"""
    base = f"{parsed['owner']}/{parsed['repo']}"
    if parsed.get("path"):
        return f"{base} : {parsed['path']}"
    return base

class GitHubClient:
    def __init__(self, token: str = "", raw_mirror: str = ""):
        self._token = (token or "").strip()
        self._raw_mirror = (raw_mirror or "").strip().rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, accept_raw: bool = False) -> dict[str, str]:
        h: dict[str, str] = {}
        if accept_raw:
            h["Accept"] = "application/vnd.github.raw+json"
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _api_get(self, url: str) -> Any:
        session = await self._get_session()
        try:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status == 404:
                    raise GitHubError("仓库/分支不存在或已删除", 404)
                if resp.status == 403:
                    raise GitHubError("GitHub API 限流（403）：请配置 GitHub Token", 403)
                if resp.status != 200:
                    raise GitHubError(f"GitHub API HTTP {resp.status}", resp.status)
                return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise GitHubError(f"GitHub API 网络错误: {e}") from e

    async def resolve_default_branch(self, parsed: dict[str, str]) -> str:
        """补齐默认分支（branch 为空或为占位符 default 时查仓库信息）。"""
        branch = (parsed.get("branch") or "").strip()
        if branch and branch != "default":
            return branch
        data = await self._api_get(f"{GITHUB_API}/repos/{parsed['owner']}/{parsed['repo']}")
        branch = data.get("default_branch") or "master"
        parsed["branch"] = branch
        return branch

    async def get_tree(self, parsed: dict[str, str]) -> list[dict[str, str]]:
        """获取分支完整文件树（recursive），返回 blob 列表 [{path, size}]。"""
        branch = await self.resolve_default_branch(parsed)
        data = await self._api_get(
            f"{GITHUB_API}/repos/{parsed['owner']}/{parsed['repo']}/git/trees/{branch}?recursive=1"
        )
        tree = data.get("tree") or []
        return [
            {"path": t.get("path", ""), "size": t.get("size") or 0}
            for t in tree
            if t.get("type") == "blob" and t.get("path")
        ]

    async def fetch_raw(self, parsed: dict[str, str], relpath: str) -> tuple[bytes, str]:
        """下载文件原始内容。返回 (bytes, 实际扩展名)。

        relpath 为相对同步子目录的路径（tree 模式），URL 由
        branch + parsed.path + relpath 拼接；配置了 Raw 加速镜像
        （github_raw_mirror）时走镜像前缀，网络类错误自动重试 2 次。
        """
        branch = await self.resolve_default_branch(parsed)
        prefix = (parsed.get("path") or "").strip("/")
        full = f"{prefix}/{relpath}".strip("/") if prefix else relpath
        host = self._raw_mirror or RAW_HOST
        url = f"{host}/{parsed['owner']}/{parsed['repo']}/{branch}/{full}"
        ext = relpath.rsplit(".", 1)[-1].lower() if "." in relpath else ""
        last_err: Exception | None = None
        for attempt in range(3):
            session = await self._get_session()
            try:
                async with session.get(url, headers=self._headers(accept_raw=True)) as resp:
                    if resp.status == 404 and self._raw_mirror:
                        # 镜像未收录该文件时回退官方源
                        return await self._fallback_raw(parsed, full)
                    if resp.status != 200:
                        raise GitHubError(f"raw 下载失败: {relpath} (HTTP {resp.status})", resp.status)
                    data = await resp.read()
                    if len(data) > 200 * 1024 * 1024:
                        raise GitHubError(f"文件超过 200MB 限制: {relpath}")
                    return data, ext
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as e:
                # 超时/连接错误统一重试（3.11+ asyncio.TimeoutError 不属于 ClientError，
                # 必须单独捕获），退避 1s/2s/4s，避免对官方 raw 高频请求触发限流
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2**attempt))
                    continue
            except GitHubError as e:
                if resp.status in (403, 429, 502, 503) and attempt < 2:
                    last_err = e
                    await asyncio.sleep(1.0 * (2**attempt))
                    continue
                raise
        raise GitHubError(f"raw 下载网络错误: {relpath}: {last_err}") from last_err

    async def _fallback_raw(self, parsed: dict[str, str], full: str) -> tuple[bytes, str]:
        """镜像 404 时回退官方 raw 源下载（超时/连接错误同样重试 2 次）。"""
        url = f"{RAW_HOST}/{parsed['owner']}/{parsed['repo']}/{parsed.get('branch', '')}/{full}"
        last_err: Exception | None = None
        for attempt in range(3):
            session = await self._get_session()
            try:
                async with session.get(url, headers=self._headers(accept_raw=True)) as resp:
                    if resp.status != 200:
                        raise GitHubError(f"raw 下载失败: {full} (HTTP {resp.status})", resp.status)
                    data = await resp.read()
                    ext = full.rsplit(".", 1)[-1].lower() if "." in full else ""
                    return data, ext
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2**attempt))
                    continue
        raise GitHubError(f"raw 下载网络错误: {full}: {last_err}") from last_err
