"""SessionLockManager 单元测试（T01 自带 · 架构 §6 T01 验收）。

覆盖：
1. 基础 acquire / release（context manager）
2. 同 thread_id 串行化（第 2 个 acquire 超时）
3. 不同 thread_id 并发不阻塞
4. ``cleanup()`` 后可重新 acquire
5. ``get_active_count()`` 计数准确
6. ``SessionLockTimeout`` 异常属性
7. 自定义 timeout 生效
8. ``get_lock_count()`` 字典大小正确
9. 模块级单例 ``session_lock_manager`` 存在
10. ``ValueError`` on 非法 timeout

运行：
    cd /path/to/GridMind
    python -m pytest tests/test_session_lock.py -v
或：
    PYTHONPATH=. python tests/test_session_lock.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from api.services.session_lock import (
    DEFAULT_LOCK_TIMEOUT_S,
    SessionLockManager,
    SessionLockTimeout,
    session_lock_manager,
)


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture
def mgr() -> SessionLockManager:
    """每个 test 独立的 SessionLockManager 实例（隔离状态）。"""
    return SessionLockManager(default_timeout_s=2.0)


# ═══════════════════════════════════════════════════════
# 1. 基础 acquire / release
# ═══════════════════════════════════════════════════════


def test_basic_acquire_and_release(mgr: SessionLockManager) -> None:
    """基础场景：acquire 后 release，不抛错。"""
    assert mgr.get_lock_count() == 0
    with mgr.acquire("t-1"):
        # 持锁期间：active=1, lock_count=1
        assert mgr.get_active_count() == 1
        assert mgr.get_lock_count() == 1
    # 释放后：active=0, lock_count 仍为 1（cleanup 才会删）
    assert mgr.get_active_count() == 0
    assert mgr.get_lock_count() == 1
    print("[PASS] basic acquire/release")


# ═══════════════════════════════════════════════════════
# 2. 同 thread_id 串行化（第 2 个超时）
# ═══════════════════════════════════════════════════════


def test_same_thread_id_serializes(mgr: SessionLockManager) -> None:
    """同 thread_id 第二个 acquire 必须超时（架构 §2.6.3 核心）。"""
    barrier = threading.Event()
    second_acquired = threading.Event()
    second_error: list[Exception] = []

    def hold_lock_for_a_while() -> None:
        with mgr.acquire("t-same", timeout=5.0):
            barrier.set()  # 通知主线程我已持锁
            # 模拟"长时间写操作"
            time.sleep(0.3)
            second_acquired.set()

    t = threading.Thread(target=hold_lock_for_a_while)
    t.start()
    try:
        # 等待线程 A 拿到锁
        barrier.wait(timeout=2.0)
        assert barrier.is_set(), "线程 A 未能在 2s 内拿到锁"

        # 主线程尝试拿同一个 lock —— 必须超时
        start = time.monotonic()
        with pytest.raises(SessionLockTimeout) as exc_info:
            with mgr.acquire("t-same", timeout=0.2):
                pass
        elapsed = time.monotonic() - start
        # 验证：抛的异常属性正确
        assert exc_info.value.thread_id == "t-same"
        assert exc_info.value.timeout == 0.2
        # 验证：实际等待时长 ≈ timeout（不阻塞太久）
        assert 0.15 <= elapsed <= 1.0, f"elapsed={elapsed:.3f}s 异常"
    finally:
        second_acquired.wait(timeout=3.0)
        t.join(timeout=3.0)

    print("[PASS] same thread_id serializes (2nd acquire times out)")


# ═══════════════════════════════════════════════════════
# 3. 不同 thread_id 并发不阻塞
# ═══════════════════════════════════════════════════════


def test_different_thread_ids_do_not_block(mgr: SessionLockManager) -> None:
    """不同 thread_id 互不干扰，可并发。"""
    both_acquired = threading.Event()

    def worker(tid: str) -> None:
        with mgr.acquire(tid, timeout=1.0):
            time.sleep(0.1)
            # 标记：在自身持锁期间，对面线程**不**应阻塞到我释放
            both_acquired.set()

    t_a = threading.Thread(target=worker, args=("t-A",))
    t_b = threading.Thread(target=worker, args=("t-B",))
    t_a.start()
    t_b.start()
    t_a.join(timeout=3.0)
    t_b.join(timeout=3.0)
    assert both_acquired.is_set(), "B 线程未能在 A 持锁期间拿到自己的锁"
    # 此时 active 应为 0（双方都释放完）
    assert mgr.get_active_count() == 0
    # lock_count 应为 2（两个独立 lock 对象）
    assert mgr.get_lock_count() == 2
    print("[PASS] different thread_ids do not block each other")


# ═══════════════════════════════════════════════════════
# 4. cleanup 后可重新 acquire
# ═══════════════════════════════════════════════════════


def test_cleanup_then_reacquire(mgr: SessionLockManager) -> None:
    """``cleanup()`` 后再 acquire 仍正常工作（验证非僵尸状态）。"""
    with mgr.acquire("t-cleanup"):
        assert mgr.get_lock_count() == 1
    mgr.cleanup("t-cleanup")
    assert mgr.get_lock_count() == 0

    # 重新拿 —— 必须成功
    with mgr.acquire("t-cleanup", timeout=0.5):
        assert mgr.get_lock_count() == 1
    print("[PASS] cleanup + reacquire works")


def test_cleanup_nonexistent_is_silent(mgr: SessionLockManager) -> None:
    """清理不存在的 thread_id 不抛错（架构 §2.6.4 契约）。"""
    mgr.cleanup("never-existed")
    mgr.cleanup("also-never-existed")
    assert mgr.get_lock_count() == 0
    print("[PASS] cleanup nonexistent is silent")


# ═══════════════════════════════════════════════════════
# 5. get_active_count 准确
# ═══════════════════════════════════════════════════════


def test_active_count_with_nested_locks(mgr: SessionLockManager) -> None:
    """多 thread_id 同时持锁时，``get_active_count()`` 准确。"""
    with mgr.acquire("t-1"):
        assert mgr.get_active_count() == 1
        with mgr.acquire("t-2"):
            assert mgr.get_active_count() == 2
            with mgr.acquire("t-3"):
                assert mgr.get_active_count() == 3
            assert mgr.get_active_count() == 2
        assert mgr.get_active_count() == 1
    assert mgr.get_active_count() == 0
    print("[PASS] active_count tracks nested acquires correctly")


# ═══════════════════════════════════════════════════════
# 6. SessionLockTimeout 异常属性
# ═══════════════════════════════════════════════════════


def test_session_lock_timeout_attributes() -> None:
    """``SessionLockTimeout`` 含 ``thread_id`` + ``timeout`` 属性（API 层依赖）。"""
    e = SessionLockTimeout("abc-123", 3.5)
    assert e.thread_id == "abc-123"
    assert e.timeout == 3.5
    # message 包含关键信息，便于日志排查
    assert "abc-123" in str(e)
    assert "3.5" in str(e)
    print("[PASS] SessionLockTimeout has thread_id/timeout attrs + readable msg")


# ═══════════════════════════════════════════════════════
# 7. 自定义 timeout
# ═══════════════════════════════════════════════════════


def test_custom_default_timeout() -> None:
    """``__init__`` 自定义 default_timeout_s 在 ``acquire(timeout=None)`` 时生效。"""
    mgr = SessionLockManager(default_timeout_s=0.1)
    assert mgr.get_default_timeout() == 0.1

    # 线程 A 持锁 0.3s
    barrier = threading.Event()
    def hold():
        with mgr.acquire("t-cust"):
            barrier.set()
            time.sleep(0.3)
    t = threading.Thread(target=hold)
    t.start()
    try:
        barrier.wait(timeout=1.0)
        start = time.monotonic()
        with pytest.raises(SessionLockTimeout) as exc:
            with mgr.acquire("t-cust", timeout=None):  # None = 用 default 0.1
                pass
        elapsed = time.monotonic() - start
        assert exc.value.timeout == 0.1
        assert elapsed < 0.25, f"default timeout 未生效：elapsed={elapsed:.3f}s"
    finally:
        t.join(timeout=2.0)
    print("[PASS] custom default_timeout_s works")


# ═══════════════════════════════════════════════════════
# 8. 字典大小（lazy 创建）
# ═══════════════════════════════════════════════════════


def test_lazy_lock_creation(mgr: SessionLockManager) -> None:
    """锁是 lazy 创建的，未 acquire 的 thread_id 不在字典中。"""
    assert mgr.get_lock_count() == 0
    mgr.cleanup("never-acquired")  # 也不应创建
    assert mgr.get_lock_count() == 0
    with mgr.acquire("t-lazy"):
        assert mgr.get_lock_count() == 1
    with mgr.acquire("t-lazy"):  # 第二次：复用同 lock，count 不变
        assert mgr.get_lock_count() == 1
    print("[PASS] locks are lazily created")


# ═══════════════════════════════════════════════════════
# 9. 模块级单例
# ═══════════════════════════════════════════════════════


def test_module_level_singleton_exists() -> None:
    """``session_lock_manager`` 是模块级单例（架构 §T04 详细工作清单 #2）。"""
    assert session_lock_manager is not None
    assert isinstance(session_lock_manager, SessionLockManager)
    # 默认 timeout = 5.0（架构 §2.6.3 主理人决策 #7）
    assert session_lock_manager.get_default_timeout() == DEFAULT_LOCK_TIMEOUT_S
    assert DEFAULT_LOCK_TIMEOUT_S == 5.0
    print("[PASS] session_lock_manager singleton with 5s default timeout")


