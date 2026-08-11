"""RBAC 权限矩阵 · 单一权威定义（V1.8.0 增量 register-rbac T2）。

**与 require_role 调用点的一致性约定（架构 register-rbac 共享知识 #4）**：
- 本文件是「端点类别 → 角色可访问性」的**唯一权威**，``GET /rbac/matrix``
  直接序列化（前端**零硬编码**权限布尔值）；
- 一致性测试（tests/test_rbac_matrix.py）逐类别断言矩阵 == 各端点
  ``Depends(require_role(...))`` 实参与 owner 语义，任一漂移 → 测试红；
- ``require_role`` / ``get_role`` / ``verify_*`` 语义**零改动**（主理人拍板 7）。

矩阵语义与 multiuser-architecture §3.4 一致：
- 会话管理 = 全员 + owner 校验（``verify_thread_ownership_if_prod`` /
  ``verify_thread_ownership``）；admin 全量 → scope own/all；
- 灰度 = ``require_role(OPERATOR, ADMIN)``（读/写同权）；KB 写 =
  ``require_role(KB_ADMIN, ADMIN)``；KB 读 = 全员（``verify_jwt_if_prod``）；
- 审计 = ``AUDIT_FULL_ACCESS_ROLES``（auditor/operator/admin 全量）
  + dispatcher/kb_admin 仅本人（scope own）；
- 系统配置 = ``require_role(OPERATOR, ADMIN)``；模型切换 = 全员 + 有
  thread_id 时 owner 校验（scope 由会话 owner 语义表达，不入 SCOPE_MATRIX）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: 角色 → 端点类别 → 是否可访问（与 multiuser-architecture §3.4 矩阵一致）
ROLE_CATEGORY_MATRIX: dict[str, dict[str, bool]] = {
    "dispatcher": {"session": True,  "grayscale": False, "kb_write": False, "kb_read": True,  "audit": True,  "system": False, "model": True},
    "operator":   {"session": True,  "grayscale": True,  "kb_write": False, "kb_read": True,  "audit": True,  "system": True,  "model": True},
    "kb_admin":   {"session": True,  "grayscale": False, "kb_write": True,  "kb_read": True,  "audit": True,  "system": False, "model": True},
    "auditor":    {"session": True,  "grayscale": False, "kb_write": False, "kb_read": True,  "audit": True,  "system": False, "model": True},
    "admin":      {"session": True,  "grayscale": True,  "kb_write": True,  "kb_read": True,  "audit": True,  "system": True,  "model": True},
}

#: 类别 → 角色 → 数据可见范围（'own'=仅本人数据；'all'=全量可见）——扩展字段（拍板 6）
SCOPE_MATRIX: dict[str, dict[str, str]] = {
    "session": {"dispatcher": "own", "operator": "own", "kb_admin": "own", "auditor": "own", "admin": "all"},
    "audit":   {"dispatcher": "own", "operator": "all", "kb_admin": "own", "auditor": "all", "admin": "all"},
}

#: 角色元信息（P1-3：description 随矩阵同源下发）
ROLE_META: dict[str, dict[str, str]] = {
    "dispatcher": {"label": "调度员",     "description": "日常调度与对话；仅能访问自己的会话"},
    "operator":   {"label": "运维",       "description": "会话 + 灰度 + 系统配置（监控与运维）"},
    "kb_admin":   {"label": "知识管理员", "description": "会话 + 知识库读写"},
    "auditor":    {"label": "审计",       "description": "会话（仅本人）+ 审计全量只读"},
    "admin":      {"label": "管理员",     "description": "全部权限 + 用户管理"},
}

#: 端点类别元信息（行头悬浮展示代表端点）
CATEGORY_META: dict[str, dict[str, object]] = {
    "session":    {"label": "会话管理", "description": "对话、历史、诊断推理、HITL 审批、会话控制", "endpoints": ["/chat", "/chat/stream/{thread_id}", "/thread/{thread_id}", "/diagnosis/{thread_id}/reasoning", "/interrupt/{thread_id}/approve|reject|decision", "/sessions/{thread_id}/pause|resume|rewind|abort|events"]},
    "grayscale":  {"label": "灰度",     "description": "灰度切流、回滚、状态/历史/指标",             "endpoints": ["/grayscale/status", "/grayscale/set", "/grayscale/history", "/grayscale/metrics", "/grayscale/manual_rollback"]},
    "kb_write":   {"label": "KB 写",    "description": "知识库上传与删除（角色写权限）",             "endpoints": ["POST /api/knowledge/upload", "DELETE /api/knowledge/uploads/{id}"]},
    "kb_read":    {"label": "KB 读",    "description": "知识库列表与检索（全局共享）",               "endpoints": ["GET /api/knowledge/uploads"]},
    "audit":      {"label": "审计",     "description": "HITL 审计查询（全量或仅本人）",              "endpoints": ["GET /audit/hitl", "GET /audit/hitl/{thread_id}"]},
    "system":     {"label": "系统配置", "description": "检查点统计、同步状态/强制同步",              "endpoints": ["/admin/checkpoint-stats", "/debug/sync_lag", "/debug/sync_force"]},
    "model":      {"label": "模型切换", "description": "模型列表与切换（会话级 owner 校验）",         "endpoints": ["GET /models", "POST /models/switch"]},
}


def _now_iso() -> str:
    """当前 UTC ISO 时间串。"""
    return datetime.now(timezone.utc).isoformat()


def serialize_matrix() -> dict[str, Any]:
    """``GET /rbac/matrix`` 数据源：roles/categories/matrix/scope/generated_at。

    直接由权威定义生成（前端**不硬编码**任何权限布尔值）。
    ``generated_at`` = 当前 UTC ISO（每次实时生成，数据量小无需缓存）。
    """
    from api.services.rbac import ROLE_VALUES  # 5 角色值空间（顺序权威）

    roles = [{"key": r, **ROLE_META[r]} for r in sorted(ROLE_VALUES)]
    categories = [
        {"key": c, **CATEGORY_META[c]} for c in ROLE_CATEGORY_MATRIX["dispatcher"]
    ]
    return {
        "roles": roles,
        "categories": categories,
        "matrix": ROLE_CATEGORY_MATRIX,
        "scope": SCOPE_MATRIX,
        "generated_at": _now_iso(),
    }
