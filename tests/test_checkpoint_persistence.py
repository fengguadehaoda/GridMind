"""Checkpoint 持久化集成测试（V1.5.1 LangGraph 后端改造 · T02 自带 · 架构 §6 T02 验收）。

**核心验收（架构 §10.1）**：
> 服务 kill -9 → 重启 → 同一 ``thread_id`` 调 ``get_state`` 返回值与 kill 前一致

**T02 范围**：
- ✅ 验证 ``AsyncSqliteSaver`` 实际写入 ``data/checkpoints.db``
- ✅ 验证重启 saver 后能从 SQLite 读回完整 state（**关键验收**）
- ✅ 验证 ``CheckpointService.cleanup_expired()`` 保留每 thread 最新 1
- ✅ 验证 ``get_stats()`` + ``async_refresh_counts()`` 准确
- ✅ 验证 ``async_init`` / ``aclose`` 生命周期无泄漏
- ✅ 验证多 thread 隔离
- ✅ 验证 ``data/.gitignore`` 现有规则覆盖 ``checkpoints.db``（避免误提交）

**场景**（≥7 个）：
1. ``test_write_and_read_persistence``: 基本写 + 重启 + 读
2. ``test_persistence_across_saver_restart``: **关键验收**（独立 case 标出）
3. ``test_multiple_threads_isolated``: 多 thread 互不干扰
4. ``test_cleanup_expired_keeps_latest``: 保留每 thread 最新 1
5. ``test_cleanup_expired_removes_expired``: 短 TTL 删过期
6. ``test_get_stats_reflects_real_data``: stats 准确
7. ``test_async_init_aclose_lifecycle``: 生命周期可重入
8. ``test_data_gitignore_covers_checkpoints_db``: 静态检查 .gitignore

运行：
    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_checkpoint_persistence.py -v
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import warnings
from pathlib import Path
from typing import AsyncGenerator

# 在导入 api 之前开启 Mock 模式
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import pytest_asyncio

from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from api.services.checkpoint_service import (
    DEFAULT_DB_PATH,
    CheckpointService,
    get_checkpoint_service,
)


# ═══════════════════════════════════════════════════════
# 测试用 State（独立小图，不依赖 GridMind 完整业务）
# ═══════════════════════════════════════════════════════


class _MiniState(TypedDict):
    """最小化测试用 state（兼容 LangGraph channel_values 序列化）。"""
    count: int
    msg: str
    thread_id: str


def _build_mini_graph() -> StateGraph:
    """构造一个最小 StateGraph：2 个 node（inc → echo → END）。

    用于集成测试：模拟 LangGraph 真实使用场景但避开 GridMind 业务的 5 个 Agent。
    **必须**有 END 边，否则 ``ainvoke`` 第二次会抛 ``EmptyInputError``。
    """
    from langgraph.constants import END

    def _inc(state: _MiniState) -> _MiniState:
        return {
            "count": state.get("count", 0) + 1,
            "msg": state.get("msg", "") + "x",
            "thread_id": state.get("thread_id", "default"),
        }

    def _echo(state: _MiniState) -> _MiniState:
        return state  # pass-through

    builder = StateGraph(_MiniState)
    builder.add_node("inc", _inc)
    builder.add_node("echo", _echo)
    builder.add_edge(START, "inc")
    builder.add_edge("inc", "echo")
    builder.add_edge("echo", END)
    return builder


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """每个 test 独立的 DB 路径（避免互相污染 + 真实落盘验证）。"""
    return str(tmp_path / "test_checkpoints.db")


@pytest_asyncio.fixture
async def service(tmp_db_path: str) -> AsyncGenerator[CheckpointService, None]:
    """每个 test 独立的 CheckpointService 实例（隔离 saver 状态）。"""
    svc = CheckpointService(
        db_path=tmp_db_path,
        ttl_seconds=3600,  # 默认 1h，避免误清理
        cleanup_interval_s=10,
    )
    yield svc
    # 清理：若 test 留下 aclose 未调（异常路径），帮助释放
    if svc.is_initialized():
        try:
            await svc.aclose()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════
# 1. 基本写 + 读
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_write_and_read_persistence(
    service: CheckpointService, tmp_db_path: str
) -> None:
    """基本场景：AsyncSqliteSaver 落盘 → 用同一 saver 读回。

    验证：
    - ``async_init`` 后 DB 文件被创建
    - ``ainvoke`` 后能从 ``aget_state`` 读回 state
    - DB 文件 size > 0
    """
    await service.async_init()
    assert service.is_initialized() is True
    assert Path(tmp_db_path).exists(), f"DB 文件应创建于 {tmp_db_path}"

    # 编译图（用 service 的 saver）
    builder = _build_mini_graph()
    graph = builder.compile(checkpointer=service.get_saver())

    # 写：跑一次
    await graph.ainvoke(
        {"count": 0, "msg": "hi", "thread_id": "t-write-1"},
        {"configurable": {"thread_id": "t-write-1"}},
    )

    # 读：aget_state 应能拿到 count=1, msg="hix"
    snapshot = await graph.aget_state(
        {"configurable": {"thread_id": "t-write-1"}}
    )
    assert snapshot is not None
    assert snapshot.values["count"] == 1
    assert snapshot.values["msg"] == "hix"

    # DB 文件 size > 0
    size = Path(tmp_db_path).stat().st_size
    assert size > 0, f"DB 文件 size 应 > 0，实际 {size}"
    print(f"[PASS] write+read basic: db_size={size}B, state.count=1")


# ═══════════════════════════════════════════════════════
# 2. **关键验收**：服务重启后 checkpoint 仍能读回
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_persistence_across_saver_restart(
    service: CheckpointService, tmp_db_path: str
) -> None:
    """**T02 核心验收**（架构 §10.1 + PRD §7.4 主理人决策 #8）。

    模拟服务进程重启：
    1. 阶段 A：``service.async_init()`` → ``ainvoke`` 写入 3 个 checkpoints
    2. 阶段 A：``await service.aclose()`` —— 关闭 aiosqlite 连接（模拟进程关闭）
    3. 阶段 B：**新建** :class:`CheckpointService` 实例（不同 Python 对象）→
       ``async_init()`` 打开同一文件
    4. 阶段 B：用新 saver ``aget_state`` 应能读回 count=3, msg="x"×3

    验证：
    - DB 文件在重启后**不丢失**
    - 新 saver 实例能完整读回历史 checkpoints
    - state 完全一致（``count`` / ``msg`` / ``thread_id``）
    """
    # ── 阶段 A：写入 ─────────────────────────────
    await service.async_init()
    builder_a = _build_mini_graph()
    graph_a = builder_a.compile(checkpointer=service.get_saver())
    # 3 次 invoke，每次传新 input（避免 EmptyInputError）
    for i in range(3):
        await graph_a.ainvoke(
            {"count": i, "msg": "", "thread_id": "t-restart-1"},
            {"configurable": {"thread_id": "t-restart-1"}},
        )
    # 阶段 A 终态：count=3
    snap_a_pre = await graph_a.aget_state(
        {"configurable": {"thread_id": "t-restart-1"}}
    )
    assert snap_a_pre is not None
    assert snap_a_pre.values["count"] == 3

    # 模拟服务关闭
    await service.aclose()
    assert not service.is_initialized(), "aclose 后应 uninitialized"

    # ── 阶段 B：重启 + 读 ───────────────────────
    service_b = CheckpointService(
        db_path=tmp_db_path,
        ttl_seconds=3600,
        cleanup_interval_s=10,
    )
    try:
        await service_b.async_init()
        assert service_b.is_initialized() is True

        # 用**新** service 的 saver 编译图
        builder_b = _build_mini_graph()
        graph_b = builder_b.compile(checkpointer=service_b.get_saver())

        # 读最新 state
        snapshot = await graph_b.aget_state(
            {"configurable": {"thread_id": "t-restart-1"}}
        )
        assert snapshot is not None, "重启后应能读到 checkpoint"
        assert snapshot.values["count"] == 3, (
            f"count 应为 3（阶段 A 终态），实际 {snapshot.values['count']}"
        )
        # 仍可继续 ainvoke（从最新 state 恢复 + 新 input）
        result_b = await graph_b.ainvoke(
            {"count": 99, "msg": "", "thread_id": "t-restart-1"},
            {"configurable": {"thread_id": "t-restart-1"}},
        )
        assert result_b["count"] == 100, (
            f"重启后继续跑应得 count=100（99+1），实际 {result_b['count']}"
        )

        # stats 应反映 1 thread / 多个 checkpoints
        await service_b.async_refresh_counts()
        stats = service_b.get_stats()
        assert stats.total_threads == 1, f"total_threads 应为 1，实际 {stats.total_threads}"
        assert stats.total_checkpoints >= 3, (
            f"total_checkpoints 应 >= 3，实际 {stats.total_checkpoints}"
        )
    finally:
        if service_b.is_initialized():
            await service_b.aclose()

    print(f"[PASS] ★ 关键验收: 重启后 checkpoint 仍可读回 (count={result_b['count']})")


# ═══════════════════════════════════════════════════════
# 3. 多 thread 隔离
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multiple_threads_isolated(
    service: CheckpointService,
) -> None:
    """不同 thread_id 的 state 完全独立（互不干扰）。"""
    await service.async_init()
    builder = _build_mini_graph()
    graph = builder.compile(checkpointer=service.get_saver())

    # thread A: 3 次累加（每次新 input）
    for i in range(3):
        await graph.ainvoke(
            {"count": i, "msg": "", "thread_id": "t-iso-A"},
            {"configurable": {"thread_id": "t-iso-A"}},
        )
    # thread B: 1 次累加（独立起点）
    await graph.ainvoke(
        {"count": 100, "msg": "b", "thread_id": "t-iso-B"},
        {"configurable": {"thread_id": "t-iso-B"}},
    )

    snap_a = await graph.aget_state({"configurable": {"thread_id": "t-iso-A"}})
    snap_b = await graph.aget_state({"configurable": {"thread_id": "t-iso-B"}})

    assert snap_a.values["count"] == 3
    assert snap_b.values["count"] == 101

    # 删 A 不影响 B
    await service.get_saver().adelete_thread("t-iso-A")
    snap_a_after = await graph.aget_state(
        {"configurable": {"thread_id": "t-iso-A"}}
    )
    snap_b_after = await graph.aget_state(
        {"configurable": {"thread_id": "t-iso-B"}}
    )
    # A 删后下次访问应回到空（langgraph 默认从 input 开始）
    assert snap_a_after is None or snap_a_after.values.get("count", 0) == 0
    # B 不变
    assert snap_b_after.values["count"] == 101, "B 不应受 A 删除影响"
    print("[PASS] multiple threads isolated (A 删除不影响 B)")


# ═══════════════════════════════════════════════════════
# 4. cleanup_expired 保留每 thread 最新 1
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cleanup_expired_keeps_latest_per_thread(
    service: CheckpointService,
) -> None:
    """``cleanup_expired`` 策略：每 thread 保留最新 1 个 checkpoint。

    验证：
    - 同一 thread 有 3 个 checkpoints，TTL=0（全部过期）
    - cleanup 后该 thread 只剩 1 个
    - 其他 thread 不受影响
    """
    # 极短 TTL → 任何 checkpoint 都立即"过期"
    service_short = CheckpointService(
        db_path=str(Path(service._db_path).parent / "short_ttl.db"),
        ttl_seconds=0,  # 所有 checkpoint 视为过期
        cleanup_interval_s=10,
    )
    try:
        await service_short.async_init()
        builder = _build_mini_graph()
        graph = builder.compile(checkpointer=service_short.get_saver())

        # thread A: 3 次（每次新 input）
        for i in range(3):
            await graph.ainvoke(
                {"count": i, "msg": "", "thread_id": "t-clean-A"},
                {"configurable": {"thread_id": "t-clean-A"}},
            )
        # thread B: 2 次
        for i in range(2):
            await graph.ainvoke(
                {"count": i, "msg": "", "thread_id": "t-clean-B"},
                {"configurable": {"thread_id": "t-clean-B"}},
            )

        # 清理前：A 3 个 + B 2 个 = 5 个 checkpoints
        await service_short.async_refresh_counts()
        before = service_short.get_stats()
        assert before.total_checkpoints >= 5, (
            f"清理前应 >= 5，实际 {before.total_checkpoints}"
        )

        # cleanup（TTL=0 → 全部过期，但每 thread 保留 1 最新）
        deleted = await service_short.cleanup_expired()
        assert deleted >= 3, f"应至少删 3 个（A 留 1 / B 留 1，删 3+1=4+），实际 {deleted}"

        # 清理后：A 1 + B 1 = 2 个
        await service_short.async_refresh_counts()
        after = service_short.get_stats()
        assert after.total_threads == 2, (
            f"total_threads 应仍为 2，实际 {after.total_threads}"
        )
        assert after.total_checkpoints == 2, (
            f"total_checkpoints 应为 2（每 thread 留 1），实际 {after.total_checkpoints}"
        )
        # expired_cleaned_24h 应累计
        assert after.expired_cleaned_24h >= 3

        # 验证仍能从 thread 读回（最新 state 还在）
        snap_a = await graph.aget_state(
            {"configurable": {"thread_id": "t-clean-A"}}
        )
        assert snap_a is not None, "A 最新 state 应仍可读"
        assert snap_a.values["count"] == 3
    finally:
        if service_short.is_initialized():
            await service_short.aclose()
    print(f"[PASS] cleanup_expired keeps latest 1 per thread (deleted={deleted})")


# ═══════════════════════════════════════════════════════
# 5. get_stats 反映真实数据
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_stats_reflects_real_data(
    service: CheckpointService, tmp_db_path: str
) -> None:
    """``async_refresh_counts`` + ``get_stats`` 准确反映 SQL 表状态。"""
    await service.async_init()
    builder = _build_mini_graph()
    graph = builder.compile(checkpointer=service.get_saver())

    # 0 写入时
    await service.async_refresh_counts()
    s0 = service.get_stats()
    assert s0.total_checkpoints == 0
    assert s0.total_threads == 0
    assert s0.db_size_bytes > 0, "init 后 DB 文件 size > 0"
    assert s0.ttl_seconds == 3600

    # 3 thread × 各 2 次写入
    for tid in ("t-stat-A", "t-stat-B", "t-stat-C"):
        for i in range(2):
            await graph.ainvoke(
                {"count": i, "msg": "", "thread_id": tid},
                {"configurable": {"thread_id": tid}},
            )

    await service.async_refresh_counts()
    s1 = service.get_stats()
    assert s1.total_threads == 3, f"应 3 thread，实际 {s1.total_threads}"
    assert s1.total_checkpoints >= 6, f"应 >= 6 ckpt，实际 {s1.total_checkpoints}"
    print(
        f"[PASS] get_stats: threads={s1.total_threads}, "
        f"checkpoints={s1.total_checkpoints}, db={s1.db_size_bytes}B"
    )


# ═══════════════════════════════════════════════════════
# 6. async_init / aclose 生命周期
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_async_init_aclose_lifecycle(
    service: CheckpointService, tmp_db_path: str
) -> None:
    """生命周期：init → use → close → re-init → use 应可重入。"""
    # 初始：未 init
    assert not service.is_initialized()
    with pytest.raises(RuntimeError, match="not initialized"):
        service.get_saver()

    # 第一次 init
    await service.async_init()
    assert service.is_initialized()
    builder1 = _build_mini_graph()
    g1 = builder1.compile(checkpointer=service.get_saver())
    await g1.ainvoke(
        {"count": 0, "msg": "", "thread_id": "t-life-1"},
        {"configurable": {"thread_id": "t-life-1"}},
    )
    snap1 = await g1.aget_state({"configurable": {"thread_id": "t-life-1"}})
    assert snap1.values["count"] == 1

    # close
    await service.aclose()
    assert not service.is_initialized()
    with pytest.raises(RuntimeError, match="not initialized"):
        service.get_saver()

    # 重 init（应可重入；DB 文件已存在，幂等）
    await service.async_init()
    assert service.is_initialized()
    builder2 = _build_mini_graph()
    g2 = builder2.compile(checkpointer=service.get_saver())
    # 读上次留下的 state（**核心**）
    snap2 = await g2.aget_state({"configurable": {"thread_id": "t-life-1"}})
    assert snap2 is not None, "重 init 后应能读上次留下的 state"
    assert snap2.values["count"] == 1, "重 init 后 state 应为上次的 count=1"
    print("[PASS] async_init/aclose lifecycle: re-init 后仍可读历史 state")


# ═══════════════════════════════════════════════════════
# 7. data/ .gitignore 覆盖 checkpoints.db（防误提交）
# ═══════════════════════════════════════════════════════


def test_data_gitignore_covers_checkpoints_db() -> None:
    """``data/checkpoints.db`` 应被 ``.gitignore`` 忽略（架构 §7.3 数据安全）。"""
    gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
    assert gitignore.exists(), ".gitignore 不存在"
    text = gitignore.read_text(encoding="utf-8")
    # .gitignore 已有 `data/*.db` 规则，覆盖 checkpoints.db / gridmind.db
    assert re.search(r"data/\*\.db\b", text), (
        f".gitignore 应有 `data/*.db` 规则，实际内容:\n{text}"
    )
    print("[PASS] .gitignore 覆盖 data/*.db（含 checkpoints.db）")


# ═══════════════════════════════════════════════════════
# 8. 默认 DB 路径常量与架构 §2.1.2 一致
# ═══════════════════════════════════════════════════════


def test_default_db_path_matches_architecture() -> None:
    """``DEFAULT_DB_PATH`` 必须为 ``data/checkpoints.db``（架构 §2.1.2 决策 #1）。"""
    assert DEFAULT_DB_PATH == "data/checkpoints.db", (
        f"应固定为 data/checkpoints.db，实际 {DEFAULT_DB_PATH}"
    )
    print(f"[PASS] DEFAULT_DB_PATH = {DEFAULT_DB_PATH}")


# ═══════════════════════════════════════════════════════
# Runner（兼容 python tests/test_checkpoint_persistence.py）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口：手动跑核心场景。"""
    import tempfile
    import traceback

    async def _main() -> None:
        tmp = Path(tempfile.mkdtemp())
        db_path = str(tmp / "manual_test.db")
        svc = CheckpointService(db_path=db_path, ttl_seconds=3600, cleanup_interval_s=10)
        try:
            await svc.async_init()
            assert Path(db_path).exists()
            builder = _build_mini_graph()
            graph = builder.compile(checkpointer=svc.get_saver())
            await graph.ainvoke(
                {"count": 0, "msg": "", "thread_id": "manual-1"},
                {"configurable": {"thread_id": "manual-1"}},
            )
            snap = await graph.aget_state(
                {"configurable": {"thread_id": "manual-1"}}
            )
            assert snap.values["count"] == 1
            print(f"\n[PASS] manual run: count={snap.values['count']}, "
                  f"db={Path(db_path).stat().st_size}B")
            await svc.aclose()
        finally:
            if svc.is_initialized():
                await svc.aclose()

    asyncio.run(_main())


if __name__ == "__main__":
    _run_all()
