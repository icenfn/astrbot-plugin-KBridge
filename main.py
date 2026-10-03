"""KBridge — AstrBot 插件：同步外部知识源（ima / 有道云笔记 / GitHub 仓库）到 AstrBot 知识库。

命令（/kbridge）：
- /kbridge                   帮助
- /kbridge kbs               列出 IMA 账号可同步的知识库（自建）
- /kbridge sub list          列出同步源
- /kbridge sub del <序号>    删除同步源
- /kbridge sync [序号|all]   手动同步（默认 all）
"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps

from astrbot.api.all import (
    AstrBotConfig,
    AstrMessageEvent,
    Context,
    Star,
    command,
    register,
)
from astrbot.api.web import error_response, json_response, request

from .ima_client import IMAError, _retry_with_backoff
from .github_client import GitHubError, display_name, parse_github_url, sub_key
from .youdao_client import YoudaoError
from .sync_manager import DEFAULT_GITHUB_MIRROR, SyncManager

logger = logging.getLogger("astrbot")

PLUGIN_NAME = "astrbot_plugin_kbridge"


def webapi_handler(func):
    """Web API 统一错误兜底：记录完整日志并返回可读错误，避免页面只看到 Internal server error。"""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except IMAError as e:
            self.logger.warning(f"API {func.__name__} IMA 错误: code={e.code} msg={e.msg}")
            return error_response(f"IMA 错误: {e.msg}", status_code=400)
        except GitHubError as e:
            self.logger.warning(f"API {func.__name__} GitHub 错误: {e.msg}")
            return error_response(f"GitHub 错误: {e.msg}", status_code=400)
        except YoudaoError as e:
            self.logger.warning(f"API {func.__name__} 有道云错误: code={e.code} msg={e.msg}")
            return error_response(f"有道云错误: {e.msg}", status_code=400)
        except Exception:  # noqa: BLE001
            self.logger.exception(f"API {func.__name__} 未捕获异常")
            return error_response("内部错误，详见 AstrBot 日志", status_code=500)

    return wrapper


HELP_TEXT = """KBridge - 外部知识源同步

