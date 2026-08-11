"""V1.7.0 多用户地基 · T2 RBAC 角色矩阵测试（P1-1 + P1-2 + P1-3）。

**范围**（架构 multiuser-architecture Task 2 验收 + PRD §四 矩阵逐项）：
1. 5 角色解析：dispatcher / operator / kb_admin / auditor / admin；
   缺 role claim / 未知 role → 默认 dispatcher（不 500）；
2. 灰度读（status/history/metrics）+ 写（set/manual_rollback）：
   匿名→401、调度员→403、运维→放行、admin token→放行、dev→放行；
3. 系统配置（/admin/checkpoint-stats、/debug/sync_lag|sync_force）：同灰度；
4. KB 写：调度员/运维/审计 → 403；知识管理员/管理员 → 放行；
   KB 读：任意角色放行（全局共享，D3）；
5. 审计列表按角色过滤：调度员/知识管理员仅本人 thread；审计/运维/管理员全量；
6. 模型切换：无 thread_id 全员可用（US-2.3）；有 thread_id 受 owner 校验。

**隔离**：主库切 tmp（database + hitl_audit_service 两处 get_connection），
reload 鉴权栈让 APP_ENV=production 生效；teardown 复位 dev。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "rbac-matrix-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "rbac-matrix-admin-token"

#: 会话读放行断言（非 401/403 即放行，业务 404/5xx 属正常）
NOT_AUTH_BLOCKED = (401, 403)


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
    import api.main as main_mod

    importlib.reload(main_mod)


@pytest.fixture
def prod_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产模式客户端（主库切 tmp）。"""
    tmp_db = tmp_path / "rbac_matrix.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    patched = _connect(tmp_db)
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(has_mod, "get_connection", patched)
    db_mod.init_db()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    _reload_stack()


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 模式客户端（矩阵不生效，全部放行）。"""
    tmp_db = tmp_path / "rbac_matrix_dev.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    patched = _connect(tmp_db)
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(has_mod, "get_connection", patched)
    db_mod.init_db()

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)

    _reload_stack()


def _token(user_id: str, role: str) -> str:
    from api.services.auth import issue_test_token

    return issue_test_token(user_id=user_id, extra_claims={"role": role})


def _jwt_headers(user_id: str, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, role)}"}


# ═══════════════════════════════════════════════════════
# 1. 角色解析单元测试
# ═══════════════════════════════════════════════════════


def test_get_role_parses_all_five_roles() -> None:
    """5 角色均可解析。"""
    from api.services.rbac import Role, get_role

    cases = {
        "dispatcher": Role.DISPATCHER,
        "operator": Role.OPERATOR,
        "kb_admin": Role.KB_ADMIN,
        "auditor": Role.AUDITOR,
        "admin": Role.ADMIN,
    }
    for raw, expected in cases.items():
        assert get_role({"role": raw}) is expected, f"role={raw} 解析失败"
    print("[PASS] 5 角色解析")


def test_get_role_missing_defaults_to_dispatcher() -> None:
    """缺 role claim / 未知 role → dispatcher（不 500）。"""
    from api.services.rbac import Role, get_role

    assert get_role({"sub": "u1"}) is Role.DISPATCHER
    assert get_role({"role": "superuser"}) is Role.DISPATCHER
    assert get_role({"role": ""}) is Role.DISPATCHER
    assert get_role(None) is Role.DISPATCHER
    assert get_role({}) is Role.DISPATCHER
    print("[PASS] 缺省/未知 role → dispatcher")


def test_role_allows() -> None:
    """role_allows 命中判定。"""
    from api.services.rbac import Role, role_allows

    assert role_allows(Role.OPERATOR, (Role.OPERATOR, Role.ADMIN)) is True
    assert role_allows(Role.DISPATCHER, (Role.OPERATOR, Role.ADMIN)) is False
    assert role_allows("admin", (Role.OPERATOR, Role.ADMIN)) is True
    assert role_allows(Role.KB_ADMIN, (Role.KB_ADMIN, Role.ADMIN)) is True
    print("[PASS] role_allows")


# ═══════════════════════════════════════════════════════
# 2. 灰度管理（读 + 写）：匿名 401 / 调度员 403 / 运维放行 / admin token 等效
# ═══════════════════════════════════════════════════════

GRAYSCALE_READ_ENDPOINTS = [
    ("GET", "/grayscale/status", None),
    ("GET", "/grayscale/history", None),
    ("GET", "/grayscale/metrics", None),
]

GRAYSCALE_WRITE_ENDPOINTS = [
    ("POST", "/grayscale/set", {"ratio": 50}),
    ("POST", "/grayscale/manual_rollback", {"reason": "test"}),
]


@pytest.mark.parametrize(("method", "path", "body"), GRAYSCALE_READ_ENDPOINTS + GRAYSCALE_WRITE_ENDPOINTS)
def test_grayscale_anonymous_401(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """生产匿名访问灰度读/写 → 401（收口匿名读，PRD P1-2）。"""
    resp = prod_client.post(path, json=body or {}) if method == "POST" else prod_client.get(path)
    assert resp.status_code == 401, (
        f"{method} {path} 匿名应 401，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), GRAYSCALE_READ_ENDPOINTS + GRAYSCALE_WRITE_ENDPOINTS)
def test_grayscale_dispatcher_forbidden_403(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """调度员访问灰度读/写 → 403（矩阵：调度员 ✗）。"""
    resp = _request(prod_client, method, path, body, _jwt_headers("d1", "dispatcher"))
    assert resp.status_code == 403, (
        f"{method} {path} 调度员应 403，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), GRAYSCALE_READ_ENDPOINTS + GRAYSCALE_WRITE_ENDPOINTS)
def test_grayscale_operator_allowed(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """运维访问灰度读/写 → 放行（非 401/403）。"""
    resp = _request(prod_client, method, path, body, _jwt_headers("op1", "operator"))
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {path} 运维应放行，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), GRAYSCALE_READ_ENDPOINTS + GRAYSCALE_WRITE_ENDPOINTS)
def test_grayscale_admin_token_equivalent(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """admin token 等效管理员 → 灰度读/写放行（二选一通过）。"""
    resp = _request(prod_client, method, path, body, {"X-Admin-Token": TEST_ADMIN_TOKEN})
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {path} admin token 应放行，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), GRAYSCALE_READ_ENDPOINTS + GRAYSCALE_WRITE_ENDPOINTS)
def test_grayscale_kb_auditor_forbidden(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """知识管理员/审计访问灰度 → 403（矩阵：✗）。"""
    for role in ("kb_admin", "auditor"):
        resp = _request(prod_client, method, path, body, _jwt_headers(f"{role}-u", role))
        assert resp.status_code == 403, (
            f"{method} {path} {role} 应 403，实际 {resp.status_code}"
        )


@pytest.mark.parametrize(("method", "path", "body"), GRAYSCALE_READ_ENDPOINTS + GRAYSCALE_WRITE_ENDPOINTS)
def test_grayscale_dev_anonymous_allowed(dev_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """dev 模式匿名访问灰度 → 放行（矩阵不生效）。"""
    resp = dev_client.post(path, json=body or {}) if method == "POST" else dev_client.get(path)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"dev {method} {path} 匿名应放行，实际 {resp.status_code}"
    )


# ═══════════════════════════════════════════════════════
# 3. 系统配置（/admin/checkpoint-stats、/debug/*）
# ═══════════════════════════════════════════════════════

SYSTEM_CONFIG_ENDPOINTS = [
    ("GET", "/admin/checkpoint-stats", None),
    ("GET", "/debug/sync_lag", None),
    ("POST", "/debug/sync_force", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), SYSTEM_CONFIG_ENDPOINTS)
def test_system_config_anonymous_401(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """生产匿名访问系统配置 → 401（无 JWT 且无 admin token）。"""
    resp = prod_client.post(path, json=body or {}) if method == "POST" else prod_client.get(path)
    assert resp.status_code == 401, (
        f"{method} {path} 匿名应 401，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), SYSTEM_CONFIG_ENDPOINTS)
def test_system_config_dispatcher_forbidden(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """调度员访问系统配置 → 403。"""
    resp = _request(prod_client, method, path, body, _jwt_headers("d1", "dispatcher"))
    assert resp.status_code == 403, (
        f"{method} {path} 调度员应 403，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), SYSTEM_CONFIG_ENDPOINTS)
def test_system_config_operator_allowed(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """运维访问系统配置 → 放行。"""
    resp = _request(prod_client, method, path, body, _jwt_headers("op1", "operator"))
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {path} 运维应放行，实际 {resp.status_code}"
    )


@pytest.mark.parametrize(("method", "path", "body"), SYSTEM_CONFIG_ENDPOINTS)
def test_system_config_admin_token_equivalent(prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    """admin token 访问系统配置 → 放行。"""
    resp = _request(prod_client, method, path, body, {"X-Admin-Token": TEST_ADMIN_TOKEN})
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {path} admin token 应放行，实际 {resp.status_code}"
    )


# ═══════════════════════════════════════════════════════
# 4. KB 角色写权限（P1-3）：仅知识管理员/管理员；读全员
# ═══════════════════════════════════════════════════════


def _kb_write_req(client: TestClient, headers: dict[str, str] | None):
    """上传一个空 txt（不实际入库，只验证鉴权层）。"""
    return client.post(
        "/api/knowledge/upload",
        files={"file": ("rbac-empty.txt", b"", "text/plain")},
        headers=headers,
    )


@pytest.mark.parametrize("role", ["dispatcher", "operator", "auditor"])
def test_kb_write_forbidden_for_non_kb_admin(
    prod_client: TestClient, role: str,
) -> None:
    """生产：调度员/运维/审计上传 → 403。"""
    resp = _kb_write_req(prod_client, _jwt_headers(f"{role}-u", role))
    assert resp.status_code == 403, (
        f"{role} 上传应 403，实际 {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize("role", ["kb_admin", "admin"])
def test_kb_write_allowed_for_kb_admin_and_admin(
    prod_client: TestClient, role: str,
) -> None:
    """生产：知识管理员/管理员上传 → 非 403（空文档 422/400 属业务）。"""
    resp = _kb_write_req(prod_client, _jwt_headers(f"{role}-u", role))
    assert resp.status_code != 403, (
        f"{role} 上传被 403 误杀: {resp.text[:200]}"
    )


def test_kb_write_dev_anonymous_allowed(dev_client: TestClient) -> None:
    """dev：匿名上传 → 放行（Q5 决策）。"""
    resp = _kb_write_req(dev_client, None)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"dev 匿名上传应放行，实际 {resp.status_code}: {resp.text[:200]}"
    )


def test_kb_write_admin_token_equivalent(prod_client: TestClient) -> None:
    """admin token 上传 → 放行（等效管理员）。"""
    resp = _kb_write_req(prod_client, {"X-Admin-Token": TEST_ADMIN_TOKEN})
    assert resp.status_code != 403, (
        f"admin token 上传被 403 误杀: {resp.text[:200]}"
    )


@pytest.mark.parametrize("role", ["dispatcher", "operator", "kb_admin", "auditor", "admin"])
def test_kb_read_all_roles_allowed(prod_client: TestClient, role: str) -> None:
    """KB 读（列表）全局共享：任意角色放行（D3）。"""
    resp = prod_client.get(
        "/api/knowledge/uploads",
        headers=_jwt_headers(f"{role}-u", role),
    )
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{role} 读 KB 应放行，实际 {resp.status_code}: {resp.text[:200]}"
    )


# ═══════════════════════════════════════════════════════
# 5. 审计列表角色过滤（PRD §四 矩阵「审计读」）
# ═══════════════════════════════════════════════════════


def _seed_audit(thread_id: str, decision: str = "approve") -> None:
    """直接向 tmp 主库写入一条审计日志（避免 AuditLogEntry 构造依赖）。"""
    from mcp_tools.db.database import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO hitl_audit_log (
                thread_id, interrupt_node, tool_name, user_id, user_name,
                user_role, decision, original_args, reason, created_at
            ) VALUES (?, 'safety_check', 'breaker_control', 'seed', 'seed',
                      'dispatcher', ?, '{}', 'seed', datetime('now','localtime'))
            """,
            (thread_id, decision),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_thread(thread_id: str, owner: str) -> None:
    from api.services.thread_store import ThreadStore

    ThreadStore().create_thread(thread_id, owner)


