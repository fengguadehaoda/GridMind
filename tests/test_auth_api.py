"""V1.8.0 认证（T02）· /auth/* 端点测试。

**覆盖**（架构 auth-architecture Task 2 验收 + PRD §5.1-5.4/5.8）：
1. 登录成功 → access+refresh 双 token、user 含 role；JWT claims 含
   sub/user_id/role/name/iss/iat/exp，**不含 thread_id**（共享知识 #1）；
2. 登录失败统一 401（账号不存在与密码错同文案，防枚举）；
3. 禁用 → 403；锁定 → 423（Retry-After）；IP 超 10/min → 429；
4. refresh 轮换：新双 token 返回、旧 token 二次使用 401、replaced_by 成链；
5. logout 幂等（不存在/重复也 200）；
6. GET /auth/me：dev 占位 / 生产真实用户；禁用用户 me → 401；
7. 改密撤销全部 refresh；新密码可登录、旧密码 401；
8. dev-login：dev 签发带 role claim JWT；生产 404（fail-closed）；
9. 产线无 admin fail-closed：ensure_initial_admin → SystemExit。

**隔离**：主库切 tmp（database + auth_service + user_service +
auth_audit_service 四处 get_connection），reload 鉴权栈让 APP_ENV=production
生效；每测试前重置 slowapi 限流计数（防跨测试累积假 429）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "auth-api-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "auth-api-admin-token"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Admin@123456"
DISPATCHER_PASSWORD = "Dispatch#2026"


def _connect(tmp_db: Path):
    """生成指向 tmp DB 的 get_connection 替代函数。"""

    def patched() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return patched


def _reload_stack() -> None:
    """reload 鉴权栈（config → rbac → thread_store → auth → services → routers → main）。"""
    import api.config as config_mod

    importlib.reload(config_mod)
    import api.services.rbac as rbac_mod

    importlib.reload(rbac_mod)
    import api.services.thread_store as ts_mod

    importlib.reload(ts_mod)
    import api.services.auth as auth_mod

    importlib.reload(auth_mod)
    from api.services import grayscale_admin_service as gas_mod

    importlib.reload(gas_mod)
    import api.services.hitl_audit_service as has_mod

    importlib.reload(has_mod)
    import api.services.user_service as user_svc

    importlib.reload(user_svc)
    import api.services.auth_audit_service as audit_svc

    importlib.reload(audit_svc)
    import api.services.auth_service as auth_svc

    importlib.reload(auth_svc)
    import api.routers.auth as auth_router

    importlib.reload(auth_router)
    import api.routers.users as users_router

    importlib.reload(users_router)
    import api.main as main_mod

    importlib.reload(main_mod)


def _reset_limiter_state() -> None:
    """重置 slowapi 计数 + 清除路由限流注册（防 reload 累积重复注册）。

    关键：``@limiter.limit(lambda: ...)`` 属动态限流，注册进
    ``_dynamic_route_limits``；每次 ``_reload_stack()`` 重跑装饰器会
    **追加**一条注册（不覆盖），导致单次请求被检查 N 次、计数 N 倍递增，
    测试间假 429。故每次客户端 fixture 设置时（reload 前）清空注册表，
    reload 后恰好剩 1 条注册。
    """
    import api.main as main_mod

    limiter = main_mod.app.state.limiter
    try:
        limiter._storage.reset()
    except AttributeError:
        pass
    limiter._route_limits.clear()
    limiter._dynamic_route_limits.clear()


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 客户端（主库切 tmp；不强制登录）。"""
    _reset_limiter_state()  # 必须先清空（reload 前），确保测试内恰好 1 条限流注册
    _patch_db(monkeypatch, tmp_path / "auth_api_dev.db")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)
    _teardown_env(monkeypatch)


