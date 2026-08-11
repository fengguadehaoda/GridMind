"""V1.8.0 认证（T02）· 认证事件审计服务。

写入 ``auth_audit_log`` 表（与 hitl_audit_log 共存，独立表）。

**审计写失败不阻断主流程**（架构共享知识 #8 + PRD AC7-4）：
:meth:`AuthAuditService.record` 内部 try/except → loguru.warning 降级，
绝不把审计当硬依赖（登录/刷新/登出主流程不因审计失败而失败）。

事件类型（PRD AC7-2）：
``login_success`` / ``login_failed`` / ``account_locked`` / ``logout`` /
``refresh`` / ``user_created`` / ``user_disabled`` / ``role_changed`` /
``password_changed``（另加 dev 专用 ``dev_login``）。

字段：user_id / username / ip_address / user_agent / detail（不存密码/明文 token）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from mcp_tools.db.database import get_connection


def _now_iso() -> str:
    """当前 UTC ISO 时间串（与 auth_service / user_service 同格式）。"""
    return datetime.now(timezone.utc).isoformat()


class AuthAuditService:
    """认证事件审计（静态方法，无状态）。"""

    @staticmethod
    def record(
        event_type: str,
        user_id: str | None = None,
        username: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        detail: str | None = None,
    ) -> None:
        """写入一条认证审计事件；失败仅告警，绝不抛错阻断主流程。

        Args:
            event_type: 事件类型（见模块 docstring）。
            user_id: 相关用户 id（可为 None，如登录失败且账号不存在）。
            username: 相关用户名（用于审计追溯）。
            ip_address: 客户端 IP。
            user_agent: 客户端 User-Agent。
            detail: 补充说明（不存密码 / 明文 token；如失败原因类别）。
        """
        try:
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO auth_audit_log "
                    "(event_type, user_id, username, ip_address, user_agent, detail, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_type,
                        user_id,
                        username,
                        ip_address,
                        user_agent,
                        detail,
                        _now_iso(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 审计写失败不阻断主流程（AC7-4）
            logger.warning(
                "auth audit record failed (non-fatal): event={} err={}",
                event_type, e,
            )

    @staticmethod
    def query_by_user(
        user_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询审计事件（**本批仅测试 / 运维调试用**；读端点属 P2，主理人拍板 #5）。

        Args:
            user_id: 按用户过滤（可选）。
            event_type: 按事件类型过滤（可选）。
            limit: 返回条数上限（默认 50）。

        Returns:
            审计事件 dict 列表（含 id/event_type/user_id/username/ip_address/
            user_agent/detail/created_at）。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            conn = get_connection()
            try:
                rows = conn.execute(
                    f"SELECT * FROM auth_audit_log {where} "
                    "ORDER BY id DESC LIMIT ?",
                    [*params, int(limit)],
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("auth audit query failed (non-fatal): {}", e)
            return []
