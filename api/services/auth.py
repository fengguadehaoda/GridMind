"""V1.5.1 T06 安全补丁：JWT 鉴权 + thread ownership 校验（QA R-X2）。

**修复背景**（QA §5.3）：

原 ``GET /sessions/{thread_id}/events`` 端点**无任何鉴权**，任意匿名客户端
可订阅任何 thread 的推理 / HITL 事件（含工单内容、设备信息等业务数据）。

本模块实现两层防护：

1. :func:`verify_jwt_token` — 解析 ``Authorization: Bearer <jwt>`` header
   并验证签名 + exp + iss claims；返回 JWT payload dict。

2. :func:`verify_thread_ownership` — 基于 JWT payload 校验当前用户对该
   ``thread_id`` 的所有权；防止"张三的 token 监听李四的 thread"。

**V1.7.0 多用户升级**（架构 multiuser-architecture §3.2 + PRD P0-2）：
- ``verify_thread_ownership`` 升级为 **DB owner 查询**，保留 token
  ``thread_id`` claim 快速路径（不匹配 → 403，防 probing）；
- 新增 :func:`verify_thread_ownership_if_prod`（dev 放行、prod 全量校验）；
- 新增 :func:`verify_audit_thread_access`（审计读：审计/运维/管理员全放行，
  调度员/知识管理员仅本人 —— PRD Q1 决策）；
- admin token 等效管理员（US-1.2）：有效 ``X-Admin-Token`` 通过 owner 校验；
- JWT ``role`` claim 解析在 ``api/services/rbac.get_role``（本模块 lazy import）。

**生产部署**：必须通过 ``JWT_SECRET`` 环境变量覆盖默认值。

**使用模式**（FastAPI Depends）：:

    from fastapi import Depends
    from api.services.auth import verify_thread_ownership

    @app.get("/sessions/{thread_id}/events",
             dependencies=[Depends(verify_thread_ownership)])
    async def subscribe_session_events(thread_id: str):
        ...
"""

from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import settings
from api.services.thread_store import (  # noqa: F401  # re-export（T2 任务要求 auth 提供）
    ThreadStore,
    ensure_thread_owned,
    get_model_for_thread,
    resolve_model,
    set_model_for_thread,
)

# ═══════════════════════════════════════════════════════
# FastAPI Security scheme
# ═══════════════════════════════════════════════════════

#: ``HTTPBearer`` 自动提取 ``Authorization: Bearer xxx`` header
#: ``auto_error=False`` → 缺失 header 时不自动抛 401，由 ``verify_jwt_token``
#: 抛出自定义 401（带 WWW-Authenticate 头，符合 REST 鉴权惯例）
_security_scheme = HTTPBearer(auto_error=False)

#: 越权响应文案（统一口径，不泄漏内部值）
_MSG_FORBIDDEN = "无权访问该会话"
_MSG_NOT_FOUND = "会话不存在"


def _raise_if_thread_deleted(thread_id: str) -> None:
    """M-5：软删除会话（``archived=2``）= 资源不存在，一律 404。

    语义（架构 session-mgmt §1.3 + 共享知识 #2）：
    - 无论 dev/prod、无论角色（**管理员同样 404**，已删会话不可复活访问）；
    - 防泄漏「会话曾存在」——与 ``ensure_thread_owned`` 的软删分支保持一致；
    - 覆盖 ``/sessions/{id}/events``、``/audit/hitl/{id}`` 等路径型依赖端点。
    """
    row = ThreadStore().get_thread(thread_id)
    if row is not None and int(row.get("archived") or 0) == 2:
        raise HTTPException(status_code=404, detail=_MSG_NOT_FOUND)


# ═══════════════════════════════════════════════════════
# JWT 鉴权依赖（FastAPI Depends）
# ═══════════════════════════════════════════════════════


def verify_jwt_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security_scheme),
    ] = None,
) -> dict[str, Any]:
    """JWT token 校验 + 解析 payload（架构 §2.5.4 + 决策 #7）。

    接受 ``Authorization: Bearer <jwt>`` header，验证：
    - 签名（HS256 / RS256 等）
    - ``exp`` claim（未过期）
    - ``iss`` claim（签发方匹配 ``settings.jwt_issuer``）
    - ``sub`` claim（用户 ID 必填）

    Args:
        credentials: 由 :class:`HTTPBearer` 自动注入的鉴权凭证。

    Returns:
        解码后的 JWT payload dict（至少含 ``sub`` / ``exp`` / ``iss``）。

    Raises:
        HTTPException 401: 缺失 header / token 无效 / 过期 / 缺必需 claim。
    """
    # 1. 缺失 header
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization header missing or malformed",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. 签名 / exp / iss 校验
    try:
        payload: dict[str, Any] = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token issuer (expected {settings.jwt_issuer!r})",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.InvalidTokenError as e:
        # 通用失败：签名错 / 格式错 / 缺 claim 等
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    # 3. user_id 必填（兼容 ``sub`` 与 ``user_id`` 两种 claim 命名）
    user_id = payload.get("user_id") or payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Token missing user_id / sub claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# ═══════════════════════════════════════════════════════
