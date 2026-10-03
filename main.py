"""KBridge — AstrBot 插件：同步外部知识源（ima / 有道云笔记）到 AstrBot 知识库。

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
from .sync_manager import SyncManager

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
        # 清理旧版本遗留配置键（前缀/定时已移除）
        for stale in ("target_kb_prefix", "sync_interval_minutes"):
            if stale in self.config:
                del self.config[stale]
        self.manager = SyncManager(self, context, config)
        self._register_web_apis()

    # ---------- WebUI 页面 API ----------

    # 允许页面写入的配置项白名单
    CONFIG_WRITABLE = {
        "ima_client_id": "str",
        "ima_api_key": "str",
        "youdao_api_key": "str",
        "youdao_target_kb": "str",
        "max_concurrency": "int",
    }

    def _register_web_apis(self) -> None:
        routes = [
            (f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "KBridge 总览状态"),
            (f"/{PLUGIN_NAME}/subs", self.api_subs, ["GET"], "可同步知识库列表"),
            (f"/{PLUGIN_NAME}/sync", self.api_sync, ["POST"], "触发同步"),
            (f"/{PLUGIN_NAME}/config", self.api_config_get, ["GET"], "读取平台配置"),
            (f"/{PLUGIN_NAME}/config", self.api_config_save, ["POST"], "保存平台配置"),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(route, handler, methods, desc)

    @webapi_handler
    async def api_config_get(self):
        """返回平台配置状态（secret 字段不回显原文，只回是否已配置）。"""
        return json_response(
            {
                "platforms": {
                    "ima": {
                        "name": "腾讯 ima",
                        "supported": True,
                        "fields": [
                            {"key": "ima_client_id", "label": "Client ID", "secret": False},
                            {"key": "ima_api_key", "label": "API Key", "secret": True},
                        ],
                    },
                    "youdao": {
                        "name": "有道云笔记",
                        "supported": True,
                        "fields": [
                            {"key": "youdao_api_key", "label": "API Key", "secret": True},
                            {
                                "key": "youdao_target_kb",
                                "label": "同步至 AstrBot 知识库",
                                "secret": False,
                            },
                        ],
                    },
                    "github": {"name": "GitHub Repository", "supported": False},
                },
                "values": {
                    "ima_client_id": (self.config.get("ima_client_id") or "").strip(),
                    "ima_api_key": bool(self.config.get("ima_api_key")),
                    "youdao_api_key": bool(self.config.get("youdao_api_key")),
                    "youdao_target_kb": (self.config.get("youdao_target_kb") or "").strip()
                    or "YoudaoNote",
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
        self.config.save_config()
        # 凭据变更后重置对应平台客户端，使新 Key 生效
        for platform, keys in (
            ("ima", ("ima_client_id", "ima_api_key")),
            ("youdao", ("youdao_api_key",)),
        ):
            if any(k in saved for k in keys):
                await self.manager.reset_client(platform)
        if "youdao_target_kb" in saved:
            await self.manager.ensure_youdao_sub()
        self.logger.info(f"[KBridge] 页面保存平台配置成功: {saved}")
        return json_response({"saved": saved})

    @webapi_handler
    async def api_stats(self):
        subs = await self.manager.get_subs()
        configured = {
            "ima": bool(self.config.get("ima_client_id") and self.config.get("ima_api_key")),
            "youdao": bool(self.config.get("youdao_api_key")),
        }
        return json_response(
            {
                "sub_count": len(subs),
                "total_synced": sum(s.synced_count for s in subs),
                "is_syncing": self.manager.is_syncing,
                "configured": configured,
                "ima_configured": configured["ima"],  # 兼容旧前端
            }
        )

    @webapi_handler
    async def api_subs(self):
        """同步页知识库列表：ima 实时自建库 + 有道云单库，自动补全订阅并合并状态。"""
        out: list[dict] = []
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
        # 有道云：单库「全部笔记」，目标库名来自配置
        if (self.config.get("youdao_api_key") or "").strip():
            sub = await self.manager.ensure_youdao_sub()
            row = sub.to_dict()
            row["display_name"] = "全部笔记"
            out.append(row)
        self.logger.info(f"[KBridge] 同步页知识库列表: {len(out)} 项")
        return json_response(out)

    @webapi_handler
    async def api_sync(self):
        payload = await request.json(default={})
        if self.manager.is_syncing:
            return error_response("已有同步任务在运行", status_code=409)
        kb_id = str(payload.get("kb_id") or "").strip()
        platform = str(payload.get("platform") or "ima").strip() or "ima"
        if kb_id:
            sub = await self.manager.get_sub_by_kb_id(kb_id, platform)
            if sub is None:
                return error_response("同步源不存在", status_code=404)
            asyncio.get_running_loop().create_task(self._bg_sync_sub(sub))
        else:
            asyncio.get_running_loop().create_task(self._bg_sync(None))
        return json_response({"started": True})

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
