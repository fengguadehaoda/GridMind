"""V1.8.0 增量（register-rbac T2）· GET /rbac/matrix 响应模型。

与架构 register-rbac-architecture §3.2 对齐：
- ``RbacRoleMeta`` / ``RbacCategoryMeta``：roles/categories 元信息
  （``roles[].description`` / ``categories[].endpoints`` 后端权威维护，P1-3 同源）；
- ``RbacMatrixResponse``：roles/categories/matrix/scope/generated_at。

数据源 = ``api/services/rbac_matrix.serialize_matrix()``（单一权威定义，
前端**零硬编码**权限布尔值——矩阵只读展示，不承担安全边界）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RbacRoleMeta(BaseModel):
    """5 角色之一（dispatcher/operator/kb_admin/auditor/admin）的元信息。"""

    key: str
    label: str            # 展示名（调度员/运维/知识管理员/审计/管理员）
    description: str      # 一句话职责说明（后端权威维护）


class RbacCategoryMeta(BaseModel):
    """7 端点类别之一（session/grayscale/kb_write/kb_read/audit/system/model）的元信息。"""

    key: str
    label: str            # 展示名（会话管理/灰度/KB 写/KB 读/审计/系统配置/模型切换）
    description: str      # 类别说明
    endpoints: list[str]  # 代表端点（行头悬浮展示）


class RbacMatrixResponse(BaseModel):
    """``GET /rbac/matrix`` 响应（管理员；dev 放行、X-Admin-Token 等效）。"""

    roles: list[RbacRoleMeta]
    categories: list[RbacCategoryMeta]
    #: 核心契约：role -> category -> bool（该角色能否访问该端点类别）
    matrix: dict[str, dict[str, bool]] = Field(default_factory=dict)
    #: 扩展字段：category -> role -> 'own' | 'all'（owner 维度语义，拍板 6）
    scope: dict[str, dict[str, str]] = Field(default_factory=dict)
    generated_at: str  # UTC ISO（实时序列化生成）