# 内部工具（V1.7.0 新增）
# ═══════════════════════════════════════════════════════


def _payload_user_id(payload: dict[str, Any]) -> str:
    """从 JWT payload 提取 user_id（兼容 sub / user_id 双命名）。"""
    return str(payload.get("user_id") or payload.get("sub") or "unknown")


def _is_valid_admin_token(request: Request | None) -> bool:
    """校验 ``X-Admin-Token`` header（等效管理员；无 request 视为无效）。"""
    if request is None:
        return False
    x_admin_token = request.headers.get("X-Admin-Token")
    if not x_admin_token:
        return False
    # lazy import 避免模块级循环（grayscale_admin_service 依赖 api.config）
    from api.services.grayscale_admin_service import GrayscaleAdminService

    return GrayscaleAdminService.verify_admin_token(x_admin_token)


def _check_thread_owner_db(
    thread_id: str,
    payload: dict[str, Any],
) -> None:
    """生产模式 DB owner 校验（懒登记优先于 404；管理员/admin token 放行）。

    步骤（架构 §1.3 步骤 2-6）：
    1. token ``thread_id`` claim 与 URL 不匹配 → 403（快速路径，防 probing，
       由调用方在进入本函数前执行——见 :func:`_claim_fast_path`）；
    2. admin token 有效 → 放行（US-1.2）；
    3. 管理员角色 → 放行；
    4. DB 无行 → 懒登记（首个已认证访问者接管，Q2 决策）；
    5. owner 不符 → 403；通过 → 放行。
    """
    # 管理员角色放行（跨用户视角）
    from api.services.rbac import Role, get_role

    # M-5：软删会话 → 404（双保险；调用方已前置检查，此处兜底）
    _raise_if_thread_deleted(thread_id)

    role = get_role(payload)
    if role == Role.ADMIN:
        return

    user_id = _payload_user_id(payload)
    # 懒登记 / owner 校验统一走 thread_store.ensure_thread_owned
    # （严格模式 404 / owner 不符 403 / 懒登记 INSERT OR IGNORE）
    ensure_thread_owned(thread_id, user_id, role)


def _claim_fast_path(thread_id: str, payload: dict[str, Any]) -> None:
    """token ``thread_id`` claim 快速路径：与 URL 不匹配 → 403（防 probing）。

    注意：不泄漏具体值；即使管理员 token 带错 thread_id claim 也 403
    （先于 DB 查询，防探测）。
    """
    token_thread_id = payload.get("thread_id")
    if token_thread_id is not None and token_thread_id != thread_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Token not authorized for this thread "
                "(thread_id mismatch)"
            ),
        )


# ═══════════════════════════════════════════════════════
# Thread ownership 依赖（V1.7.0 升级）
# ═══════════════════════════════════════════════════════


def verify_thread_ownership(
    thread_id: str,
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入；测试可省略
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security_scheme),
    ] = None,
) -> dict[str, Any]:
    """校验用户对 ``thread_id`` 的所有权（架构 §2.5.4 + 决策 #7 + V1.7.0 升级）。

    **校验逻辑**（任一不满足即拒绝）：

    1. JWT 必须有效（委托 :func:`verify_jwt_token`，缺/错/过期 → 401）
       —— events 端点保持「始终要求 token」的既有行为；
    2. 如 JWT 含 ``thread_id`` claim，必须与 URL path 中的 thread_id 一致
       （不一致 → 403，避免"张三的 token 监听李四的 thread"）；
    3. ``X-Admin-Token`` 有效 → 放行（等效管理员，US-1.2）；
    4. 非生产（dev）→ 返回（不做 DB owner 校验，本地开发零改动）；
    5. 生产：
       a. 管理员角色 → 放行；
       b. DB 无行 → 懒登记（首个已认证访问者接管）；
       c. owner 不符 → 403；严格模式未知 thread → 404。

    Args:
        thread_id: 从 URL path 自动注入的 thread_id。
        credentials: 由 :class:`HTTPBearer` 自动注入的鉴权凭证。
        request: FastAPI Request（读取 X-Admin-Token）。

    Returns:
        ownership dict ``{"user_id": str, "thread_id": str}``，
        端点可用 ``Depends(verify_thread_ownership)`` 显式接收并使用。

    Raises:
        HTTPException 401 / 403 / 404: 见校验逻辑说明。
    """
    # 1. 基础 JWT 校验（含 user_id 必填）
    payload = verify_jwt_token(credentials=credentials)

    # 2. claim 快速路径（先于 DB，防 probing）
    _claim_fast_path(thread_id, payload)

    # M-5：软删会话 → 404（dev/prod 一致、管理员/admin token 同样 404）
    _raise_if_thread_deleted(thread_id)

    # 3. admin token 等效管理员
    if _is_valid_admin_token(request):
        return {"user_id": _payload_user_id(payload), "thread_id": thread_id}

    # 4. dev 放行（不做 DB owner 校验）
    if not settings.is_production:
        return {"user_id": _payload_user_id(payload), "thread_id": thread_id}

    # 5. 生产 DB owner 校验
    _check_thread_owner_db(thread_id, payload)
    return {"user_id": _payload_user_id(payload), "thread_id": thread_id}


