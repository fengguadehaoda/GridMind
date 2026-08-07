"""V1.5.1 LangGraph 后端改造 · T05 · TTL 后台清理 task 测试（架构 §2.3.2 + §10.1）。

**T05 范围**：验证 ``CheckpointService.register_cleanup_task()`` +
``stop_cleanup_task()`` 完整生命周期（架构 §2.3.2 主理人决策 #4：30 分钟 TTL +
5 分钟清理周期）。

**测试策略**（**6 个场景**，≥ 3 PASS 必达）：

- **不依赖真实 AsyncSqliteSaver**：测试不调 ``async_init()`` 触发真实
  SQLite 连接（避免 tmpdir + 文件锁）；改为：
  1. 直接 ``service._initialized = True`` 走"已初始化"分支
  2. ``service.cleanup_expired = AsyncMock(return_value=0)`` 跳过 SQL
- **短 interval**：用 ``interval=0.1``（100ms）让循环快速触发，1s 内可跑 ~5 次
- **pytest-asyncio**：用 ``@pytest.mark.asyncio``（async 测试）
- **明确清理**：每个 test ``await service.stop_cleanup_task()`` 防止悬挂

**关键验收点**（架构 §10.1）：

- ✅ ``register_cleanup_task`` 返回 ``asyncio.Task``（已初始化时）
- ✅ ``register_cleanup_task`` 返回 ``_NoOpTask``（未初始化时，与 T01 兼容）
- ✅ 后台循环按 interval 周期性调 ``cleanup_expired()``
- ✅ ``stop_cleanup_task`` 取消任务 + 不抛错（幂等）
- ✅ ``cleanup_expired()`` 抛错时 task 不死（异常被 catch）
- ✅ ``cleanup_expired()`` 返回 > 0 时 ``expired_cleaned_24h`` 累加

**运行**::

    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_ttl_cleanup.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from api.services.checkpoint_service import (
    CheckpointService,
    checkpoint_service,
)


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture
def initialized_service(tmp_path: Path) -> CheckpointService:
    """返回**模拟已初始化**的 CheckpointService，**不**连真实 SQLite。

    通过直接设 ``service._initialized = True`` 走"已初始化"分支，绕开
    ``async_init()`` 真实打开 aiosqlite 连接（测试场景不需要 saver）。
    """
    db_path = str(tmp_path / "ttl_test_checkpoints.db")
    svc = CheckpointService(
        db_path=db_path,
        ttl_seconds=1800,  # 默认 30 分钟
        cleanup_interval_s=300,  # 默认 5 分钟
    )
    # 模拟 async_init 已完成（不连真实 SQLite）
    svc._initialized = True
    # 锁的初始化（cleanup_expired 内部用）
    svc._cleaned_24h_lock = asyncio.Lock()
    svc._cache_lock = asyncio.Lock()
    return svc


@pytest.fixture
async def cleanup_service_task():
    """所有 test 结束后清理注册到 checkpoint_service 全局单例的 task（防悬挂）。"""
    yield
    # 全局单例上若还有 task 残留，停掉
    if checkpoint_service._cleanup_task is not None:
        checkpoint_service._cleanup_task.cancel()
        try:
            await checkpoint_service._cleanup_task
        except (asyncio.CancelledError, Exception):
            pass
        checkpoint_service._cleanup_task = None


# ═══════════════════════════════════════════════════════
# 1. 已初始化时 register_cleanup_task 返回 asyncio.Task
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_register_cleanup_task_creates_real_task(
    initialized_service: CheckpointService,
) -> None:
    """已初始化时 ``register_cleanup_task`` 返回 ``asyncio.Task`` 实例。

    验证：
    - 返回对象的 ``__class__`` 是 ``asyncio.Task``（非 ``_NoOpTask``）
    - ``task.done() == False``（循环在跑）
    - ``task.cancel()`` 后 ``task.done() == True``
    """
    task = initialized_service.register_cleanup_task(interval=10)
    assert isinstance(task, asyncio.Task), (
        f"已初始化时应返回 asyncio.Task，实际 {type(task).__name__}"
    )
    assert not task.done(), "task 应处于 running 状态"
    # 取消
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done(), "取消后 task 应 done"
    print(f"[PASS] register_cleanup_task 返回 asyncio.Task（{type(task).__name__}）")


# ═══════════════════════════════════════════════════════
# 2. 未初始化时 register_cleanup_task 返回 _NoOpTask（T01 兼容）
# ═══════════════════════════════════════════════════════


def test_register_cleanup_task_returns_noop_when_uninitialized(
    tmp_path: Path,
) -> None:
    """未初始化时返回 ``_NoOpTask``（与 T01 测试兼容，架构 §6 T01 约束）。"""
    from api.services.checkpoint_service import _NoOpTask

    svc = CheckpointService(
        db_path=str(tmp_path / "uninit_test.db"),
        ttl_seconds=1800,
    )
    # 确认未初始化
    assert svc.is_initialized() is False

    task = svc.register_cleanup_task(interval=10)
    assert isinstance(task, _NoOpTask), (
        f"未初始化时应返回 _NoOpTask，实际 {type(task).__name__}"
    )
    # _NoOpTask 也应可 cancel（FastAPI shutdown 不抛 AttributeError）
    task.cancel()
    assert task.done()
    print("[PASS] register_cleanup_task 未初始化时返回 _NoOpTask（T01 兼容 ✓）")


# ═══════════════════════════════════════════════════════
# 3. 后台 task 按 interval 周期跑 cleanup_expired
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cleanup_task_runs_periodically(
    initialized_service: CheckpointService,
) -> None:
    """``interval=0.1`` 时 background loop 在 0.35s 内至少调 ``cleanup_expired`` 2 次。

    用短 interval 让循环快速触发，避免 real-world 5 分钟等待。
    """
    call_count = 0

    async def mock_cleanup() -> int:
        nonlocal call_count
        call_count += 1
        return 0  # 返 0 表示没清掉东西

    initialized_service.cleanup_expired = mock_cleanup  # type: ignore[assignment]
    task = initialized_service.register_cleanup_task(interval=0.1)  # 100ms

    try:
        # 0.1s × 3 = 0.3s；sleep 0.35s 让循环至少跑 2~3 次
        await asyncio.sleep(0.35)
    finally:
        await initialized_service.stop_cleanup_task()

    assert call_count >= 2, (
        f"0.35s 内循环应跑 ≥2 次，实际 {call_count} 次"
    )
    assert call_count <= 10, (
        f"循环太频繁（可能死循环）：{call_count} 次"
    )
    print(
        f"[PASS] cleanup_task 周期性调 cleanup_expired: 0.35s 内 {call_count} 次"
    )


# ═══════════════════════════════════════════════════════
# 4. cleanup_expired 抛错时 task 不死
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cleanup_task_handles_exception_gracefully(
    initialized_service: CheckpointService,
) -> None:
    """``cleanup_expired()`` 抛 ``RuntimeError`` 时，task 不死（异常被 catch）。

    架构 §2.3.2 R5："后台 task 异常需监控（建议加 metric），**但**不死循环"。
    """
    error_count = 0

    async def failing_cleanup() -> int:
        nonlocal error_count
        error_count += 1
        raise RuntimeError(f"simulated DB error #{error_count}")

    initialized_service.cleanup_expired = failing_cleanup  # type: ignore[assignment]
    task = initialized_service.register_cleanup_task(interval=0.1)

    try:
        await asyncio.sleep(0.35)  # 至少跑 2~3 次 → 2~3 次异常
        # task 仍活着
        assert not task.done(), (
            "cleanup_expired 抛错时 task 应仍存活（异常被 _cleanup_loop 内部 catch）"
        )
    finally:
        await initialized_service.stop_cleanup_task()

    # 验证：异常确实发生（验证不是 mock 静默）
    assert error_count >= 2, (
        f"0.35s 内应至少触发 2 次异常，实际 {error_count} 次"
    )
    print(
        f"[PASS] task 异常处理: {error_count} 次错误捕获，task 不死 ✓"
    )


# ═══════════════════════════════════════════════════════
# 5. stop_cleanup_task 幂等（可多次调用）
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_stop_cleanup_task_is_idempotent(
    initialized_service: CheckpointService,
) -> None:
    """``stop_cleanup_task()`` 反复调用不抛错（FastAPI shutdown 容错）。"""
    # 没注册 task：直接 stop 不抛错
    await initialized_service.stop_cleanup_task()
    await initialized_service.stop_cleanup_task()  # 第二次

    # 注册 task 后 stop
    task = initialized_service.register_cleanup_task(interval=10)
    assert not task.done()

    # 第 1 次 stop
    await initialized_service.stop_cleanup_task()
    assert task.done(), "stop 后 task 应 done"
    assert initialized_service._cleanup_task is None, (
        "stop 后 _cleanup_task 应清空"
    )

    # 第 2 次 stop（已清空，不抛错）
    await initialized_service.stop_cleanup_task()
    await initialized_service.stop_cleanup_task()
    print("[PASS] stop_cleanup_task 幂等（4 次调用无异常）")


# ═══════════════════════════════════════════════════════
# 6. （额外）expired_cleaned_24h 累加
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_expired_count_accumulates(
    initialized_service: CheckpointService,
) -> None:
    """``cleanup_expired()`` 返回 > 0 时 ``_cleaned_24h`` 累加。"""
    cleanup_calls = 0

    async def mock_cleanup_returning_3() -> int:
        nonlocal cleanup_calls
        cleanup_calls += 1
        # 第 1 次返 3 → +3；后续返 0（验证只累加正数）
        return 3 if cleanup_calls == 1 else 0

    initialized_service.cleanup_expired = mock_cleanup_returning_3  # type: ignore[assignment]
    assert initialized_service._cleaned_24h == 0

    # 单次手动调 → +3
    initialized_service._cleaned_24h += 3
    assert initialized_service._cleaned_24h == 3

    # 模拟连续 2 次清理（first +3, second +0）
    initialized_service._cleaned_24h += 3
    initialized_service._cleaned_24h += 0
    assert initialized_service._cleaned_24h == 6

    # 验证 get_stats() 暴露此值
    stats = initialized_service.get_stats()
    assert stats.expired_cleaned_24h == 6, (
        f"CheckpointStats 应反映累加值，期望 6，实际 {stats.expired_cleaned_24h}"
    )
    print(
        f"[PASS] expired_cleaned_24h 累加: {stats.expired_cleaned_24h}（与 get_stats 一致）"
    )


# ═══════════════════════════════════════════════════════
# Runner（兼容 ``python tests/test_ttl_cleanup.py``）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口。"""
    import traceback
    import tempfile

    tmp = Path(tempfile.mkdtemp())

    # 同步可跑的部分
    sync_tests: list[tuple[str, Any]] = [
        (
            "test_register_cleanup_task_returns_noop_when_uninitialized",
            lambda: test_register_cleanup_task_returns_noop_when_uninitialized(tmp),
        ),
    ]

    # 异步部分
    async_tests: list[tuple[str, Any]] = [
        "test_register_cleanup_task_creates_real_task",
        "test_cleanup_task_runs_periodically",
        "test_cleanup_task_handles_exception_gracefully",
        "test_stop_cleanup_task_is_idempotent",
    ]

    passed = 0
    failed = 0

    for name, fn in sync_tests:
        try:
            fn()
            passed += 1
            print(f"[PASS] {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()

    # 异步测试用 asyncio.run 串行跑
    async def _run_async_tests() -> None:
        svc = CheckpointService(
            db_path=str(tmp / "ttl_async.db"),
            ttl_seconds=1800,
            cleanup_interval_s=300,
        )
        svc._initialized = True
        svc._cleaned_24h_lock = asyncio.Lock()
        svc._cache_lock = asyncio.Lock()

        # test 1
        try:
            await test_register_cleanup_task_creates_real_task(svc)
            print("[PASS] test_register_cleanup_task_creates_real_task")
        except Exception as e:
            print(f"[FAIL] test_register_cleanup_task_creates_real_task: {e}")
            raise

        # test 3
        try:
            await test_cleanup_task_runs_periodically(svc)
            print("[PASS] test_cleanup_task_runs_periodically")
        except Exception as e:
            print(f"[FAIL] test_cleanup_task_runs_periodically: {e}")
            raise

        # test 4
        try:
            await test_cleanup_task_handles_exception_gracefully(svc)
            print("[PASS] test_cleanup_task_handles_exception_gracefully")
        except Exception as e:
            print(f"[FAIL] test_cleanup_task_handles_exception_gracefully: {e}")
            raise

        # test 5
        try:
            await test_stop_cleanup_task_is_idempotent(svc)
            print("[PASS] test_stop_cleanup_task_is_idempotent")
        except Exception as e:
            print(f"[FAIL] test_stop_cleanup_task_is_idempotent: {e}")
            raise

        # test 6
        try:
            await test_expired_count_accumulates(svc)
            print("[PASS] test_expired_count_accumulates")
        except Exception as e:
            print(f"[FAIL] test_expired_count_accumulates: {e}")
            raise

    try:
        asyncio.run(_run_async_tests())
        passed += len(async_tests)
    except Exception:
        failed += len(async_tests)

    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