# ═══════════════════════════════════════════════════════
# 10. ValueError on 非法 timeout
# ═══════════════════════════════════════════════════════


def test_negative_timeout_raises_value_error(mgr: SessionLockManager) -> None:
    """``timeout < 0`` 必须抛 ValueError（防御性）。"""
    with pytest.raises(ValueError, match="timeout must be >= 0"):
        with mgr.acquire("t-neg", timeout=-1.0):
            pass
    print("[PASS] negative timeout raises ValueError")


# ═══════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口：兼容 ``python tests/test_session_lock.py``。"""
    import traceback

    mgr_fixture = SessionLockManager(default_timeout_s=2.0)
    # 修正：手动注入 fixture
    globals()["mgr"] = mgr_fixture

    tests = [
        test_basic_acquire_and_release,
        test_same_thread_id_serializes,
        test_different_thread_ids_do_not_block,
        test_cleanup_then_reacquire,
        test_cleanup_nonexistent_is_silent,
        test_active_count_with_nested_locks,
        test_session_lock_timeout_attributes,
        test_custom_default_timeout,
        test_lazy_lock_creation,
        test_module_level_singleton_exists,
        test_negative_timeout_raises_value_error,
    ]
    passed, failed = 0, 0
    for t in tests:
        name = t.__name__
        try:
            t(mgr_fixture) if "mgr" in t.__code__.co_varnames else t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)
    print("ALL SESSION LOCK TESTS PASSED ✅")


if __name__ == "__main__":
    _run_all()
