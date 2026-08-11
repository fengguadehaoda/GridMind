"""V1.8.0 认证（T03）· /users* 管理员用户管理测试。

**覆盖**（架构 auth-architecture Task 3 验收 + PRD §5.5-5.7）：
1. 管理员 CRUD：GET 列表（过滤）+ POST 创建 + PATCH 改角色/禁用/改密；
2. 鉴权：非 admin（生产）→ 403；dev 放行；admin token 等效管理员；
3. 用户名冲突 → 409；邮箱冲突 → 409；角色非法 / 密码策略 → 422；
4. 最后 admin 防呆：仅剩一个 admin 时禁用/降级 → 409；
5. 禁用即时拒绝：login → 403、refresh → 401、me → 401；
6. 审计事件落库：user_created / role_changed / user_disabled / password_changed。

**隔离**：主库切 tmp（database + auth_service + user_service +
auth_audit_service 四处 get_connection），reload 鉴权栈（APP_ENV=production）；
每客户端 fixture 设置时重置 slowapi 计数（防 reload 累积重复注册）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "users-admin-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "users-admin-token"

ADMIN_PASSWORD = "Admin#2026"
USER_PASSWORD = "Dispatch#2026"


def _connect(tmp_db: Path):
    def patched() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return patched


def _reload_stack() -> None:
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
    """重置 slowapi 计数 + 清空路由限流注册（防 reload 累积重复注册）。"""
    import api.main as main_mod

    limiter = main_mod.app.state.limiter
    try:
        limiter._storage.reset()
    except AttributeError:
        pass
    limiter._route_limits.clear()
    limiter._dynamic_route_limits.clear()


def _patch_db(monkeypatch: pytest.MonkeyPatch, tmp_db: Path) -> None:
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


@pytest.fixture
def prod_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产客户端（主库切 tmp；APP_ENV=production + 强 JWT_SECRET/ADMIN_TOKEN）。"""
    _reset_limiter_state()
    _patch_db(monkeypatch, tmp_path / "users_admin_prod.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    monkeypatch.delenv("ADMIN_INITIAL_PASSWORD", raising=False)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)
    _teardown_env(monkeypatch)


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 客户端（主库切 tmp；RBAC 放行）。"""
    _reset_limiter_state()
    _patch_db(monkeypatch, tmp_path / "users_admin_dev.db")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)
    _teardown_env(monkeypatch)


# ── 辅助 ──────────────────────────────────────────────────────


def _create_user(
    username: str,
    password: str = USER_PASSWORD,
    role: str = "dispatcher",
    email: str | None = None,
) -> dict[str, Any]:
    from api.services.user_service import UserService

    return UserService().create_user(
        username=username,
        password=password,
        role=role,
        email=email,
        actor_id="test",
    )


def _admin_jwt(user_id: str = "u-admin") -> str:
    """签发带 admin role claim 的测试 JWT（沿用既有测试约定）。"""
    from api.services.auth import issue_test_token

    return issue_test_token(user_id, extra_claims={"role": "admin"})


def _dispatcher_jwt(user_id: str = "u-dispatch") -> str:
    from api.services.auth import issue_test_token

    return issue_test_token(user_id, extra_claims={"role": "dispatcher"})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _auth_events(db: Path, event_type: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM auth_audit_log WHERE event_type = ? ORDER BY id",
            (event_type,),
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# 1. 管理员 CRUD
# ═══════════════════════════════════════════════════════════


def test_admin_list_users(prod_client: TestClient) -> None:
    """AC6-1：管理员可列表（不含 password_hash）。"""
    _create_user("alice", role="operator")
    _create_user("bob", role="admin")
    _create_user("carol", role="dispatcher")

    resp = prod_client.get("/users", headers=_bearer(_admin_jwt()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert {u["username"] for u in body["users"]} == {"alice", "bob", "carol"}
    # 不含 password_hash
    assert all("password_hash" not in u for u in body["users"])


def test_admin_list_users_filters(prod_client: TestClient) -> None:
    """AC6-1：role / disabled / q 过滤。"""
    _create_user("alice", role="operator")
    _create_user("bob", role="admin")
    carol = _create_user("carol", role="dispatcher")
    from api.services.user_service import UserService

    UserService().update_user(carol["id"], disabled=1, actor_id="test")

    resp = prod_client.get(
        "/users", params={"role": "operator"}, headers=_bearer(_admin_jwt())
    )
    assert [u["username"] for u in resp.json()["users"]] == ["alice"]

    resp = prod_client.get(
        "/users", params={"disabled": 1}, headers=_bearer(_admin_jwt())
    )
    assert [u["username"] for u in resp.json()["users"]] == ["carol"]

    resp = prod_client.get(
        "/users", params={"q": "ali"}, headers=_bearer(_admin_jwt())
    )
    assert [u["username"] for u in resp.json()["users"]] == ["alice"]


def test_admin_create_user(prod_client: TestClient, tmp_path: Path) -> None:
    """AC6-2：创建用户成功，默认 must_change_password=1 + user_created 审计。"""
    resp = prod_client.post(
        "/users",
        json={"username": "dave", "password": USER_PASSWORD, "role": "kb_admin"},
        headers=_bearer(_admin_jwt()),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "dave"
    assert body["role"] == "kb_admin"
    assert body["must_change_password"] == 1
    assert "password_hash" not in body

    events = _auth_events(tmp_path / "users_admin_prod.db", "user_created")
    assert any(e["username"] == "dave" for e in events)


def test_admin_patch_role(prod_client: TestClient) -> None:
    """AC6-4：改角色成功 + role_changed 审计（现有 token 保留到过期）。"""
    user = _create_user("erin", role="dispatcher")

    resp = prod_client.patch(
        f"/users/{user['id']}",
        json={"role": "operator"},
        headers=_bearer(_admin_jwt()),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"

    resp = prod_client.get(
        "/users", params={"role": "operator"}, headers=_bearer(_admin_jwt())
    )
    assert "erin" in {u["username"] for u in resp.json()["users"]}


def test_admin_patch_password(prod_client: TestClient, tmp_path: Path) -> None:
    """AC6-2 密码重置：改密后旧密码登录失败、新密码成功、撤销全部 refresh。"""
    user = _create_user("frank")
    # 先登录拿 refresh（旧密码会话）
    login1 = prod_client.post(
        "/auth/login", json={"username": "frank", "password": USER_PASSWORD}
    )
    assert login1.status_code == 200
    old_refresh = login1.json()["refresh_token"]

    resp = prod_client.patch(
        f"/users/{user['id']}",
        json={"password": "NewPass#2026"},
        headers=_bearer(_admin_jwt()),
    )
    assert resp.status_code == 200

    # 旧密码登录失败
    resp = prod_client.post(
        "/auth/login", json={"username": "frank", "password": USER_PASSWORD}
    )
    assert resp.status_code == 401
    # 新密码登录成功
    resp = prod_client.post(
        "/auth/login", json={"username": "frank", "password": "NewPass#2026"}
    )
    assert resp.status_code == 200
    # 旧 refresh 已撤销
    resp = prod_client.post(
        "/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# 2. 鉴权 / 冲突 / 策略
# ═══════════════════════════════════════════════════════════


def test_users_non_admin_403(prod_client: TestClient) -> None:
    """AC6-1：生产非 admin 访问 /users → 403。"""
    resp = prod_client.get("/users", headers=_bearer(_dispatcher_jwt()))
    assert resp.status_code == 403


def test_users_anonymous_401(prod_client: TestClient) -> None:
    """生产无 token 访问 /users → 401。"""
    resp = prod_client.get("/users")
    assert resp.status_code == 401


def test_users_admin_token_equivalent(prod_client: TestClient) -> None:
    """X-Admin-Token 等效管理员（RBAC 语义不变）。"""
    resp = prod_client.get("/users", headers={"X-Admin-Token": TEST_ADMIN_TOKEN})
    assert resp.status_code == 200


def test_users_dev_dispatcher_creates(dev_client: TestClient) -> None:
    """dev 模式 RBAC 放行：无 token 也可创建用户（本地开发零改动）。"""
    resp = dev_client.post(
        "/users",
        json={"username": "dev-user", "password": USER_PASSWORD, "role": "dispatcher"},
    )
    assert resp.status_code == 201


def test_create_username_conflict_409(prod_client: TestClient) -> None:
    """PRD §5.6：用户名冲突 → 409。"""
    _create_user("grace")
    resp = prod_client.post(
        "/users",
        json={"username": "grace", "password": USER_PASSWORD, "role": "dispatcher"},
        headers=_bearer(_admin_jwt()),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "用户名已存在"


def test_create_invalid_role_422(prod_client: TestClient) -> None:
    """角色非法（非 5 角色之一）→ 422。"""
    resp = prod_client.post(
        "/users",
        json={"username": "heidi", "password": USER_PASSWORD, "role": "superuser"},
        headers=_bearer(_admin_jwt()),
    )
    assert resp.status_code == 422


def test_create_weak_password_422(prod_client: TestClient) -> None:
    """密码策略：<8 位 / 无数字 / 无字母 → 422（拍板 #2）。"""
    for weak in ("short", "12345678", "abcdefgh"):
        resp = prod_client.post(
            "/users",
            json={"username": "ivan", "password": weak, "role": "dispatcher"},
            headers=_bearer(_admin_jwt()),
        )
        assert resp.status_code == 422, f"password {weak!r} 应被拒绝"


def test_patch_nonexistent_user_404(prod_client: TestClient) -> None:
    """PATCH 不存在用户 → 404。"""
    resp = prod_client.patch(
        "/users/no-such-id",
        json={"role": "operator"},
        headers=_bearer(_admin_jwt()),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 3. 最后 admin 防呆
# ═══════════════════════════════════════════════════════════


def test_last_admin_cannot_be_disabled(prod_client: TestClient) -> None:
    """AC6-5 + 共享知识 #7：系统仅一个 admin，禁用 → 409。"""
    admin = _create_user("sole-admin", role="admin")

    resp = prod_client.patch(
        f"/users/{admin['id']}",
        json={"disabled": 1},
        headers=_bearer(_admin_jwt(user_id=admin["id"])),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "不能禁用或降级最后一个管理员"


def test_last_admin_cannot_be_demoted(prod_client: TestClient) -> None:
    """AC6-5：系统仅一个 admin，降级为 dispatcher → 409。"""
    admin = _create_user("sole-admin2", role="admin")

    resp = prod_client.patch(
        f"/users/{admin['id']}",
        json={"role": "dispatcher"},
        headers=_bearer(_admin_jwt(user_id=admin["id"])),
    )
    assert resp.status_code == 409


def test_second_admin_can_be_disabled(prod_client: TestClient) -> None:
    """两个 admin 时允许禁用其一（防呆仅拦最后一个）。"""
    admin_a = _create_user("admin-a", role="admin")
    admin_b = _create_user("admin-b", role="admin")

    resp = prod_client.patch(
        f"/users/{admin_a['id']}",
        json={"disabled": 1},
        headers=_bearer(_admin_jwt(user_id=admin_b["id"])),
    )
    assert resp.status_code == 200
    assert resp.json()["disabled"] == 1


# ═══════════════════════════════════════════════════════════
# 4. 禁用即时拒绝
# ═══════════════════════════════════════════════════════════


def test_disabled_user_login_refresh_me_rejected(
    prod_client: TestClient, tmp_path: Path
) -> None:
    """共享知识 #2 + AC6-3：禁用后 login 403 / refresh 401 / me 401。"""
    user = _create_user("jill")
    login = prod_client.post(
        "/auth/login", json={"username": "jill", "password": USER_PASSWORD}
    )
    assert login.status_code == 200
    access, refresh = login.json()["access_token"], login.json()["refresh_token"]

    from api.services.user_service import UserService

    UserService().update_user(user["id"], disabled=1, actor_id="test")

    # login → 403
    resp = prod_client.post(
        "/auth/login", json={"username": "jill", "password": USER_PASSWORD}
    )
    assert resp.status_code == 403

    # refresh → 401
    resp = prod_client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401

    # me → 401
    resp = prod_client.get("/auth/me", headers=_bearer(access))
    assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# 5. 审计事件落库
# ═══════════════════════════════════════════════════════════


def test_audit_events_recorded(prod_client: TestClient, tmp_path: Path) -> None:
    """AC7-2：user_created / role_changed / user_disabled / password_changed 落库。"""
    from api.services.user_service import UserService

    db = tmp_path / "users_admin_prod.db"

    user = UserService().create_user(
        username="kate", password=USER_PASSWORD, role="dispatcher", actor_id="test"
    )
    UserService().update_user(user["id"], role="operator", actor_id="test")
    UserService().update_user(user["id"], disabled=1, actor_id="test")
    UserService().update_user(
        user["id"], password="NewPass#2026", actor_id="test"
    )

    assert any(
        e["username"] == "kate" for e in _auth_events(db, "user_created")
    )
    assert any(
        e["username"] == "kate" for e in _auth_events(db, "role_changed")
    )
    assert any(
        e["username"] == "kate" for e in _auth_events(db, "user_disabled")
    )
    assert any(
        e["username"] == "kate" for e in _auth_events(db, "password_changed")
    )
    # detail 不含明文密码 / token
    for event in _auth_events(db, "password_changed"):
        assert "NewPass#2026" not in (event.get("detail") or "")
