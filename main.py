"""KBridge — AstrBot 插件：订阅并同步外部知识源（ima）到 AstrBot 知识库。

命令（/kbridge）：
- /kbridge                   帮助
- /kbridge kbs               列出 IMA 账号可订阅的知识库
- /kbridge sub add <id|名称> [--to 目标知识库名]   添加订阅
- /kbridge sub list          列出订阅
- /kbridge sub del <序号>    删除订阅
- /kbridge sync [序号|all]   手动同步（默认 all）
- /kbridge cron on [分钟]    开启定时同步（默认取配置 sync_interval_minutes）
- /kbridge cron off         关闭定时同步
"""

from __future__ import annotations

import asyncio
import logging

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
from .sync_manager import KV_SUBS, SyncManager

logger = logging.getLogger("astrbot")

PLUGIN_NAME = "astrbot_plugin_kbridge"
CRON_JOB_NAME = "kbridge_auto_sync"
HELP_TEXT = """KBridge - 外部知识源订阅同步

/kbridge kbs                   列出 IMA 知识库
/kbridge sub add <id|名称> [--to 目标知识库名]   添加订阅
/kbridge sub list              订阅列表
/kbridge sub del <序号>        删除订阅
/kbridge sync [序号|all]       手动同步
/kbridge cron on [分钟]        开启定时同步
/kbridge cron off              关闭定时同步"""


