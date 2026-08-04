"""P1-4：DiagnosisFusionResult 持久化服务。

将三层融合结果（DiagnosisFusionResult）写入独立的 ``diagnosis_fusion_log`` 表，
供事后追溯、QA 复核、回归测试使用。

**不修改 hitl_audit_log 表结构**（P0-3 已 LOCKED），避免破坏现有审计查询。

设计原则：
- **fail-closed（写入失败不影响主流程）**：异常时仅打 warning 日志，不抛错
- **零依赖**：复用 ``mcp_tools.db.database.get_connection``，不引入新连接池
- **JSON 持久化**：整条 ``fusion_result`` 序列化为 JSON 字符串（避免宽表）
- **轻量索引**：(thread_id, created_at, final_severity) 三索引支持常用查询
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from core.schemas.diagnosis import DiagnosisFusionResult
from mcp_tools.db.database import get_connection


# 保留期（年），与 hitl_audit_log 对齐
RETENTION_YEARS: int = 3


def persist_fusion_result(result: DiagnosisFusionResult) -> int | None:
    """将 DiagnosisFusionResult 写入 ``diagnosis_fusion_log`` 表。

    Args:
        result: 完整的三层融合结果（含 reasoning_chain）

    Returns:
        新插入行的 id（成功）；None（失败或无 thread_id）

    Notes:
        - 写入失败时 **不抛异常**，仅记录 warning 日志。
          这是 P1-4 的关键约束：**诊断可用性优先于审计完整性**。
        - 若 ``thread_id`` 为空，返回 None（无主键无法定位）。
    """
    thread_id = result.thread_id
    if not thread_id:
        logger.warning("DiagnosisFusionPersistence: thread_id is empty, skip persist")
        return None

    try:
        # 安全 JSON 序列化（Pydantic v2 model_dump 已确保字段类型正确）
        fusion_json = json.dumps(result.model_dump(), ensure_ascii=False, default=str)
        llm_confidence = float(result.llm_output.confidence) if result.llm_output else None
        final_severity = str(result.final_severity) if result.final_severity else None
        requires_human = 1 if result.requires_human_review else 0

        conn = get_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO diagnosis_fusion_log
                  (thread_id, fusion_result, llm_confidence,
                   final_severity, requires_human_review)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    fusion_json,
                    llm_confidence,
                    final_severity,
                    requires_human,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
            logger.info(
                "DiagnosisFusionPersistence: saved id={} thread_id={} severity={} hitl={}",
                new_id, thread_id, final_severity, requires_human,
            )
            return int(new_id) if new_id is not None else None
        finally:
            conn.close()
    except Exception as e:
        # P1-4 关键约束：写入失败不影响主流程
        logger.warning(
            "DiagnosisFusionPersistence: failed to persist thread_id={} ({}); "
            "main diagnosis flow continues without persistence",
            thread_id, e,
        )
        return None


def query_fusion_log(thread_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """按 thread_id 查询融合结果历史（最新在前）。

    Args:
        thread_id: 会话 ID
        limit: 返回最多条数（默认 10）

    Returns:
        包含完整 fusion_result JSON（已反序列化）的 dict 列表。
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, thread_id, fusion_result, llm_confidence,
                       final_severity, requires_human_review, created_at
                FROM diagnosis_fusion_log
                WHERE thread_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (thread_id, int(limit)),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("DiagnosisFusionPersistence: query failed for thread_id={}: {}", thread_id, e)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            fusion_obj = json.loads(row["fusion_result"]) if row["fusion_result"] else {}
        except json.JSONDecodeError:
            fusion_obj = {}
        out.append({
            "id": row["id"],
            "thread_id": row["thread_id"],
            "llm_confidence": row["llm_confidence"],
            "final_severity": row["final_severity"],
            "requires_human_review": bool(row["requires_human_review"]),
            "created_at": row["created_at"],
            "fusion_result": fusion_obj,
        })
    return out


__all__ = ["persist_fusion_result", "query_fusion_log", "RETENTION_YEARS"]
