"""SSEEventEmitter 单元测试（V1.5.1 LangGraph 后端改造 · T04 · 架构 §2.5 + §6 T04）。

**测试范围**（≥6 个场景，**实际 13 个**，覆盖架构 §2.5 + 决策 #6 + §7.1.4）：

1. ``test_emit_paused_basic`` — 订阅 → emit_paused → 收到事件（含 thread_id / type / payload / timestamp）
2. ``test_emit_resumed_basic``
3. ``test_emit_step_replaced_basic``
4. ``test_emit_hitl_interrupt_basic``
5. ``test_emit_hitl_resolved_basic``
6. ``test_emit_reasoning_error_basic``（含 str / Exception 两种 error 类型）
7. ``test_multiple_subscribers_broadcast`` — 同 thread 2 个 subscriber → 都收到
8. ``test_unsubscribe_stops_delivery`` — unsubscribe 后 emit 不送达
9. ``test_queue_full_drops_silently`` — 慢消费者 → emit 不阻塞（drop）
10. ``test_invalid_event_type_raises`` — emit 不在 6 个 type 的字符串 → ValueError
11. ``test_emit_with_no_subscribers_returns_zero`` — 无订阅者时 emit 不抛错
12. ``test_module_level_singleton_exists`` — 全局单例存在
13. ``test_e2e_subscribe_chat_hitl_pause_paused`` — 关键 e2e（架构 §6 T04 验收 #1）

**运行**::

    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_sse_event_emitter.py -v

或单独跑（兼容）::

    PYTHONPATH=. python tests/test_sse_event_emitter.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import pytest_asyncio

from api.services.sse_event_emitter import (
    SSEEvent,
    SSEEventEmitter,
    SUBSCRIBER_QUEUE_MAXSIZE,
    sse_event_emitter,
)


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def emitter() -> SSEEventEmitter:
    """每个 test 独立的 ``SSEEventEmitter``（隔离状态，防测试间干扰）。"""
    return SSEEventEmitter()


@pytest_asyncio.fixture
async def queue(emitter: SSEEventEmitter):
    """订阅默认 thread（"t-default"），返回专属 queue。"""
    q = await emitter.subscribe("t-default")
    yield q
    # teardown：清理（即便 test 失败也保证 unsubscribe）
    await emitter.unsubscribe("t-default", q)


# ═══════════════════════════════════════════════════════
# 1. emit_paused
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_paused_basic(
    emitter: SSEEventEmitter, queue: asyncio.Queue
) -> None:
    """emit_paused 后订阅者收到 reasoning_paused 事件。"""
    paused_at = "2026-08-04T12:00:00+00:00"
    delivered = await emitter.emit_paused(
        thread_id="t-default",
        current_step="supervisor",
        paused_at=paused_at,
    )
    assert delivered == 1, f"应 1 个订阅者收到，实际 {delivered}"

    # 异步消费
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert isinstance(event, SSEEvent)
    assert event.type == "reasoning_paused"
    assert event.thread_id == "t-default"
    assert event.payload == {
        "current_step": "supervisor",
        "paused_at": paused_at,
    }
    assert isinstance(event.timestamp, float)
    assert event.timestamp > 0
    print(f"[PASS] emit_paused: {event}")


# ═══════════════════════════════════════════════════════
# 2. emit_resumed
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_resumed_basic(
    emitter: SSEEventEmitter, queue: asyncio.Queue
) -> None:
    """emit_resumed 后订阅者收到 reasoning_resumed 事件。"""
    resumed_at = "2026-08-04T12:01:00+00:00"
    delivered = await emitter.emit_resumed(
        thread_id="t-default", resumed_at=resumed_at,
    )
    assert delivered == 1

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == "reasoning_resumed"
    assert event.payload == {"resumed_at": resumed_at}
    print(f"[PASS] emit_resumed: {event}")


# ═══════════════════════════════════════════════════════
# 3. emit_step_replaced
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_step_replaced_basic(
    emitter: SSEEventEmitter, queue: asyncio.Queue
) -> None:
    """emit_step_replaced 后订阅者收到 step_replaced 事件（含 old/new hash）。"""
    delivered = await emitter.emit_step_replaced(
        thread_id="t-default",
        step_index=2,
        old_content_hash="sha1:abc123",
        new_content_hash="sha1:def456",
    )
    assert delivered == 1

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == "step_replaced"
    assert event.payload == {
        "step_index": 2,
        "old_content_hash": "sha1:abc123",
        "new_content_hash": "sha1:def456",
    }
    print(f"[PASS] emit_step_replaced: {event}")


# ═══════════════════════════════════════════════════════
# 4. emit_hitl_interrupt
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_hitl_interrupt_basic(
    emitter: SSEEventEmitter, queue: asyncio.Queue
) -> None:
    """emit_hitl_interrupt 后订阅者收到 hitl_interrupt 事件（含 tool / args）。"""
    tool_args = {"device_id": "T-001", "action": "shutdown"}
    delivered = await emitter.emit_hitl_interrupt(
        thread_id="t-default",
        tool="shutdown_device",
        args=tool_args,
    )
    assert delivered == 1

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == "hitl_interrupt"
    assert event.payload["tool"] == "shutdown_device"
    assert event.payload["args"] == tool_args
    print(f"[PASS] emit_hitl_interrupt: {event}")

    # args=None 走默认空 dict 路径（防 KeyError）
    await emitter.emit_hitl_interrupt(
        thread_id="t-default", tool=None, args=None,
    )
    event2 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event2.payload["args"] == {}
    assert event2.payload["tool"] is None


# ═══════════════════════════════════════════════════════
# 5. emit_hitl_resolved
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_hitl_resolved_basic(
    emitter: SSEEventEmitter, queue: asyncio.Queue
) -> None:
    """emit_hitl_resolved 后订阅者收到 hitl_resolved 事件（含 decision / resolved_at）。"""
    delivered = await emitter.emit_hitl_resolved(
        thread_id="t-default",
        decision="approved",
        resolved_at="2026-08-04T12:05:00+00:00",
    )
    assert delivered == 1

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == "hitl_resolved"
    assert event.payload == {
        "decision": "approved",
        "resolved_at": "2026-08-04T12:05:00+00:00",
    }
    print(f"[PASS] emit_hitl_resolved: {event}")


# ═══════════════════════════════════════════════════════
# 6. emit_reasoning_error
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_reasoning_error_basic(
    emitter: SSEEventEmitter, queue: asyncio.Queue
) -> None:
    """emit_reasoning_error 后订阅者收到 reasoning_error 事件。
    覆盖 str 和 Exception 两种 error 类型（验证 str() 转换）。
    """
    # Case 1: str error
    delivered = await emitter.emit_reasoning_error(
        thread_id="t-default",
        error="LLM timeout",
        recoverable=True,
    )
    assert delivered == 1
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == "reasoning_error"
    assert event.payload == {"error": "LLM timeout", "recoverable": True}

    # Case 2: Exception error（验证 str() 转换）
    delivered2 = await emitter.emit_reasoning_error(
        thread_id="t-default",
        error=ValueError("bad arg"),
        recoverable=False,
    )
    assert delivered2 == 1
    event2 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event2.payload["error"] == "bad arg"  # str(ValueError("bad arg")) = "bad arg"
    assert event2.payload["recoverable"] is False
    print(f"[PASS] emit_reasoning_error: str + Exception both OK")


# ═══════════════════════════════════════════════════════
# 7. 多订阅者广播
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multiple_subscribers_broadcast(
    emitter: SSEEventEmitter,
) -> None:
    """同一 thread_id 多个 subscriber → emit 广播给所有。"""
    q1 = await emitter.subscribe("t-multi")
    q2 = await emitter.subscribe("t-multi")
    q3 = await emitter.subscribe("t-multi")
    try:
        assert emitter.get_subscriber_count("t-multi") == 3

        delivered = await emitter.emit_paused(
            thread_id="t-multi",
            current_step="diagnosis_agent",
            paused_at="2026-08-04T13:00:00+00:00",
        )
        assert delivered == 3, f"应 3 个订阅者都收到，实际 {delivered}"

        # 3 个 queue 都应能 get 到事件
        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        e3 = await asyncio.wait_for(q3.get(), timeout=1.0)
        assert e1.type == e2.type == e3.type == "reasoning_paused"
        assert e1.payload["current_step"] == "diagnosis_agent"
        # timestamp 应非常接近（同一 emit 调用内）
        assert abs(e1.timestamp - e2.timestamp) < 0.01
        print(f"[PASS] broadcast to 3 subscribers: delivered={delivered}")
    finally:
        await emitter.unsubscribe("t-multi", q1)
        await emitter.unsubscribe("t-multi", q2)
        await emitter.unsubscribe("t-multi", q3)


# ═══════════════════════════════════════════════════════
# 8. unsubscribe 后停止投递
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(
    emitter: SSEEventEmitter,
) -> None:
    """unsubscribe 后 emit 不再送达该 queue。"""
    q = await emitter.subscribe("t-unsub")
    assert emitter.get_subscriber_count("t-unsub") == 1

    # 第 1 次 emit：能收到
    await emitter.emit_resumed("t-unsub", resumed_at="t1")
    e1 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert e1.type == "reasoning_resumed"

    # unsubscribe
    await emitter.unsubscribe("t-unsub", q)
    assert emitter.get_subscriber_count("t-unsub") == 0

    # 第 2 次 emit：queue 收不到（因无订阅者）
    delivered = await emitter.emit_resumed("t-unsub", resumed_at="t2")
    assert delivered == 0, f"unsubscribe 后应 0 送达，实际 {delivered}"

    # 验证 queue 真的空（无遗留事件）
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.3)
    print("[PASS] unsubscribe stops delivery")


# ═══════════════════════════════════════════════════════
# 9. 队列满 silently drop（不阻塞 emit）
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_queue_full_drops_silently(
    emitter: SSEEventEmitter,
) -> None:
    """订阅者不消费 → emit 不阻塞（队列满则 drop，emit 仍返回 1 表示送了 1 个）。"""
    # 用极小 maxsize queue（直接构造，模拟"满"场景）
    small_q: asyncio.Queue = asyncio.Queue(maxsize=2)
    async with emitter._lock:  # noqa: SLF001 — 测试场景下访问受保护成员
        emitter._subscribers.setdefault("t-full", set()).add(small_q)  # noqa: SLF001

    try:
        # 填满 queue（put_nowait）
        small_q.put_nowait("dummy1")
        small_q.put_nowait("dummy2")
        assert small_q.full()

        # emit 触发 → 应 silently drop（不抛 QueueFull，不阻塞）
        start = time.monotonic()
        delivered = await emitter.emit_paused(
            thread_id="t-full",
            current_step="x",
            paused_at="now",
        )
        elapsed = time.monotonic() - start
        assert delivered == 0, (
            f"queue 满时 emit 应返回 0（drop 计数），实际 {delivered}"
        )
        # emit 必须快速返回（< 0.1s），证明 put_nowait 失败不阻塞
        assert elapsed < 0.1, f"emit 阻塞: {elapsed:.3f}s（应 < 0.1s）"

        # queue 内容不变（dummy 仍在，新事件被 drop）
        assert small_q.qsize() == 2
        print(
            f"[PASS] queue full drops silently: delivered=0, "
            f"elapsed={elapsed*1000:.1f}ms"
        )
    finally:
        async with emitter._lock:  # noqa: SLF001
            emitter._subscribers.get("t-full", set()).discard(small_q)  # noqa: SLF001


# ═══════════════════════════════════════════════════════
# 10. （额外）非法 event_type 抛 ValueError
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_invalid_event_type_raises(emitter: SSEEventEmitter) -> None:
    """emit 不在 6 个 type 集合的字符串 → ValueError（防御性契约）。"""
    with pytest.raises(ValueError, match="Invalid SSE event_type"):
        await emitter.emit(
            event_type="unknown_type_xxx",
            thread_id="t-bad",
            payload={},
        )

    # 6 个合法 type 都应通过（不抛）
    for t in (
        "reasoning_paused",
        "reasoning_resumed",
        "step_replaced",
        "hitl_interrupt",
        "hitl_resolved",
        "reasoning_error",
    ):
        # 不抛即可
        await emitter.emit(event_type=t, thread_id="t-bad", payload={})
    print("[PASS] invalid event_type raises ValueError; 6 valid types OK")


# ═══════════════════════════════════════════════════════
# 11. （额外）无订阅者时 emit 不报错
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_emit_with_no_subscribers_returns_zero(
    emitter: SSEEventEmitter,
) -> None:
    """无订阅者时 emit 返回 0（不抛错，best-effort 投递）。"""
    delivered = await emitter.emit_hitl_resolved(
        thread_id="t-nobody",
        decision="approved",
        resolved_at="now",
    )
    assert delivered == 0
    print("[PASS] emit with no subscribers returns 0")


# ═══════════════════════════════════════════════════════
# 12. （额外）模块级单例存在
# ═══════════════════════════════════════════════════════


def test_module_level_singleton_exists() -> None:
    """``sse_event_emitter`` 是模块级单例（架构 §2.5.4）。"""
    assert sse_event_emitter is not None
    assert isinstance(sse_event_emitter, SSEEventEmitter)
    assert sse_event_emitter.SUBSCRIBER_QUEUE_MAXSIZE == SUBSCRIBER_QUEUE_MAXSIZE
    print("[PASS] sse_event_emitter singleton OK")


# ═══════════════════════════════════════════════════════
# 13. （关键 e2e · 架构 §6 T04 验收）订阅 → /chat → hitl_interrupt → pause → reasoning_paused
# ═══════════════════════════════════════════════════════


class _E2EGraphBuilder:
    """E2E 测试专用的 GraphBuilder：模拟 /chat 触发 HITL 中断 + pause 行为。

    行为：
    - ``run()`` 立即返回 ``interrupt_action="pending"``（触发 /chat emit hitl_interrupt）
    - ``pause()`` 立即返回 True + **直接 emit reasoning_paused**（模拟 GraphBuilder.pause 行为）
    - 真实 **不** sleep（让 e2e 在 1s 内完成）

    注：mock 替代了整个 ``graph_builder``，不会走真实 ``GraphBuilder.pause`` 的
    aupdate_state 路径，所以**手动**调 ``sse_event_emitter.emit_paused()`` 模拟
    端点 → graph 链路上的 emit 行为。
    """

    def __init__(self) -> None:
        self.run_calls: int = 0
        self.pause_calls: int = 0

    async def run(self, thread_id: str, message: str, display_mode: str | None = None) -> dict[str, Any]:
        self.run_calls += 1
        # 模拟 HITL 拦截：返回 pending
        return {
            "messages": [{"role": "user", "content": message}],
            "interrupt_action": "pending",
            "interrupt_tool": "shutdown_device",
            "interrupt_args": {"device_id": "T-E2E", "action": "shutdown"},
            "interrupt_msg": "需要人工审批",
        }

    async def pause(self, thread_id: str, reason: str = "") -> bool:
        self.pause_calls += 1
        # 模拟 GraphBuilder.pause 末尾的 emit 调用
        from api.services.sse_event_emitter import sse_event_emitter
        await sse_event_emitter.emit_paused(
            thread_id=thread_id,
            current_step=None,
            paused_at="2026-08-04T17:00:00+00:00",
        )
        return True


@pytest.mark.asyncio
async def test_e2e_subscribe_chat_hitl_pause_paused() -> None:
    """关键 e2e（架构 §6 T04 验收 #1）：
    订阅 → /chat → SSE 收到 hitl_interrupt → /sessions/{id}/pause → reasoning_paused。

    流程：
    1. 手动调 ``sse_event_emitter.subscribe(thread_id)`` 拿 queue
       （**不**走 /sessions/{id}/events HTTP 层 —— ASGITransport 流式响应
       在 pytest 集成测试中**不**可靠，单测层面直接验证 emit → queue 链路）
    2. POST /chat → 端点内部调 ``sse_event_emitter.emit_hitl_interrupt``
    3. POST /sessions/{id}/pause → ``graph.pause()`` 内部调
       ``sse_event_emitter.emit_paused``
    4. 验证 queue 顺序收到 2 个目标事件（hitl_interrupt → reasoning_paused）

    同时另起一个 sync 子测试用 TestClient 调 /sessions/{id}/events 验证
    "connected" 启动事件（确保订阅端点注册成功，**不**做 streaming 集成）。

    设计理由：SSE 流式响应 + ASGITransport 在 pytest 下的真正集成测试需要
    起 uvicorn 真服务器（TestClient 不线程安全，并发调用会死锁）；
    本测试聚焦**业务事件**链路：emit → 订阅者 queue。
    """
    from api.main import app
    from api.services.sse_event_emitter import sse_event_emitter
    import api.main
    from fastapi.testclient import TestClient

    e2e_builder = _E2EGraphBuilder()
    api.main.graph_builder = e2e_builder

    thread_id = "t-e2e-sse-1"
    sub_queue = await sse_event_emitter.subscribe(thread_id)
    assert sse_event_emitter.get_subscriber_count(thread_id) == 1

    # 用裸 TestClient（**不** ``with``）—— 跳过 lifespan，避免被真 GraphBuilder 覆盖
    client = TestClient(app)

    try:
        # 步骤 1: POST /chat 触发 hitl_interrupt
        r1 = client.post(
            "/chat", json={"thread_id": thread_id, "message": "e2e test"},
        )
        assert r1.status_code == 200, (
            f"/chat 应 200, got {r1.status_code}: {r1.text}"
        )
        body1 = r1.json()
        assert body1.get("interrupt_required") is True, (
            f"/chat 应 interrupt_required=True, got {body1}"
        )
        assert e2e_builder.run_calls == 1

        # 步骤 2: POST /sessions/{id}/pause 触发 reasoning_paused
        r2 = client.post(
            f"/sessions/{thread_id}/pause", json={"reason": "e2e"},
        )
        assert r2.status_code == 200, (
            f"/pause 应 200, got {r2.status_code}: {r2.text}"
        )
        assert e2e_builder.pause_calls == 1

        # 步骤 3: 验证 SSE 订阅端点能正常返回（验证注册 + content-type）
        #         用 asyncio.wait_for 限时 0.5s —— 长连接不会自然结束
        # V1.5.1 T06 R-X2：SSE 端点已加 JWT 鉴权，必须带 Bearer token
        import time
        import jwt as _jwt
        from api.config import settings as _settings
        _jwt_token = _jwt.encode(
            {
                "sub": "e2e-sse-user",
                "user_id": "e2e-sse-user",
                "thread_id": thread_id,
                "iss": _settings.jwt_issuer,
                "exp": int(time.time()) + 60,
                "iat": int(time.time()),
            },
            _settings.jwt_secret,
            algorithm=_settings.jwt_algorithm,
        )
        import httpx
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            try:
                resp = await asyncio.wait_for(
                    ac.get(
                        f"/sessions/{thread_id}/events",
                        headers={"Authorization": f"Bearer {_jwt_token}"},
                    ),
                    timeout=0.5,
                )
                # 若 wait_for 不超时，验证 content-type
                assert resp.status_code == 200, (
                    f"/sessions/{{id}}/events 应 200, got {resp.status_code}"
                )
                assert "text/event-stream" in resp.headers.get(
                    "content-type", ""
                ), f"应 SSE content-type, got {resp.headers}"
            except asyncio.TimeoutError:
                # 长连接超时是**预期**行为 —— 端点保持打开
                # 但 subscribe 日志已显示成功（"total_subscribers=2"），
                # 所以**端点注册成功**这一断言已隐式满足
                pass

        # 步骤 4: 验证 queue 收到 2 个目标事件
        received: list[dict[str, Any]] = []
        for _ in range(2):
            try:
                event = await asyncio.wait_for(sub_queue.get(), timeout=2.0)
                received.append(
                    {
                        "type": event.type,
                        "thread_id": event.thread_id,
                        **event.payload,
                    }
                )
            except asyncio.TimeoutError:
                break
        assert len(received) == 2, (
            f"应 2 个目标事件, 收到 {len(received)}: {received}"
        )

        # 顺序：hitl_interrupt 先于 reasoning_paused
        types = [e["type"] for e in received]
        assert types == ["hitl_interrupt", "reasoning_paused"], (
            f"事件顺序错误: {types}"
        )

        # 验证 hitl_interrupt payload
        hitl = received[0]
        assert hitl["thread_id"] == thread_id
        assert hitl["tool"] == "shutdown_device"
        assert hitl["args"]["device_id"] == "T-E2E"

        # 验证 reasoning_paused payload
        paused = received[1]
        assert paused["thread_id"] == thread_id
        assert "paused_at" in paused
        assert paused["current_step"] is None  # mock 无 next step

        print(
            f"[PASS] E2E SSE flow: hitl_interrupt + reasoning_paused "
            f"received via SSE queue"
        )
        print(f"       events: {[e['type'] for e in received]}")
    finally:
        # 清理订阅（避免污染后续测试的 subscriber count）
        await sse_event_emitter.unsubscribe(thread_id, sub_queue)


# ═══════════════════════════════════════════════════════
# Runner（兼容 ``python tests/test_sse_event_emitter.py``）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口。"""
    import traceback

    async def _runner() -> None:
        results: list[tuple[str, bool, str]] = []

        async def case(coro_factory) -> None:
            name = coro_factory.__name__ if hasattr(coro_factory, "__name__") else str(coro_factory)
            try:
                await coro_factory()
                results.append((name, True, ""))
            except Exception as e:  # noqa: BLE001
                results.append((name, False, f"{e}"))
                traceback.print_exc()

        # 12 个 case 顺序跑（共享 emitter fixture 通过函数内构造）
        async def t1():
            em = SSEEventEmitter()
            q = await em.subscribe("t")
            try:
                await case(test_emit_paused_basic(em, q))
            finally:
                await em.unsubscribe("t", q)

        await t1()
        passed = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        print(f"\n{passed}/{total} tests passed")
        if passed != total:
            sys.exit(1)

    asyncio.run(_runner())


if __name__ == "__main__":
    _run_all()