def _audit_list(client: TestClient, headers: dict[str, str] | None):
    resp = client.get("/audit/hitl", headers=headers)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"/audit/hitl 应放行，实际 {resp.status_code}: {resp.text[:200]}"
    )
    return [e["thread_id"] for e in resp.json().get("entries", [])]


def test_audit_list_dispatcher_sees_own_threads_only(prod_client: TestClient) -> None:
    """调度员仅见本人 thread 的审计（张三 1 条，李四 1 条）。"""
    _seed_thread("t-audit-zhang", "zhangsan")
    _seed_thread("t-audit-lisi", "lisi")
    _seed_audit("t-audit-zhang")
    _seed_audit("t-audit-lisi")

    seen = _audit_list(prod_client, _jwt_headers("zhangsan", "dispatcher"))
    assert "t-audit-zhang" in seen, f"张三应看到自己的审计，实际 {seen}"
    assert "t-audit-lisi" not in seen, f"张三不应看到李四的审计，实际 {seen}"
    print("[PASS] 调度员审计列表仅本人 thread")


def test_audit_list_kb_admin_sees_own_threads_only(prod_client: TestClient) -> None:
    """知识管理员同样仅本人 thread。"""
    _seed_thread("t-audit-kb", "kb1")
    _seed_thread("t-audit-other", "other")
    _seed_audit("t-audit-kb")
    _seed_audit("t-audit-other")

    seen = _audit_list(prod_client, _jwt_headers("kb1", "kb_admin"))
    assert "t-audit-kb" in seen
    assert "t-audit-other" not in seen
    print("[PASS] 知识管理员审计列表仅本人 thread")


