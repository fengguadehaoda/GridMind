"""Per-thread_id 锁管理器（V1.5.1 多 Tab 串行化 · T01 骨架）。

设计（架构 §2.6）：
- ``threading.Lock`` per thread_id 字典
- 写路径（pause / resume / rewind / abort / chat）加锁；读路径不加
- 5 秒默认超时，超时抛 :class:`SessionLockTimeout`
- 字典自身用 ``_meta_lock`` 保护（防并发创建同 thread_id 的锁）
- ``cleanup(thread_id)`` 在 session 终止时调用，防字典无限增长

**职责边界（架构 §2.6.5）**：LangGraph 1.2.10 自身对同一 ``thread_id`` 的
``ainvoke`` 串行化，但 ``update_state``（pause/rewind 用）**不**串行化；
本 ``SessionLockManager`` 弥补这一缺口。

**T01 范围**：本模块**仅提供锁机制**，不实现任何业务（pause/rewind 实际逻辑
在 T03，本模块不依赖 LangGraph）。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from loguru import logger


# ═══════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════


class SessionLockTimeout(Exception):
    """获取 session 锁超时异常（架构 §2.6.3）。

    HTTP 层应捕获此异常并返回 503 + ``{"code": "SESSION_LOCKED", ...}``。

    Attributes:
        thread_id: 超时的 thread_id。
        timeout: 实际等待的秒数。
    """

    def __init__(self, thread_id: str, timeout: float) -> None:
        self.thread_id = thread_id
        self.timeout = timeout
        super().__init__(
            f"Failed to acquire session lock for thread_id={thread_id!r} "
            f"within {timeout}s (another tab is operating)"
        )


# ═══════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════

#: 默认锁等待超时（秒，架构 §2.6.3 主理人决策 #7）
DEFAULT_LOCK_TIMEOUT_S: Final[float] = 5.0


# ═══════════════════════════════════════════════════════
# SessionLockManager
# ═══════════════════════════════════════════════════════


class SessionLockManager:
    """per-thread_id 锁管理器（线程安全，进程内）。

    用法::

        mgr = SessionLockManager()
        with mgr.acquire(thread_id="t-1", timeout=5.0):
            do_thing()  # 写操作（pause / rewind / resume / abort）

    线程安全保证：
        - ``_meta_lock`` 保护 ``_locks`` 字典的读写
        - 同一 ``thread_id`` 永远返回同一 ``threading.Lock`` 实例
        - 不同 ``thread_id`` 互不干扰（可并发）

    内存占用（架构 §2.6.4）：
        - 单实例 10 万 thread_id ≈ 10 MB（threading.Lock 对象 + 字典开销）
        - 无需 LRU；可接受
    """

    def __init__(self, default_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S) -> None:
        """初始化锁管理器。

        Args:
            default_timeout_s: ``acquire()`` 默认超时秒数（默认 5.0）。
        """
        self._default_timeout: float = default_timeout_s
        self._locks: dict[str, threading.Lock] = {}
        # 保护 _locks 字典自身的读写（避免并发创建同 thread_id 的锁）
        self._meta_lock: threading.Lock = threading.Lock()
        # 记录当前被持有的 thread_id（用于 active_sessions 统计）
        self._held: set[str] = set()
        self._held_lock: threading.Lock = threading.Lock()

    # ── 公共 API ──────────────────────────────────────

    def acquire(
        self, thread_id: str, timeout: float | None = None
    ) -> "_AcquireContext":
        """获取 thread_id 的写锁（context manager 接口）。

        Args:
            thread_id: 会话线程 ID。
            timeout: 等待秒数（None = 使用 ``__init__`` 时的 default_timeout_s）。

        Returns:
            上下文管理器；在 ``with`` 块内持锁。

        Raises:
            SessionLockTimeout: 超时未获取到锁。
            ValueError: ``timeout`` 非法（< 0）。
        """
        if timeout is None:
            timeout = self._default_timeout
        if timeout < 0:
            raise ValueError(f"timeout must be >= 0, got {timeout}")
        return _AcquireContext(self, thread_id, timeout)

    def cleanup(self, thread_id: str) -> None:
        """清理指定 thread_id 的锁（abort / TTL 过期时调用）。

        注意：仅在**确认 thread_id 不会再被使用**时调用；调用时不应有其他
        线程持有该锁（否则后续 ``acquire`` 会得到新锁，但旧锁仍会被原持有
        线程 release，导致状态错乱）。

        Args:
            thread_id: 要清理的 thread_id（不存在时静默忽略）。
        """
        with self._meta_lock:
            removed = self._locks.pop(thread_id, None)
        if removed is not None:
            logger.debug("SessionLock cleaned up: thread_id={}", thread_id)
        with self._held_lock:
            self._held.discard(thread_id)

    def get_active_count(self) -> int:
        """返回当前持有锁的 thread_id 数（架构 §2.3.3 active_sessions 字段）。"""
        with self._held_lock:
            return len(self._held)

    def get_lock_count(self) -> int:
        """返回当前字典中已创建的总锁数（含未持有）。"""
        with self._meta_lock:
            return len(self._locks)

    def get_default_timeout(self) -> float:
        """返回默认超时秒数。"""
        return self._default_timeout

    # ── 内部 API（供 _AcquireContext 调用）───────────────

    def _get_lock(self, thread_id: str) -> threading.Lock:
        """懒创建并返回 thread_id 对应的锁（线程安全）。"""
        with self._meta_lock:
            lock = self._locks.get(thread_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[thread_id] = lock
            return lock

    def _mark_held(self, thread_id: str) -> None:
        with self._held_lock:
            self._held.add(thread_id)

    def _mark_released(self, thread_id: str) -> None:
        with self._held_lock:
            self._held.discard(thread_id)


# ═══════════════════════════════════════════════════════
# 内部 context manager
# ═══════════════════════════════════════════════════════


class _AcquireContext:
    """``SessionLockManager.acquire()`` 返回的 context manager。

    拆出来是为了让类型签名清晰（``mgr.acquire() -> _AcquireContext``），
    也方便单元测试逐层 mock。
    """

    def __init__(
        self, mgr: SessionLockManager, thread_id: str, timeout: float
    ) -> None:
        self._mgr = mgr
        self._thread_id = thread_id
        self._timeout = timeout
        self._lock: threading.Lock | None = None
        self._acquired: bool = False

    @property
    def thread_id(self) -> str:
        return self._thread_id

    @property
    def timeout(self) -> float:
        return self._timeout

    def __enter__(self) -> "_AcquireContext":
        lock = self._mgr._get_lock(self._thread_id)
        # 注意：threading.Lock.acquire(blocking, timeout) 返回 bool
        acquired = lock.acquire(blocking=True, timeout=self._timeout)
        if not acquired:
            # 超时：抛 SessionLockTimeout
            raise SessionLockTimeout(self._thread_id, self._timeout)
        self._lock = lock
        self._acquired = True
        self._mgr._mark_held(self._thread_id)
        logger.debug(
            "SessionLock acquired: thread_id={}, timeout={}s",
            self._thread_id,
            self._timeout,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._acquired and self._lock is not None:
            try:
                self._lock.release()
            except RuntimeError:
                # 双重 release：日志告警但不抛（防 poison）
                logger.warning(
                    "SessionLock double-release: thread_id={}", self._thread_id
                )
            self._mgr._mark_released(self._thread_id)
            self._acquired = False
            logger.debug(
                "SessionLock released: thread_id={}", self._thread_id
            )


# ═══════════════════════════════════════════════════════
# 模块级单例（架构 §2.6 建议，便于 main.py 直接 import）
# ═══════════════════════════════════════════════════════

#: 全局单例（按架构 §T04 详细工作清单 #2 约定）
session_lock_manager: SessionLockManager = SessionLockManager()


__all__ = [
    "DEFAULT_LOCK_TIMEOUT_S",
    "SessionLockTimeout",
    "SessionLockManager",
    "session_lock_manager",
]