@register("KBridge", "icenfn", "外部知识源（ima）订阅同步到 AstrBot 知识库", "0.1.0")
class KBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.manager = SyncManager(context, config)
        self._cron_job_id: str | None = None
        self._cron_initialized = False
        self._register_web_apis()

    # ---------- WebUI 页面 API ----------

    # 允许页面写入的配置项白名单
    CONFIG_WRITABLE = {
        "ima_client_id": "str",
        "ima_api_key": "str",
        "target_kb_prefix": "str",
        "auto_create_kb": "bool",
        "sync_interval_minutes": "int",
        "max_concurrency": "int",
    }

    def _register_web_apis(self) -> None:
        routes = [
            (f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "KBridge 总览状态"),
            (f"/{PLUGIN_NAME}/subs", self.api_subs, ["GET"], "订阅列表"),
            (f"/{PLUGIN_NAME}/subs/add", self.api_subs_add, ["POST"], "添加订阅"),
            (f"/{PLUGIN_NAME}/subs/<idx>/remove", self.api_subs_remove, ["POST"], "删除订阅"),
            (f"/{PLUGIN_NAME}/sync", self.api_sync, ["POST"], "触发同步"),
            (f"/{PLUGIN_NAME}/cron", self.api_cron, ["POST"], "定时同步开关"),
            (f"/{PLUGIN_NAME}/kbs", self.api_kbs, ["GET"], "IMA 知识库列表"),
            (f"/{PLUGIN_NAME}/config", self.api_config_get, ["GET"], "读取平台配置"),
            (f"/{PLUGIN_NAME}/config", self.api_config_save, ["POST"], "保存平台配置"),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(route, handler, methods, desc)

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
                    "obsidian": {"name": "Obsidian", "supported": False},
                    "github": {"name": "GitHub Repository", "supported": False},
                    "notebook": {"name": "Open Notebook", "supported": False},
                    "url2kb": {"name": "url2kb", "supported": False},
                },
                "values": {
                    "ima_client_id": bool(self.config.get("ima_client_id")),
                    "ima_api_key": bool(self.config.get("ima_api_key")),
                },
                "common": {
                    "target_kb_prefix": self.config.get("target_kb_prefix", "ima-"),
                    "auto_create_kb": bool(self.config.get("auto_create_kb", True)),
                    "sync_interval_minutes": int(self.config.get("sync_interval_minutes", 60) or 0),
                    "max_concurrency": int(self.config.get("max_concurrency", 3) or 3),
                },
            }
        )

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
        # 凭据变更后重置 IMA 客户端
        if "ima_client_id" in saved or "ima_api_key" in saved:
            await self.manager.reset_client()
        # 同步间隔变更后重建定时任务
        if "sync_interval_minutes" in saved:
            minutes = int(self.config.get("sync_interval_minutes", 0) or 0)
            await self._set_cron(minutes > 0, minutes if minutes > 0 else None)
        return json_response({"saved": saved})

    async def api_stats(self):
        subs = await self.manager.get_subs()
        return json_response(
            {
                "sub_count": len(subs),
                "total_synced": sum(s.synced_count for s in subs),
                "is_syncing": self.manager.is_syncing,
                "cron_enabled": self._cron_job_id is not None,
                "cron_interval": int(self.config.get("sync_interval_minutes", 60) or 0),
                "ima_configured": bool(
                    self.config.get("ima_client_id") and self.config.get("ima_api_key")
                ),
                "auto_create_kb": bool(self.config.get("auto_create_kb", True)),
                "target_prefix": self.config.get("target_kb_prefix", "ima-"),
            }
        )

    async def api_subs(self):
        subs = await self.manager.get_subs()
        return json_response([s.to_dict() for s in subs])

    async def api_subs_add(self):
        payload = await request.json(default={})
        kb_id = str(payload.get("kb_id") or "").strip()
        if not kb_id:
            return error_response("缺少 kb_id", status_code=400)
        target_kb = str(payload.get("target_kb") or "").strip()
        client = await self.manager.get_client()
        try:
            items = await _retry_with_backoff(client.search_knowledge_bases(kb_id))
        except IMAError as e:
            return error_response(f"IMA 错误: {e.msg}", status_code=400)
        matched = next(
            (it for it in items if it.get("id") == kb_id or it.get("name") == kb_id),
            None,
        )
        if matched is None and items:
            matched = items[0]
        if matched is None:
            return error_response(f"未找到知识库: {kb_id}", status_code=404)
        sub = await self.manager.add_subscription(
            kb_id=matched["id"],
            kb_name=matched.get("name", matched["id"]),
            target_kb=target_kb,
        )
        return json_response({"added": True, "sub": sub.to_dict()})

    async def api_subs_remove(self, idx: int):
        try:
            sub = await self.manager.remove_subscription(idx)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        return json_response({"removed": True, "name": sub.kb_name})

    async def api_sync(self):
        payload = await request.json(default={})
        if self.manager.is_syncing:
            return error_response("已有同步任务在运行", status_code=409)
        index = payload.get("index")
        if index is not None and not isinstance(index, int):
            try:
                index = int(index)
            except (TypeError, ValueError):
                return error_response("index 必须是数字", status_code=400)
        asyncio.get_running_loop().create_task(self._bg_sync(index))
        return json_response({"started": True})

    async def api_cron(self):
        payload = await request.json(default={})
        on = bool(payload.get("on"))
        minutes = payload.get("minutes")
        if minutes is not None:
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                return error_response("minutes 必须是数字", status_code=400)
        try:
            desc, enabled = await self._set_cron(on, minutes)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        return json_response({"enabled": enabled, "message": desc})

    async def api_kbs(self):
        client = await self.manager.get_client()
        items = await _retry_with_backoff(client.search_knowledge_bases())
        return json_response(items)

    async def _bg_sync(self, index: int | None) -> None:
        """后台同步任务（WebUI 触发）。"""
        try:
            if index is None:
                await self.manager.sync_all()
            else:
                subs = await self.manager.get_subs()
                if 0 <= index < len(subs):
                    await self.manager.sync_subscription(subs[index])
        except Exception:  # noqa: BLE001
            logger.exception("KBridge 页面触发同步失败")

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        try:
            await self._sync_cron_job()
            self._cron_initialized = True
        except Exception:  # noqa: BLE001
            logger.exception("KBridge 初始化定时任务失败")

    async def terminate(self) -> None:
        await self.manager.close()

    # ---------- 定时同步 ----------

    async def _auto_sync(self) -> None:
        logger.info("KBridge 定时同步开始")
        try:
            results = await self.manager.sync_all()
            for r in results:
                logger.info(
                    f"KBridge 同步 {r.kb_id}: 新增 {r.synced}, 跳过 {r.skipped}, 失败 {r.failed}"
                )
        except Exception:  # noqa: BLE001
            logger.exception("KBridge 定时同步失败")

    async def _sync_cron_job(self) -> None:
        """根据配置 sync_interval_minutes 同步定时任务状态。"""
        minutes = int(self.config.get("sync_interval_minutes", 60) or 0)
        try:
            await self._set_cron(minutes > 0, minutes if minutes > 0 else None)
        except Exception:  # noqa: BLE001
            logger.exception("KBridge 初始化定时任务失败")

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
            elif cmd == "cron":
                yield await self._cmd_cron(event, rest)
            else:
                yield event.plain_result(f"未知命令 {cmd}\n{HELP_TEXT}")
        except IMAError as e:
            yield event.plain_result(f"IMA 错误: {e.msg} (code={e.code})")
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
        except Exception as e:  # noqa: BLE001
            logger.exception("KBridge 命令执行失败")
            yield event.plain_result(f"执行失败: {e}")

    async def _cmd_kbs(self, event: AstrMessageEvent):
        client = await self.manager.get_client()
        items = await _retry_with_backoff(client.search_knowledge_bases())
        if not items:
            return event.plain_result("IMA 账号下暂无知识库")
        lines = ["IMA 可订阅知识库："]
        for it in items:
            lines.append(f"• {it.get('name', '?')}  (id: {it.get('id', '?')})")
        return event.plain_result("\n".join(lines))

    async def _cmd_sub(self, event: AstrMessageEvent, rest: list[str]):
        if not rest:
            return event.plain_result("用法: /kbridge sub add <id|名称> [--to 目标库名] | list | del <序号>")
        action = rest[0].lower()
        if action == "list":
            subs = await self.manager.get_subs()
            if not subs:
                return event.plain_result("暂无订阅。使用 /kbridge kbs 查看可订阅知识库")
            lines = ["当前订阅："]
            for i, s in enumerate(subs):
                status = "✔" if s.last_status == "ok" else s.last_status
                lines.append(
                    f"{i}. {s.kb_name} (ima: {s.kb_id}) -> {s.target_kb or '自动'}"
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
            return event.plain_result(f"已删除订阅: {sub.kb_name} ({sub.kb_id})")
        if action == "add":
            if len(rest) < 2:
                return event.plain_result("用法: /kbridge sub add <id|名称> [--to 目标库名]")
            target = ""
            if "--to" in rest:
                pos = rest.index("--to")
                if pos + 1 < len(rest):
                    target = rest[pos + 1]
            key = rest[1]
            client = await self.manager.get_client()
            items = await _retry_with_backoff(client.search_knowledge_bases(key))
            if not items:
                return event.plain_result(f"未找到知识库: {key}")
            matched = next((it for it in items if it.get("id") == key or it.get("name") == key), items[0])
            sub = await self.manager.add_subscription(
                kb_id=matched["id"], kb_name=matched.get("name", matched["id"]), target_kb=target
            )
            return event.plain_result(
                f"已添加订阅: {sub.kb_name} -> {sub.target_kb or '自动创建'}\n"
                f"立即同步: /kbridge sync"
            )
        return event.plain_result(f"未知 sub 操作: {action}")

    async def _cmd_sync(self, event: AstrMessageEvent, rest: list[str]):
        subs = await self.manager.get_subs()
        if not subs:
            yield event.plain_result("暂无订阅，先执行 /kbridge sub add")
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
        yield event.plain_result(f"正在同步 {len(subs)} 个订阅…")
        results = await self.manager.sync_all()
        yield event.plain_result("\n\n".join(_format_result(r) for r in results))

    async def _set_cron(self, on: bool, minutes: int | None = None) -> tuple[str, bool]:
        """开启/关闭定时同步。返回 (描述, 是否启用)。"""
        cron_mgr = self.context.cron_manager
        # 先清理现有任务
        try:
            jobs = await cron_mgr.list_jobs()
            for job in jobs:
                if job.name == CRON_JOB_NAME:
                    if job.job_id == self._cron_job_id:
                        self._cron_job_id = None
                    await cron_mgr.delete_job(job.job_id)
        except Exception:  # noqa: BLE001
            logger.warning("KBridge 清理定时任务失败", exc_info=True)
        if not on:
            self._cron_job_id = None
            return "已关闭定时同步", False
        if minutes is None or minutes <= 0:
            minutes = int(self.config.get("sync_interval_minutes", 60) or 60)
        self.config["sync_interval_minutes"] = minutes
        self.config.save_config()
        job = await cron_mgr.add_basic_job(
            name=CRON_JOB_NAME,
            cron_expression=_minutes_to_cron(minutes),
            handler=self._auto_sync,
            description="KBridge 定时同步外部知识源",
            persistent=False,
        )
        self._cron_job_id = job.job_id
        return f"已开启定时同步：每 {minutes} 分钟", True

    async def _cmd_cron(self, event: AstrMessageEvent, rest: list[str]):
        if not rest or rest[0] not in ("on", "off"):
            return event.plain_result("用法: /kbridge cron on [分钟] | off")
        if rest[0] == "off":
            desc, _ = await self._set_cron(False)
            return event.plain_result(desc)
        minutes = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
        desc, _ = await self._set_cron(True, minutes)
        return event.plain_result(desc)


def _minutes_to_cron(minutes: int) -> str:
    minutes = max(1, minutes)
    if minutes < 60:
        return f"*/{minutes} * * * *"
    hours = minutes // 60
    return f"0 */{hours} * * *"


def _format_result(r) -> str:
    lines = [f"同步完成: 共 {r.total} 条"]
    lines.append(f"新增 {r.synced} | 跳过 {r.skipped} | 失败 {r.failed}")
    if r.errors:
        lines.append("失败明细:")
        lines.extend(f"- {e}" for e in r.errors[:5])
    return "\n".join(lines)
