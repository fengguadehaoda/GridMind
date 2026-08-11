"""final-audit · GET /audit/pending-count 端点测试（P1 修复）。

**背景**：前端 auditStore 每 5s 轮询 ``/audit/pending-count`` 校正 HITL 徽标
计数（防 SSE 断连漂移），但端点此前**未实现** → 每 5s 404（logs 刷屏）+
徽标永久降级灰点。现补实现：计数来自 ``sse_event_emitter`` 进程内登记
（``emit_hitl_interrupt`` 加入 / ``emit_hitl_resolved`` 移除）。

**覆盖**：
1. 空态 → 200 ``{"count": 0}``（含 lastUpdated 契约字段）；
2. emit_hitl_interrupt → count 递增（同 thread 幂等）；
3. emit_hitl_resolved → count 递减（未登记也安全）；
4. 多 thread 独立计数。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "pending-count-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "pending-count-admin-token"


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


def _reset_pending_registry() -> None:
    """清空 sse_event_emitter 进程内 pending 登记（防跨测试污染）。"""
    from api.services.sse_event_emitter import sse_event_emitter

    sse_event_emitter._pending_interrupt_threads.clear()


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 客户端（主库切 tmp；不强制登录）。"""
    _reset_limiter_state()
    _reset_pending_registry()
    import mcp_tools.db.database as db_mod
    import api.services.auth_service as auth_svc
    import api.services.user_service as user_svc
    import api.services.auth_audit_service as audit_svc

    patched = _connect(tmp_path / "pending_count.db")
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

    _reset_pending_registry()
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    _reload_stack()


def _get_count(client: TestClient) -> int:
    resp = client.get("/audit/pending-count")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("count"), int)
    return body["count"]


def test_pending_count_initial_zero(dev_client: TestClient) -> None:
    """空态 → 200 {count: 0}（含 lastUpdated 契约字段）。"""
    resp = dev_client.get("/audit/pending-count")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert "lastUpdated" in body  # 文档契约字段（前端只消费 count）


def test_pending_count_increments_on_interrupt(dev_client: TestClient) -> None:
    """emit_hitl_interrupt → count 递增（同 thread 幂等）。"""
    from api.services.sse_event_emitter import sse_event_emitter

    assert _get_count(dev_client) == 0

    asyncio.run(
        sse_event_emitter.emit_hitl_interrupt("thread-a", tool="shutdown_device", args={})
    )
    assert _get_count(dev_client) == 1

    # 同 thread 重复 interrupt → 幂等（不重复计数）
    asyncio.run(
        sse_event_emitter.emit_hitl_interrupt("thread-a", tool="shutdown_device", args={})
    )
    assert _get_count(dev_client) == 1

    # 第二个 thread → 独立计数
    asyncio.run(
        sse_event_emitter.emit_hitl_interrupt("thread-b", tool="open_breaker", args={})
    )
    assert _get_count(dev_client) == 2


def test_pending_count_decrements_on_resolved(dev_client: TestClient) -> None:
    """emit_hitl_resolved → count 递减（未登记 thread 也安全）。"""
    from api.services.sse_event_emitter import sse_event_emitter

    asyncio.run(
        sse_event_emitter.emit_hitl_interrupt("thread-a", tool="shutdown_device", args={})
    )
    asyncio.run(
        sse_event_emitter.emit_hitl_interrupt("thread-b", tool="open_breaker", args={})
    )
    assert _get_count(dev_client) == 2

    asyncio.run(sse_event_emitter.emit_hitl_resolved("thread-a", "approved", "now"))
    assert _get_count(dev_client) == 1

    # 未登记的 thread resolve → 幂等安全
    asyncio.run(sse_event_emitter.emit_hitl_resolved("no-such-thread", "rejected", "now"))
    assert _get_count(dev_client) == 1

    asyncio.run(sse_event_emitter.emit_hitl_resolved("thread-b", "rejected", "now"))
    assert _get_count(dev_client) == 0
