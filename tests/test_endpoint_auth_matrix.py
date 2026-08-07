"""端点鉴权矩阵回归测试（QA R2 新增）。

背景
----
R1 审计发现：生产模式下 13 个写/控制端点匿名可达，其中
``POST /interrupt/{tid}/approve`` 是电网高危操作的 HITL 审批闸门。
R2 工程师给 8 个端点挂了 ``Depends(verify_jwt_if_prod)``、给 3 个 admin
端点挂了 ``Depends(verify_admin_token)``，但**未附带任何测试**。

本文件把鉴权矩阵固化为回归测试，防止后续重构再次静默摘掉依赖。

覆盖三个方向（缺一不可）
------------------------
1. fail-closed：生产模式匿名 → 401（该拦的拦住）
2. fail-open ：生产模式带合法 JWT → 非 401（不误杀合法流量）
3. dev 放行  ：dev 模式匿名 → 非 401（``verify_jwt_if_prod`` 的 dev 分支仍在，
   本地开发零配置不被破坏）

测试隔离说明（重要）
--------------------
``_reload_settings`` 走 ``importlib.reload``，是**不可被 monkeypatch 回滚**的
全局副作用。R1 已在 ``test_kb_upload_api.py`` 因此踩过坑（生产态泄漏到后续
模块，把 ``POST /chat`` 打成 401）。因此本文件的 fixture **必须**在 teardown
显式复位 dev 态并再 reload 一次。
"""

from __future__ import annotations

import importlib
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

TID = "t-auth-matrix"

TEST_JWT_SECRET = "auth-matrix-secret-not-default-0123456789"
TEST_ADMIN_TOKEN = "auth-matrix-admin-token-not-default"

# ── 挂 verify_jwt_if_prod 的端点：(method, path, body) ──────────────────
JWT_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", f"/chat/stream/{TID}?message=hi", None),
    ("POST", "/chat", {"message": "hi", "thread_id": TID, "stream": False}),
    ("POST", f"/interrupt/{TID}/approve", {"reason": ""}),
    ("POST", f"/interrupt/{TID}/reject", {"reason": ""}),
    ("POST", f"/interrupt/{TID}/decision", {"decision": "approve"}),
    ("POST", f"/sessions/{TID}/pause", {}),
    ("POST", f"/sessions/{TID}/resume", {}),
    ("POST", f"/sessions/{TID}/abort", {}),
    ("POST", f"/sessions/{TID}/rewind", {"to_step": 0}),
    ("GET", "/devices", None),
    # R2 回归修复：模型切换是主聊天界面内的普通用户操作（ModelSwitcher），
    # 与 interrupt/session 写端点同属用户级控制面，复用 verify_jwt_if_prod；
    # 此前误挂 verify_admin_token，前端无 X-Admin-Token 来源导致活跃 UI 被打死。
    ("POST", "/models/switch", {"model_id": "mock"}),
]

# ── 挂 verify_admin_token 的端点（无 dev 分支，任何环境都要 header）──────
ADMIN_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/grayscale/set", {"ratio": 100}),
    ("POST", "/debug/sync_force", {}),
]


def _reload_auth_stack() -> None:
    """按依赖顺序重载，让最新 env 对鉴权依赖生效。

    - ``api.config``：settings 读新 env
    - ``api.services.auth``：``verify_jwt_if_prod`` 的 ``__globals__`` 原地更新
      （路由持有的旧函数对象同样读到新 settings，无需 reload 路由模块）
    - ``api.services.grayscale_admin_service``：``verify_admin_token`` 校验时
      函数内 import 该模块，其模块级 settings 需同步刷新
    """
    import api.config as config_mod

    importlib.reload(config_mod)
    import api.services.auth as auth_mod

    importlib.reload(auth_mod)
    from api.services import grayscale_admin_service as gas_mod

    importlib.reload(gas_mod)


def _make_client() -> TestClient:
    """不走 lifespan（避免真实 MCP / ChromaSync 副作用）。"""
    import api.main as main_mod

    return TestClient(main_mod.app, raise_server_exceptions=False)


