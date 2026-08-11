"""V1.8.0 增量（register-rbac T1）· POST /auth/register 端点测试。

**覆盖**（架构 register-rbac Task 1 验收 + PRD §4.1 / US-1 / US-2）：
1. 注册成功 → LoginResponse 与 login **完全同构**（access+refresh+token_type+
   expires_in+mfa_required+user）；新用户 role=dispatcher、disabled=0、
   must_change_password=0（拍板 5）；
2. JWT claims 与 login 同构（sub/user_id/role/name/iss/iat/exp，
   **不含 thread_id**——共享知识 #9）；
3. 注册即登录：返回的 refresh 可换新 access（refresh 轮换）；注册后立即可
   用既有登录链路（用户名+密码登录成功）；
4. 冲突 409（用户名已存在 / 邮箱已被使用）；用户名非法 / 密码弱 → 422；
5. per-IP 限流：连打 6 次第 6 次 → 429（REGISTER_RATE_LIMIT_PER_MINUTE=5）；
6. 审计：成功 register_success + user_created（actor=register role=dispatcher）；
   失败 register_failed（detail 记状态码+文案；**不存密码/明文 token**）；
7. 恶意 role 字段被静默忽略（Pydantic ``extra="ignore"``），新用户仍 dispatcher
   （防注册即提权，共享知识 #1）。

**隔离**：沿用 test_auth_api.py 模式——主库切 tmp（database + auth_service +
user_service + auth_audit_service 四处 get_connection），reload 鉴权栈；
每客户端 fixture 前重置 slowapi 计数（防跨测试累积假 429）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "register-api-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "register-api-admin-token"

REGISTER_PASSWORD = "RegTest#2026"


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
    import api.routers.rbac as rbac_router

    importlib.reload(rbac_router)
    import api.main as main_mod

    importlib.reload(main_mod)


def _reset_limiter_state() -> None:
    """重置 slowapi 计数 + 清除路由限流注册（防 reload 累积重复注册）。

    关键：``@limiter.limit(lambda: ...)`` 属动态限流，注册进
    ``_dynamic_route_limits``；每次 ``_reload_stack()`` 重跑装饰器会
    **追加**一条注册（不覆盖），导致单次请求被检查 N 次、计数 N 倍递增，
    测试间假 429。故每次客户端 fixture 设置时（reload 前）清空注册表，
    reload 后恰好剩 1 条注册（login 10/min + register 5/min）。
    """
    import api.main as main_mod

    limiter = main_mod.app.state.limiter
    try:
        limiter._storage.reset()
    except AttributeError:
        pass
    limiter._route_limits.clear()
    limiter._dynamic_route_limits.clear()


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


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 客户端（主库切 tmp；register 公开、dev 同样可用）。"""
    _reset_limiter_state()  # 必须先清空（reload 前）
    _patch_db(monkeypatch, tmp_path / "register_dev.db")
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
    """生产客户端（主库切 tmp；register 生产照常开放——正是本需求目标）。"""
    _reset_limiter_state()
    _patch_db(monkeypatch, tmp_path / "register_prod.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "ProdAdmin#2026")
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)
    _teardown_env(monkeypatch)


# ── 辅助 ──────────────────────────────────────────────────────


def _register(
    client: TestClient,
    username: str,
    password: str = REGISTER_PASSWORD,
    email: str | None = None,
    extra: dict[str, Any] | None = None,
):
    payload: dict[str, Any] = {"username": username, "password": password}
    if email is not None:
        payload["email"] = email
    if extra:
        payload.update(extra)
    return client.post("/auth/register", json=payload)


def _decode(token: str) -> dict[str, Any]:
    """解签 access token（TEST_JWT_SECRET）。"""
    return jwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])


def _user_row(_client: TestClient, username: str) -> dict[str, Any] | None:
    """经 patched get_connection 读用户行（校验 must_change_password / disabled 等）。"""
    import mcp_tools.db.database as db_mod

    db = db_mod.get_connection()
    try:
        row = db.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        db.close()


