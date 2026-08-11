"""final-audit · refresh 轮换原子性测试（P1 安全修复）。

**背景**：原实现（deferred 事务）下，两个携带**同一 refresh token** 的并发
``/auth/refresh`` 请求可在任一者写库前都通过 ``revoked_at`` 检查 → 双双轮换
成功（同一 refresh 产生两个有效会话，构成 replay 窗口）。修复后 ``BEGIN
IMMEDIATE`` 在 SELECT 前获取写锁，第二个请求阻塞至第一个 commit，读到
``revoked_at`` → 401。

**测试**：并发发起 N 个相同 refresh token 的刷新请求 → 断言恰好 1 个 200、
其余 401（至多一次成功轮换）；且成功者返回的新 refresh 可正常使用。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "refresh-concurrency-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "refresh-concurrency-admin-token"
DISPATCHER_PASSWORD = "Dispatch#2026"


def _connect(tmp_db: Path):
    """生成指向 tmp DB 的 get_connection 替代函数（与 test_auth_api 同模式）。"""

    def patched() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return patched


def _reload_stack() -> None:
    """reload 鉴权栈（config → rbac → auth → services → routers → main）。"""
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
    _reset_limiter_state()
    import mcp_tools.db.database as db_mod
    import api.services.auth_service as auth_svc
    import api.services.user_service as user_svc
    import api.services.auth_audit_service as audit_svc

    patched = _connect(tmp_path / "refresh_conc.db")
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(auth_svc, "get_connection", patched)
    monkeypatch.setattr(user_svc, "get_connection", patched)
    monkeypatch.setattr(audit_svc, "get_connection", patched)
    db_mod.init_db()

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    _reload_stack()


def _create_user(username: str, password: str, role: str = "dispatcher") -> dict[str, Any]:
    from api.services.user_service import UserService

    return UserService().create_user(
        username=username, password=password, role=role, actor_id="test"
    )


def test_refresh_same_token_concurrent_only_one_succeeds(
    dev_client: TestClient,
) -> None:
    """P1 安全：同一 refresh token 并发刷新 → 恰好 1 个 200，其余 401。"""
    _create_user("concurrent-user", DISPATCHER_PASSWORD)
    resp = dev_client.post(
        "/auth/login",
        json={"username": "concurrent-user", "password": DISPATCHER_PASSWORD},
    )
    assert resp.status_code == 200
    refresh_token = resp.json()["refresh_token"]

    n = 5
    barrier = threading.Barrier(n)
    results: list[int] = []
    results_lock = threading.Lock()

    def _do_refresh() -> int:
        # 用 barrier 让 N 个请求尽可能同时进入（放大竞态窗口）
        barrier.wait(timeout=5)
        r = dev_client.post("/auth/refresh", json={"refresh_token": refresh_token})
        with results_lock:
            results.append(r.status_code)
        return r.status_code

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_do_refresh) for _ in range(n)]
        codes = [f.result(timeout=15) for f in futures]

    assert codes.count(200) == 1, f"同一 refresh token 并发刷新应恰好 1 次成功: {codes}"
    assert codes.count(401) == n - 1, f"其余请求应 401: {codes}"


def test_refresh_concurrent_winner_token_still_usable(
    dev_client: TestClient,
) -> None:
    """P1 安全：并发轮换成功者返回的新 refresh 可继续使用（链未断）。"""
    _create_user("chain-user", DISPATCHER_PASSWORD)
    resp = dev_client.post(
        "/auth/login",
        json={"username": "chain-user", "password": DISPATCHER_PASSWORD},
    )
    assert resp.status_code == 200
    refresh_token = resp.json()["refresh_token"]

    n = 3
    barrier = threading.Barrier(n)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _do_refresh() -> tuple[int, str]:
        barrier.wait(timeout=5)
        r = dev_client.post("/auth/refresh", json={"refresh_token": refresh_token})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        with results_lock:
            results.append((r.status_code, body.get("refresh_token", "")))
        return r.status_code, body.get("refresh_token", "")

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_do_refresh) for _ in range(n)]
        [f.result(timeout=15) for f in futures]

    winners = [rt for code, rt in results if code == 200 and rt]
    assert len(winners) == 1, f"应恰好 1 个成功者: {results}"

    # 成功者的新 refresh 可再次轮换（证明链完整、未被并发破坏）
    r2 = dev_client.post("/auth/refresh", json={"refresh_token": winners[0]})
    assert r2.status_code == 200
    assert r2.json()["refresh_token"] != winners[0]


def test_refresh_sequential_still_rotates(dev_client: TestClient) -> None:
    """回归：顺序刷新行为不变（一次 200 + 旧 token 二次使用 401）。"""
    _create_user("seq-user", DISPATCHER_PASSWORD)
    resp = dev_client.post(
        "/auth/login",
        json={"username": "seq-user", "password": DISPATCHER_PASSWORD},
    )
    assert resp.status_code == 200
    first = resp.json()

    r1 = dev_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r1.status_code == 200
    second = r1.json()
    assert second["refresh_token"] != first["refresh_token"]

    r2 = dev_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r2.status_code == 401