@pytest.fixture
def prod_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产模式客户端。"""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_auth_stack()
    return _make_client()


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 模式客户端。"""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_auth_stack()
    return _make_client()


@pytest.fixture(autouse=True)
def _restore_dev_state():
    """teardown 复位 dev 态，防 importlib.reload 副作用泄漏到后续测试模块。

    见模块 docstring「测试隔离说明」——R1 真实踩坑，务必保留。
    """
    yield
    os.environ.pop("APP_ENV", None)
    os.environ.pop("PRODUCTION", None)
    _reload_auth_stack()


def _request(client: TestClient, method: str, path: str,
             body: dict[str, Any] | None, headers: dict[str, str] | None = None):
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, json=body if body is not None else {}, headers=headers)


# ═══════════════════════════════════════════════════════
# 1. fail-closed：生产模式匿名 → 401
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(("method", "path", "body"), JWT_ENDPOINTS)
def test_prod_anonymous_jwt_endpoint_returns_401(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """生产模式下匿名访问 JWT 端点必须 401（而非 503/422/200）。

    非 401 即代表请求已越过依赖注入进入业务处理 —— 鉴权缺失。
    """
    resp = _request(prod_client, method, path, body)
    assert resp.status_code == 401, (
        f"{method} {path} 应 401（匿名被拦），实际 {resp.status_code}；"
        f"非 401 说明请求已进入处理函数，鉴权依赖缺失"
    )


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
def test_prod_anonymous_admin_endpoint_returns_401(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """生产模式下匿名访问 admin 端点必须 401（缺 X-Admin-Token）。"""
    resp = _request(prod_client, method, path, body)
    assert resp.status_code == 401, (
        f"{method} {path} 应 401（缺 X-Admin-Token），实际 {resp.status_code}"
    )


# ═══════════════════════════════════════════════════════
# 2. fail-open：带合法凭证 → 非 401（不误杀合法流量）
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(("method", "path", "body"), JWT_ENDPOINTS)
def test_prod_valid_jwt_not_rejected(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """带合法 JWT 的请求不得被 401 误杀。

    header 结构与前端 ``useJwtAuth.getAuthHeaders()`` 一致：
    ``{"Authorization": "Bearer <jwt>"}``。
    业务码 503（graph 未就绪）/ 422（body 校验）均属正常，只要不是 401。
    """
    from api.services.auth import issue_test_token

    headers = {"Authorization": f"Bearer {issue_test_token(user_id='qa-user')}"}
    resp = _request(prod_client, method, path, body, headers=headers)
    assert resp.status_code != 401, (
        f"{method} {path} 携带合法 JWT 仍被 401 —— 合法流量被误杀"
    )


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
def test_prod_valid_admin_token_not_rejected(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """带正确 X-Admin-Token 的请求不得被 401/403 拒绝。"""
    headers = {"X-Admin-Token": TEST_ADMIN_TOKEN}
    resp = _request(prod_client, method, path, body, headers=headers)
    assert resp.status_code not in (401, 403), (
        f"{method} {path} 携带正确 admin token 仍被 {resp.status_code} 拒绝"
    )


# ═══════════════════════════════════════════════════════
# 3. dev 放行：本地开发零配置不被破坏
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(("method", "path", "body"), JWT_ENDPOINTS)
def test_dev_anonymous_jwt_endpoint_allowed(
    dev_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """dev 模式匿名访问 JWT 端点应放行（verify_jwt_if_prod 的 dev 分支）。

    这条守护「加鉴权不能破坏本地开发」——若某天有人把
    ``verify_jwt_if_prod`` 误换成 ``verify_jwt_token``，这里会立刻红。
    """
    resp = _request(dev_client, method, path, body)
    assert resp.status_code != 401, (
        f"dev 模式 {method} {path} 被 401 —— 本地开发链路被鉴权误伤"
    )
