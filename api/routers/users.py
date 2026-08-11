"""V1.8.0 认证（T03）· /users* 路由（管理员用户管理）。

端点（架构 auth-architecture §3.4 + PRD §5.5-5.7）：
- ``GET   /users``        管理员列表 + role/disabled/q/page 过滤（不含 hash）
- ``POST  /users``        管理员创建（用户名冲突 409 / 密码策略 422）
- ``PATCH /users/{id}``   管理员改角色/禁用/改密（最后 admin 防呆 409）

全部 ``Depends(require_role(Role.ADMIN))``——dev 放行、生产强制 JWT+角色、
``X-Admin-Token`` 等效管理员（与既有 RBAC 语义完全一致，零改动）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from api.schemas.auth import (
    UserCreateRequest,
    UserSummary,
    UsersListResponse,
    UserUpdateRequest,
)
from api.services.rbac import Role, require_role
from api.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

#: 管理员依赖（dev 放行；生产 JWT+角色 / X-Admin-Token 等效管理员）
_AdminIdentity = Annotated[
    dict[str, Any], Depends(require_role(Role.ADMIN))
]


def _request_meta(request: Request) -> tuple[str | None, str | None]:
    """提取客户端 IP / User-Agent（审计用，缺失容忍）。"""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.get("", response_model=UsersListResponse)
async def list_users(
    request: Request,
    role: str | None = None,
    disabled: int | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    identity: _AdminIdentity = None,  # type: ignore[assignment]  # FastAPI 注入
) -> UsersListResponse:
    """用户列表（role/disabled 过滤 + q 模糊 + 分页；**不含 password_hash**）。"""
    data = UserService().list_users(
        role=role, disabled=disabled, q=q, page=page, page_size=page_size
    )
    return UsersListResponse(**data)


@router.post("", response_model=UserSummary, status_code=201)
async def create_user(
    request: Request,
    req: UserCreateRequest,
    identity: _AdminIdentity = None,  # type: ignore[assignment]  # FastAPI 注入
) -> UserSummary:
    """创建用户（默认 must_change_password=1；用户名冲突 409；密码策略 422）。"""
    ip, ua = _request_meta(request)
    user = UserService().create_user(
        username=req.username,
        password=req.password,
        role=req.role,
        email=req.email,
        actor_id=identity.get("user_id"),
        ip_address=ip,
        user_agent=ua,
    )
    return UserSummary(**user)


@router.patch("/{user_id}", response_model=UserSummary)
async def update_user(
    user_id: str,
    request: Request,
    req: UserUpdateRequest,
    identity: _AdminIdentity = None,  # type: ignore[assignment]  # FastAPI 注入
) -> UserSummary:
    """更新用户（改角色/禁用/改密；404 不存在 / 422 策略 / 409 最后 admin 防呆）。"""
    ip, ua = _request_meta(request)
    user = UserService().update_user(
        user_id=user_id,
        role=req.role,
        disabled=req.disabled,
        password=req.password,
        actor_id=identity.get("user_id"),
        ip_address=ip,
        user_agent=ua,
    )
    return UserSummary(**user)