def _audit_events(_client: TestClient, event_type: str) -> list[dict[str, Any]]:
    """经 patched get_connection 查 auth_audit_log（避开 reload 后的模块引用问题）。"""
    import mcp_tools.db.database as db_mod

    db = db_mod.get_connection()
    try:
        rows = db.execute(
            "SELECT * FROM auth_audit_log WHERE event_type = ? ORDER BY id ASC",
            (event_type,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# 1. 注册成功 → LoginResponse 同构 / claims / 注册即登录
# ═══════════════════════════════════════════════════════════


def test_register_success_returns_login_response(dev_client: TestClient) -> None:
    """AC1-3：注册成功返回与 login 完全同构的 LoginResponse。"""
    resp = _register(dev_client, "alice", email="alice@example.com")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert data["mfa_required"] is False
    assert data["user"]["username"] == "alice"
    assert data["user"]["role"] == "dispatcher"  # 默认最小权限（拍板 3）
    assert data["user"]["display_name"] == "alice"
    assert data["user"]["id"]

    # 新用户落库字段（拍板 5：自设密码 → must_change_password=0；注册即可用）
    row = _user_row(dev_client, "alice")
    assert row is not None
    assert row["role"] == "dispatcher"
    assert int(row["disabled"]) == 0
    assert int(row["must_change_password"]) == 0
    assert row["email"] == "alice@example.com"


def test_register_jwt_claims_no_thread_id(dev_client: TestClient) -> None:
    """共享知识 #9：注册签发 access 与 login 同构 claims；不含 thread_id。"""
    resp = _register(dev_client, "bob")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    payload = _decode(data["access_token"])
    assert payload["sub"] == data["user"]["id"]
    assert payload["user_id"] == data["user"]["id"]
    assert payload["role"] == "dispatcher"
    assert payload["name"] == "bob"
    assert payload["iss"] == "gridmind"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert "thread_id" not in payload  # 关键：绝不注入 thread_id（防快速路径误伤）


def test_register_then_refresh_works(dev_client: TestClient) -> None:
    """AC1-3 注册即登录：返回的 refresh 可换新 access（refresh 轮换）。"""
    resp = _register(dev_client, "carol")
    assert resp.status_code == 200, resp.text
    first = resp.json()

    refresh_resp = dev_client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    second = refresh_resp.json()
    assert second["refresh_token"] != first["refresh_token"]  # 轮换
    assert _decode(second["access_token"])["sub"] == first["user"]["id"]


def test_register_then_login_works(dev_client: TestClient) -> None:
    """AC1-6：注册后立即可用既有登录链路（用户名+密码）。"""
    resp = _register(dev_client, "dave")
    assert resp.status_code == 200, resp.text

    login_resp = dev_client.post(
        "/auth/login", json={"username": "dave", "password": REGISTER_PASSWORD}
    )
    assert login_resp.status_code == 200, login_resp.text
    assert login_resp.json()["user"]["role"] == "dispatcher"


def test_register_works_in_production(prod_client: TestClient) -> None:
    """架构 §九 3：register 生产照常开放（正是本需求目标）。"""
    resp = _register(prod_client, "erin")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["role"] == "dispatcher"


# ═══════════════════════════════════════════════════════════
# 2. 冲突 409 / 策略 422
# ═══════════════════════════════════════════════════════════


def test_register_username_conflict_409(dev_client: TestClient) -> None:
    """AC1-4：用户名已存在 → 409 明确文案。"""
    assert _register(dev_client, "frank").status_code == 200
    resp = _register(dev_client, "frank")  # 同一用户名
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "用户名已存在"


def test_register_email_conflict_409(dev_client: TestClient) -> None:
    """AC1-4：邮箱已被使用 → 409 明确文案。"""
    assert _register(dev_client, "grace", email="shared@example.com").status_code == 200
    resp = _register(dev_client, "heidi", email="shared@example.com")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "邮箱已被使用"


def test_register_invalid_username_422(dev_client: TestClient) -> None:
    """AC1-4：用户名非法（大写/非法字符）→ 422 明确文案。"""
    resp = _register(dev_client, "Bad User!")
    assert resp.status_code == 422, resp.text
    assert "用户名" in resp.json()["detail"]


def test_register_weak_password_422(dev_client: TestClient) -> None:
    """AC1-4：密码弱（<8 位 / 无数字 / 无字母）→ 422 明确文案。"""
    for weak in ("short", "12345678", "abcdefgh"):
        resp = _register(dev_client, f"weak-{weak[:4]}", password=weak)
        assert resp.status_code == 422, f"{weak} 应 422: {resp.text}"
        assert resp.json()["detail"]


# ═══════════════════════════════════════════════════════════
# 3. per-IP 限流（5/min）
# ═══════════════════════════════════════════════════════════


def test_register_rate_limit_429(dev_client: TestClient) -> None:
    """AC2-1：per-IP 5/min，连打 6 次第 6 次 → 429（slowapi）。"""
    for i in range(5):
        resp = _register(dev_client, f"rate-user-{i}")
        assert resp.status_code == 200, f"第 {i + 1} 次应 200: {resp.text}"

    resp = _register(dev_client, "rate-user-5")
    assert resp.status_code == 429, f"第 6 次应 429，实际 {resp.status_code}: {resp.text}"


# ═══════════════════════════════════════════════════════════
# 4. 审计：register_success + user_created + register_failed
# ═══════════════════════════════════════════════════════════


def test_register_audit_success_events(dev_client: TestClient) -> None:
    """共享知识 #2：成功 = register_success + user_created（actor=register）。"""
    resp = _register(dev_client, "irene")
    assert resp.status_code == 200, resp.text

    created = _audit_events(dev_client, "user_created")
    assert len(created) == 1
    assert created[0]["username"] == "irene"
    assert created[0]["detail"] == "actor=register role=dispatcher"

    success = _audit_events(dev_client, "register_success")
    assert len(success) == 1
    assert success[0]["user_id"] == resp.json()["user"]["id"]
    assert success[0]["username"] == "irene"

    # 审计不存密码/明文 token（共享知识 #2）
    all_rows = _audit_events(dev_client, "register_success") + _audit_events(
        dev_client, "user_created"
    )
    for ev in all_rows:
        assert REGISTER_PASSWORD not in str(ev.get("detail") or "")
        assert REGISTER_PASSWORD not in str(ev.get("username") or "")


def test_register_audit_failed_event(dev_client: TestClient) -> None:
    """共享知识 #2：失败（409/422）→ register_failed（detail 记状态码+文案）。"""
    assert _register(dev_client, "john").status_code == 200
    # 409 冲突
    resp = _register(dev_client, "john")
    assert resp.status_code == 409
    # 422 弱密码
    resp422 = _register(dev_client, "john2", password="short")
    assert resp422.status_code == 422

    failed = _audit_events(dev_client, "register_failed")
    assert len(failed) == 2
    assert failed[0]["detail"].startswith("409:")
    assert "用户名已存在" in failed[0]["detail"]
    assert failed[1]["detail"].startswith("422:")
    assert "密码" in failed[1]["detail"]

    # 不存密码/明文 token
    for ev in failed:
        assert REGISTER_PASSWORD not in str(ev.get("detail") or "")
        assert REGISTER_PASSWORD not in str(ev.get("username") or "")


# ═══════════════════════════════════════════════════════════
# 5. 恶意 role 忽略（防注册即提权）
# ═══════════════════════════════════════════════════════════


def test_register_malicious_role_ignored(dev_client: TestClient) -> None:
    """共享知识 #1：请求体带 role=admin 被 Pydantic extra="ignore" 静默忽略。"""
    resp = _register(dev_client, "mallory", extra={"role": "admin"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user"]["role"] == "dispatcher"

    payload = _decode(data["access_token"])
    assert payload["role"] == "dispatcher"

    row = _user_row(dev_client, "mallory")
    assert row is not None
    assert row["role"] == "dispatcher"