/kbridge kbs                   列出 IMA 知识库（自建）
/kbridge sub list              同步源列表
/kbridge sub del <序号>        删除同步源
/kbridge sync [序号|all]       手动同步"""


@register("KBridge", "icenfn", "外部知识源（ima）同步到 AstrBot 知识库", "0.1.0")
class KBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 清理旧版本遗留配置键（前缀/旧定时分钟键已移除）
        for stale in ("target_kb_prefix", "sync_interval_minutes"):
            if stale in self.config:
                del self.config[stale]
        self.manager = SyncManager(self, context, config)
        self._sched_task: asyncio.Task | None = None
        self._register_web_apis()
        self._restart_schedule()

    # ---------- 定时同步 ----------

    def _restart_schedule(self) -> None:
        """按配置（schedule_enabled / schedule_interval）重建后台定时任务。"""
        if self._sched_task and not self._sched_task.done():
            self._sched_task.cancel()
        self._sched_task = None
        interval = max(1, int(self.config.get("schedule_interval") or 0))
        if not self.config.get("schedule_enabled") or interval <= 0:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环环境（极罕见），定时不启动
        self._sched_task = asyncio.create_task(self._schedule_loop(interval))
        self.logger.info(f"[KBridge] 定时同步已启动：每 {self._fmt_interval(interval)}")

    async def _schedule_loop(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                if not self.config.get("schedule_enabled"):
                    continue
                await self._run_scheduled_sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.logger.exception("定时同步任务异常")

    @staticmethod
    def _fmt_interval(sec: int) -> str:
        d, sec = divmod(sec, 86400)
        h, sec = divmod(sec, 3600)
        m, s = divmod(sec, 60)
        parts = []
        if d:
            parts.append(f"{d}天")
        if h:
            parts.append(f"{h}小时")
        if m:
            parts.append(f"{m}分")
        if s:
            parts.append(f"{s}秒")
        return "".join(parts) or "0"

    async def _run_scheduled_sync(self) -> None:
        """执行一轮定时同步：仅同步已开启（enabled）的同步源，并写入日志。"""
        if self.manager.is_syncing:
            await self.manager.add_schedule_log("warn", "跳过：已有同步任务进行中")
            return
        subs = await self.manager.get_subs()
        enabled = [s for s in subs if s.enabled]
        if not enabled:
            await self.manager.add_schedule_log("warn", "没有开启定时同步的同步源，跳过本轮")
            return
        await self.manager.add_schedule_log("ok", f"定时同步开始：{len(enabled)} 个同步源")
        try:
            results = await self.manager.sync_all()
            for r in results:
                name = next((s.kb_name for s in enabled if s.kb_id == r.kb_id), r.kb_id)
                if r.failed > 0:
                    level = "error"
                elif r.skipped > 0:
                    level = "warn"
                else:
                    level = "ok"
                await self.manager.add_schedule_log(
                    level,
                    f"{name}：共 {r.total}，新增 {r.synced}，跳过 {r.skipped}，失败 {r.failed}",
                )
            await self.manager.add_schedule_log("ok", "定时同步完成")
        except Exception as e:  # noqa: BLE001
            self.logger.exception("定时同步失败")
            await self.manager.add_schedule_log("error", f"定时同步异常：{e}")

    # ---------- WebUI 页面 API ----------

    # 允许页面写入的配置项白名单
    CONFIG_WRITABLE = {
        "ima_client_id": "str",
        "ima_api_key": "str",
        "youdao_api_key": "str",
        "youdao_target_kb": "str",
        "github_token": "str",
        "github_raw_mirror": "str",
        "max_concurrency": "int",
    }

    def _register_web_apis(self) -> None:
        routes = [
            (f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "KBridge 总览状态"),
            (f"/{PLUGIN_NAME}/subs", self.api_subs, ["GET"], "可同步知识库列表"),
            (f"/{PLUGIN_NAME}/subs/add", self.api_subs_add, ["POST"], "添加 GitHub 仓库"),
            (f"/{PLUGIN_NAME}/subs/remove", self.api_subs_remove, ["POST"], "删除同步源"),
            (f"/{PLUGIN_NAME}/subs/toggle", self.api_subs_toggle, ["POST"], "定时同步开关"),
            (f"/{PLUGIN_NAME}/subs/delete-local", self.api_subs_delete_local, ["POST"], "删除本地知识库"),
            (f"/{PLUGIN_NAME}/sync", self.api_sync, ["POST"], "触发同步"),
            (f"/{PLUGIN_NAME}/sync/cancel", self.api_cancel, ["POST"], "取消同步"),
            (f"/{PLUGIN_NAME}/schedule", self.api_schedule_get, ["GET"], "读取定时同步"),
            (f"/{PLUGIN_NAME}/schedule", self.api_schedule_save, ["POST"], "保存定时同步"),
            (f"/{PLUGIN_NAME}/schedule/clear", self.api_schedule_clear, ["POST"], "清空同步日志"),
            (f"/{PLUGIN_NAME}/config", self.api_config_get, ["GET"], "读取平台配置"),
            (f"/{PLUGIN_NAME}/config", self.api_config_save, ["POST"], "保存平台配置"),
            (f"/{PLUGIN_NAME}/url2kb", self.api_url2kb_get, ["GET"], "url2kb 分组列表"),
            (f"/{PLUGIN_NAME}/url2kb/groups", self.api_url2kb_groups, ["POST"], "url2kb 分组增删改"),
            (f"/{PLUGIN_NAME}/url2kb/urls", self.api_url2kb_urls, ["POST"], "url2kb URL 增删"),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(route, handler, methods, desc)

    @webapi_handler
    async def api_config_get(self):
        """返回平台配置状态（secret 字段不回显原文，只回是否已配置）。"""
        enabled = self.manager.platform_enabled()
        return json_response(
            {
                "platforms": {
                    "ima": {
                        "name": "腾讯 ima",
                        "supported": True,
                        "enabled": enabled.get("ima", True),
                        "fields": [
                            {"key": "ima_client_id", "label": "Client ID", "secret": False},
                            {"key": "ima_api_key", "label": "API Key", "secret": True},
                        ],
                    },
                    "youdao": {
                        "name": "有道云笔记",
                        "supported": True,
                        "enabled": enabled.get("youdao", True),
                        "fields": [
                            {"key": "youdao_api_key", "label": "API Key", "secret": True},
                            {
                                "key": "youdao_target_kb",
                                "label": "同步至 AstrBot 知识库",
                                "secret": False,
                            },
                        ],
                    },
                    "github": {
                        "name": "Github",
                        "supported": True,
                        "enabled": enabled.get("github", True),
                        "fields": [
                            {
                                "key": "github_token",
                                "label": "GitHub Token（必填）",
                                "secret": True,
                            },
                            {
                                "key": "github_raw_mirror",
                                "label": "Raw 加速镜像（可选，如 https://ghfast.top/）",
                                "secret": False,
                            },
                        ],
                    },
                    "url2kb": {
                        "name": "url2kb",
                        "supported": True,
                        "enabled": enabled.get("url2kb", True),
                        "fields": [],
                    },
                },
                "values": {
                    "ima_client_id": (self.config.get("ima_client_id") or "").strip(),
                    "ima_api_key": bool(self.config.get("ima_api_key")),
                    "youdao_api_key": bool(self.config.get("youdao_api_key")),
                    "youdao_target_kb": (self.config.get("youdao_target_kb") or "").strip()
                    or "YoudaoNote",
                    "github_token": bool(self.config.get("github_token")),
                    "github_raw_mirror": (self.config.get("github_raw_mirror") or "").strip()
                    or DEFAULT_GITHUB_MIRROR,
                },
                "common": {
                    "max_concurrency": int(self.config.get("max_concurrency", 3) or 3),
                },
            }
        )

    @webapi_handler
    async def api_config_save(self):
        payload = await request.json(default={})
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict):
            return error_response("fields 必须是对象", status_code=400)
        saved = []
        for key, value in fields.items():
            if key not in self.CONFIG_WRITABLE:
                continue
            vtype = self.CONFIG_WRITABLE[key]
            if vtype == "str":
                self.config[key] = str(value).strip()
            elif vtype == "bool":
                self.config[key] = bool(value)
            elif vtype == "int":
                try:
                    self.config[key] = max(0, int(value))
                except (TypeError, ValueError):
                    return error_response(f"{key} 必须是数字", status_code=400)
            saved.append(key)
        # 平台启用/禁用开关
        pe = payload.get("platform_enabled")
        if isinstance(pe, dict):
            cur = self.manager.platform_enabled()
            for p, v in pe.items():
                if p in ("ima", "youdao", "github", "url2kb"):
                    cur[p] = bool(v)
            self.config["platform_enabled"] = cur
            saved.append("platform_enabled")
        self.config.save_config()
        # 凭据变更后重置对应平台客户端，使新 Key 生效
        for platform, keys in (
            ("ima", ("ima_client_id", "ima_api_key")),
            ("youdao", ("youdao_api_key",)),
            ("github", ("github_token", "github_raw_mirror")),
        ):
            if any(k in saved for k in keys):
                await self.manager.reset_client(platform)
        if "youdao_target_kb" in saved:
            await self.manager.ensure_youdao_sub()
        self.logger.info(f"[KBridge] 页面保存平台配置成功: {saved}")
        return json_response({"saved": saved})

    @webapi_handler
    async def api_url2kb_get(self):
        """url2kb 分组列表（含每组 url）。"""
        return json_response({"groups": await self.manager.get_url2kb_groups()})

    @webapi_handler
    async def api_url2kb_groups(self):
        """分组管理：action = add / update / remove。"""
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        gid = str(payload.get("id") or "").strip()
        if action == "add":
            name = str(payload.get("name") or "").strip()
            if not name:
                return error_response("分组名称（AstrBot 知识库名）必填", status_code=400)
            g = await self.manager.url2kb_add_group(name, str(payload.get("note") or ""))
            return json_response({"group": g})
        if action == "update":
            ok = await self.manager.url2kb_update_group(
                gid,
                str(payload.get("name") or ""),
                str(payload.get("note") or ""),
            )
            if not ok:
                return error_response("分组不存在", status_code=404)
            return json_response({"ok": True})
        if action == "remove":
            ok = await self.manager.url2kb_remove_group(gid)
            if not ok:
                return error_response("分组不存在", status_code=404)
            return json_response({"ok": True})
        return error_response("未知操作", status_code=400)

    @webapi_handler
    async def api_url2kb_urls(self):
        """URL 管理：action = add / remove；添加时自动识别网页标题。"""
        payload = await request.json(default={})
        action = str(payload.get("action") or "").strip()
        gid = str(payload.get("group_id") or "").strip()
        if action == "add":
            url = str(payload.get("url") or "").strip()
            if not url:
                return error_response("URL 必填", status_code=400)
            ok, title = await self.manager.url2kb_add_url(gid, url)
            if not ok:
                return error_response("分组不存在", status_code=404)
            return json_response({"ok": True, "title": title})
        if action == "remove":
            uid = str(payload.get("url_id") or "").strip()
            ok = await self.manager.url2kb_remove_url(gid, uid)
            if not ok:
                return error_response("URL 不存在", status_code=404)
            return json_response({"ok": True})
        return error_response("未知操作", status_code=400)

    @webapi_handler
    async def api_stats(self):
        subs = await self.manager.get_subs()
        enabled = self.manager.platform_enabled()
        configured = {
            "ima": bool(self.config.get("ima_client_id") and self.config.get("ima_api_key"))
            and enabled.get("ima", True),
            "youdao": bool(self.config.get("youdao_api_key")) and enabled.get("youdao", True),
            "github": bool(self.config.get("github_token")) and enabled.get("github", True),
            "url2kb": True and enabled.get("url2kb", True),
        }
        # 定时同步概览（供总览页定时块展示）
        sec = int(self.config.get("schedule_interval") or 0)
        sched_on = bool(self.config.get("schedule_enabled")) and sec > 0
        d, r = divmod(max(sec, 0), 86400)
        h, r = divmod(r, 3600)
        m, s2 = divmod(r, 60)
        parts = []
        if d:
            parts.append(f"{d} 天")
        if h:
            parts.append(f"{h} 时")
        if m:
            parts.append(f"{m} 分")
        if s2:
            parts.append(f"{s2} 秒")
        return json_response(
            {
                "sub_count": len(subs),
                "total_synced": sum(s.synced_count for s in subs),
                "is_syncing": self.manager.is_syncing,
                "current": self.manager.current_progress,
                "configured": configured,
                "ima_configured": configured["ima"],  # 兼容旧前端
                "sched": {
                    "enabled": sched_on,
                    "text": "每 " + " ".join(parts) if parts and sched_on else "",
                },
            }
        )

    @webapi_handler
    async def api_subs(self):
        """同步页知识库列表：ima 实时自建库 + 有道云单库 + GitHub 已添加仓库。"""
        out: list[dict] = []
        subs = await self.manager.get_subs()
        by_key = {(s.platform, s.kb_id): s for s in subs}
        # ima：实时列表，自动 ensure 订阅（无需手动拉取）
        try:
            items = await self.manager.ensure_ima_subs()
            subs = await self.manager.get_subs()
            by_key = {(s.platform, s.kb_id): s for s in subs}
            for it in items:
                kb_id = str(it.get("id") or "")
                if not kb_id:
                    continue
                sub = by_key.get(("ima", kb_id))
                if sub is None:
                    continue
                row = sub.to_dict()
                row["display_name"] = it.get("name") or sub.kb_name
                out.append(row)
        except IMAError as e:
            out.append({"error": True, "platform": "ima", "message": e.msg})
        except Exception:  # noqa: BLE001
            self.logger.exception("查询 ima 可同步知识库失败")
            out.append({"error": True, "platform": "ima", "message": "查询失败，详见日志"})
        # 有道云：单库同步，名称取自配置 youdao_target_kb
        if (self.config.get("youdao_api_key") or "").strip():
            sub = await self.manager.ensure_youdao_sub()
            row = sub.to_dict()
            row["display_name"] = sub.target_kb or sub.kb_name or "YoudaoNote"
            out.append(row)
        # GitHub：手动添加的仓库订阅（不做自动补全）
        for s in subs:
            if s.platform != "github":
                continue
            row = s.to_dict()
            row["display_name"] = s.kb_name
            out.append(row)
        # url2kb：分组即同步源（自动 ensure）
        try:
            groups = await self.manager.ensure_url2kb_subs()
            subs = await self.manager.get_subs()
            by_key = {(s.platform, s.kb_id): s for s in subs}
            for g in groups:
                gid = str(g.get("id") or "")
                if not gid:
                    continue
                sub = by_key.get(("url2kb", gid))
                if sub is None:
                    continue
                row = sub.to_dict()
                row["display_name"] = g.get("name") or sub.kb_name
                row["url_count"] = len(g.get("urls") or [])
                out.append(row)
        except Exception:  # noqa: BLE001
            self.logger.exception("查询 url2kb 分组失败")
            out.append({"error": True, "platform": "url2kb", "message": "查询失败，详见日志"})

        # 目标 AstrBot 知识库已删除时标记，页面显示「未同步」
        async def mark_missing(row: dict) -> None:
            if row.get("error"):
                return
            name = (row.get("target_kb") or "").strip() or (row.get("kb_name") or "").strip()
            if not name:
                return
            kb = await self.manager.context.kb_manager.get_kb_by_name(name)
            row["kb_missing"] = kb is None

        await asyncio.gather(*(mark_missing(row) for row in out))
        return json_response(out)

    @webapi_handler
    async def api_subs_add(self):
        """添加 GitHub 仓库同步源（URL 解析 + 查重）。"""
        payload = await request.json(default={})
        platform = str(payload.get("platform") or "").strip() or "ima"
        if platform != "github":
            return error_response("仅支持添加 GitHub 仓库同步源", status_code=400)
        url = str(payload.get("url") or "").strip()
        if not url:
            return error_response("缺少仓库 URL", status_code=400)
        if not (self.config.get("github_token") or "").strip():
            return error_response("请先在「Github 平台配置」中填写 GitHub Token（必填）", status_code=400)
        try:
            parsed = parse_github_url(url)
        except GitHubError as e:
            return error_response(e.msg, status_code=400)
        key = sub_key(parsed)
        existing = await self.manager.get_sub_by_kb_id(key, "github")
        if existing is not None:
            return json_response({"added": False, "exists": True, "sub": existing.to_dict()})
        sub = await self.manager.add_subscription(
            kb_id=key,
            kb_name=display_name(parsed),
            target_kb=parsed["repo"],
            platform="github",
        )
        self.logger.info(f"[KBridge] 添加 GitHub 仓库: {sub.kb_name} -> {key}")
        return json_response({"added": True, "exists": False, "sub": sub.to_dict()})

    @webapi_handler
    async def api_subs_remove(self):
        payload = await request.json(default={})
        kb_id = str(payload.get("kb_id") or "").strip()
        platform = str(payload.get("platform") or "ima").strip() or "ima"
        if not kb_id:
            return error_response("缺少 kb_id", status_code=400)
        try:
            sub = await self.manager.remove_subscription_by_key(kb_id, platform)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        return json_response({"removed": True, "name": sub.kb_name, "platform": sub.platform})

    @webapi_handler
    async def api_subs_delete_local(self):
        payload = await request.json(default={})
        kb_id = str(payload.get("kb_id") or "").strip()
        platform = str(payload.get("platform") or "ima").strip() or "ima"
        if not kb_id:
            return error_response("缺少 kb_id", status_code=400)
        sub = next(
            (
                s
                for s in await self.manager.get_subs()
                if s.kb_id == kb_id and s.platform == platform
            ),
            None,
        )
        if sub is None:
            return error_response("同步源不存在", status_code=404)
        try:
            r = await self.manager.delete_local(sub)
            return json_response({"name": r["name"], "deleted": r["deleted"]})
        except Exception as e:
            self.logger.error(f"[KBridge] 删除本地失败 {kb_id}: {e}")
            return error_response(str(e), status_code=500)

    @webapi_handler
    async def api_subs_toggle(self):
        payload = await request.json(default={})
        kb_id = str(payload.get("kb_id") or "").strip()
        platform = str(payload.get("platform") or "ima").strip() or "ima"
        if not kb_id:
            return error_response("缺少 kb_id", status_code=400)
        try:
            sub = await self.manager.set_sub_enabled(
                kb_id, platform, bool(payload.get("enabled"))
            )
        except ValueError as e:
            return error_response(str(e), status_code=400)
        return json_response({"enabled": sub.enabled, "name": sub.kb_name})

    @webapi_handler
    async def api_schedule_get(self):
        sec = int(self.config.get("schedule_interval") or 0)
        if sec < 1:
            sec = 0
        d, r = divmod(sec, 86400)
        h, r = divmod(r, 3600)
        m, s = divmod(r, 60)
        return json_response(
            {
                "enabled": bool(self.config.get("schedule_enabled")),
                "days": d,
                "hours": h,
                "minutes": m,
                "seconds": s,
                "logs": await self.manager.get_schedule_logs(),
            }
        )

    @webapi_handler
    async def api_schedule_clear(self):
        """清空定时同步日志。"""
        await self.manager.clear_schedule_logs()
        self.logger.info("[KBridge] 已清空定时同步日志")
        return json_response({"cleared": True})

    @webapi_handler
    async def api_schedule_save(self):
        payload = await request.json(default={})
        try:
            days = max(0, int(payload.get("days") or 0))
            hours = max(0, int(payload.get("hours") or 0))
            minutes = max(0, int(payload.get("minutes") or 0))
            seconds = max(0, int(payload.get("seconds") or 0))
        except (TypeError, ValueError):
            return error_response("间隔时间必须是数字", status_code=400)
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        if total < 1:
            return error_response("间隔时间至少 1 秒", status_code=400)
        enabled = bool(payload.get("enabled"))
        self.config["schedule_enabled"] = enabled
        self.config["schedule_interval"] = total
        self.config.save_config()
        self._restart_schedule()
        if enabled:
            await self.manager.add_schedule_log(
                "ok", f"定时同步已启用：每 {self._fmt_interval(total)}"
            )
        else:
            await self.manager.add_schedule_log("warn", "定时同步已关闭")
        self.logger.info(f"[KBridge] 定时同步配置更新: enabled={enabled} interval={total}s")
        return json_response({"enabled": enabled, "interval": total})

    @webapi_handler
    async def api_sync(self):
        payload = await request.json(default={})
        if self.manager.is_syncing:
            return error_response("已有同步任务在运行", status_code=409)
        self.manager._cancel = False  # 新同步开始前清除取消标记
        kb_id = str(payload.get("kb_id") or "").strip()
        platform = str(payload.get("platform") or "ima").strip() or "ima"
        if kb_id:
            sub = await self.manager.get_sub_by_kb_id(kb_id, platform)
            if sub is None:
                return error_response("同步源不存在", status_code=404)
            if not self.manager.platform_enabled().get(sub.platform, True):
                return error_response(f"平台 {sub.platform} 已禁用，请先在「平台配置」中启用", status_code=400)
            asyncio.get_running_loop().create_task(self._bg_sync_sub(sub))
        else:
            asyncio.get_running_loop().create_task(self._bg_sync(None))
        return json_response({"started": True})

    @webapi_handler
    async def api_cancel(self):
        """取消当前进行中的同步（已入库部分保留）。"""
        if not self.manager.is_syncing and self.manager.current_progress is None:
            return json_response({"cancelled": False, "message": "当前没有进行中的同步"})
        self.manager.request_cancel()
        self.logger.info("[KBridge] 收到取消请求，正在终止同步")
        return json_response({"cancelled": True, "message": "正在取消…"})

    async def _bg_sync(self, index: int | None) -> None:
        """后台同步任务（WebUI 触发，全量）。"""
        try:
            await self.manager.sync_all()
        except Exception:  # noqa: BLE001
            self.logger.exception("KBridge 页面触发同步失败")

    async def _bg_sync_sub(self, sub) -> None:
        """后台同步任务（WebUI 触发，单个知识库）。"""
        try:
            await self.manager.sync_subscription(sub)
        except Exception:  # noqa: BLE001
            self.logger.exception("KBridge 页面触发同步失败")

    # ---------- 生命周期 ----------

    async def terminate(self) -> None:
        if self._sched_task and not self._sched_task.done():
            self._sched_task.cancel()
            self._sched_task = None
        await self.manager.close()

    # ---------- 命令 ----------

    @command("kbridge")
    async def kbridge(self, event: AstrMessageEvent):
        # 新版 AstrBot：event.message_str 为完整消息（含命令名），先剥离命令前缀
        raw = (event.message_str or "").strip()
        tokens = raw.split()
        if tokens and tokens[0].lstrip("/").lower() == "kbridge":
            tokens = tokens[1:]
        if not tokens or tokens[0] in ("help", "h"):
            yield event.plain_result(HELP_TEXT)
            return
        cmd, rest = tokens[0].lower(), tokens[1:]
        try:
            if cmd == "kbs":
                yield await self._cmd_kbs(event)
            elif cmd == "sub":
                yield await self._cmd_sub(event, rest)
            elif cmd == "sync":
                async for r in self._cmd_sync(event, rest):
                    yield r
            else:
                yield event.plain_result(f"未知命令 {cmd}\n{HELP_TEXT}")
        except IMAError as e:
            yield event.plain_result(f"IMA 错误: {e.msg} (code={e.code})")
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:  # noqa: BLE001
            self.logger.exception("KBridge 命令执行失败")
            yield event.plain_result(f"执行失败: {e}")

    async def _cmd_kbs(self, event: AstrMessageEvent):
        client = await self.manager.get_client()
        items = await _retry_with_backoff(lambda: client.search_knowledge_bases())
        # 只列自建库（订阅库无法经 OpenAPI 读取文件）
        items = [it for it in items if it.get("base_type") != "我加入的订阅知识库"]
        if not items:
            return event.plain_result("IMA 账号下暂无自建知识库")
        lines = ["IMA 可同步知识库（自建）："]
        for it in items:
            lines.append(f"• {it.get('name', '?')}  (id: {it.get('id', '?')})")
        return event.plain_result("\n".join(lines))

    async def _cmd_sub(self, event: AstrMessageEvent, rest: list[str]):
        if not rest:
            return event.plain_result("用法: /kbridge sub list | del <序号>")
        action = rest[0].lower()
        if action == "list":
            subs = await self.manager.get_subs()
            if not subs:
                return event.plain_result("暂无同步源。使用 /kbridge kbs 查看可同步知识库")
            lines = ["当前同步源："]
            for i, s in enumerate(subs):
                status = "✔" if s.last_status == "ok" else s.last_status
                target = s.target_kb or (
                    "YoudaoNote" if s.platform == "youdao" else "自动"
                )
                lines.append(
                    f"{i}. {s.kb_name} ({s.platform}) -> {target}"
                    f" | 已同步 {s.synced_count} | {status} | 上次: {s.last_sync_at or '-'}"
                )
            return event.plain_result("\n".join(lines))
        if action == "del":
            if len(rest) < 2:
                return event.plain_result("用法: /kbridge sub del <序号>")
            try:
                idx = int(rest[1])
            except ValueError:
                return event.plain_result("序号必须是数字")
            sub = await self.manager.remove_subscription(idx)
            return event.plain_result(f"已删除同步源: {sub.kb_name} ({sub.kb_id})")
        return event.plain_result(f"未知 sub 操作: {action}")

    async def _cmd_sync(self, event: AstrMessageEvent, rest: list[str]):
        subs = await self.manager.get_subs()
        if not subs:
            yield event.plain_result("暂无同步源，先执行 /kbridge sub add")
            return
        if rest and rest[0].isdigit():
            idx = int(rest[0])
            if idx >= len(subs):
                yield event.plain_result(f"序号 {idx} 不存在")
                return
            yield event.plain_result(f"正在同步 {subs[idx].kb_name} …")
            r = await self.manager.sync_subscription(subs[idx])
            yield event.plain_result(_format_result(r))
            return
        yield event.plain_result(f"正在同步 {len(subs)} 个同步源…")
        results = await self.manager.sync_all()
        yield event.plain_result("\n\n".join(_format_result(r) for r in results))


def _format_result(r) -> str:
    lines = [f"同步完成: 共 {r.total} 条"]
    lines.append(f"新增 {r.synced} | 跳过 {r.skipped} | 失败 {r.failed}")
    if r.errors:
        lines.append("失败明细:")
        lines.extend(f"- {e}" for e in r.errors[:5])
    return "\n".join(lines)
