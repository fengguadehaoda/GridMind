"""V1.8.0 认证（T02/T03）· Pydantic 请求/响应模型。

对齐架构 auth-architecture §3.3-3.4 + PRD §5 API 契约：

- 认证端点（/auth/*）：LoginRequest/Response、RefreshRequest/Response、
  LogoutRequest/Response、MeResponse、ChangePasswordRequest、DevLoginRequest；
- 用户管理端点（/users*）：UserSummary、UserCreateRequest、UserUpdateRequest、
  UsersListResponse（**不含 password_hash**）。

统一错误体 ``{"detail": "..."}`` 由 FastAPI HTTPException 生成（不在此定义）。

MFA 扩展点（主理人拍板 #6）：``LoginResponse.mfa_required`` 占位字段，
P2 接 TOTP 时在此扩展（登录流程已留 mfa 校验钩子）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════
# 认证端点
# ═══════════════════════════════════════════════════════


class LoginRequest(BaseModel):
    """POST /auth/login 请求体（登录标识 = username，主理人拍板 #5）。"""

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    """POST /auth/register 请求体（**不含 role**——固定 dispatcher，防注册即提权）。

    即使客户端恶意传 ``role``，Pydantic 默认 ``extra="ignore"`` 静默忽略，
    后端固定 ``role="dispatcher"``（架构 register-rbac 拍板 3 + PRD §八 2）。
    """

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=256)   # 可选（与 create_user 一致，拍板 3）


class LoginUserInfo(BaseModel):
    """登录成功响应中的用户摘要（不含敏感字段）。"""

    id: str
    username: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    """POST /auth/login 与 POST /auth/refresh 成功响应（结构一致）。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    # MFA 扩展点（P2）：本批恒为 False，前端按字段预留 UI 分支
    mfa_required: bool = False
    user: LoginUserInfo


class RefreshRequest(BaseModel):
    """POST /auth/refresh 请求体（opaque refresh token，明文仅传输一次）。"""

    refresh_token: str = Field(..., min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    """POST /auth/logout 请求体（revoke 对应 refresh 行，幂等）。"""

    refresh_token: str = Field(..., min_length=1, max_length=512)


class LogoutResponse(BaseModel):
    """POST /auth/logout 成功响应（幂等——已撤销/不存在也返回 ok）。"""

    ok: bool = True


class MeResponse(BaseModel):
    """GET /auth/me 响应（含密码过期信息，90 天策略拍板 #2）。"""

    id: str
    username: str
    display_name: str
    role: str
    must_change_password: bool
    last_login_at: str | None = None
    # 密码过期时间（password_changed_at + PASSWORD_EXPIRY_DAYS）；未设置过 → None
    password_expires_at: str | None = None
    # ≤7 天或已过期 → True（前端一次性 ElMessage 提醒，不强制）
    password_expiring: bool = False


class ChangePasswordRequest(BaseModel):
    """POST /auth/change-password 请求体（当前密码 + 新密码）。"""

    old_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


class DevLoginRequest(BaseModel):
    """POST /auth/dev-login 请求体（**仅非生产**；生产必须 404）。"""

    role: str = Field(..., min_length=1, max_length=32)


# ═══════════════════════════════════════════════════════
# 用户管理端点（/users*）
# ═══════════════════════════════════════════════════════


class UserSummary(BaseModel):
    """用户摘要（GET/POST/PATCH /users 通用；**不含 password_hash**）。"""

    id: str
    username: str
    email: str | None = None
    role: str
    disabled: int
    must_change_password: int
    last_login_at: str | None = None
    created_at: str | None = None


class UserCreateRequest(BaseModel):
    """POST /users 请求体（仅管理员；创建后默认 must_change_password=1）。"""

    username: str = Field(..., min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=256)
    password: str = Field(..., min_length=1, max_length=256)
    role: str = Field(..., min_length=1, max_length=32)


class UserUpdateRequest(BaseModel):
    """PATCH /users/{id} 请求体（至少一项；role/disabled/password 均可选）。"""

    role: str | None = Field(default=None, max_length=32)
    disabled: int | None = Field(default=None, ge=0, le=1)
    password: str | None = Field(default=None, min_length=1, max_length=256)


class UsersListResponse(BaseModel):
    """GET /users 响应（分页 + 过滤；不含 password_hash）。"""

    users: list[UserSummary]
    total: int
