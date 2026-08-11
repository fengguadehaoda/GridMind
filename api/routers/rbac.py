"""V1.8.0 增量（register-rbac T2）· /rbac/* 路由（权限矩阵可视化）。

端点：
- ``GET /rbac/matrix`` —— 5 角色 × 7 端点类别权限矩阵（仅 admin；
  dev 放行、``X-Admin-Token`` 等效管理员——与 ``/users*`` 同权语义）。

数据源：``api/services/rbac_matrix.serialize_matrix()``（单一权威定义 +
实时生成 ``generated_at``；数据量小、无 DB 依赖，无需缓存）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from api.schemas.rbac_matrix import RbacMatrixResponse
from api.services.rbac import Role, require_role
from api.services.rbac_matrix import serialize_matrix

router = APIRouter(prefix="/rbac", tags=["rbac"])

#: 管理员依赖（dev 放行；生产 JWT+角色 / X-Admin-Token 等效管理员）
_AdminIdentity = Annotated[
    dict[str, Any], Depends(require_role(Role.ADMIN))
]


@router.get("/matrix", response_model=RbacMatrixResponse)
async def get_rbac_matrix(
    identity: _AdminIdentity = None,  # type: ignore[assignment]  # FastAPI 注入
) -> RbacMatrixResponse:
    """权限矩阵（5 角色 × 7 端点类别，只读展示数据源）。

    鉴权：``require_role(Role.ADMIN)``——dev 放行、生产强制 JWT+角色、
    ``X-Admin-Token`` 等效管理员（与 UsersView 同权；PRD §七 3 + 拍板 4）。

    矩阵数据**只来自**本端点（后端权威定义序列化），前端不硬编码任何
    权限布尔值；即使显示 ✓ 实际访问仍由后端 ``require_role`` / ``verify_*``
    判定（矩阵只读不承担安全，PRD AC5-3）。
    """
    return RbacMatrixResponse(**serialize_matrix())