@pytest.mark.parametrize("role", ["auditor", "operator", "admin"])
def test_audit_list_full_access_roles_see_all(
    prod_client: TestClient, role: str,
) -> None:
    """审计/运维/管理员看到全部 thread 的审计。"""
    _seed_thread("t-audit-x", "userx")
    _seed_thread("t-audit-y", "usery")
    _seed_audit("t-audit-x")
    _seed_audit("t-audit-y")

    seen = _audit_list(prod_client, _jwt_headers(f"{role}-u", role))
    assert "t-audit-x" in seen and "t-audit-y" in seen, (
        f"{role} 应看到全部审计，实际 {seen}"
    )
    print(f"[PASS] {role} 审计列表全量")


def test_audit_list_admin_token_equivalent(prod_client: TestClient) -> None:
    """admin token + JWT（dispatcher）→ 等效管理员全量可见。"""
    _seed_thread("t-audit-x", "userx")
    _seed_audit("t-audit-x")
    headers = {
        "Authorization": f"Bearer {_token('d1', 'dispatcher')}",
        "X-Admin-Token": TEST_ADMIN_TOKEN,
    }
    seen = _audit_list(prod_client, headers)
    assert "t-audit-x" in seen
    print("[PASS] admin token 审计全量可见")


# ═══════════════════════════════════════════════════════
# 6. 模型切换：无 thread_id 全员可用；有 thread_id 受 owner 校验
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize("role", ["dispatcher", "operator", "kb_admin", "auditor", "admin"])
def test_model_switch_without_thread_id_all_roles(prod_client: TestClient, role: str) -> None:
    """POST /models/switch {model_id}（无 thread_id）→ 全员可用（US-2.3）。"""
    resp = prod_client.post(
        "/models/switch",
        json={"model_id": "mock"},
        headers=_jwt_headers(f"{role}-u", role),
    )
    assert resp.status_code != 403, (
        f"{role} 全局模型切换被 403 误杀: {resp.text[:200]}"
    )


