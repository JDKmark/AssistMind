"""服务端会话热态记忆（DialogStore）。

短期记忆收口（架构评估 S6）：此前对话历史由前端每轮全量回传、服务端只写不读回
（PG 仅供历史会话回看，不参与推理）——历史可被客户端任意伪造，且换端/丢缓存即
失去上下文。现把「推理用最近窗口」落 Redis 热态：

- key：``assistmind:conv:{username}:{conversation_id}:history``（与 PG 会话归属
  同一身份维度双键隔离，防跨用户读取）
- load：服务端记忆优先，读时 EXPIRE 续期（滑动 TTL）；存在摘要时头部注入
  「此前对话摘要」消息（摘要不占窗口槽位，不被 LTRIM 裁掉）
- seed：客户端 history 仅在 key 不存在时作种子写入（防伪造覆写服务端记录）
- append_round：done 路径把本轮问答追加进窗口；超窗消息攒满阈值（MEMORY_SUMMARY_
  THRESHOLD）时先送后台任务 LLM 压缩成摘要、再 LTRIM 回窗口（MEMORY_SUMMARY_ENABLED
  控制，fast=True，失败仅 warning）

失败语义：Redis 不可用/异常时返回空值 + logger.warning，绝不抛异常——
降级链为「服务端记忆失败 → 请求内 history 兜底」（与失败降级表
「Redis 记忆失败 → 用请求内上下文」一致）。PG 持久化与会话回看不受影响。
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.config import get_settings
from app.core.dialog.manager import SUMMARY_MARK, trim_history
from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.infra.redis import RedisClient, get_redis

logger = logging.getLogger(__name__)
settings = get_settings()


def history_key(username: str, conversation_id: str) -> str:
    """会话热态记忆 key（username + conversation_id 双键隔离）。"""
    return f"assistmind:conv:{username}:{conversation_id}:history"


def summary_key(username: str, conversation_id: str) -> str:
    """会话摘要 key（与 history 同生命周期，随 load/append 续期）。"""
    return f"assistmind:conv:{username}:{conversation_id}:summary"


_SUMMARY_PROMPT = (
    "请把以下客服对话压缩成一段简洁的中文摘要（150字内），保留关键事实："
    "用户提到的问题、订单号/工单号/商品ID、已给出的结论与承诺。"
    "只输出摘要正文，不要任何前缀或解释。\n\n{existing}对话内容：\n{messages}\n\n摘要："
)


def _dump_message(role: str, content: str) -> str:
    return json.dumps({"role": role, "content": content}, ensure_ascii=False)


def _parse_message(raw: str) -> dict[str, str] | None:
    """解析 list 条目为 {role, content}；脏数据（非 JSON/字段缺失）按丢弃处理。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    role = data.get("role")
    content = data.get("content")
    if role not in ("user", "assistant") or not isinstance(content, str):
        return None
    return {"role": role, "content": content}


