"""sync_log 表的服务封装——M2 双向同步审计 + 持久化队列。

设计目标
--------
- **统一入口**：所有读写 sync_log 表的操作必须走本 service，禁止散落 SQL。
- **状态机**：`pending → success / failed / conflict`（冲突 = Neo4j 覆盖 Chroma）
- **重试机制**：`retry_count++`，最大 3 次后强制标 `failed`
- **可观测**：提供 `count_by_status` / `get_recent` / `query_by_entity` 三个查询入口

跨文件约定（与 M2 架构文档一致）
--------------------------------
- 写入事件先入 `sync_log(pending)`，处理完成后改 `success` / `failed`
- 失败自动重试，超 3 次标 `failed`（不再自动重试）
- Neo4j 权威源：冲突时 status='conflict'，Neo4j 数据胜
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from typing import Any

from loguru import logger

from mcp_tools.db.database import get_connection


# 同步类型枚举（与 sync_log 表 CHECK 约束对齐）
SYNC_TYPE_GRAPH_TO_VECTOR = "graph_to_vector"
SYNC_TYPE_VECTOR_TO_GRAPH = "vector_to_graph"
SYNC_TYPE_EVENT = "event"
SYNC_TYPE_ROLLBACK = "rollback"

VALID_SYNC_TYPES = {
    SYNC_TYPE_GRAPH_TO_VECTOR,
    SYNC_TYPE_VECTOR_TO_GRAPH,
    SYNC_TYPE_EVENT,
    SYNC_TYPE_ROLLBACK,
}

# 状态枚举
STATUS_PENDING = "pending"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CONFLICT = "conflict"

VALID_STATUSES = {STATUS_PENDING, STATUS_SUCCESS, STATUS_FAILED, STATUS_CONFLICT}

# 最大重试次数
MAX_RETRY_COUNT = 3


class SyncLogService:
    """sync_log 表读写服务（单例 + 进程级）。"""

    _instance: "SyncLogService | None" = None

    def __new__(cls) -> "SyncLogService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 写入接口 ────────────────────────────────────────

    def write_pending(
        self,
        sync_type: str,
        entity_id: str,
        *,
        chunk_id: str | None = None,
        thread_id: str | None = None,
        payload: dict[str, Any] | None = None,
        neo4j_updated_at: float | None = None,
        chroma_updated_at: float | None = None,
    ) -> int:
        """写入 pending 状态记录，返回新插入的 id。

        Args:
            sync_type:    'graph_to_vector' / 'vector_to_graph' / 'event' / 'rollback'
            entity_id:    关联的实体 ID
            chunk_id:     Chroma 文档 chunk_id（图→向量同步时使用）
            thread_id:    会话 ID（用于追溯）
            payload:      附加上下文（JSON 序列化）
            neo4j_updated_at: Neo4j 节点更新时间戳（秒）
            chroma_updated_at: Chroma 元数据更新时间戳（秒）

        Returns:
            新插入记录的 id；返回 -1 表示写入失败。
        """
        if sync_type not in VALID_SYNC_TYPES:
            raise ValueError(f"非法 sync_type: {sync_type}")

        payload_json = json.dumps(payload, ensure_ascii=False, default=str) if payload else None
        conn = get_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO sync_log
                  (sync_type, entity_id, chunk_id, status, retry_count,
                   neo4j_updated_at, chroma_updated_at, payload, thread_id)
                VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    sync_type, entity_id, chunk_id,
                    neo4j_updated_at, chroma_updated_at,
                    payload_json, thread_id,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid or -1
            logger.debug(
                "sync_log[{}] pending: type={} entity={} thread={}",
                new_id, sync_type, entity_id, thread_id,
            )
            return int(new_id)
        finally:
            conn.close()

    def log_success(
        self,
        log_id: int,
        *,
        duration_ms: int | None = None,
        neo4j_updated_at: float | None = None,
        chroma_updated_at: float | None = None,
    ) -> bool:
        """将指定记录标记为 success。"""
        if log_id <= 0:
            return False
        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE sync_log
                SET status = 'success',
                    finished_at = ?,
                    duration_ms = COALESCE(?, duration_ms),
                    neo4j_updated_at = COALESCE(?, neo4j_updated_at),
                    chroma_updated_at = COALESCE(?, chroma_updated_at),
                    error_message = NULL
                WHERE id = ?
                """,
                (finished, duration_ms, neo4j_updated_at, chroma_updated_at, log_id),
            )
            conn.commit()
            return True
        except sqlite3.Error as exc:  # noqa: BLE001
            logger.warning("sync_log[{}] log_success failed: {}", log_id, exc)
            return False
        finally:
            conn.close()

    def log_failed(
        self,
        log_id: int,
        error_message: str,
        *,
        increment_retry: bool = True,
        duration_ms: int | None = None,
    ) -> int:
        """将指定记录标记为 failed（可自动 retry_count++）。

        Returns:
            当前 retry_count（>=3 时表示不再重试）。
        """
        if log_id <= 0:
            return MAX_RETRY_COUNT
        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_connection()
        try:
            # 先读取当前 retry_count
            row = conn.execute(
                "SELECT retry_count FROM sync_log WHERE id = ?", (log_id,)
            ).fetchone()
            if row is None:
                return MAX_RETRY_COUNT
            current_retry = int(row["retry_count"] or 0)
            new_retry = current_retry + 1 if increment_retry else current_retry
            # 达到 MAX_RETRY_COUNT 后强制 failed，不再保留 pending
            new_status = STATUS_FAILED if new_retry >= MAX_RETRY_COUNT else STATUS_PENDING
            conn.execute(
                """
                UPDATE sync_log
                SET status = ?,
                    finished_at = ?,
                    error_message = ?,
                    retry_count = ?,
                    duration_ms = COALESCE(?, duration_ms)
                WHERE id = ?
                """,
                (new_status, finished, str(error_message)[:1000], new_retry, duration_ms, log_id),
            )
            conn.commit()
            return new_retry
        except sqlite3.Error as exc:  # noqa: BLE001
            logger.warning("sync_log[{}] log_failed error: {}", log_id, exc)
            return MAX_RETRY_COUNT
        finally:
            conn.close()

    def log_conflict(
        self,
        log_id: int,
        *,
        neo4j_updated_at: float | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        """将指定记录标记为 conflict（Neo4j 权威源覆盖 Chroma）。"""
        if log_id <= 0:
            return False
        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE sync_log
                SET status = 'conflict',
                    finished_at = ?,
                    duration_ms = COALESCE(?, duration_ms),
                    neo4j_updated_at = COALESCE(?, neo4j_updated_at)
                WHERE id = ?
                """,
                (finished, duration_ms, neo4j_updated_at, log_id),
            )
            conn.commit()
            return True
        except sqlite3.Error as exc:  # noqa: BLE001
            logger.warning("sync_log[{}] log_conflict error: {}", log_id, exc)
            return False
        finally:
            conn.close()

    def log_rollback_event(
        self,
        reason: str,
        *,
        thread_id: str | None = None,
        actor: str = "auto_rollback",
        details: dict[str, Any] | None = None,
    ) -> int:
        """记录灰度回滚事件（用于审计）。

        Returns:
            新插入的 sync_log id。
        """
        payload = {
            "actor": actor,
            "details": details or {},
            "ts": time.time(),
        }
        return self.write_pending(
            sync_type=SYNC_TYPE_ROLLBACK,
            entity_id=f"rollback-{int(time.time())}",
            thread_id=thread_id,
            payload=payload,
        )

    # ── 查询接口 ────────────────────────────────────────

    def get_recent(self, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        """查询最近 N 条同步记录（按 started_at DESC）。"""
        conn = get_connection()
        try:
            if status and status in VALID_STATUSES:
                rows = conn.execute(
                    """
                    SELECT id, sync_type, entity_id, chunk_id, status, retry_count,
                           neo4j_updated_at, chroma_updated_at, payload,
                           started_at, finished_at, error_message, thread_id,
                           duration_ms, reason
                    FROM sync_log
                    WHERE status = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (status, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, sync_type, entity_id, chunk_id, status, retry_count,
                           neo4j_updated_at, chroma_updated_at, payload,
                           started_at, finished_at, error_message, thread_id,
                           duration_ms, reason
                    FROM sync_log
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count_by_status(self) -> dict[str, int]:
        """按 status 聚合统计（用于监控 / 调试端点）。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM sync_log GROUP BY status"
            ).fetchall()
            result = {s: 0 for s in VALID_STATUSES}
            for r in rows:
                result[str(r["status"])] = int(r["cnt"])
            return result
        finally:
            conn.close()

    def query_by_entity(self, entity_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """按 entity_id 查询同步历史。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, sync_type, entity_id, chunk_id, status, retry_count,
                       neo4j_updated_at, chroma_updated_at, payload,
                       started_at, finished_at, error_message, thread_id,
                       duration_ms, reason
                FROM sync_log
                WHERE entity_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (entity_id, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_pending_for_recovery(self, limit: int = 100) -> list[dict[str, Any]]:
        """查询待恢复的 pending 记录（进程崩溃重启时使用）。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, sync_type, entity_id, chunk_id, status, retry_count,
                       neo4j_updated_at, chroma_updated_at, payload,
                       started_at, finished_at, error_message, thread_id,
                       duration_ms, reason
                FROM sync_log
                WHERE status = 'pending'
                  AND retry_count < ?
                ORDER BY started_at ASC
                LIMIT ?
                """,
                (MAX_RETRY_COUNT, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def cleanup_old_records(self, days: int = 30) -> int:
        """清理超过 N 天的终态记录（success / failed / conflict）。

        Returns:
            清理的记录数。
        """
        conn = get_connection()
        try:
            cur = conn.execute(
                """
                DELETE FROM sync_log
                WHERE status IN ('success', 'failed', 'conflict')
                  AND started_at < datetime('now', '-' || ? || ' days', 'localtime')
                """,
                (int(days),),
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()


def get_sync_log_service() -> SyncLogService:
    """获取 SyncLogService 单例。"""
    return SyncLogService()


__all__ = [
    "SyncLogService",
    "get_sync_log_service",
    "SYNC_TYPE_GRAPH_TO_VECTOR",
    "SYNC_TYPE_VECTOR_TO_GRAPH",
    "SYNC_TYPE_EVENT",
    "SYNC_TYPE_ROLLBACK",
    "VALID_SYNC_TYPES",
    "STATUS_PENDING",
    "STATUS_SUCCESS",
    "STATUS_FAILED",
    "STATUS_CONFLICT",
    "VALID_STATUSES",
    "MAX_RETRY_COUNT",
]