def test_model_switch_foreign_thread_forbidden(prod_client: TestClient) -> None:
    """生产：切换他人会话模型 → 403（M-2 越权）。"""
    _seed_thread("t-sw-zhang", "zhangsan")
    resp = prod_client.post(
        "/models/switch",
        json={"model_id": "mock", "thread_id": "t-sw-zhang"},
        headers=_jwt_headers("lisi", "dispatcher"),
    )
    assert resp.status_code == 403, (
        f"切换他人会话模型应 403，实际 {resp.status_code}: {resp.text[:200]}"
    )


def test_model_switch_own_thread_allowed(prod_client: TestClient) -> None:
    """生产：切换自己会话模型 → 放行。"""
    _seed_thread("t-sw-zhang", "zhangsan")
    resp = prod_client.post(
        "/models/switch",
        json={"model_id": "mock", "thread_id": "t-sw-zhang"},
        headers=_jwt_headers("zhangsan", "dispatcher"),
    )
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"切换自己会话模型应放行，实际 {resp.status_code}: {resp.text[:200]}"
    )


def test_get_models_with_foreign_thread_forbidden(prod_client: TestClient) -> None:
    """GET /models?thread_id=他人会话 → 403（不能窥探他人会话模型）。"""
    _seed_thread("t-sw-zhang", "zhangsan")
    resp = prod_client.get(
        "/models?thread_id=t-sw-zhang",
        headers=_jwt_headers("lisi", "dispatcher"),
    )
    assert resp.status_code == 403, (
        f"窥探他人会话模型应 403，实际 {resp.status_code}: {resp.text[:200]}"
    )


def _request(
    client: TestClient, method: str, path: str,
    body: dict[str, Any] | None, headers: dict[str, str] | None,
):
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, json=body or {}, headers=headers)