class DialogStore:
    """会话热态记忆读写（Redis list，纯热态，不承担持久化职责）。"""

    def __init__(self, redis_client: RedisClient | None = None) -> None:
        # 可注入 redis_client（测试用 fakeredis 包一层）；缺省走全局单例
        self._injected = redis_client
        # 后台摘要任务强引用集（防 GC；done 回调自动清理）
        self._bg_tasks: set[asyncio.Task] = set()
        # 摘要合并串行化锁（按 skey）：防并发双触发 last-write-wins 丢批次事实
        self._summary_locks: dict[str, asyncio.Lock] = {}

    def _summary_lock(self, skey: str) -> asyncio.Lock:
        lock = self._summary_locks.get(skey)
        if lock is None:
            lock = asyncio.Lock()
            self._summary_locks[skey] = lock
        return lock

    def _client(self) -> RedisClient | None:
        client = self._injected or get_redis()
        return client if client.is_connected else None

    async def load(
        self, username: str, conversation_id: str
    ) -> list[dict[str, str]] | None:
        """读会话热态历史（读时续期）。无记录/Redis 不可用返回 None（调用方回落请求内 history）。

        存在摘要时头部注入一条「此前对话摘要」assistant 消息（在 trim 之后拼接，
        不占窗口槽位；下游 format_history / _format_messages 按 assistant→客服 渲染）。
        """
        client = self._client()
        if client is None or not conversation_id:
            return None
        key = history_key(username, conversation_id)
        raw_items = await client.lrange(key, 0, -1)
        messages = [
            msg
            for msg in (_parse_message(raw) for raw in raw_items)
            if msg is not None
        ]
        if not messages:
            return None
        # 滑动 TTL：有往来即续期（读时续期，与缓存命中刷新语义一致）；摘要同步续期
        await client.expire(key, settings.MEMORY_TTL_SECONDS)
        trimmed = trim_history(messages)
        if settings.MEMORY_SUMMARY_ENABLED:
            skey = summary_key(username, conversation_id)
            summary = await client.get(skey)
            if summary:
                await client.expire(skey, settings.MEMORY_TTL_SECONDS)
                trimmed = [
                    {"role": "assistant", "content": f"{SUMMARY_MARK}{summary}）"},
                    *trimmed,
                ]
        return trimmed

    async def seed(
        self,
        username: str,
        conversation_id: str,
        history: list[dict[str, str]] | None,
    ) -> bool:
        """用客户端携带的历史作种子写入（仅该会话从未有过任何写入时）。

        SETNX 哨兵保证原子性：exists+rpush 的 check-then-act 有竞态窗口，且
        append 之后的迟到 seed 会把旧历史插到新鲜轮次之后（随后的窗口裁剪
        可能挤出刚落的轮次）——哨兵两者都防，任何写入方（seed/append）都会置位。
        """
        client = self._client()
        if client is None or not conversation_id or not history:
            return False
        key = history_key(username, conversation_id)
        marker = f"{key}:seeded"
        if not await client.set(marker, "1", ttl=settings.MEMORY_TTL_SECONDS, nx=True):
            return False
        payload = [
            _dump_message(h.get("role", "user"), h.get("content", ""))
            for h in history
        ]
        if not await client.rpush(key, *payload):
            return False
        await client.ltrim(key, -settings.MEMORY_WINDOW, -1)
        await client.expire(key, settings.MEMORY_TTL_SECONDS)
        await client.expire(marker, settings.MEMORY_TTL_SECONDS)
        logger.info(
            "[DialogStore] 会话热态种子写入: conv=%s 条数=%d", conversation_id, len(payload)
        )
        return True

    async def append_round(
        self, username: str, conversation_id: str, query: str, answer: str
    ) -> bool:
        """done 路径追加一轮问答进窗口（RPUSH 两条 + 摘要调度 + 超窗裁剪 + 续期）。失败仅 warning。

        裁剪策略：超窗消息先在 list 里「暂存」攒批——攒满 MEMORY_SUMMARY_THRESHOLD
        条溢出时一次性送后台摘要、再 LTRIM 回窗口（摘要调度必须在 LTRIM 之前，
        挤出的消息此刻还在 list 里可读）；关闭摘要或未达阈值时直接裁剪。
        load 侧始终 trim_history，暂存段不进推理 prompt，推理窗口语义不变。
        """
        client = self._client()
        if client is None or not conversation_id:
            return False
        key = history_key(username, conversation_id)
        ok = await client.rpush(
            key,
            _dump_message("user", query),
            _dump_message("assistant", answer),
        )
        if not ok:
            return False
        # 首次写入即置哨兵（幂等）：此后迟到 seed 不再把旧历史插到新鲜轮次之后
        await client.set(
            f"{key}:seeded", "1", ttl=settings.MEMORY_TTL_SECONDS, nx=True
        )
        total = await client.llen(key)
        if total <= 0:
            # rpush 刚成功而 llen=0：只可能是 llen 查询失败（异常被吞返回 0）——
            # 兜底硬裁剪防「永不触发窗口裁剪、列表无限增长」
            await client.ltrim(
                key,
                -(settings.MEMORY_WINDOW + settings.MEMORY_SUMMARY_THRESHOLD),
                -1,
            )
            await client.expire(key, settings.MEMORY_TTL_SECONDS)
            return True
        overflow = total - settings.MEMORY_WINDOW
        if overflow > 0:
            if settings.MEMORY_SUMMARY_ENABLED:
                if overflow >= settings.MEMORY_SUMMARY_THRESHOLD:
                    # 攒批到位：先送后台摘要（快照），再裁回窗口
                    await self._schedule_summary(client, username, conversation_id, key, overflow)
                    await client.ltrim(key, -settings.MEMORY_WINDOW, -1)
                # 未达阈值：溢出消息暂存不裁（load 侧 trim_history 保证推理窗口不变）
            else:
                await client.ltrim(key, -settings.MEMORY_WINDOW, -1)
        await client.expire(key, settings.MEMORY_TTL_SECONDS)
        return True

    async def _schedule_summary(
        self,
        client: RedisClient,
        username: str,
        conversation_id: str,
        key: str,
        overflow: int,
    ) -> None:
        """把将被挤出的最老消息（快照）交后台任务压缩成摘要。

        调度即返回（不阻塞 SSE done 路径）；LLM 慢/失败不影响本轮窗口裁剪
        （降级 = 无摘要，纯滑动窗口）。
        """
        raw_trimmed = await client.lrange(key, 0, overflow - 1)
        if not raw_trimmed:
            return
        task = asyncio.create_task(
            self.summarize_trimmed(username, conversation_id, raw_trimmed)
        )
        self._bg_tasks.add(task)

        def _on_done(done: asyncio.Task) -> None:
            self._bg_tasks.discard(done)
            # summarize_trimmed 自身已全量捕获异常；此处兜底防「未取回异常」静默
            if not done.cancelled() and done.exception() is not None:
                logger.warning(
                    "[DialogStore] 摘要后台任务异常（已忽略，纯滑动窗口）: %s",
                    done.exception(),
                )

        task.add_done_callback(_on_done)

    async def summarize_trimmed(
        self, username: str, conversation_id: str, raw_messages: list[str]
    ) -> bool:
        """把被窗口挤出的最老消息压缩进摘要（后台任务入口；失败仅 warning）。

        已有摘要时增量合并（prompt 要求在旧摘要基础上补充，不丢已有事实）。
        读-合并-写全程持有 skey 锁：并发双触发时后写者基于先写者的摘要合并，
        而非 last-write-wins 覆盖丢批次。
        """
        client = self._client()
        if client is None or not conversation_id or not raw_messages:
            return False
        skey = summary_key(username, conversation_id)
        messages = [m for m in (_parse_message(r) for r in raw_messages) if m]
        if not messages:
            return False
        transcript = "\n".join(
            f"{'用户' if m['role'] == 'user' else '客服'}: {m['content']}"
            for m in messages
        )
        async with self._summary_lock(skey):
            try:
                prev = await client.get(skey)
                existing = (
                    f"已有摘要（在其基础上补充合并，不要丢失已有事实）：\n{prev}\n\n"
                    if prev
                    else ""
                )
                summary = await call_llm(
                    _SUMMARY_PROMPT.format(existing=existing, messages=transcript),
                    system="你是对话摘要助手。只输出摘要正文。",
                    fast=True,  # 失败可降级（无摘要），不拖住后台任务与主链路
                )
            except LLMUnavailableError as e:
                logger.warning("[DialogStore] 摘要压缩降级（LLM 不可用，纯滑动窗口）: %s", e)
                return False
            except Exception as e:
                logger.warning("[DialogStore] 摘要压缩失败（忽略，纯滑动窗口）: %s", e)
                return False
        summary = summary.strip()
        if not summary:
            return False
        await client.set(skey, summary, ttl=settings.MEMORY_TTL_SECONDS)
        logger.info(
            "[DialogStore] 会话摘要已更新: conv=%s 压缩消息数=%d", conversation_id, len(messages)
        )
        return True


dialog_store = DialogStore()
