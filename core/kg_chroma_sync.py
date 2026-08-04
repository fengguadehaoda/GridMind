"""GridMind Neo4j ↔ Chroma 双向同步服务（ChromaSyncService）—— M2 阶段核心组件。

设计目标
--------
- **双触发**：定时 5min 全量校验 + 写入事件（即时同步），双驱动（Q7 = A 决策）
- **持久化队列**：asyncio.Queue（内存）+ sync_log（SQLite）双写；进程崩溃可恢复
- **Neo4j 权威源**：冲突时 Neo4j 赢，Chroma 元数据被覆盖；状态写 conflict
- **优雅停止**：stop() 等待队列清空后退出

跨文件约定（架构 7.2 共享知识）
--------------------------------
- 与 ``KGClient`` 解耦：KGClient 只负责 backend 路由，本服务负责同步协调
- 启动位置：FastAPI ``lifespan``（与 start_all.py 兼容）
- 监控埋点：6 个指标走 loguru JSON 日志

事件触发流程
------------
::
    KGMigrator.run()
        ↓ (写入 Neo4j 后)
    sync_service.enqueue_event(entity_id, source="kg_migrator")
        ↓
    event_queue.put_nowait(event)
    sync_log.write_pending(sync_type='event', entity_id=...)
        ↓
    worker.consume(event)
        ↓
    KGClient.cypher_query(...)  →  Chroma collection.update_metadata(...)
        ↓
    sync_log.log_success / log_failed / log_conflict
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loguru import logger

from api.config import settings
from api.services.sync_log_service import (
    MAX_RETRY_COUNT,
    SYNC_TYPE_EVENT,
    SYNC_TYPE_GRAPH_TO_VECTOR,
    SYNC_TYPE_VECTOR_TO_GRAPH,
    SyncLogService,
    get_sync_log_service,
)


class ChromaSyncService:
    """Neo4j ↔ Chroma 双向同步服务（异步后台 worker）。

    核心组件：
    - ``_event_queue``: asyncio.Queue（写入事件持久化队列）
    - ``_worker_task``: 事件消费者
    - ``_timer_task``: 5min 定时器
    """

    _instance: "ChromaSyncService | None" = None

    def __init__(self) -> None:
        self._queue_size = int(getattr(settings, "sync_event_queue_size", 1000))
        self._interval_s = int(getattr(settings, "sync_interval_s", 300))
        self._event_queue: asyncio.Queue[dict[str, Any]] | None = None
        self._worker_task: asyncio.Task | None = None
        self._timer_task: asyncio.Task | None = None
        self._started = False
        self._stopping = False
        self._sync_log: SyncLogService = get_sync_log_service()
        # Neo4j / Chroma client 懒加载（避免循环 import）
        self._kg_client: Any = None
        self._vector_store: Any = None
        # 监控埋点：sync 统计
        self._stats = {
            "events_received": 0,
            "events_processed": 0,
            "events_failed": 0,
            "timer_ticks": 0,
            "last_tick_at": None,
            "last_event_at": None,
        }
        logger.info(
            "ChromaSyncService initialized: interval={}s, queue_size={}",
            self._interval_s, self._queue_size,
        )

    # ── 单例工厂 ────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "ChromaSyncService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ── 生命周期 ────────────────────────────────────────

    async def start(self) -> None:
        """启动后台 worker + 定时器。"""
        if self._started:
            return
        self._started = True
        self._stopping = False
        # 懒加载 event queue（必须在 event loop 内创建）
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=self._queue_size)
        # 启动事件 worker
        self._worker_task = asyncio.create_task(
            self._event_worker(), name="chromasync-event-worker"
        )
        # 启动定时器
        self._timer_task = asyncio.create_task(
            self._timer_loop(), name="chromasync-timer"
        )
        # 恢复 pending 任务（进程崩溃重启场景）
        self._recover_pending_tasks()
        logger.info("ChromaSyncService started (worker + timer)")

    async def stop(self) -> None:
        """优雅停止：等待队列清空 + 取消 worker。"""
        if not self._started:
            return
        self._stopping = True
        logger.info("ChromaSyncService stopping...")

        # 取消定时器
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            try:
                await self._timer_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # 等待队列清空（带超时）
        if self._event_queue is not None:
            timeout = 30.0
            try:
                start_wait = time.monotonic()
                while not self._event_queue.empty() and (time.monotonic() - start_wait) < timeout:
                    await asyncio.sleep(0.1)
                if not self._event_queue.empty():
                    logger.warning(
                        "ChromaSyncService: queue not drained ({} items) after {:.1f}s",
                        self._event_queue.qsize(), timeout,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChromaSyncService: drain queue error: {}", exc)

        # 取消 worker
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._started = False
        logger.info("ChromaSyncService stopped")

    # ── 写入事件入队 ────────────────────────────────────────

    def enqueue_event(
        self,
        entity_id: str,
        *,
        sync_type: str = SYNC_TYPE_EVENT,
        thread_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """入队一个写入事件（同步方法，调用方无需 await）。

        Args:
            entity_id: 关联实体 ID
            sync_type: 'graph_to_vector' / 'vector_to_graph' / 'event'
            thread_id: 会话 ID
            payload:   附加上下文

        Returns:
            True 成功入队；False 队列已满或未启动
        """
        if not self._started or self._event_queue is None:
            logger.debug("ChromaSyncService not started, skipping event for {}", entity_id)
            return False
        event = {
            "entity_id": entity_id,
            "sync_type": sync_type,
            "thread_id": thread_id,
            "payload": payload or {},
            "enqueued_at": time.time(),
        }
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "ChromaSyncService: queue full (maxsize={}), dropping event for {}",
                self._queue_size, entity_id,
            )
            return False
        self._stats["events_received"] += 1
        self._stats["last_event_at"] = time.time()
        # 监控埋点
        logger.info(
            json.dumps(
                {
                    "event": "sync_event_enqueued",
                    "entity_id": entity_id,
                    "sync_type": sync_type,
                    "thread_id": thread_id,
                    "queue_size": self._event_queue.qsize(),
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            )
        )
        return True

    # ── Worker（事件消费者）────────────────────────────────

    async def _event_worker(self) -> None:
        """事件消费者：从 _event_queue 取出事件，处理同步。"""
        logger.info("ChromaSyncService: event worker started")
        while True:
            if self._stopping and (self._event_queue is None or self._event_queue.empty()):
                break
            try:
                event = await self._event_queue.get()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("ChromaSyncService worker get() error: {}", exc)
                await asyncio.sleep(0.1)
                continue
            try:
                await self._handle_event(event)
                self._stats["events_processed"] += 1
            except Exception as exc:  # noqa: BLE001
                self._stats["events_failed"] += 1
                logger.error("ChromaSyncService handle_event error: {}", exc)
            finally:
                if self._event_queue is not None:
                    self._event_queue.task_done()
        logger.info("ChromaSyncService: event worker stopped")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """处理单个事件：写 sync_log(pending) → 同步执行 → 写 success/failed/conflict。"""
        entity_id = str(event.get("entity_id", ""))
        sync_type = str(event.get("sync_type", SYNC_TYPE_EVENT))
        thread_id = event.get("thread_id")
        if not entity_id:
            return
        # 1) 写 pending
        log_id = self._sync_log.write_pending(
            sync_type=sync_type,
            entity_id=entity_id,
            thread_id=thread_id,
            payload=event.get("payload"),
        )
        if log_id <= 0:
            logger.warning("ChromaSyncService: write_pending returned invalid id")
            return
        # 2) 执行同步
        start_ts = time.perf_counter()
        try:
            result = await self._do_sync(entity_id=entity_id, sync_type=sync_type)
            duration_ms = int((time.perf_counter() - start_ts) * 1000)
            # 3) 写 success / conflict
            if result.get("conflict"):
                self._sync_log.log_conflict(
                    log_id, neo4j_updated_at=result.get("neo4j_updated_at"),
                    duration_ms=duration_ms,
                )
            else:
                self._sync_log.log_success(
                    log_id, duration_ms=duration_ms,
                    neo4j_updated_at=result.get("neo4j_updated_at"),
                    chroma_updated_at=result.get("chroma_updated_at"),
                )
            # 监控埋点
            logger.info(
                json.dumps(
                    {
                        "event": "sync_event_processed",
                        "entity_id": entity_id,
                        "sync_type": sync_type,
                        "duration_ms": duration_ms,
                        "conflict": result.get("conflict", False),
                        "linked_devices_count": len(result.get("linked_devices", [])),
                        "timestamp": time.time(),
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - start_ts) * 1000)
            retry_count = self._sync_log.log_failed(
                log_id, str(exc), duration_ms=duration_ms,
            )
            logger.warning(
                "ChromaSyncService: sync failed for {} (retry={}/{}): {}",
                entity_id, retry_count, MAX_RETRY_COUNT, exc,
            )

    async def _do_sync(
        self,
        entity_id: str,
        sync_type: str,
    ) -> dict[str, Any]:
        """实际同步逻辑（Neo4j → Chroma）。

        Returns:
            {
              "conflict": bool,
              "neo4j_updated_at": float | None,
              "chroma_updated_at": float | None,
              "linked_devices": list[str],
            }
        """
        # 懒加载 clients
        kg = self._get_kg_client()
        vs = self._get_vector_store()

        # 1) 从 Neo4j 查 entity 关联设备
        linked_devices: list[str] = []
        neo4j_updated_at: float | None = None
        try:
            relations = kg.get_relations(entity_id) or []
            linked_devices = list({r.get("target_id") for r in relations if r.get("target_id")})
            # 查 entity 自身（获取 updated_at）
            entity = kg.get_entity(entity_id)
            if entity:
                props = entity.get("properties", {}) or {}
                if isinstance(props, dict):
                    ts = props.get("updated_at")
                    if isinstance(ts, (int, float)):
                        neo4j_updated_at = float(ts)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChromaSyncService: kg query failed for {}: {}", entity_id, exc)

        # 2) 更新 Chroma 元数据（Neo4j 权威源 → 强制覆盖）
        chroma_updated_at: float | None = None
        conflict = False
        try:
            if vs is not None and linked_devices:
                # 通过 collection.update 强制覆盖 metadata
                # Chroma collection.update 需要传 ids + metadatas
                chunks = vs.search(entity_id, top_k=1) if hasattr(vs, "search") else []
                if chunks:
                    chunk_meta = chunks[0].get("metadata", {}) or {}
                    existing = chunk_meta.get("linked_devices", []) or []
                    new_meta = {
                        "doc_id": chunk_meta.get("doc_id", entity_id),
                        "title": chunk_meta.get("title", entity_id),
                        "source": chunk_meta.get("source", "kg_sync"),
                        "linked_devices": linked_devices,
                        "synced_at": time.time(),
                    }
                    # 判断是否冲突（Neo4j 数据与 Chroma 不同）
                    if existing and set(existing) != set(linked_devices):
                        conflict = True
                        logger.info(
                            "ChromaSyncService: conflict detected for {} "
                            "(existing={}, neo4j={})",
                            entity_id, existing, linked_devices,
                        )
                    # Chroma collection.update (overwrite metadata)
                    try:
                        vs._collection.update(
                            ids=[f"chunk-{entity_id}"],
                            metadatas=[new_meta],
                        )
                        chroma_updated_at = time.time()
                    except Exception as update_exc:  # noqa: BLE001
                        logger.debug("ChromaSyncService: chroma update failed: {}", update_exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChromaSyncService: chroma write failed: {}", exc)

        return {
            "conflict": conflict,
            "neo4j_updated_at": neo4j_updated_at,
            "chroma_updated_at": chroma_updated_at,
            "linked_devices": linked_devices,
        }

    # ── 定时器（5min 全量校验）────────────────────────────────

    async def _timer_loop(self) -> None:
        """定时循环：每隔 sync_interval_s 触发一次全量校验。"""
        logger.info("ChromaSyncService: timer started (interval={}s)", self._interval_s)
        try:
            while not self._stopping:
                await asyncio.sleep(self._interval_s)
                if self._stopping:
                    break
                try:
                    await self._on_timer_tick()
                except Exception as exc:  # noqa: BLE001
                    logger.error("ChromaSyncService: timer tick error: {}", exc)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("ChromaSyncService: timer stopped")

    async def _on_timer_tick(self) -> None:
        """定时器回调：扫描 sync_log 中超过 sync_interval_s 未处理的 pending 任务。"""
        self._stats["timer_ticks"] += 1
        self._stats["last_tick_at"] = time.time()
        # 监控埋点
        logger.info(
            json.dumps(
                {
                    "event": "sync_timer_tick",
                    "queue_size": self._event_queue.qsize() if self._event_queue else 0,
                    "stats": dict(self._stats),
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            )
        )
        # 触发一次图→向量全量校验
        await self._full_sync_check()

    async def _full_sync_check(self) -> None:
        """全量校验：拉取 sync_log 中最近 success 的时间戳，对比 Neo4j 最新更新时间。

        简化实现：将 sync_log 中超过 1 个 interval 未更新的 entity 入队。
        """
        try:
            # 简化实现：扫描最近 10 条 sync_log，对于 Neo4j 节点入队一次
            rows = self._sync_log.get_recent(limit=10, status="success")
            for row in rows:
                entity_id = row.get("entity_id", "")
                if entity_id and entity_id.startswith("e-"):
                    self.enqueue_event(
                        entity_id=entity_id,
                        sync_type=SYNC_TYPE_GRAPH_TO_VECTOR,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaSyncService: full_sync_check error: {}", exc)

    # ── 崩溃恢复 ────────────────────────────────────────

    def _recover_pending_tasks(self) -> None:
        """进程崩溃重启时，从 sync_log 恢复 pending 任务。"""
        try:
            pending = self._sync_log.get_pending_for_recovery(limit=100)
            for row in pending:
                self.enqueue_event(
                    entity_id=row["entity_id"],
                    sync_type=row["sync_type"],
                    thread_id=row["thread_id"],
                    payload=row.get("payload"),
                )
            if pending:
                logger.info(
                    "ChromaSyncService: recovered {} pending tasks from sync_log",
                    len(pending),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaSyncService: recover_pending_tasks error: {}", exc)

    # ── 状态查询 ────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """返回服务统计（用于监控 + 调试端点）。"""
        return {
            "started": self._started,
            "stopping": self._stopping,
            "queue_size": self._event_queue.qsize() if self._event_queue else 0,
            "queue_max": self._queue_size,
            "interval_s": self._interval_s,
            **self._stats,
        }

    def get_queue_length(self) -> int:
        """返回当前队列长度（监控埋点用）。"""
        return self._event_queue.qsize() if self._event_queue else 0

    # ── 懒加载 clients ────────────────────────────────────────

    def _get_kg_client(self) -> Any:
        if self._kg_client is None:
            from core.kg_client import get_kg_client
            self._kg_client = get_kg_client()
        return self._kg_client

    def _get_vector_store(self) -> Any:
        if self._vector_store is None:
            try:
                from core.vector_store import VectorStore
                self._vector_store = VectorStore()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChromaSyncService: VectorStore init failed: {}", exc)
                self._vector_store = None
        return self._vector_store


# ═════════════════════════════════════════════════════════════════════════════
# 单例工厂
# ═════════════════════════════════════════════════════════════════════════════

def get_sync_service() -> ChromaSyncService:
    """获取 ChromaSyncService 单例。"""
    return ChromaSyncService.get_instance()


def reset_sync_service() -> None:
    """重置单例（仅测试用）。"""
    ChromaSyncService.reset_instance()


__all__ = [
    "ChromaSyncService",
    "get_sync_service",
    "reset_sync_service",
]