async def verify_thread_ownership_if_prod(
    thread_id: str,
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security_scheme),
    ] = None,
) -> dict[str, Any] | None:
    """生产模式强制 owner 校验；dev 直接放行（返回 None）。

    与 ``verify_jwt_if_prod`` 同语义（架构 §7.3：生产强制、dev 放行）。
    用于 ``/chat/stream/{id}``、``/thread/{id}``、``/interrupt/{id}/*``、
    ``/sessions/{id}/*``、``/diagnosis/{id}/reasoning`` 等会话端点。

    Returns:
        生产模式：owner 校验通过的 ownership dict；
        dev 模式：``None``（放行，本地开发零改动）。
    """
    if not settings.is_production:
        return None
    return verify_thread_ownership(
        thread_id,
        credentials=credentials,
        request=request,
    )


def verify_audit_thread_access(
    thread_id: str,
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security_scheme),
    ] = None,
) -> dict[str, Any] | None:
    """审计读单条端点（``/audit/hitl/{thread_id}``）角色化访问控制。

    **语义**（PRD §四 矩阵 + Q1 决策「本人放行」）：
    - dev → 放行（返回 None，与 verify_jwt_if_prod 一致）；
    - 生产：
      a. ``X-Admin-Token`` 有效 → 放行（等效管理员）；
      b. 角色为 审计/运维/管理员 → 全放行（跨用户监管视角）；
      c. 调度员/知识管理员 → owner 校验（仅本人 thread；懒登记 / 403 / 404
         语义与 ``verify_thread_ownership`` 一致）。

    Returns:
        生产模式：ownership dict；dev 模式：``None``。
    """
    if not settings.is_production:
        return None

    # 1. JWT 必填（401）
    payload = verify_jwt_token(credentials=credentials)

    # 2. claim 快速路径
    _claim_fast_path(thread_id, payload)

    # M-5：软删会话 → 404（dev/prod 一致、管理员/admin token 同样 404）
    _raise_if_thread_deleted(thread_id)

    # 3. admin token 等效管理员
    if _is_valid_admin_token(request):
        return {"user_id": _payload_user_id(payload), "thread_id": thread_id}

    # 4. 审计/运维/管理员全放行
    from api.services.rbac import AUDIT_FULL_ACCESS_ROLES, get_role

    role = get_role(payload)
    if role in AUDIT_FULL_ACCESS_ROLES:
        return {"user_id": _payload_user_id(payload), "thread_id": thread_id}

    # 5. 调度员/知识管理员：仅本人（owner 校验 + 懒登记）
    _check_thread_owner_db(thread_id, payload)
    return {"user_id": _payload_user_id(payload), "thread_id": thread_id}


def verify_jwt_if_prod(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security_scheme),
    ] = None,
) -> dict[str, Any] | None:
    """生产模式强制 JWT；dev 模式放行（B5：数据端点鉴权且不破坏本地开发）。

    用法与 ``verify_jwt_token`` 一致（FastAPI ``Depends``）：
    - ``APP_ENV=production``：缺失 / 无效 / 过期 token → 401（委托
      :func:`verify_jwt_token`），审计 / 设备 / 对话数据不再可匿名读取。
    - 默认 dev 模式（无 ``APP_ENV``）：直接返回 ``None``（不校验），
      前端无需携带 token，本地开发行为保持不变。

    Returns:
        生产模式下返回解码后的 JWT payload；dev 模式返回 ``None``。
    """
    if not settings.is_production:
        return None
    return verify_jwt_token(credentials=credentials)


# ═══════════════════════════════════════════════════════
# Test helpers（**仅** test 目录可见，避免污染生产 API）
# ═══════════════════════════════════════════════════════


def issue_test_token(
    user_id: str,
    thread_id: str | None = None,
    extra_claims: dict[str, Any] | None = None,
    expires_in_s: int = 3600,
) -> str:
    """签发测试用 JWT（仅供 tests/ 下使用）。

    Args:
        user_id: 用户 ID（会写入 ``sub`` claim）。
        thread_id: 可选 thread_id（写入 ``thread_id`` claim，用于校验绑定）。
        extra_claims: 其它自定义 claims（如 ``{"role": "operator"}``）。
        expires_in_s: 过期时间（秒），默认 3600（1h）。

    Returns:
        编码后的 JWT 字符串。
    """
    import time

    payload: dict[str, Any] = {
        "sub": user_id,
        "user_id": user_id,
        "iss": settings.jwt_issuer,
        "exp": int(time.time()) + expires_in_s,
        "iat": int(time.time()),
    }
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