@pytest.fixture
def prod_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产客户端（主库切 tmp；APP_ENV=production + 强 JWT_SECRET/ADMIN_TOKEN）。"""
    _reset_limiter_state()  # 必须先清空（reload 前），确保测试内恰好 1 条限流注册
    _patch_db(monkeypatch, tmp_path / "auth_api_prod.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)
    _teardown_env(monkeypatch)


def _patch_db(monkeypatch: pytest.MonkeyPatch, tmp_db: Path) -> None:
    """把 database / auth_service / user_service / auth_audit_service 的
    get_connection 一并切到 tmp 库（沿用既有测试模式），并 init_db。"""
    import mcp_tools.db.database as db_mod
    import api.services.auth_service as auth_svc
    import api.services.user_service as user_svc
    import api.services.auth_audit_service as audit_svc

    patched = _connect(tmp_db)
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(auth_svc, "get_connection", patched)
    monkeypatch.setattr(user_svc, "get_connection", patched)
    monkeypatch.setattr(audit_svc, "get_connection", patched)
    db_mod.init_db()


def _teardown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    _reload_stack()


# ── 辅助 ──────────────────────────────────────────────────────


def _create_user(
    username: str,
    password: str,
    role: str = "dispatcher",
    email: str | None = None,
) -> dict[str, Any]:
    """直接经 UserService 创建用户（绕过 lifespan / 路由鉴权）。"""
    from api.services.user_service import UserService

    return UserService().create_user(
        username=username,
        password=password,
        role=role,
        email=email,
        actor_id="test",
    )


def _decode(token: str) -> dict[str, Any]:
    """解签 access token（TEST_JWT_SECRET）。"""
    return jwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])


def _login(client: TestClient, username: str, password: str) -> dict[str, Any]:
    resp = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _count_refresh_rows(tmp_db: Path) -> int:
    with sqlite3.connect(str(tmp_db)) as conn:
        return conn.execute("SELECT COUNT(*) FROM refresh_tokens").fetchone()[0]


# ═══════════════════════════════════════════════════════════
# 1. 登录成功 / claims
# ═══════════════════════════════════════════════════════════


def test_login_success_returns_dual_tokens_and_role(
    dev_client: TestClient,
) -> None:
    """AC3-1：登录成功返回 access + refresh + user(role)。"""
    _create_user("alice", DISPATCHER_PASSWORD, role="operator")
    data = _login(dev_client, "alice", DISPATCHER_PASSWORD)

    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert data["user"]["username"] == "alice"
    assert data["user"]["role"] == "operator"
    assert data["user"]["id"]


def test_login_jwt_claims_include_role_name_no_thread_id(
    dev_client: TestClient,
) -> None:
    """共享知识 #1：claims 与 issue_test_token 同构 + role/name；不含 thread_id。"""
    _create_user("bob", DISPATCHER_PASSWORD, role="auditor")
    data = _login(dev_client, "bob", DISPATCHER_PASSWORD)

    payload = _decode(data["access_token"])
    assert payload["sub"] == data["user"]["id"]
    assert payload["user_id"] == data["user"]["id"]
    assert payload["role"] == "auditor"
    assert payload["name"] == "bob"
    assert payload["iss"] == "gridmind"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert "thread_id" not in payload  # 关键：绝不注入 thread_id（防快速路径误伤）


# ═══════════════════════════════════════════════════════════
# 2. 登录失败统一文案 / 禁用 / 锁定 / 限流
# ═══════════════════════════════════════════════════════════


def test_login_failure_uniform_401(dev_client: TestClient) -> None:
    """AC2-1：账号不存在与密码错统一 401 文案（防枚举）。"""
    _create_user("carol", DISPATCHER_PASSWORD)

    resp1 = dev_client.post(
        "/auth/login", json={"username": "no-such-user", "password": "Whatever#1"}
    )
    assert resp1.status_code == 401
    assert resp1.json()["detail"] == "用户名或密码错误"

    resp2 = dev_client.post(
        "/auth/login", json={"username": "carol", "password": "WrongPass#1"}
    )
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == "用户名或密码错误"


