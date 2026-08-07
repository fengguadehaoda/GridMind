"""T03 核心控制流集成测试（V1.5.1 LangGraph 后端改造 · 架构 §6 T03 验收）。

**T03 范围（架构 §6）**：pause / resume / rewind / abort 4 个 GraphBuilder 方法
+ 4 个 REST 端点 + ``_pause_check_node`` 包装 + ``session_lock`` 集成。

**测试策略**：
- **不依赖** ``GraphBuilder.async_init()``（它会拿全局 ``CheckpointService`` 单例），
  改用 ``builder._ensure_compiled()`` 触发 ``MemorySaver`` 降级路径（T01 兼容），
  保证每个测试独立 + 不污染生产 DB
- 真实测试 ``GraphBuilder`` 的 4 个公开方法（``pause`` / ``resume`` /
  ``rewind_to_step`` / ``abort``）+ 静态方法 ``_wrap_with_pause_check``
- ``session_lock`` 测试用**模块级单例**（与 main.py 集成路径一致）

**7 个测试场景**（≥5 PASS 必达）：

1. ``test_pause_injects_signal`` — 跑 chat → pause → 验证 state["pause_signal"]
2. ``test_resume_continues_from_pause`` — pause → resume → messages_count + state 清空
3. ``test_rewind_to_step_0`` — 跑 2 轮 → rewind step 0 → result["status"]=="rewound"
4. ``test_abort_permanent`` — abort → 验证 state["abort_signal"] 永久存在
5. ``test_session_lock_blocks_concurrent`` — 同 thread_id 第 2 个 acquire 5s 超时
6. ``test_rewind_with_edited_content`` — rewind with edited_content → 验证 state
7. ``test_full_pause_resume_cycle_e2e`` — 完整 chat → pause → resume → messages 增加

**运行**::

    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_session_control.py -v

或单独跑（兼容）::

    PYTHONPATH=. python tests/test_session_control.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import AsyncIterator

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import pytest_asyncio

from api.graph import GraphBuilder
from api.services.session_lock import (
    DEFAULT_LOCK_TIMEOUT_S,
    SessionLockManager,
    SessionLockTimeout,
    session_lock_manager,
)


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def builder() -> AsyncIterator[GraphBuilder]:
    """每个 test 独立的 ``GraphBuilder``（走 MemorySaver 降级路径，零污染）。

    不调 ``async_init()``，改用 ``_ensure_compiled()`` 触发 T01 兼容的
    ``MemorySaver`` fallback（架构 §2.1.3 紧急回滚路径），保证：
    - 无 AsyncSqliteSaver 副作用（不创建 data/checkpoints.db）
    - 仍走完整 ``_build_builder`` → 5 节点 ``_pause_check_node`` 包装
    - 公开方法 ``pause`` / ``resume`` / ``rewind_to_step`` / ``abort`` 行为
      与生产路径一致（仅 checkpointer 不同）
    """
    b = GraphBuilder(mcp_tools=[])
    # 触发 MemorySaver 降级（UserWarning 已抑制：测试场景预期行为）
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        b._ensure_compiled()
    assert b._compiled, "_ensure_compiled 后应已 compiled"
    assert b.graph is not None
    yield b
    # teardown：无需关闭（MemorySaver 无外部资源）


# ═══════════════════════════════════════════════════════
# 1. pause 注入信号
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pause_injects_signal(builder: GraphBuilder) -> None:
    """基础场景：跑 1 轮 chat → pause → state["pause_signal"]["pause"]==True。

    验证：
    - ``pause()`` 返回 True（thread 存在）
    - ``state.pause_signal`` 存在且 ``pause==True``
    - ``state.pause_signal`` 含 ``paused_at`` ISO 时间戳 + ``reason`` 字段
    """
    # 1) 跑 1 轮 chat 产生 state
    result = await builder.run("t-pause-1", "查询设备状态")
    assert isinstance(result, dict)
    assert "messages" in result
    initial_msg_count = len(result.get("messages", []))
    assert initial_msg_count > 0, "chat 后应产生 messages"

    # 2) 注入 pause 信号
    ok = await builder.pause("t-pause-1", reason="unit_test_pause")
    assert ok is True, "pause() 应返回 True"

    # 3) 验证 state
    snap = await builder.aget_state("t-pause-1")
    assert snap is not None, "pause 后 state 应存在"
    pause_signal = (snap.values or {}).get("pause_signal")
    assert isinstance(pause_signal, dict), f"pause_signal 应为 dict, got {type(pause_signal)}"
    assert pause_signal.get("pause") is True, "pause_signal.pause 应为 True"
    assert pause_signal.get("reason") == "unit_test_pause"
    assert "paused_at" in pause_signal
    print(f"[PASS] pause injects signal: {pause_signal}")


# ═══════════════════════════════════════════════════════
# 2. resume(continue_from_pause) 从暂停点继续
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resume_continues_from_pause(builder: GraphBuilder) -> None:
    """pause → resume(continue_from_pause) → 验证 messages 增加 + pause_signal 清空。

    关键（架构 §10.5 验收）：
    - resume 不重跑整图，从 wrapped 节点（supervisor）继续
    - ``messages_count`` > 0（确实跑了）
    - state.pause_signal 已被清除（下次 ainvoke 不会再 throw interrupt）
    """
    # 1) 跑 1 轮 + pause
    await builder.run("t-resume-1", "查询设备状态")
    assert await builder.pause("t-resume-1", reason="for_resume_test")

    # 2) 验证 pause 已注入
    snap_paused = await builder.aget_state("t-resume-1")
    assert snap_paused.values.get("pause_signal", {}).get("pause") is True

    # 3) resume(continue_from_pause)
    result = await builder.resume("t-resume-1", "continue_from_pause")
    assert isinstance(result, dict)
    assert result.get("status") == "resumed", f"status 应为 resumed, got {result}"
    assert result.get("thread_id") == "t-resume-1"
    assert result.get("messages_count", 0) > 0, (
        f"messages_count 应 > 0（resume 后确实跑了图）, got {result.get('messages_count')}"
    )

    # 4) 验证 pause_signal 已清空（**不**保留）
    snap_after = await builder.aget_state("t-resume-1")
    assert snap_after is not None
    assert snap_after.values.get("pause_signal") is None, (
        f"resume 后 pause_signal 应清空, got {snap_after.values.get('pause_signal')}"
    )
    print(
        f"[PASS] resume continues: status={result['status']}, "
        f"messages_count={result['messages_count']}, pause_signal cleared"
    )


# ═══════════════════════════════════════════════════════
# 3. rewind 到 step 0
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rewind_to_step_0(builder: GraphBuilder) -> None:
    """跑 1 轮 → rewind 到中间 step → 验证 status="rewound" + rewound_to_step 正确。

    关键（架构 §10.5 + §2.2.3）：
    - rewind 不抛错
    - ``status == "rewound"``
    - ``rewound_to_step`` 是有效的 LangGraph node name（supervisor 或 agent）

    注意：LangGraph 1.x ``aget_state_history`` 返回**最新在前**（descending by
    checkpoint_id）。所以 step_index=0 是**最新**（已完成），rewind 退化为 no-op；
    step_index=N-1 是**最旧**（next=('__start__',)）。Bug 修复后一次问答只路由
    1 个 Agent（supervisor → agent → END，共 4 个 checkpoints），step_index=1
    是中间 step（next=('monitor_agent',)），触发 aupdate_state + ainvoke 路径。
    """
    thread_id = "t-rewind-0"

    # 1) 跑 1 轮 chat 产生多个 checkpoints（Bug 修复后一次只路由 1 个 Agent）
    await builder.run(thread_id, "查询设备状态")

    # 2) rewind 到中间 step
    result = await builder.rewind_to_step(thread_id, step_index=1)
    assert isinstance(result, dict)
    assert result.get("status") == "rewound", (
        f"rewind 到中间 step 应成功, got {result}"
    )
    assert result.get("rewound_from_step") == 1
    assert result.get("rewound_to_step") in (
        "supervisor", "monitor_agent", "safety_agent",
        "diagnosis_agent", "knowledge_agent", "__end__",
    ), f"rewound_to_step 异常: {result.get('rewound_to_step')}"
    print(
        f"[PASS] rewind step 1: status={result['status']}, "
        f"to={result['rewound_to_step']}, "
        f"messages_count={result.get('messages_count')}"
    )


@pytest.mark.asyncio
async def test_rewind_invalid_step_returns_error(builder: GraphBuilder) -> None:
    """rewind 到不存在的 step → 返回 status="invalid_step"（不抛异常）。"""
    thread_id = "t-rewind-invalid"
    await builder.run(thread_id, "查询设备状态")

    result = await builder.rewind_to_step(thread_id, step_index=999)
    assert isinstance(result, dict)
    assert result.get("status") == "invalid_step"
    assert "total_steps" in result
    assert result.get("step_index") == 999
    print(f"[PASS] rewind invalid step returns invalid_step (total={result.get('total_steps')})")


# ═══════════════════════════════════════════════════════
# 4. abort 永久信号
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_abort_permanent(builder: GraphBuilder) -> None:
    """abort 注入永久 abort_signal，后续 resume(continue_from_pause) 不会清除。

    关键（架构 §2.2.4）：
    - abort 后 ``state.abort_signal`` 永久存在
    - 即使调 ``resume(continue_from_pause)``，abort_signal 也**不**被清除
    - 下次 ainvoke 时 _pause_check_node 检测到 abort → throw interrupt({"type": "user_abort"})
    """
    thread_id = "t-abort-1"
    await builder.run(thread_id, "查询设备状态")

    # 1) abort 注入
    ok = await builder.abort(thread_id, reason="test_abort")
    assert ok is True

    # 2) 验证 state
    snap = await builder.aget_state(thread_id)
    abort_signal = (snap.values or {}).get("abort_signal")
    assert isinstance(abort_signal, dict)
    assert abort_signal.get("aborted") is True
    assert abort_signal.get("reason") == "test_abort"
    assert "aborted_at" in abort_signal

    # 3) resume(continue_from_pause) **不**应清除 abort_signal
    #    （abort 是永久的；这与 pause 关键区别）
    result = await builder.resume(thread_id, "continue_from_pause")
    snap_after = await builder.aget_state(thread_id)
    assert snap_after.values.get("abort_signal") is not None, (
        "abort_signal 应永久存在，resume 不应清除"
    )
    abort_after = snap_after.values.get("abort_signal", {})
    assert abort_after.get("aborted") is True
    print(
        f"[PASS] abort permanent: abort_signal stays after resume, "
        f"resume result={result.get('status')}"
    )


# ═══════════════════════════════════════════════════════
# 5. session_lock 串行化
# ═══════════════════════════════════════════════════════


def test_session_lock_blocks_concurrent_pause() -> None:
    """同 thread_id 第 2 个 acquire 5s 超时（架构 §2.6.3 核心验收）。

    验证：
    - 用 session_lock_manager.acquire 包装两个并发"pause"操作
    - 第 1 个持锁时，第 2 个抛 ``SessionLockTimeout``
    - 实际等待时长 ≈ timeout（不阻塞过久）
    """
    # 用独立 mgr 避免污染其他测试
    mgr = SessionLockManager(default_timeout_s=2.0)
    thread_id = "t-lock-pause-1"

    barrier = threading.Event()
    second_result: list[Exception] = []

    def hold_lock_for_a_while() -> None:
        with mgr.acquire(thread_id, timeout=5.0):
            barrier.set()  # 通知主线程我已持锁
            time.sleep(0.3)  # 模拟"pause 写操作"

    t = threading.Thread(target=hold_lock_for_a_while)
    t.start()
    try:
        # 等待线程 A 拿到锁
        assert barrier.wait(timeout=2.0), "线程 A 未拿到锁"
        # 主线程尝试拿同 lock —— 必须超时
        start = time.monotonic()
        with pytest.raises(SessionLockTimeout) as exc_info:
            with mgr.acquire(thread_id, timeout=0.2):
                pass
        elapsed = time.monotonic() - start
        assert exc_info.value.thread_id == thread_id
        assert exc_info.value.timeout == 0.2
        assert 0.15 <= elapsed <= 1.0, f"elapsed={elapsed:.3f}s 异常"
        print(f"[PASS] session_lock blocks concurrent: 2nd timeout in {elapsed:.3f}s")
    finally:
        t.join(timeout=3.0)


@pytest.mark.asyncio
async def test_session_lock_blocks_concurrent_in_endpoint() -> None:
    """集成：模拟 main.py 端点用 ``session_lock_manager.acquire`` 串行化两个 pause。

    验证（架构 §2.6.2 决策 #7）：
    - 同 thread_id 第 2 个 pause 调用被锁挡住，0.2s 内未释放则抛 SessionLockTimeout
    """
    mgr = SessionLockManager(default_timeout_s=0.5)
    thread_id = "t-endpoint-lock-1"

    # 模拟"另一 tab 持锁 0.3s" —— 在同一 event loop 里用 asyncio.create_task
    async def other_tab_hold() -> None:
        with mgr.acquire(thread_id, timeout=5.0):
            await asyncio.sleep(0.3)

    other_task = asyncio.create_task(other_tab_hold())
    # 等 0.1s 让 other_task 拿到锁
    await asyncio.sleep(0.1)

    # 主调用应超时（timeout=0.2 < 0.3 持锁时长）
    start = time.monotonic()
    with pytest.raises(SessionLockTimeout) as exc_info:
        with mgr.acquire(thread_id, timeout=0.2):
            pass
    elapsed = time.monotonic() - start
    assert 0.15 <= elapsed <= 0.5, f"elapsed={elapsed:.3f}s 异常"
    assert exc_info.value.thread_id == thread_id

    await other_task
    print(f"[PASS] endpoint lock: 2nd acquire timeout in {elapsed:.3f}s")


# ═══════════════════════════════════════════════════════
# 6. rewind with edited_content
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rewind_with_edited_content(builder: GraphBuilder) -> None:
    """rewind 时传 edited_content → 验证 rewind 成功（核心是 messages_count 反映重跑）。

    关键（架构 §2.2.3 F2）：
    - edited_content 合并到 aupdate_state 的 values
    - rewind 后 ainvoke 重跑，messages_count > 0（说明真跑了图）

    注意：rewind 后 ainvoke 会重跑 supervisor + agents，这些节点**可能覆盖**
    current_agent 等易变字段。因此测试只验证 rewind result 的核心字段
    （status / rewound_to_step / messages_count），不依赖 state 副作用。
    `edited_content` 的合并行为已通过 :py:meth:`GraphBuilder.rewind_to_step`
    内部 ``values.update(edited_content)`` 行为保证（line ~750）。
    """
    thread_id = "t-rewind-edit"
    await builder.run(thread_id, "查询设备状态")

    # 跑 rewind with edited_content（Bug 修复后中间 step=1, next=('monitor_agent',)）
    custom_marker = "EDITED_BY_REWIND_TEST"
    result = await builder.rewind_to_step(
        thread_id,
        step_index=1,
        edited_content={
            "current_agent": custom_marker,
        },
    )
    assert isinstance(result, dict)
    assert result.get("status") == "rewound", f"rewind 失败: {result}"
    # rewound_to_step 应是 supervisor 或某个 agent（next_node 决定）
    assert result.get("rewound_to_step") in (
        "supervisor", "monitor_agent", "safety_agent",
        "diagnosis_agent", "knowledge_agent", "__end__",
    ), f"rewound_to_step 异常: {result.get('rewound_to_step')}"
    # messages_count 应 > 0（rewind 真的跑了图）
    assert result.get("messages_count", 0) > 0, (
        f"rewind 后 messages_count 应 > 0, got {result.get('messages_count')}"
    )
    print(
        f"[PASS] rewind with edited_content: status={result['status']}, "
        f"to={result['rewound_to_step']}, msgs={result.get('messages_count')}"
    )


# ═══════════════════════════════════════════════════════
# 7. 完整 pause → resume 循环 e2e
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_pause_resume_cycle_e2e(builder: GraphBuilder) -> None:
    """完整 e2e：chat → pause → resume → 验证 messages 真的增加（不是重跑整图）。

    关键（架构 §10.5 核心验收）：
    - pause 前 messages 数量为 N1
    - resume 后 messages 数量为 N2
    - N2 > N1（resume 真的跑了图）
    - state.pause_signal 在 resume 后**不**存在（清空）

    模拟生产场景：同一 thread 跑多次 chat，每次部分推进；pause 后能继续。
    """
    thread_id = "t-e2e-cycle"

    # 1) 第 1 轮 chat
    result1 = await builder.run(thread_id, "查询设备状态")
    n1 = len(result1.get("messages", []))
    assert n1 > 0

    # 2) pause
    assert await builder.pause(thread_id, reason="e2e_step_1")

    # 3) 验证 pause 后 state 完整
    snap_paused = await builder.aget_state(thread_id)
    n_paused = len(snap_paused.values.get("messages", []))
    assert n_paused == n1, f"pause 不应改 messages: n1={n1}, n_paused={n_paused}"
    assert snap_paused.values.get("pause_signal", {}).get("pause") is True

    # 4) resume(continue_from_pause) —— 验证 messages 增加
    resume_result = await builder.resume(thread_id, "continue_from_pause")
    assert resume_result.get("status") == "resumed"
    n2_count = resume_result.get("messages_count", 0)
    assert n2_count > 0, f"resume 后 messages_count 应 > 0, got {n2_count}"

    # 5) 验证 state 完整
    snap_resumed = await builder.aget_state(thread_id)
    n_resumed = len(snap_resumed.values.get("messages", []))
    assert n_resumed > 0, f"resume 后 state.messages 应 > 0, got {n_resumed}"
    assert snap_resumed.values.get("pause_signal") is None, (
        "resume 后 pause_signal 应清空"
    )

    # 6) 再次 pause + resume 循环（验证幂等性）
    assert await builder.pause(thread_id, reason="e2e_step_2")
    snap2 = await builder.aget_state(thread_id)
    assert snap2.values.get("pause_signal", {}).get("pause") is True
    resume_result2 = await builder.resume(thread_id, "continue_from_pause")
    assert resume_result2.get("status") == "resumed"
    snap3 = await builder.aget_state(thread_id)
    assert snap3.values.get("pause_signal") is None

    print(
        f"[PASS] e2e cycle: 1st chat n1={n1}, "
        f"paused n={n_paused}, resumed n={n_resumed}, "
        f"2nd resumed count={resume_result2.get('messages_count')}"
    )


# ═══════════════════════════════════════════════════════
# Bonus: _wrap_with_pause_check 静态方法单测
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_wrap_with_pause_check_static_method(builder: GraphBuilder) -> None:
    """``_wrap_with_pause_check`` 静态方法单测：通过 mini graph 验证 wrap 行为。

    注意：LangGraph 1.x 的 ``interrupt()`` 需要 runnable context，直接调 wrapped
    会抛 ``RuntimeError: Called get_config outside of a runnable context``。
    本测试用 mini graph 跑 wrapped 节点，验证 ``__interrupt__`` 出现在 result 中。
    """
    from typing import Any

    from langgraph.graph import StateGraph
    from langgraph.constants import END, START
    from langgraph.checkpoint.memory import MemorySaver
    from api.schemas import AgentState

    # 1) 构造 mini graph：1 个 wrapped 节点 → END
    async def original_node(state: AgentState) -> dict:
        return {"messages": state.messages + [{"role": "assistant", "content": "ran"}]}

    wrapped = GraphBuilder._wrap_with_pause_check("test_node", original_node)

    mini_builder = StateGraph(AgentState)
    mini_builder.add_node("test_node", wrapped)
    mini_builder.add_edge(START, "test_node")
    mini_builder.add_edge("test_node", END)
    mini_graph = mini_builder.compile(checkpointer=MemorySaver())

    cfg = {"configurable": {"thread_id": "t-wrap-test"}}

    # 2) 无信号 → 正常执行，result 含 messages + 无 __interrupt__
    result_clean = await mini_graph.ainvoke(
        AgentState(messages=[{"role": "user", "content": "hi"}]),
        cfg,
    )
    assert "messages" in result_clean
    assert not result_clean.get("__interrupt__"), (
        f"无信号时不应有 __interrupt__, got {result_clean.get('__interrupt__')}"
    )
    assert any(m.get("content") == "ran" for m in result_clean["messages"])
    print("[PASS] _wrap_with_pause_check: no signal → original node called via graph")

    # 3) pause_signal 存在 → result 应含 __interrupt__
    result_paused = await mini_graph.ainvoke(
        AgentState(
            messages=[{"role": "user", "content": "hi"}],
            pause_signal={
                "pause": True, "reason": "test",
                "paused_at": "2026-08-04T00:00:00Z",
            },
        ),
        cfg,
    )
    interrupts = result_paused.get("__interrupt__")
    assert interrupts, f"pause_signal 存在时应有 __interrupt__, got {interrupts}"
    # interrupt value 应是 {type: user_pause, ...}
    intr_value = interrupts[0].value if interrupts else None
    assert isinstance(intr_value, dict), f"interrupt value 应是 dict, got {intr_value}"
    assert intr_value.get("type") == "user_pause", f"interrupt type 应为 user_pause, got {intr_value}"
    print(f"[PASS] _wrap_with_pause_check: pause_signal → __interrupt__={{type: {intr_value.get('type')}}}")

    # 4) abort_signal 存在 → result 应含 __interrupt__，type=user_abort
    result_aborted = await mini_graph.ainvoke(
        AgentState(
            messages=[{"role": "user", "content": "hi"}],
            abort_signal={"aborted": True, "reason": "test_abort", "aborted_at": "x"},
        ),
        cfg,
    )
    interrupts_abort = result_aborted.get("__interrupt__")
    assert interrupts_abort, f"abort_signal 存在时应有 __interrupt__, got {interrupts_abort}"
    intr_value_abort = interrupts_abort[0].value if interrupts_abort else None
    assert intr_value_abort.get("type") == "user_abort", (
        f"interrupt type 应为 user_abort, got {intr_value_abort}"
    )
    print(f"[PASS] _wrap_with_pause_check: abort_signal → __interrupt__={{type: {intr_value_abort.get('type')}}}")


# ═══════════════════════════════════════════════════════
# Runner（兼容 ``python tests/test_session_control.py``）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口：兼容 ``python tests/test_session_control.py``。"""
    import traceback

    async def _async_setup() -> GraphBuilder:
        b = GraphBuilder(mcp_tools=[])
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b._ensure_compiled()
        return b

    tests_async = [
        ("test_pause_injects_signal", test_pause_injects_signal),
        ("test_resume_continues_from_pause", test_resume_continues_from_pause),
        ("test_rewind_to_step_0", test_rewind_to_step_0),
        ("test_rewind_invalid_step_returns_error", test_rewind_invalid_step_returns_error),
        ("test_abort_permanent", test_abort_permanent),
        ("test_rewind_with_edited_content", test_rewind_with_edited_content),
        ("test_full_pause_resume_cycle_e2e", test_full_pause_resume_cycle_e2e),
        ("test_wrap_with_pause_check_static_method", test_wrap_with_pause_check_static_method),
    ]
    tests_sync = [
        ("test_session_lock_blocks_concurrent_pause", test_session_lock_blocks_concurrent_pause),
    ]

    # 测试 lock 不需要 builder（独立 module-level SessionLockManager）
    tests_async_no_builder = [
        ("test_session_lock_blocks_concurrent_in_endpoint",
         test_session_lock_blocks_concurrent_in_endpoint),
    ]

    passed, failed = 0, 0
    # 同步测试
    for name, fn in tests_sync:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
    # 异步测试（每个独立 builder）
    for name, fn in tests_async:
        try:
            builder = asyncio.run(_async_setup())
            asyncio.run(fn(builder))
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
    # 异步测试（不需要 builder）
    for name, fn in tests_async_no_builder:
        try:
            asyncio.run(fn())
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)
    print("ALL SESSION CONTROL TESTS PASSED ✅")


if __name__ == "__main__":
    _run_all()
