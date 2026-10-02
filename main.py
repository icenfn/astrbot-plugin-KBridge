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
    filter,
    register,
)

from ima_client import IMAError, _retry_with_backoff
from sync_manager import KV_SUBS, SyncManager

logger = logging.getLogger("astrbot")

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
        if minutes <= 0:
            self._cron_job_id = None
            return
        expr = _minutes_to_cron(minutes)
        job = await cron_mgr.add_basic_job(
            name=CRON_JOB_NAME,
            cron_expression=expr,
            handler=self._auto_sync,
            description="KBridge 定时同步外部知识源",
            persistent=False,
        )
        self._cron_job_id = job.job_id

    # ---------- 命令 ----------

    @filter.command("kbridge")
    async def kbridge(self, event: AstrMessageEvent):
        args = (event.message_str or "").strip().split()
        if not args or args[0] in ("help", "h"):
            yield event.plain_result(HELP_TEXT)
            return
        cmd, rest = args[0].lower(), args[1:]
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

    async def _cmd_cron(self, event: AstrMessageEvent, rest: list[str]):
        if not rest or rest[0] not in ("on", "off"):
            return event.plain_result("用法: /kbridge cron on [分钟] | off")
        if rest[0] == "off":
            self._cron_job_id = None
            cron_mgr = self.context.cron_manager
            jobs = await cron_mgr.list_jobs()
            for job in jobs:
                if job.name == CRON_JOB_NAME:
                    await cron_mgr.delete_job(job.job_id)
            return event.plain_result("已关闭定时同步")
        minutes = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else int(
            self.config.get("sync_interval_minutes", 60) or 60
        )
        if minutes <= 0:
            return event.plain_result("分钟数必须大于 0")
        self.config["sync_interval_minutes"] = minutes
        self.config.save_config()
        await self._sync_cron_job()
        return event.plain_result(f"已开启定时同步：每 {minutes} 分钟")


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
