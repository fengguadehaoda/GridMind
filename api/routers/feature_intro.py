"""功能介绍知识库路由（V1.6 · 功能介绍知识库化 T4）。

**背景**：GridMind 前端引导（onboarding 场景卡、driver.js tour、向导第 3 步）
的文案原先硬编码在 Vue 组件里，运营改一句话要发一次前端版本。本需求把这些
文案抽离到 ``docs/gridmind-feature-introduction.md``，经
``scripts/seed_feature_intro.py`` 灌入 SQLite + Chroma，再由本路由对外提供。

**端点**：

======================================================  ======  ==========================
路径                                                     方法    鉴权
======================================================  ======  ==========================
``/api/knowledge/feature-intro``                        GET     可选 JWT（见下方说明）
``/api/knowledge/feature-intro/reload``                 POST    X-Admin-Token 或 JWT admin
======================================================  ======  ==========================

**GET 鉴权设计（与任务书的偏差，已评审）**：

任务书原定 ``Depends(verify_jwt_token)`` 强制 JWT。实施中发现两点冲突：

1. 前端 dev token 默认值是 ``gridmind-dev-token``（见
   ``web/src/composables/useJwtAuth.ts``），它是**普通字符串而非合法 JWT**，
   强制校验会让开发/演示环境 100% 落到 401 → 前端永远走本地兜底，
   本需求等于没上线。
2. 功能介绍文案是**面向所有访客的公开 UI 文案**，不含任何工单、设备、
   用户数据，不构成信息泄漏面。

因此 GET 采用 :func:`verify_jwt_optional`：带合法 JWT 则解析出身份写入日志，
带非法 / 不带 token 也放行（视为匿名）。写操作（reload）维持强鉴权不变。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel, Field

from api.config import settings
from core.vector_store import get_vector_store

# ═══════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════

#: 功能介绍分片的根标签（seed 脚本保证每个分片都带该标签）
ROOT_TAG: str = "feature-intro"

#: GET 单次返回上限（当前文档 18 个分片，留足冗余）
MAX_ITEMS: int = 200

#: JWT payload 中被视为管理员的 role / roles 取值
_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "administrator", "superuser"})

#: 可选鉴权 scheme：auto_error=False → 缺 header 不自动抛 401
_optional_security = HTTPBearer(auto_error=False)


# ═══════════════════════════════════════════════════════
# 响应模型
# ═══════════════════════════════════════════════════════


class FeatureIntroItem(BaseModel):
    """单条功能介绍分片（与 ``VectorStore._to_item`` 输出一一对应）。"""

    id: str = Field(default="", description="业务 id，如 tour-chat / monitor-overview")
    doc_id: str = Field(default="", description="库内唯一 id，如 feature-intro:tour-chat")
    title: str = Field(default="", description="章节标题")
    content: str = Field(default="", description="正文（Markdown 纯文本）")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    icon: str | None = Field(default=None, description="Element Plus 图标名")
    starterMessage: str | None = Field(  # noqa: N815 — 对齐前端 camelCase 契约
        default=None,
        description="场景卡片点击后自动填入的引导语",
    )
    source: str = Field(default="", description="来源文档相对路径")
    meta: dict[str, Any] = Field(default_factory=dict, description="结构化附加字段")


class FeatureIntroResponse(BaseModel):
    """``GET /api/knowledge/feature-intro`` 响应体。"""

    items: list[FeatureIntroItem] = Field(default_factory=list, description="分片列表")
    total: int = Field(default=0, description="本次返回条数")
    tag: str = Field(default="", description="本次过滤使用的标签（空串表示全部）")


class ReloadResponse(BaseModel):
    """``POST /api/knowledge/feature-intro/reload`` 响应体。"""

    status: str = Field(default="ok", description="固定为 ok；失败时以 HTTP 5xx 返回")
    count: int = Field(default=0, description="重载后库内分片总数")


# ═══════════════════════════════════════════════════════
# 鉴权依赖
# ═══════════════════════════════════════════════════════


def verify_jwt_optional(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_optional_security),
    ] = None,
) -> dict[str, Any]:
    """可选 JWT 校验：解析成功返回 payload，失败 / 缺失一律返回匿名身份。

    只用于**只读公开内容**端点；任何写操作都不得使用本依赖。

    Args:
        credentials: 由 :class:`HTTPBearer` 注入的 Bearer 凭证（可为 None）。

    Returns:
        JWT payload dict；无法解析时返回 ``{"sub": "anonymous", "anonymous": True}``。
    """
    anonymous: dict[str, Any] = {"sub": "anonymous", "anonymous": True}
    if credentials is None or not credentials.credentials:
        return anonymous
    try:
        payload: dict[str, Any] = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.InvalidTokenError:
        # dev token（非 JWT 明文串）会走到这里；只读端点降级为匿名，不阻断
        return anonymous
    return payload


def _is_admin_payload(payload: dict[str, Any]) -> bool:
    """判断 JWT payload 是否具备管理员身份。

    兼容三种常见 claim 写法：``role: "admin"`` / ``roles: ["admin"]`` /
    ``is_admin: true``。

    Args:
        payload: 已解码的 JWT payload。

    Returns:
        True 表示是管理员。
    """
    if payload.get("is_admin") is True:
        return True
    role = str(payload.get("role") or "").strip().lower()
    if role in _ADMIN_ROLES:
        return True
    roles = payload.get("roles")
    if isinstance(roles, (list, tuple, set)):
        return any(str(r).strip().lower() in _ADMIN_ROLES for r in roles)
    return False


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_optional_security),
    ] = None,
) -> dict[str, Any]:
    """管理员鉴权：``X-Admin-Token`` **或** 带 admin 角色的合法 JWT，二选一。

    Args:
        x_admin_token: ``X-Admin-Token`` 请求头，与 ``settings.admin_token`` 比对。
        credentials: Bearer 凭证，用于 JWT admin 分支。

    Returns:
        鉴权主体信息 dict，如 ``{"principal": "admin-token"}``。

    Raises:
        HTTPException 401: 两种凭证都未提供。
        HTTPException 403: 提供了凭证但校验不通过 / 非管理员。
    """
    has_admin_header = bool(x_admin_token)
    has_bearer = credentials is not None and bool(credentials.credentials)

    # 分支 1：X-Admin-Token（恒定时间比较，复用灰度服务实现防时序攻击）
    if has_admin_header:
        from api.services.grayscale_admin_service import GrayscaleAdminService

        if GrayscaleAdminService.verify_admin_token(x_admin_token or ""):
            return {"principal": "admin-token"}

    # 分支 2：JWT admin
    if has_bearer:
        payload = verify_jwt_optional(credentials=credentials)
        if not payload.get("anonymous") and _is_admin_payload(payload):
            return {
                "principal": "jwt-admin",
                "user_id": payload.get("user_id") or payload.get("sub"),
            }

    if not has_admin_header and not has_bearer:
        raise HTTPException(
            status_code=401,
            detail="Admin credential required (X-Admin-Token or Bearer admin JWT)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise HTTPException(status_code=403, detail="Invalid admin credential")


# ═══════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/feature-intro", response_model=FeatureIntroResponse)
async def get_feature_intro(
    tag: Annotated[
        str | None,
        Query(
            description=(
                "过滤标签；省略则返回全部功能介绍分片。"
                "常用值：feature-intro / scenario:fault-diagnosis / "
                "tour:chat / view:monitor / wizard:step3"
            ),
            max_length=120,
        ),
    ] = None,
    identity: Annotated[dict[str, Any], Depends(verify_jwt_optional)] = None,  # type: ignore[assignment]
) -> FeatureIntroResponse:
    """读取功能介绍分片（前端 onboarding / tour 文案数据源）。

    默认返回 ``feature-intro`` 根标签下的全部分片，由前端
    ``useFeatureIntro`` 按 ``scenario:`` / ``tour:`` 前缀二次分类。

    Args:
        tag: 可选过滤标签。
        identity: 可选 JWT 身份（匿名亦可访问，仅用于日志）。

    Returns:
        :class:`FeatureIntroResponse` —— ``{items, total, tag}``。

    Raises:
        HTTPException 503: 知识库不可用（Chroma / SQLite 初始化失败）。
    """
    wanted = (tag or "").strip() or ROOT_TAG
    try:
        store = get_vector_store()
        raw_items = store.search_by_tag(wanted, top_k=MAX_ITEMS)
    except Exception as exc:  # noqa: BLE001 — 知识库故障不应 500 打断前端
        logger.warning("feature-intro 读取失败（前端将走本地兜底）：{}", exc)
        raise HTTPException(
            status_code=503,
            detail="Feature-intro knowledge base unavailable",
        ) from exc

    principal = identity.get("sub") if isinstance(identity, dict) else "anonymous"
    logger.debug(
        "feature-intro 命中 {} 条（tag={}，principal={}）",
        len(raw_items),
        wanted,
        principal,
    )
    return FeatureIntroResponse(
        items=[FeatureIntroItem(**item) for item in raw_items],
        total=len(raw_items),
        tag=wanted,
    )


@router.post("/feature-intro/reload", response_model=ReloadResponse)
async def reload_feature_intro(
    principal: Annotated[dict[str, Any], Depends(require_admin)],
) -> ReloadResponse:
    """重新解析文档并灌库（运营改完 Markdown 后热更新，无需重启服务）。

    执行链路：``scripts.seed_feature_intro.seed_feature_intro()``
    → 解析 Markdown → ``VectorStore.upsert_chunks()`` → 覆盖 SQLite + Chroma
    → ``VectorStore.reload()`` 刷新进程内缓存。

    Args:
        principal: 由 :func:`require_admin` 注入的鉴权主体。

    Returns:
        :class:`ReloadResponse` —— ``{"status": "ok", "count": N}``。

    Raises:
        HTTPException 500: 文档解析或写库失败（原因写入 detail）。
    """
    # 延迟导入：避免 API 启动即加载 seed 脚本（其会触发 sys.path 调整）
    from scripts.seed_feature_intro import seed_feature_intro

    store = get_vector_store()
    try:
        result = seed_feature_intro(store=store)
    except FileNotFoundError as exc:
        logger.error("feature-intro reload 失败：文档不存在 {}", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Feature-intro document not found: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("feature-intro reload 失败：{}", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Feature-intro reload failed: {exc}",
        ) from exc

    count = int(result.get("count", 0))
    logger.info(
        "feature-intro reload 完成：{} 个分片（principal={}）",
        count,
        principal.get("principal", "unknown"),
    )
    return ReloadResponse(status="ok", count=count)
