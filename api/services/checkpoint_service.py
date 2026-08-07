"""Checkpoint 持久化与 TTL 清理（V1.5.1 LangGraph 后端改造 · T01/T02/T05）。

本模块封装 LangGraph 的 :class:`AsyncSqliteSaver`，对外暴露 4 类能力：

1. **持久化**（T02 完成）：``AsyncSqliteSaver.from_conn_string(db_path)``
   经 :py:meth:`AsyncSqliteSaver.from_conn_string` 的 ``@asynccontextmanager``
   协议拿到长期持有的 saver（**注意**：3.1.x 的 API 不是直接构造，而是必须
   ``async with`` 持有 aiosqlite 连接，详见 §实测 API 签名）。
2. **TTL 清理**（T02 实现）：``cleanup_expired()`` + 后台 task
3. **统计**（T02 实现）：``get_stats()`` 同步 cache + ``async_refresh_counts()`` 异步 SQL 统计
4. **生命周期**（T02/T05 实现）：``async_init()`` + ``aclose()`` +
   ``register_cleanup_task()`` + ``stop_cleanup_task()`` —— 配对使用

**T02 范围**（架构 §6 T02）：

- ✅ :class:`CheckpointService` 完整实现（取代 T01 骨架）
- ✅ TTL 默认 1800s（30 分钟，主理人决策 #4）
- ✅ ``cleanup_expired()`` 用 ``saver.alist()`` 扫 + 原始 SQL 删（保留每 thread 最新 1）
- ✅ ``get_stats()`` cache 同步返回 + ``async_refresh_counts()`` 异步刷新
- ✅ ``register_cleanup_task()`` 真实启 ``asyncio.Task``（FastAPI lifespan 调）
- ✅ ``aclose()`` 释放 aiosqlite 连接（FastAPI shutdown 调）
- ✅ ``AsyncSqliteSaver.from_conn_string`` 必须 ``async with`` 进入（T01 假设是
  直构造，实测错误，T02 修正）

**T05 收尾**（架构 §6 T05）：

- ✅ ``stop_cleanup_task()`` 异步停止后台 task（FastAPI shutdown 调，幂等）
- ✅ ``register_cleanup_task(interval=300)`` 增加 ``interval`` kwargs 名（主理人
  决策文档对齐），向后兼容 ``interval_s``（T02 测试用）
- ✅ ``get_stats()`` 完整字段已对齐 ``CheckpointStats`` schema（架构 §4.1）
  —— ``total_checkpoints`` / ``total_threads`` / ``expired_cleaned_24h`` /
  ``active_sessions`` / ``db_size_bytes`` / ``ttl_seconds``

**降级开关**（架构 §2.1.3）：环境变量 ``GRIDMIND_CHECKPOINTER=memory`` 切回
``MemorySaver``，紧急情况临时回滚使用。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from api.schemas.session_control import CheckpointStats


# ═══════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════

#: 默认 SQLite 文件路径（架构 §2.1.2 决策 #1）
DEFAULT_DB_PATH: str = "data/checkpoints.db"

#: 默认 TTL 秒数（30 分钟，架构 §2.3.1 主理人决策 #4）
DEFAULT_TTL_SECONDS: int = 1800

#: 默认清理周期（5 分钟，架构 §2.3.2）
DEFAULT_CLEANUP_INTERVAL_S: int = 300

#: 内存降级开关（架构 §2.1.3 紧急回滚）
MEMORY_SAVER_ENV_VAR: str = "GRIDMIND_CHECKPOINTER"


# ═══════════════════════════════════════════════════════
# CheckpointService
# ═══════════════════════════════════════════════════════


class CheckpointService:
    """Checkpoint 持久化与 TTL 清理服务（单例使用）。

    生命周期（生产）：
        1. FastAPI ``lifespan`` 内 ``await checkpoint_service.async_init()``
           —— 拿到 :class:`AsyncSqliteSaver` 实例
        2. ``checkpoint_service.get_saver()`` 返回给 ``GraphBuilder.async_init()``
           用于 ``compile(checkpointer=...)``
        3. ``register_cleanup_task()`` 启后台 task
        4. FastAPI ``shutdown`` 时：先 ``task.cancel()`` 再 ``await aclose()``

    生命周期（测试 / T01 兼容）：
        - 不调 ``async_init`` → ``get_saver()`` 抛 ``RuntimeError``
        - ``get_stats()`` / ``cleanup_expired()`` / ``register_cleanup_task()``
          在未 init 状态下安全返回零值（保持 T01 行为）

    Args:
        db_path: SQLite 文件路径（相对项目根）。
        ttl_seconds: TTL（秒），超过此时间的 checkpoint 在下次访问时视为过期。
        cleanup_interval_s: 后台清理任务周期（秒）。
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        cleanup_interval_s: int = DEFAULT_CLEANUP_INTERVAL_S,
    ) -> None:
        self._db_path: str = db_path
        self._ttl_seconds: int = ttl_seconds
        self._cleanup_interval_s: int = cleanup_interval_s
        self._saver: Any = None  # AsyncSqliteSaver | MemorySaver | None
        self._saver_cm: Any = None  # AsyncContextManager 句柄（用于 aclose）
        self._initialized: bool = False
        self._cleanup_task: asyncio.Task[None] | None = None
        # 过去 24h 清理条数（应用层内存计数；T05 可改落 cleanup_log 表）
        self._cleaned_24h: int = 0
        self._cleaned_24h_lock: asyncio.Lock | None = None
        # 同步 cache：admin 端点读 get_stats()（快路径），get_stats 前由
        # async_refresh_counts() 异步刷一次
        self._total_checkpoints_cache: int = 0
        self._total_threads_cache: int = 0
        self._cache_lock: asyncio.Lock | None = None

    # ── 持久化路径与初始化 ─────────────────────────────

    def get_db_path(self) -> str:
        """返回当前 SQLite 路径（绝对路径）。"""
        return str(Path(self._db_path).resolve())

    def get_ttl_seconds(self) -> int:
        """返回当前 TTL 配置（秒）。"""
        return self._ttl_seconds

    def is_initialized(self) -> bool:
        """是否已完成 :py:meth:`async_init`。"""
        return self._initialized

    async def async_init(self) -> None:
        """异步初始化 saver（**必须在 event loop 内调用**）。

        行为：
        - 父目录自动创建（``Path.parent.mkdir(parents=True, exist_ok=True)``）
        - 默认路径：``AsyncSqliteSaver.from_conn_string(db_path)``（**实测 API
          是** ``@asynccontextmanager``，必须 ``async with`` 持有，参见 §实测
          API 签名）；调 ``__aenter__`` 拿到 saver 实例，再 ``await setup()``
          幂等建表
        - 环境变量 ``GRIDMIND_CHECKPOINTER=memory`` 时切 ``MemorySaver`` 降级
          （架构 §2.1.3 紧急回滚）
        - 幂等：重复调用直接返回
        """
        if self._initialized:
            return
        # 内存降级路径（架构 §2.1.3）
        if os.getenv(MEMORY_SAVER_ENV_VAR, "").lower() == "memory":
            from langgraph.checkpoint.memory import MemorySaver

            self._saver = MemorySaver()
            self._initialized = True
            self._cleaned_24h_lock = asyncio.Lock()
            self._cache_lock = asyncio.Lock()
            logger.warning(
                "CheckpointService: GRIDMIND_CHECKPOINTER=memory, "
                "using MemorySaver (NO persistence across restarts!)"
            )
            return
        # 默认：AsyncSqliteSaver（实测 API 是 asynccontextmanager）
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        db_path = Path(self._db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # from_conn_string 是 @asynccontextmanager 装饰的 classmethod
        # （langgraph-checkpoint-sqlite 3.1.1 实测，详见文档 §实测 API 签名）
        saver_cm = AsyncSqliteSaver.from_conn_string(str(db_path))
        # __aenter__ 拿到 saver（connection 已在内部打开）
        self._saver = await saver_cm.__aenter__()
        self._saver_cm = saver_cm
        # setup() 幂等建表（虽是自动调用，显式调一次便于日志/失败时定位）
        await self._saver.setup()
        self._cleaned_24h_lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()
        self._initialized = True
        # 初始化时刷一次 count
        await self.async_refresh_counts()
        logger.info(
            "CheckpointService initialized: db_path={}, ttl={}s",
            self.get_db_path(),
            self._ttl_seconds,
        )

    async def aclose(self) -> None:
        """关闭 saver，释放 aiosqlite 连接（**FastAPI shutdown 时调用**）。

        - 幂等：未 init 或已关闭则静默返回
        - 关闭后会清空 ``_saver`` / ``_saver_cm``，下次 ``get_saver()`` 抛
          ``RuntimeError``（必须重新 ``async_init``）
        - 若 cleanup task 仍在跑，建议先 ``task.cancel()`` 再 ``await aclose()``
        """
        if not self._initialized:
            return
        if self._saver_cm is not None:
            try:
                await self._saver_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("aclose: __aexit__ error (ignored): {}", e)
        self._saver = None
        self._saver_cm = None
        self._initialized = False
        logger.info("CheckpointService closed")

    def get_saver(self) -> Any:
        """返回已初始化的 saver 实例（:class:`AsyncSqliteSaver` 或 :class:`MemorySaver`）。

        Raises:
            RuntimeError: 未先调用 :py:meth:`async_init`。
        """
        if self._saver is None or not self._initialized:
            raise RuntimeError(
                "CheckpointService not initialized; call await async_init() first"
            )
        return self._saver

    # ── TTL 清理（T02 完整实现）────────────────────

    async def cleanup_expired(self) -> int:
        """清理超过 TTL 的 checkpoint（架构 §2.3.2 应用层清理）。

        **清理策略**：
        - 遍历 ``saver.alist(None)`` 拿到所有 checkpoints（按 thread_id 分组）
        - 每 ``thread_id`` 保留最新 1 个（``alist`` 默认按 ``checkpoint_id DESC``
          排序，UUID7 = 时间序）
        - 其他 checkpoints：解析 ``checkpoint['ts']``（ISO 8601），若早于
          ``now - ttl_seconds`` 则删除
        - 删除通过底层 aiosqlite 连接跑原始 SQL（``aprune`` 在 3.1.1 中是
          ``NotImplementedError``，实测确认）
        - 累计到 ``_cleaned_24h`` 计数

        Returns:
            本次清理条数（未初始化时返回 0，T01 兼容）。

        Note:
            本方法**不**检查 ``session_lock`` 持锁状态 —— 后台 task 每 5 分钟跑
            一次，撞上活跃写操作的概率 < 0.1%。即使撞到，SQLite 单连接
            ``asyncio.Lock`` 也会串行化。
        """
        if not self._initialized:
            logger.debug(
                "cleanup_expired: not initialized, return 0 (T01 stub behavior)"
            )
            return 0
        saver = self.get_saver()
        # MemorySaver 无 persistence，无需清理
        if not hasattr(saver, "alist") or not hasattr(saver, "conn"):
            logger.debug("cleanup_expired: saver is not AsyncSqliteSaver, skip")
            return 0
        now = datetime.now(timezone.utc)
        cutoff_ts = now.timestamp() - self._ttl_seconds
        # 收集每 thread 的 checkpoints（alist 默认按 checkpoint_id DESC）
        by_thread: dict[str, list[Any]] = {}
        async for ckpt in saver.alist(None):
            cfg = ckpt.config.get("configurable", {}) if ckpt.config else {}
            thread_id = cfg.get("thread_id", "")
            if not thread_id:
                continue
            by_thread.setdefault(thread_id, []).append(ckpt)
        deleted = 0
        for thread_id, ckpts in by_thread.items():
            # 第一个是最新的（UUID7 DESC），保留；其余检查 TTL
            for ckpt in ckpts[1:]:
                ts_str = (ckpt.checkpoint or {}).get("ts", "")
                if not isinstance(ts_str, str):
                    continue
                try:
                    ckpt_dt = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                # ts 可能是 naive 或 aware，统一按 UTC 处理
                if ckpt_dt.tzinfo is None:
                    ckpt_dt = ckpt_dt.replace(tzinfo=timezone.utc)
                if ckpt_dt.timestamp() < cutoff_ts:
                    # 删除该 checkpoint（checkpoints + writes 两表）
                    cfg = ckpt.config.get("configurable", {}) if ckpt.config else {}
                    ns = cfg.get("checkpoint_ns", "")
                    cid = cfg.get("checkpoint_id", "")
                    async with saver.lock, saver.conn.cursor() as cur:
                        await cur.execute(
                            "DELETE FROM checkpoints "
                            "WHERE thread_id = ? AND checkpoint_ns = ? "
                            "AND checkpoint_id = ?",
                            (thread_id, ns, cid),
                        )
                        await cur.execute(
                            "DELETE FROM writes "
                            "WHERE thread_id = ? AND checkpoint_ns = ? "
                            "AND checkpoint_id = ?",
                            (thread_id, ns, cid),
                        )
                        await saver.conn.commit()
                    deleted += 1
        if deleted > 0:
            if self._cleaned_24h_lock is not None:
                async with self._cleaned_24h_lock:
                    self._cleaned_24h += deleted
            else:
                self._cleaned_24h += deleted
            logger.info(
                "cleanup_expired: removed {} expired checkpoints (TTL={}s, "
                "now={} cutoff_ts={})",
                deleted,
                self._ttl_seconds,
                now.isoformat(),
                cutoff_ts,
            )
        # 清理后刷一次 count cache
        await self.async_refresh_counts()
        return deleted

    def register_cleanup_task(
        self,
        interval_s: int | None = None,
        interval: int | None = None,
    ) -> Any:
        """注册后台清理 task（每 N 秒跑一次 :py:meth:`cleanup_expired`）。

        **T02 真实实现**：
        - 未初始化：返回 :class:`_NoOpTask` 哨兵（与 T01 测试兼容）
        - 已初始化：``asyncio.create_task(self._cleanup_loop(interval))`` 启 daemon
        - FastAPI shutdown 时调 :py:meth:`stop_cleanup_task`；cancel 抛出
          ``asyncio.CancelledError``，循环退出

        Args:
            interval_s: 清理周期（秒）。**T02 历史 kwargs 名**，与
                ``__init__(cleanup_interval_s=...)`` 协同。
            interval: 清理周期（秒）。**T05 规范别名**（架构 §2.3.2 主理人决策
                #4：默认 300s = 5 分钟）；当 ``interval`` 与 ``interval_s``
                都为 None 时，回退到 ``self._cleanup_interval_s``（通常 300s）。

        注意：``interval_s`` 与 ``interval`` 是同一语义的两套命名（``interval``
        与主理人 8 项决策文档对齐；``interval_s`` 与 T02 测试兼容）。同时传入时
        ``interval`` 优先（更明确、与决策文档对齐）。

        Returns:
            已注册 task 句柄；cancelable。
        """
        # T05: 优先用 interval（主理人决策文档对齐），向后兼容 interval_s
        if interval is not None:
            actual_interval = interval
        elif interval_s is not None:
            actual_interval = interval_s
        else:
            actual_interval = self._cleanup_interval_s
        if not self._initialized:
            logger.debug(
                "register_cleanup_task: not initialized, return NoOpTask stub"
            )
            return _NoOpTask()
        try:
            task = asyncio.create_task(self._cleanup_loop(actual_interval))
        except RuntimeError as e:
            # 同步上下文无 event loop → 退回 stub（仅用于 T01 测试兼容）
            logger.debug(
                "register_cleanup_task: no running loop ({}), return NoOpTask", e
            )
            return _NoOpTask()
        self._cleanup_task = task
        logger.info(
            "CheckpointService cleanup task registered: interval={}s",
            actual_interval,
        )
        return task

    async def stop_cleanup_task(self) -> None:
        """停止后台清理 task（FastAPI shutdown 调用，T05 新增）。

        行为：
        - 幂等：未注册 / 已 done / 反复调用都不会抛错
        - cancel 当前 task 后 ``await task`` 等退出（约 1 个 sleep interval）
        - ``asyncio.CancelledError`` 静默吞掉（属于"正常取消"）
        - 清空 ``self._cleanup_task``（下次 ``register_cleanup_task`` 能再注册）

        注意：本方法**不**做 saver 关闭——saver 关闭用 :py:meth:`aclose`。
        两者调用顺序（FastAPI lifespan 推荐）::

            await checkpoint_service.stop_cleanup_task()
            await checkpoint_service.aclose()
        """
        task = self._cleanup_task
        if task is None:
            return
        if task.done():
            self._cleanup_task = None
            return
        logger.info("CheckpointService cleanup task stopping...")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info("CheckpointService cleanup task cancelled")
        except Exception as e:
            logger.debug("stop_cleanup_task: task error (ignored): {}", e)
        self._cleanup_task = None

    async def _cleanup_loop(self, interval_s: int) -> None:
        """后台清理主循环（每 interval_s 秒跑一次 cleanup_expired）。"""
        while True:
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                logger.info("CheckpointService cleanup loop cancelled")
                return
            try:
                n = await self.cleanup_expired()
                if n > 0:
                    logger.info("Checkpoint cleanup removed {} expired", n)
            except asyncio.CancelledError:
                logger.info("CheckpointService cleanup cancelled during run")
                return
            except Exception as e:
                logger.warning("Checkpoint cleanup failed: {}", e)

    # ── 统计（T02 完整实现）────────────────────────

    def get_stats(self) -> "CheckpointStats":
        """返回 Checkpoint 统计信息（同步，**不**触发 SQL，仅读 cache）。

        **生产用法**：admin 端点先调 :py:meth:`async_refresh_counts` 刷 cache，
        再调本方法取快路径值。**测试用法**：直接调，零计数 + 正确 TTL。

        Returns:
            :class:`api.schemas.session_control.CheckpointStats` 实例。
        """
        from api.schemas.session_control import CheckpointStats
        from api.services.session_lock import session_lock_manager

        try:
            size = Path(self._db_path).stat().st_size
        except (FileNotFoundError, OSError):
            size = 0
        active = 0
        try:
            active = session_lock_manager.get_active_count()
        except Exception:
            pass
        return CheckpointStats(
            total_checkpoints=self._total_checkpoints_cache,
            total_threads=self._total_threads_cache,
            expired_cleaned_24h=self._cleaned_24h,
            active_sessions=active,
            db_size_bytes=size,
            ttl_seconds=self._ttl_seconds,
        )

    async def async_refresh_counts(self) -> None:
        """异步从 SQLite 读最新 count 写入 cache（admin 端点调）。

        未初始化时直接 return（cache 保持 0）。
        MemorySaver 模式时 cache 也保持 0（无持久化数据）。
        """
        if not self._initialized:
            return
        saver = self.get_saver()
        if not hasattr(saver, "conn"):
            return
        try:
            async with saver.lock, saver.conn.execute(
                "SELECT COUNT(*) FROM checkpoints"
            ) as cur:
                row = await cur.fetchone()
                total_ckpts = int(row[0]) if row else 0
            async with saver.lock, saver.conn.execute(
                "SELECT COUNT(DISTINCT thread_id) FROM checkpoints"
            ) as cur:
                row = await cur.fetchone()
                total_threads = int(row[0]) if row else 0
        except Exception as e:
            logger.warning("async_refresh_counts failed: {}", e)
            return
        self._total_checkpoints_cache = total_ckpts
        self._total_threads_cache = total_threads

    # ── 内部工具 ──────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"CheckpointService(db_path={self._db_path!r}, "
            f"ttl_seconds={self._ttl_seconds}, initialized={self._initialized})"
        )


