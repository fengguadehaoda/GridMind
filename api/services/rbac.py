"""V1.7.0 多用户地基 · RBAC 角色模型 + 权限解析器（P1-1）。

**角色来源 = JWT ``role`` claim**（PRD Q3 决策，不建用户表）：

- 5 角色枚举：``dispatcher / operator / kb_admin / auditor / admin``；
- ``get_role(payload)``：无 ``role`` claim 或未知值 → 默认 ``dispatcher``
  （fail-safe 最小权限，绝不 500）；
- ``require_role(*roles)``：FastAPI 依赖工厂——dev 直接放行；生产先验
  ``X-Admin-Token``（有效则等效管理员），否则 ``verify_jwt_token``（401），
  角色命中即过，未命中 403（admin token 与管理员角色等效，二选一通过）。

**循环依赖规避**（架构 §7.5）：``rbac`` 与 ``auth`` 相互引用必须函数内
lazy import —— 本模块对 ``auth.verify_jwt_token`` 采用函数内 import。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import enum
from typing import Annotated, Any, Awaitable, Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

#: ``HTTPBearer`` 自动提取 ``Authorization: Bearer xxx`` header（缺失不自动 401）
_security_scheme = HTTPBearer(auto_error=False)

#: 越权响应文案（统一口径，不泄漏内部值）
_MSG_FORBIDDEN = "权限不足"


class Role(str, enum.Enum):
    """GridMind 5 角色枚举（架构 §3.4 + PRD §四）。"""

    DISPATCHER = "dispatcher"   # 调度员
    OPERATOR = "operator"       # 运维
    KB_ADMIN = "kb_admin"       # 知识管理员
    AUDITOR = "auditor"         # 审计
    ADMIN = "admin"             # 管理员


#: 合法 role claim 值空间（与 JWT 签发方约定，PRD §四）
ROLE_VALUES: frozenset[str] = frozenset(r.value for r in Role)

#: 管理员等效角色集合（owner 校验跨用户放行用）
ADMIN_ROLES: frozenset[Role] = frozenset({Role.ADMIN})

#: 审计全量可见角色（audit 列表/单条：auditor/operator/admin）
AUDIT_FULL_ACCESS_ROLES: frozenset[Role] = frozenset(
    {Role.AUDITOR, Role.OPERATOR, Role.ADMIN}
)


def get_role(payload: dict[str, Any] | None) -> Role:
    """从 JWT payload 解析角色；缺失 / 未知值一律默认 ``dispatcher``。

    Args:
        payload: ``verify_jwt_token`` 返回的 JWT payload dict（可为 None）。

    Returns:
        :class:`Role` 枚举值，缺省/未知 → ``Role.DISPATCHER``（最小权限）。
    """
    if not isinstance(payload, dict):
        return Role.DISPATCHER
    raw = payload.get("role")
    if not isinstance(raw, str):
        return Role.DISPATCHER
    try:
        return Role(raw.strip().lower())
    except ValueError:
        # 未知 role claim → fail-safe 最小权限，绝不 500
        return Role.DISPATCHER


def role_allows(role: Role | str, required_roles: Any) -> bool:
    """判断角色是否命中所需角色集合。

    Args:
        role: 当前角色（Role 或字符串）。
        required_roles: 可迭代的 :class:`Role`（如 ``(Role.OPERATOR, Role.ADMIN)``）。

    Returns:
        True=命中；False=未命中。
    """
    role_value = role.value if isinstance(role, Role) else str(role)
    return role_value in {r.value for r in required_roles}


def _is_valid_admin_token(request: Request | None) -> bool:
    """校验 ``X-Admin-Token`` header（等效管理员；无 request 时视为无效）。"""
    if request is None:
        return False
    x_admin_token = request.headers.get("X-Admin-Token")
    if not x_admin_token:
        return False
    # lazy import 避免模块级循环（grayscale_admin_service 依赖 api.config）
    from api.services.grayscale_admin_service import GrayscaleAdminService

    return GrayscaleAdminService.verify_admin_token(x_admin_token)


def require_role(
    *roles: Role,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """生成一个 FastAPI 依赖：要求当前身份命中给定角色（生产强制）。

    **语义**（PRD P1-1 + 架构 §1.5 + 矩阵说明 4）：
    1. dev 模式（非 production）→ 直接放行，返回
       ``{"user_id": "dev", "role": "dispatcher"}``（本地开发零改动）；
    2. 生产模式：
       a. ``X-Admin-Token`` 有效 → 等效「管理员」，直接放行（二选一通过，
          兼容既有灰度/管理客户端，无需 JWT）；
       b. 否则 ``verify_jwt_token``（缺失/无效 → 401）；
       c. ``get_role(payload)`` 命中给定角色 → 放行；
       d. 未命中 → 403。

    Args:
        *roles: 允许的角色集合（如 ``require_role(Role.OPERATOR, Role.ADMIN)``）。

    Returns:
        鉴权主体 dict（含 ``user_id`` / ``role``），端点可用 Depends 接收。
    """
    allowed = frozenset(roles)

    async def _dependency(
        request: Request = None,  # type: ignore[assignment]  # FastAPI 注入
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_security_scheme),
        ] = None,
    ) -> dict[str, Any]:
        # lazy import：reload api.config 后取到最新 settings（避免引用过期）
        from api.config import settings

        # 1. admin token 显式提供（X-Admin-Token 非空）：
        #    - 有效 → 等效管理员（二选一通过，矩阵说明 4）；
        #    - 无效 → 403（fail-closed：显式提供错误凭证即拒绝，兼容旧
        #      verify_admin_token「错 token→403」语义，dev/prod 一致）
        x_admin_token = request.headers.get("X-Admin-Token") if request else None
        if x_admin_token:
            from api.services.grayscale_admin_service import GrayscaleAdminService

            if GrayscaleAdminService.verify_admin_token(x_admin_token):
                return {"user_id": "admin-token", "role": Role.ADMIN.value}
            raise HTTPException(status_code=403, detail="Invalid admin token")

        # 2. dev 模式（非 production，且无 admin token）→ 直接放行
        if not settings.is_production:
            return {"user_id": "dev", "role": Role.DISPATCHER.value}

        # 3. JWT 校验（缺失/无效 → 401）
        # lazy import 避免模块级循环（auth 内部 lazy import rbac）
        from api.services.auth import verify_jwt_token

        payload = verify_jwt_token(credentials=credentials)

        # 4. 角色命中
        role = get_role(payload)
        if role_allows(role, allowed):
            user_id = payload.get("user_id") or payload.get("sub") or "unknown"
            return {"user_id": user_id, "role": role.value}

        # 5. 未命中 → 403（越权，不泄漏内部值）
        raise HTTPException(status_code=403, detail=_MSG_FORBIDDEN)

    return _dependency
