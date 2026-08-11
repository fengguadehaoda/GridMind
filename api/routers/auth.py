"""V1.8.0 认证（T02）· /auth/* 路由。

端点（架构 auth-architecture §3.3 + PRD §5）：
- ``POST /auth/login``            公开 + slowapi 10/min/IP（per-IP 第二层防线）
- ``POST /auth/refresh``          公开（refresh 轮换）
- ``POST /auth/logout``           公开（revoke 幂等）
- ``GET  /auth/me``               ``verify_jwt_if_prod``（dev 放行 → 占位用户）
- ``POST /auth/change-password``  严格 ``verify_jwt_token``（改密撤销全部 refresh；
  见端点 docstring 关于 dev 模式的有据偏差说明）
- ``POST /auth/dev-login``        **仅非生产**（生产 404，fail-closed）

limiter 必须从共享模块导入（与 main.py app.state.limiter 同一实例，见
api/services/rate_limit.py）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.config import settings
from api.schemas.auth import (
    ChangePasswordRequest,
    DevLoginRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
)
from api.services.auth import (
    verify_jwt_if_prod,
    verify_jwt_token,
)
from api.services.auth_service import AuthService
from api.services.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])


def _request_meta(request: Request) -> tuple[str | None, str | None]:
    """提取客户端 IP / User-Agent（审计用，缺失容忍）。"""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.post("/login", response_model=LoginResponse)
@limiter.limit(lambda: f"{settings.login_rate_limit_per_minute}/minute")
async def login(request: Request, req: LoginRequest) -> LoginResponse:
    """登录（用户名+密码 → access+refresh；失败统一 401 文案防枚举）。

    slowapi per-IP 限流装饰器要求 ``request`` 作为首个参数；限流超限 → 429。
    """
    ip, ua = _request_meta(request)
    data = AuthService().login(req.username, req.password, ip=ip, user_agent=ua)
    return LoginResponse(**data)


@router.post("/refresh", response_model=LoginResponse)
async def refresh(request: Request, req: RefreshRequest) -> LoginResponse:
    """刷新：refresh → 新 access + **轮换后**新 refresh（旧 token 立即作废）。"""
    ip, ua = _request_meta(request)
    data = AuthService().refresh(req.refresh_token, ip=ip, user_agent=ua)
    return LoginResponse(**data)


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request, req: LogoutRequest) -> LogoutResponse:
    """登出：revoke 对应 refresh 行（幂等——不存在/已撤销也返回 200 ok）。"""
    AuthService().logout(req.refresh_token)
    return LogoutResponse(ok=True)


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> MeResponse:
    """当前用户信息（含密码过期提醒；dev 返回占位用户，前端以 id==="dev" 区分）。"""
    if identity is None:
        # dev 模式占位用户（架构 §八 待明确 #4：dev 走真实账号体系属 P2）
        return MeResponse(
            id="dev",
            username="dev",
            display_name="访客",
            role="dispatcher",
            must_change_password=False,
        )
    uid = identity.get("user_id") or identity.get("sub")
    data = AuthService().get_me(str(uid))
    return MeResponse(**data)


@router.post("/change-password")
async def change_password(
    request: Request,
    req: ChangePasswordRequest,
    identity: Annotated[dict[str, Any], Depends(verify_jwt_token)],
) -> dict[str, bool]:
    """修改密码（当前密码验证 → 策略校验 → 撤销该用户全部 refresh）。

    鉴权说明（相对架构 §3.3「verify_jwt_if_prod」的有据偏差）：
    改密属安全敏感操作，**必须**有明确身份主体；若沿用 verify_jwt_if_prod，
    dev 模式恒返回 None → 首次登录强制改密流程在 dev 直接 401（初始 admin
    must_change_password=1 无法完成改密）。改为严格 ``verify_jwt_token``——
    生产行为与 verify_jwt_if_prod 完全等价（生产即强制验签），dev 下只有
    携带真实登录 token 才可改密（dev 匿名无 token → 401，符合语义）。
    """
    uid = identity.get("user_id") or identity.get("sub")
    ip, ua = _request_meta(request)
    AuthService().change_password(
        str(uid), req.old_password, req.new_password, ip=ip, user_agent=ua
    )
    return {"ok": True}


@router.post("/dev-login", response_model=LoginResponse)
async def dev_login(request: Request, req: DevLoginRequest) -> LoginResponse:
    """dev 联调：签发带 role claim 的真实 JWT（**生产必须 404**，fail-closed）。"""
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not Found")
    ip, ua = _request_meta(request)
    data = AuthService().dev_login(req.role, ip=ip, user_agent=ua)
    return LoginResponse(**data)
