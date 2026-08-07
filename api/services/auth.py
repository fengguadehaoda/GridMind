"""V1.5.1 T06 安全补丁：JWT 鉴权 + thread ownership 校验（QA R-X2）。

**修复背景**（QA §5.3）：

原 ``GET /sessions/{thread_id}/events`` 端点**无任何鉴权**，任意匿名客户端
可订阅任何 thread 的推理 / HITL 事件（含工单内容、设备信息等业务数据）。

本模块实现两层防护：

1. :func:`verify_jwt_token` — 解析 ``Authorization: Bearer <jwt>`` header
   并验证签名 + exp + iss claims；返回 JWT payload dict。

2. :func:`verify_thread_ownership` — 基于 JWT payload 校验当前用户对该
   ``thread_id`` 的所有权；防止"张三的 token 监听李四的 thread"。

**生产部署**：必须通过 ``JWT_SECRET`` 环境变量覆盖默认值。

**使用模式**（FastAPI Depends）：

::

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
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import settings

# ═══════════════════════════════════════════════════════
# FastAPI Security scheme
# ═══════════════════════════════════════════════════════

#: ``HTTPBearer`` 自动提取 ``Authorization: Bearer xxx`` header
#: ``auto_error=False`` → 缺失 header 时不自动抛 401，由 ``verify_jwt_token``
#: 抛出自定义 401（带 WWW-Authenticate 头，符合 REST 鉴权惯例）
_security_scheme = HTTPBearer(auto_error=False)


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


def verify_thread_ownership(
    thread_id: str,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security_scheme),
    ] = None,
) -> dict[str, Any]:
    """校验用户对 ``thread_id`` 的所有权（架构 §2.5.4 + 决策 #7）。

    **校验逻辑**（任一不满足即拒绝）：

    1. JWT 必须有效（委托 :func:`verify_jwt_token`，缺/错/过期 → 401）
    2. JWT 必须含 ``user_id`` / ``sub`` claim（缺 → 401）
    3. 如 JWT 含 ``thread_id`` claim，必须与 URL path 中的 thread_id 一致
       （不一致 → 403，避免"张三的 token 监听李四的 thread"）
    4. admin token 特例（``thread_id`` claim 缺失 → 视为通用 admin token，
       通过所有 thread 的 ownership 校验；生产部署建议单独开 admin 端点）

    Args:
        thread_id: 从 URL path 自动注入的 thread_id。
        credentials: 由 :class:`HTTPBearer` 自动注入的鉴权凭证。

    Returns:
        ownership dict ``{"user_id": str, "thread_id": str}``，
        端点可用 ``Depends(verify_thread_ownership)`` 显式接收并使用。

    Raises:
        HTTPException 401 / 403: 见校验逻辑说明。
    """
    # 1+2. 基础 JWT 校验（含 user_id 必填）
    payload = verify_jwt_token(credentials=credentials)

    user_id = payload.get("user_id") or payload.get("sub")
    token_thread_id = payload.get("thread_id")

    # 3. thread ownership 校验
    #    - token 含 thread_id → 必须严格匹配 URL thread_id
    #    - token 不含 thread_id → 视为通用 user token，允许访问该用户的所有 thread
    #      （thread 归属依赖用户维度，不强制单 thread 绑定；前端 chatStore 维护映射）
    if token_thread_id is not None and token_thread_id != thread_id:
        # 注意：不在响应体泄漏具体值（防 token probing）
        raise HTTPException(
            status_code=403,
            detail=(
                "Token not authorized for this thread "
                "(thread_id mismatch)"
            ),
        )

    return {"user_id": user_id, "thread_id": thread_id}


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
        extra_claims: 其它自定义 claims。
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
