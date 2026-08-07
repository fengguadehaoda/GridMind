"""SSE 事件广播器（V1.5.1 LangGraph 后端改造 · T04 · 架构 §2.5）。

设计要点
========

1. **6 个事件 type**（主理人决策 #6 + 架构 §2.5.2）：
   - ``reasoning_paused`` / ``reasoning_resumed`` / ``step_replaced``
   - ``hitl_interrupt`` / ``hitl_resolved`` / ``reasoning_error``

2. **多订阅者广播**：每个 ``thread_id`` 维护一组 ``asyncio.Queue``，
   ``emit()`` 时非阻塞地 ``put_nowait`` 到所有订阅者的队列。
   队列满时 silently drop（不阻塞 emit，订阅者消费太慢则丢事件）。
   业务影响：前端 SSE 断连重连时不会收到 backlog —— 因为是 push-based 而非持久化。

3. **单例模式**（架构 §2.5.4）：模块级 ``sse_event_emitter`` 供全局复用；
   进程内共享，跨进程隔离（FastAPI 默认单 worker 单进程）。

4. **线程安全**：``_lock`` 保护 ``_subscribers`` 字典的读写；
   锁外做 ``put_nowait``（非阻塞），避免 emit 期间持锁。

5. **与 v1.5.0 现有 SSE 兼容**：现有 ``/chat/stream/{id}`` 端点（``data: {"type":"token|done|error"}``）
   **不**经过本 emitter，T04 仅扩展**新**端点 ``GET /sessions/{id}/events``。

不在范围内
==========

- 跨进程广播（需 Redis pub/sub 等，V1.6.0+ 评估）
- 事件持久化（rewind 后的"补发"不支持；订阅者必须保持连接）
- 订阅者背压（队列满即丢，与 v1.5.0 SSE 行为一致）
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal, get_args

from loguru import logger

# ═══════════════════════════════════════════════════════
# 类型定义
# ═══════════════════════════════════════════════════════

#: 6 个 SSE 事件 type（架构 §2.5.2 + 主理人决策 #6，**严格枚举禁止扩展**）
SSEEventType = Literal[
    "reasoning_paused",
    "reasoning_resumed",
    "step_replaced",
    "hitl_interrupt",
    "hitl_resolved",
    "reasoning_error",
]

#: Literal 派生 tuple，供 isinstance-style runtime check 使用
_VALID_EVENT_TYPES: tuple[str, ...] = get_args(SSEEventType)

#: 单个订阅者队列最大长度（架构 §2.5 提示）；满则 drop
SUBSCRIBER_QUEUE_MAXSIZE: int = 100


@dataclass(frozen=True)
class SSEEvent:
    """不可变 SSE 事件值对象。

    Attributes:
        type:       事件 type（6 个 enum 之一）。
        thread_id:  目标会话 ID（用于路由 + payload 冗余）。
        payload:    事件业务字段（time / content / tool / args 等）。
        timestamp:  事件产生的 Unix epoch（秒，含小数）。
    """

    type: str
    thread_id: str
    payload: dict[str, Any]
    timestamp: float


# ═══════════════════════════════════════════════════════
# SSEEventEmitter
# ═══════════════════════════════════════════════════════


class SSEEventEmitter:
    """线程/协程安全的 SSE 事件广播器（架构 §2.5）。

    典型用法::

        emitter = sse_event_emitter
        queue = await emitter.subscribe("thread-123")
        try:
            while True:
                event = await queue.get()
                ... # 写入 SSE 流
        finally:
            await emitter.unsubscribe("thread-123", queue)

    业务调用::

        await sse_event_emitter.emit_paused("t-1", current_step="supervisor", paused_at=...)
        await sse_event_emitter.emit_hitl_interrupt("t-1", tool="shutdown_device", args={...})

    Attributes:
        SUBSCRIBER_QUEUE_MAXSIZE: 类属性，单个队列最大长度。
    """

    #: 单个订阅者队列最大长度（防止慢消费者拖垮 emitter）
    SUBSCRIBER_QUEUE_MAXSIZE: int = SUBSCRIBER_QUEUE_MAXSIZE

    def __init__(self) -> None:
        # thread_id -> set[asyncio.Queue[SSEEvent]]
        # 用 set 而非 list：unsubscribe O(1)，避免重复订阅累积
        self._subscribers: dict[str, set[asyncio.Queue[SSEEvent]]] = {}
        # 保护 _subscribers 字典读写（subscribe / unsubscribe / 读 snapshot）
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── 订阅管理 ──────────────────────────────────────

    async def subscribe(self, thread_id: str) -> asyncio.Queue[SSEEvent]:
        """订阅某 thread 的事件，返回专属 ``asyncio.Queue``。

        调用方负责：
        1. ``await queue.get()`` 消费事件（事件不会自动消失）
        2. 断开时调 :py:meth:`unsubscribe` 清理（推荐在 ``finally`` 中）

        Args:
            thread_id: 目标会话 ID。

        Returns:
            该订阅者专属的 ``asyncio.Queue[SSEEvent]``。
        """
        q: asyncio.Queue[SSEEvent] = asyncio.Queue(
            maxsize=self.SUBSCRIBER_QUEUE_MAXSIZE
        )
        async with self._lock:
            self._subscribers.setdefault(thread_id, set()).add(q)
        logger.debug(
            "SSEEventEmitter.subscribe: thread_id={}, total_subscribers={}",
            thread_id,
            self._subscriber_count_locked(thread_id),
        )
        return q

    async def unsubscribe(
        self, thread_id: str, q: asyncio.Queue[SSEEvent]
    ) -> None:
        """取消订阅：把 queue 从 thread 的订阅集合中移除。

        幂等：同一 (thread_id, q) 重复调不出错。
        副作用：若该 thread 的订阅集合变空，**不会**主动清理 key（保留
        空 set 避免频繁 dict 抖动；下次 subscribe 复用）。

        Args:
            thread_id: 目标会话 ID。
            q: :py:meth:`subscribe` 返回的 queue 引用。
        """
        async with self._lock:
            subs = self._subscribers.get(thread_id)
            if subs is None:
                return
            subs.discard(q)  # discard 幂等；remove 会抛 KeyError
            if not subs:
                # 保留空 set 占位，避免下次 subscribe 重建 dict entry
                # 真正清理在 process 退出时由 GC 完成
                pass

    def _subscriber_count_locked(self, thread_id: str) -> int:
        """内部：在已持锁上下文读 subscriber 数。"""
        return len(self._subscribers.get(thread_id, set()))

    # ── 内部 emit 核心 ─────────────────────────────────

    async def emit(
        self,
        event_type: str,
        thread_id: str,
        payload: dict[str, Any],
    ) -> int:
        """推送事件到该 thread 的所有订阅者。

        Args:
            event_type: 6 个合法 type 之一（**运行时校验**，不在 enum 抛 ``ValueError``）。
            thread_id:  目标会话 ID。
            payload:    事件业务字段（会原样传给订阅者）。

        Returns:
            实际成功 ``put_nowait`` 的订阅者数（用于 stats / 日志）;
            队列满的订阅者**不**计入（silently drop）。

        Raises:
            ValueError: ``event_type`` 不在 6 个合法 type 集合中。
        """
        if event_type not in _VALID_EVENT_TYPES:
            raise ValueError(
                f"Invalid SSE event_type {event_type!r}; "
                f"must be one of {_VALID_EVENT_TYPES}"
            )

        # 锁内：拿 snapshot（避免 list(set()) 期间 set 被改）
        async with self._lock:
            subs = list(self._subscribers.get(thread_id, set()))

        if not subs:
            logger.debug(
                "SSEEventEmitter.emit: thread_id={}, type={}, "
                "no subscribers, dropping",
                thread_id, event_type,
            )
            return 0

        event = SSEEvent(
            type=event_type,
            thread_id=thread_id,
            payload=dict(payload),  # 浅拷贝防外部 mutate
            timestamp=time.time(),
        )

        delivered = 0
        for q in subs:
            try:
                q.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                # 订阅者消费太慢 → silently drop（不阻塞 emit 链路）
                logger.warning(
                    "SSEEventEmitter.emit: subscriber queue full, "
                    "dropping event (thread_id={}, type={})",
                    thread_id, event_type,
                )
        logger.debug(
            "SSEEventEmitter.emit: thread_id={}, type={}, "
            "delivered={}/{}",
            thread_id, event_type, delivered, len(subs),
        )
        return delivered

    # ── 6 个类型化 helper（架构 §2.5.2 + 主理人决策 #6）────

    async def emit_paused(
        self,
        thread_id: str,
        current_step: str | None,
        paused_at: str,
    ) -> int:
        """``reasoning_paused``：后端确认 pause。

        Args:
            thread_id:   会话 ID。
            current_step: 暂停时所在节点（``supervisor`` / agent 名）；可能 None。
            paused_at:   ISO 8601 字符串。
        """
        return await self.emit(
            "reasoning_paused",
            thread_id,
            {"current_step": current_step, "paused_at": paused_at},
        )

    async def emit_resumed(
        self,
        thread_id: str,
        resumed_at: str,
    ) -> int:
        """``reasoning_resumed``：后端确认 resume。"""
        return await self.emit(
            "reasoning_resumed",
            thread_id,
            {"resumed_at": resumed_at},
        )

    async def emit_step_replaced(
        self,
        thread_id: str,
        step_index: int,
        old_content_hash: str,
        new_content_hash: str,
    ) -> int:
        """``step_replaced``：rewind 后从该 step 重新生成。

        Args:
            thread_id:         会话 ID。
            step_index:        被替换的 step 索引（0-based）。
            old_content_hash:  替换前内容 hash（前端用于 diff 高亮）。
            new_content_hash:  替换后内容 hash。
        """
        return await self.emit(
            "step_replaced",
            thread_id,
            {
                "step_index": step_index,
                "old_content_hash": old_content_hash,
                "new_content_hash": new_content_hash,
            },
        )

    async def emit_hitl_interrupt(
        self,
        thread_id: str,
        tool: str | None,
        args: dict[str, Any] | None,
    ) -> int:
        """``hitl_interrupt``：后端请求 HITL 审批。

        Args:
            thread_id: 会话 ID。
            tool:      触发拦截的工具名（如 ``shutdown_device``）。
            args:      工具调用参数 dict；可能 None（用户已审批时通常不传）。
        """
        return await self.emit(
            "hitl_interrupt",
            thread_id,
            {"tool": tool, "args": args or {}},
        )

    async def emit_hitl_resolved(
        self,
        thread_id: str,
        decision: str,
        resolved_at: str,
    ) -> int:
        """``hitl_resolved``：用户审批后。

        Args:
            thread_id:  会话 ID。
            decision:   ``approved`` / ``rejected`` / ``edit_approved``。
            resolved_at: ISO 8601 字符串。
        """
        return await self.emit(
            "hitl_resolved",
            thread_id,
            {"decision": decision, "resolved_at": resolved_at},
        )

    async def emit_reasoning_error(
        self,
        thread_id: str,
        error: str | Exception,
        recoverable: bool,
    ) -> int:
        """``reasoning_error``：推理异常。

        Args:
            thread_id:   会话 ID。
            error:       异常对象或字符串（会被 str() 转换）。
            recoverable: 是否可恢复（前端用于决定是否展示"重试"按钮）。
        """
        return await self.emit(
            "reasoning_error",
            thread_id,
            {"error": str(error), "recoverable": recoverable},
        )

    # ── 测试 / 诊断 helper ───────────────────────────

    def get_subscriber_count(self, thread_id: str) -> int:
        """同步读某 thread 的订阅者数（仅用于 stats / 测试，**不**加锁）。

        生产代码请勿依赖此值做业务决策（可能与实际短暂不一致）。
        """
        return len(self._subscribers.get(thread_id, set()))

    def get_total_thread_count(self) -> int:
        """同步读当前活跃 thread 数（仅用于 stats / 测试）。"""
        return len(self._subscribers)


# ═══════════════════════════════════════════════════════
# 模块级单例（架构 §2.5.4 + §3.1 推荐）
# ═══════════════════════════════════════════════════════

#: 全局单例（FastAPI 单进程内共享）
sse_event_emitter: SSEEventEmitter = SSEEventEmitter()


# ═══════════════════════════════════════════════════════
# 公开导出
# ═══════════════════════════════════════════════════════

__all__ = [
    "SSEEvent",
    "SSEEventEmitter",
    "SSEEventType",
    "SUBSCRIBER_QUEUE_MAXSIZE",
    "sse_event_emitter",
]