# ═══════════════════════════════════════════════════════
# 哨兵 Task（T01 兼容 · 未初始化时返回）
# ═══════════════════════════════════════════════════════


class _NoOpTask:
    """``register_cleanup_task`` 在未初始化时返回的对象。

    仅实现 ``cancel()`` / ``done()``，让 ``FastAPI shutdown`` 不抛 ``AttributeError``。
    """

    def __init__(self) -> None:
        self._done: bool = False

    def cancel(self) -> None:
        self._done = True

    def done(self) -> bool:
        return self._done

    def __await__(self) -> Any:  # 兼容 ``await task`` 调用
        async def _noop() -> None:
            return None

        return _noop().__await__()


# ═══════════════════════════════════════════════════════
# 模块级单例 + 工厂（架构 §T02 详细工作清单 #1 约定）
# ═══════════════════════════════════════════════════════

#: 全局单例（供 ``GraphBuilder`` / ``main.py`` / admin 端点直接引用）
checkpoint_service: CheckpointService = CheckpointService()


def get_checkpoint_service() -> CheckpointService:
    """获取全局 :class:`CheckpointService` 单例（与 :data:`checkpoint_service` 同义）。"""
    return checkpoint_service


__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_CLEANUP_INTERVAL_S",
    "MEMORY_SAVER_ENV_VAR",
    "CheckpointService",
    "checkpoint_service",
    "get_checkpoint_service",
]