def test_login_disabled_403(dev_client: TestClient) -> None:
    """AC2-2：禁用账号 → 403「账号已被禁用」（仅密码验证通过后）。"""
    user = _create_user("dave", DISPATCHER_PASSWORD)
    from api.services.user_service import UserService

    UserService().update_user(user["id"], disabled=1, actor_id="test")

    resp = dev_client.post(
        "/auth/login", json={"username": "dave", "password": DISPATCHER_PASSWORD}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "账号已被禁用"


def test_login_lockout_423_with_retry_after(dev_client: TestClient) -> None:
    """AC2-3/2-4：连续失败 ≥5 锁定 15min；锁定期即使密码正确也 423 + Retry-After。"""
    _create_user("erin", DISPATCHER_PASSWORD)

    for _ in range(5):
        resp = dev_client.post(
            "/auth/login", json={"username": "erin", "password": "WrongPass#1"}
        )
        assert resp.status_code == 401

    # 锁定期：正确密码也 423
    resp = dev_client.post(
        "/auth/login", json={"username": "erin", "password": DISPATCHER_PASSWORD}
    )
    assert resp.status_code == 423
    assert resp.json()["detail"] == "尝试次数过多，账号已锁定，请稍后再试"
    assert resp.headers.get("Retry-After") is not None


def test_login_rate_limit_429(dev_client: TestClient) -> None:
    """AC2-5：per-IP 10/min 限流，第 11 次 → 429（slowapi）。"""
    # 用不存在账号避免锁定干扰；前 10 次统一 401
    for _ in range(10):
        resp = dev_client.post(
            "/auth/login", json={"username": "ghost", "password": "Whatever#1"}
        )
        assert resp.status_code == 401

    resp = dev_client.post(
        "/auth/login", json={"username": "ghost", "password": "Whatever#1"}
    )
    assert resp.status_code == 429


# ═══════════════════════════════════════════════════════════
# 3. refresh 轮换 / 登出幂等
# ═══════════════════════════════════════════════════════════


def test_refresh_rotates_and_old_token_rejected(
    dev_client: TestClient, tmp_path: Path
) -> None:
    """AC5-1 + 共享知识 #2：refresh 轮换，旧 token 二次使用 401。"""
    _create_user("frank", DISPATCHER_PASSWORD)
    first = _login(dev_client, "frank", DISPATCHER_PASSWORD)

    resp = dev_client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert resp.status_code == 200
    second = resp.json()
    # 同一秒内签发的 access（相同 iat/exp）可能字面相同——核心断言是 refresh 轮换
    assert second["refresh_token"] != first["refresh_token"]
    # 新 access 可正常解签（sub 一致）
    assert _decode(second["access_token"])["sub"] == first["user"]["id"]

    # 旧 refresh 二次使用 → 401
    resp = dev_client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert resp.status_code == 401


def test_refresh_replaced_by_chain(dev_client: TestClient, tmp_path: Path) -> None:
    """共享知识 #2：轮换后旧行 revoked_at 置值 + replaced_by 成链。"""
    _create_user("grace", DISPATCHER_PASSWORD)
    first = _login(dev_client, "grace", DISPATCHER_PASSWORD)

    resp = dev_client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert resp.status_code == 200
    second = resp.json()

    db = tmp_path / "auth_api_dev.db"
    with sqlite3.connect(str(db)) as conn:
        old = conn.execute(
            "SELECT revoked_at, replaced_by FROM refresh_tokens WHERE token_hash = ?",
            (__import__("hashlib").sha256(first["refresh_token"].encode()).hexdigest(),),
        ).fetchone()
        new = conn.execute(
            "SELECT id FROM refresh_tokens WHERE token_hash = ?",
            (__import__("hashlib").sha256(second["refresh_token"].encode()).hexdigest(),),
        ).fetchone()
    assert old is not None and old[0] is not None  # revoked_at 已置值
    assert old[1] == new[0]  # replaced_by == 新行 id（成链）


def test_logout_idempotent(dev_client: TestClient) -> None:
    """AC4-3 + 幂等：不存在 token / 重复登出均 200 ok。"""
    _create_user("heidi", DISPATCHER_PASSWORD)
    data = _login(dev_client, "heidi", DISPATCHER_PASSWORD)

    resp = dev_client.post(
        "/auth/logout", json={"refresh_token": "no-such-token-xyz"}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp = dev_client.post(
        "/auth/logout", json={"refresh_token": data["refresh_token"]}
    )
    assert resp.status_code == 200
    # 重复登出仍 200
    resp = dev_client.post(
        "/auth/logout", json={"refresh_token": data["refresh_token"]}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ═══════════════════════════════════════════════════════════
# 4. me（dev 占位 / 生产真实 / 禁用拒绝）
# ═══════════════════════════════════════════════════════════


def test_me_dev_placeholder(dev_client: TestClient) -> None:
    """架构 §八 待明确 #4：dev 模式 /auth/me 返回占位用户（id=dev）。"""
    resp = dev_client.get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "dev"
    assert body["username"] == "dev"
    assert body["role"] == "dispatcher"


def test_me_prod_returns_real_user(prod_client: TestClient) -> None:
    """AC3-5：生产 /auth/me 用登录 token 返回当前用户。"""
    _create_user("iris", DISPATCHER_PASSWORD, role="kb_admin")
    data = _login(prod_client, "iris", DISPATCHER_PASSWORD)

    resp = prod_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == data["user"]["id"]
    assert body["username"] == "iris"
    assert body["role"] == "kb_admin"
    # create_user 默认 must_change_password=1（PRD：管理员建号后首次登录强制改密）
    assert body["must_change_password"] is True
    assert body["password_expires_at"] is None  # 未改过密 → 无过期时间


def test_me_prod_disabled_user_rejected(prod_client: TestClient) -> None:
    """共享知识 #2：禁用用户 me → 401。"""
    user = _create_user("jill", DISPATCHER_PASSWORD)
    data = _login(prod_client, "jill", DISPATCHER_PASSWORD)
    from api.services.user_service import UserService

    UserService().update_user(user["id"], disabled=1, actor_id="test")

    resp = prod_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# 5. 改密撤销全部 refresh
# ═══════════════════════════════════════════════════════════


def test_change_password_revokes_all_refresh(prod_client: TestClient) -> None:
    """AC8-5：改密后撤销该用户全部 refresh（多设备两个 refresh 全部失效）。"""
    _create_user("kevin", DISPATCHER_PASSWORD)
    data1 = _login(prod_client, "kevin", DISPATCHER_PASSWORD)
    data2 = _login(prod_client, "kevin", DISPATCHER_PASSWORD)  # 第二个设备会话

    resp = prod_client.post(
        "/auth/change-password",
        json={
            "old_password": DISPATCHER_PASSWORD,
            "new_password": "NewPassword#2026",
        },
        headers={"Authorization": f"Bearer {data1['access_token']}"},
    )
    assert resp.status_code == 200

    # 两个旧 refresh 全部拒绝
    for old in (data1["refresh_token"], data2["refresh_token"]):
        resp = prod_client.post(
            "/auth/refresh", json={"refresh_token": old}
        )
        assert resp.status_code == 401

    # 新密码可登录
    new_data = _login(prod_client, "kevin", "NewPassword#2026")
    assert new_data["access_token"]
    # 旧密码不可登录
    resp = prod_client.post(
        "/auth/login", json={"username": "kevin", "password": DISPATCHER_PASSWORD}
    )
    assert resp.status_code == 401


def test_change_password_wrong_old_401(prod_client: TestClient) -> None:
    """改密旧密码错 → 401。"""
    _create_user("lily", DISPATCHER_PASSWORD)
    data = _login(prod_client, "lily", DISPATCHER_PASSWORD)
    resp = prod_client.post(
        "/auth/change-password",
        json={"old_password": "WrongOld#1", "new_password": "NewPassword#2026"},
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert resp.status_code == 401


def test_change_password_weak_new_422(prod_client: TestClient) -> None:
    """改密新密码不满足策略（<8 位 / 无数字）→ 422。"""
    _create_user("mike", DISPATCHER_PASSWORD)
    data = _login(prod_client, "mike", DISPATCHER_PASSWORD)
    resp = prod_client.post(
        "/auth/change-password",
        json={"old_password": DISPATCHER_PASSWORD, "new_password": "short"},
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════
# 6. dev-login
# ═══════════════════════════════════════════════════════════


def test_dev_login_issues_role_jwt(dev_client: TestClient) -> None:
    """AC10-3：dev 模式 /auth/dev-login 签发带 role claim 的真实 JWT。"""
    resp = dev_client.post("/auth/dev-login", json={"role": "admin"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["role"] == "admin"
    payload = _decode(data["access_token"])
    assert payload["role"] == "admin"
    assert "thread_id" not in payload


def test_dev_login_idempotent_reuse(dev_client: TestClient) -> None:
    """dev-login 幂等：同一 role 重复调用均 200（dev 用户行 id 稳定复用）。"""
    first = dev_client.post("/auth/dev-login", json={"role": "operator"})
    assert first.status_code == 200
    second = dev_client.post("/auth/dev-login", json={"role": "operator"})
    assert second.status_code == 200
    # 两次签发同一 dev 用户（id = dev-operator），refresh 会话独立可轮换
    assert first.json()["user"]["id"] == second.json()["user"]["id"] == "dev-operator"
    r1 = first.json()["refresh_token"]
    resp = dev_client.post("/auth/refresh", json={"refresh_token": r1})
    assert resp.status_code == 200


def test_dev_login_invalid_role_422(dev_client: TestClient) -> None:
    """dev-login 非法角色 → 422。"""
    resp = dev_client.post("/auth/dev-login", json={"role": "superuser"})
    assert resp.status_code == 422


def test_dev_login_production_404(prod_client: TestClient) -> None:
    """AC10-4：生产 /auth/dev-login 必须 404（fail-closed）。"""
    resp = prod_client.post("/auth/dev-login", json={"role": "admin"})
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 7. 初始管理员 fail-closed
# ═══════════════════════════════════════════════════════════


def test_production_no_admin_password_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """待明确 #1：生产且 users 表无 admin 且 ADMIN_INITIAL_PASSWORD 未配 → SystemExit。"""
    _reset_limiter_state()
    _patch_db(monkeypatch, tmp_path / "auth_failclosed.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)
    _reload_stack()

    from api.services.user_service import UserService

    with pytest.raises(SystemExit):
        UserService().ensure_initial_admin()

    _teardown_env(monkeypatch)


def test_production_admin_created_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """拍板 #8：生产配置 ADMIN_INITIAL_PASSWORD → 创建 admin 且可登录。"""
    _reset_limiter_state()
    _patch_db(monkeypatch, tmp_path / "auth_adminenv.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "ProdAdmin#2026")
    _reload_stack()

    from api.services.user_service import UserService

    UserService().ensure_initial_admin()
    # 幂等：再次调用不重复创建
    UserService().ensure_initial_admin()

    from api.services.user_service import UserService as US

    admin = US().get_by_username("admin")
    assert admin is not None
    assert int(admin["must_change_password"]) == 1  # 首次登录强制改密

    import api.main as main_mod

    client = TestClient(main_mod.app, raise_server_exceptions=False)
    resp = client.post(
        "/auth/login", json={"username": "admin", "password": "ProdAdmin#2026"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "admin"

    _teardown_env(monkeypatch)


def test_ensure_initial_admin_dev_idempotent(dev_client: TestClient) -> None:
    """dev：ensure_initial_admin 幂等（重复调用仅一个 admin 行）。"""
    from api.services.user_service import UserService

    us = UserService()
    us.ensure_initial_admin()
    us.ensure_initial_admin()

    admin = us.get_by_username("admin")
    assert admin is not None
    assert admin["role"] == "admin"
    # 登录 dev 占位 admin（dev 默认密码）
    resp = dev_client.post(
        "/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )
    assert resp.status_code == 200
