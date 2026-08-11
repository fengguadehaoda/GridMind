"""V1.7.0 多用户地基 · T2 RBAC 角色矩阵测试（P1-1 + P1-2 + P1-3）。

**范围**（架构 multiuser-architecture Task 2 验收 + PRD §四 矩阵逐项 +
register-rbac Task 2 一致性守护）：
1. 5 角色解析：dispatcher / operator / kb_admin / auditor / admin；
   缺 role claim / 未知 role → 默认 dispatcher（不 500）；
2. 灰度读（status/history/metrics）+ 写（set/manual_rollback）：
   匿名→401、调度员→403、运维→放行、admin token→放行、dev→放行；
3. 系统配置（/admin/checkpoint-stats、/debug/sync_lag|sync_force）：同灰度；
4. KB 写：调度员/运维/审计 → 403；知识管理员/管理员 → 放行；
   KB 读：任意角色放行（全局共享，D3）；
5. 审计列表按角色过滤：调度员/知识管理员仅本人 thread；审计/运维/管理员全量；
6. 模型切换：无 thread_id 全员可用（US-2.3）；有 thread_id 受 owner 校验；
7. 权限矩阵权威定义一致性守护：矩阵允许角色 == 各端点 require_role 实参
   + owner 语义（scope own/all），任一漂移 → 红；
8. GET /rbac/matrix 端点：admin 200 / dispatcher 403 / dev 放行 /
   X-Admin-Token 等效 / 响应与权威定义逐字段一致。

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


# ═══════════════════════════════════════════════════════
# 7. 权限矩阵权威定义 + 一致性守护（register-rbac T2）
#    矩阵允许角色 == 各端点 Depends(require_role(...)) 实参 + owner 语义，
#    任一漂移 → 测试红（架构 register-rbac 共享知识 #4）。
# ═══════════════════════════════════════════════════════

_CATEGORIES = ("session", "grayscale", "kb_write", "kb_read", "audit", "system", "model")


def test_matrix_structure_5x7() -> None:
    """ROLE_CATEGORY_MATRIX：恰好 5 角色 × 7 类别，键与 ROLE_VALUES/元信息一致。"""
    from api.services.rbac import ROLE_VALUES
    from api.services.rbac_matrix import (
        CATEGORY_META,
        ROLE_CATEGORY_MATRIX,
        ROLE_META,
    )

    assert set(ROLE_CATEGORY_MATRIX.keys()) == set(ROLE_VALUES)
    for role, row in ROLE_CATEGORY_MATRIX.items():
        assert set(row.keys()) == set(_CATEGORIES), f"{role} 类别缺失/多余"
        assert all(isinstance(v, bool) for v in row.values())
        assert role in ROLE_META, f"ROLE_META 缺 {role}"
    # 所有类别都有元信息（行头悬浮 endpoints 数据源）
    assert set(CATEGORY_META.keys()) == set(_CATEGORIES)
    for cat in _CATEGORIES:
        assert CATEGORY_META[cat]["label"]
        assert CATEGORY_META[cat]["description"]
        assert CATEGORY_META[cat]["endpoints"]


def test_matrix_allowed_roles_match_require_role_call_sites() -> None:
    """逐类别：矩阵允许角色 == 各端点 Depends(require_role(...)) 实参。

    实参来源（grep 现状，register-rbac 基线）：
    - 灰度（/grayscale/* 5 端点）          → require_role(OPERATOR, ADMIN)
    - 系统（/admin/checkpoint-stats、/debug/*）→ require_role(OPERATOR, ADMIN)
    - KB 写（knowledge_upload.py 2 端点）  → require_role(KB_ADMIN, ADMIN)
    - KB 读（GET /api/knowledge/uploads）  → verify_jwt_if_prod（全员）
    - 会话 / 模型 / 审计                   → 全员 + owner 语义（见 scope 断言）
    """
    from api.services.rbac import ROLE_VALUES
    from api.services.rbac_matrix import ROLE_CATEGORY_MATRIX

    def allowed(cat: str) -> set[str]:
        return {r for r in ROLE_VALUES if ROLE_CATEGORY_MATRIX[r][cat]}

    # 灰度 / 系统：require_role(OPERATOR, ADMIN)
    assert allowed("grayscale") == {"operator", "admin"}
    assert allowed("system") == {"operator", "admin"}
    # KB 写：require_role(KB_ADMIN, ADMIN)
    assert allowed("kb_write") == {"kb_admin", "admin"}
    # KB 读 / 会话 / 审计 / 模型：全员（认证即可）
    for cat in ("kb_read", "session", "audit", "model"):
        assert allowed(cat) == set(ROLE_VALUES), f"{cat} 应全员可访问"


def test_matrix_scope_consistency_with_owner_semantics() -> None:
    """scope（own/all）与 owner 语义一致（共享知识 #5 + multiuser §3.4）。

    - 会话：dispatcher/operator/kb_admin/auditor 仅本人（own），admin 全量（all）；
    - 审计：dispatcher/kb_admin 仅本人（own），AUDIT_FULL_ACCESS_ROLES
      （auditor/operator/admin）全量（all）。
    """
    from api.services.rbac import AUDIT_FULL_ACCESS_ROLES, ROLE_VALUES, Role
    from api.services.rbac_matrix import SCOPE_MATRIX

    full_roles = {r.value for r in AUDIT_FULL_ACCESS_ROLES}
    own_roles = set(ROLE_VALUES) - full_roles  # dispatcher / kb_admin

    # 会话 scope
    session_scope = SCOPE_MATRIX["session"]
    assert session_scope["dispatcher"] == "own"
    assert session_scope["operator"] == "own"
    assert session_scope["kb_admin"] == "own"
    assert session_scope["auditor"] == "own"
    assert session_scope["admin"] == "all"
    # 审计 scope：own == 非全量角色；all == AUDIT_FULL_ACCESS_ROLES
    audit_scope = SCOPE_MATRIX["audit"]
    for role in own_roles:
        assert audit_scope[role] == "own", f"审计 {role} 应仅本人"
    for role in full_roles:
        assert audit_scope[role] == "all", f"审计 {role} 应全量"


def _iter_api_routes(main_mod: Any):
    """收集 app 全部 APIRoute（顶层 + 各 include_router 的 router.routes）。

    直接遍历 ``main.py`` 模块级 router 引用（auth/users/rbac/knowledge/
    feature_intro），避免依赖 ``app.routes`` 中 ``_IncludedRouter`` 的
    嵌套结构（FastAPI 版本相关、易漂移）。
    """
    routers = [
        main_mod.app,
        getattr(main_mod, "auth_router", None),
        getattr(main_mod, "users_router", None),
        getattr(main_mod, "rbac_router", None),
        getattr(main_mod, "knowledge_upload_router", None),
        getattr(main_mod, "feature_intro_router", None),
    ]
    for router in routers:
        if router is None:
            continue
        routes = getattr(router, "routes", None)
        if routes is None:
            continue
        for route in routes:
            if hasattr(route, "dependant"):
                yield route


def test_matrix_drift_guard_introspects_require_role_deps(
    prod_client: TestClient,
) -> None:
    """防漂移守卫：扫描 app 全部路由（含 include_router）的 require_role 依赖，
    断言与矩阵一致。

    - 灰度/系统/KB 写类别前缀路由的 require_role 角色集 == 矩阵允许角色；
    - /users*、/rbac/matrix 为 admin-only（用户管理不在 7 类别，属页面级守卫）。
    若新增端点引入新权限类别而不同步矩阵 → 本测试红。
    """
    import inspect

    from api.services.rbac import ROLE_VALUES
    from api.services.rbac_matrix import ROLE_CATEGORY_MATRIX

    import api.main as main_mod

    # 路由前缀 → 矩阵类别（与 require_role 调用点一一对应）
    category_hints = [
        ("/grayscale/", "grayscale"),
        ("/debug/", "system"),
        ("/admin/checkpoint-stats", "system"),
        ("/api/knowledge/upload", "kb_write"),
        ("/api/knowledge/uploads/", "kb_write"),
    ]

    def allowed(cat: str) -> set[str]:
        return {r for r in ROLE_VALUES if ROLE_CATEGORY_MATRIX[r][cat]}

    checked: list[str] = []
    for route in _iter_api_routes(main_mod):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dep in dependant.dependencies:
            call = getattr(dep, "call", None)
            if call is None:
                continue
            try:
                closure = inspect.getclosurevars(call).nonlocals
            except (TypeError, ValueError):
                continue
            allowed_set = closure.get("allowed")
            if not isinstance(allowed_set, frozenset):
                continue
            # 注意：reload 鉴权栈后，未 reload 的 router 闭包持有旧代 Role 枚举
            # 成员（isinstance 新旧类不相等）。统一取 .value 字符串值，跨代稳定。
            route_roles = frozenset(
                getattr(r, "value", r) for r in allowed_set
            )
            if not route_roles:
                continue
            path = route.path
            # 用户管理 / 矩阵端点：admin-only（非 7 类别，单独守护）
            if path.startswith("/users") or path.startswith("/rbac/"):
                assert route_roles == frozenset({"admin"}), (
                    f"{path} 应 admin-only，实际 {sorted(route_roles)}"
                )
                checked.append(path)
                continue
            matched = False
            for prefix, cat in category_hints:
                if path.startswith(prefix):
                    assert route_roles == frozenset(allowed(cat)), (
                        f"{path} require_role={sorted(route_roles)} "
                        f"≠ 矩阵[{cat}]={sorted(allowed(cat))}（漂移！同步矩阵或测试）"
                    )
                    checked.append(path)
                    matched = True
                    break
            assert matched, (
                f"{path} 使用 require_role={sorted(route_roles)} 但未映射到矩阵类别"
            )

    # 必须至少检查到灰度/系统/KB 写全部调用点（防漏检）
    expected_paths = {
        "/grayscale/status", "/grayscale/set", "/grayscale/history",
        "/grayscale/metrics", "/grayscale/manual_rollback",
        "/debug/sync_lag", "/debug/sync_force", "/admin/checkpoint-stats",
        "/api/knowledge/upload", "/api/knowledge/uploads/{doc_id}",
    }
    assert expected_paths.issubset(set(checked)), (
        f"漏检 require_role 调用点：{expected_paths - set(checked)}"
    )


def test_serialize_matrix_shape() -> None:
    """serialize_matrix：roles/categories/matrix/scope/generated_at 结构正确。"""
    from api.services.rbac import ROLE_VALUES
    from api.services.rbac_matrix import (
        CATEGORY_META,
        ROLE_CATEGORY_MATRIX,
        ROLE_META,
        SCOPE_MATRIX,
        serialize_matrix,
    )

    data = serialize_matrix()
    assert set(data.keys()) == {"roles", "categories", "matrix", "scope", "generated_at"}

    assert [r["key"] for r in data["roles"]] == sorted(ROLE_VALUES)
    for r in data["roles"]:
        assert set(r.keys()) == {"key", "label", "description"}
        assert r["label"] == ROLE_META[r["key"]]["label"]
        assert r["description"] == ROLE_META[r["key"]]["description"]

    assert [c["key"] for c in data["categories"]] == list(ROLE_CATEGORY_MATRIX["dispatcher"])
    for c in data["categories"]:
        assert set(c.keys()) == {"key", "label", "description", "endpoints"}
        assert c["endpoints"] == CATEGORY_META[c["key"]]["endpoints"]
        assert c["endpoints"]

    assert data["matrix"] == ROLE_CATEGORY_MATRIX
    assert data["scope"] == SCOPE_MATRIX
    assert data["generated_at"]  # 非空 ISO


# ═══════════════════════════════════════════════════════
# 8. GET /rbac/matrix 端点测试（register-rbac T2）
# ═══════════════════════════════════════════════════════


def test_rbac_matrix_admin_200(prod_client: TestClient) -> None:
    """管理员（生产 JWT role=admin）→ 200 矩阵完整。"""
    resp = prod_client.get("/rbac/matrix", headers=_jwt_headers("admin-u", "admin"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"roles", "categories", "matrix", "scope", "generated_at"}
    assert len(body["roles"]) == 5
    assert len(body["categories"]) == 7
    for role_row in body["matrix"].values():
        assert set(role_row.keys()) == set(_CATEGORIES)


def test_rbac_matrix_dispatcher_403(prod_client: TestClient) -> None:
    """调度员（生产 JWT role=dispatcher）→ 403（矩阵仅 admin，拍板 4）。"""
    resp = prod_client.get("/rbac/matrix", headers=_jwt_headers("d1", "dispatcher"))
    assert resp.status_code == 403, f"调度员应 403，实际 {resp.status_code}: {resp.text}"


def test_rbac_matrix_dev_anonymous_200(dev_client: TestClient) -> None:
    """dev 匿名 → 200（require_role dev 放行，便于前端联调）。"""
    resp = dev_client.get("/rbac/matrix")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["roles"]) == 5


def test_rbac_matrix_admin_token_equivalent(prod_client: TestClient) -> None:
    """X-Admin-Token → 200（等效管理员，二选一通过）。"""
    resp = prod_client.get("/rbac/matrix", headers={"X-Admin-Token": TEST_ADMIN_TOKEN})
    assert resp.status_code == 200, resp.text


def test_rbac_matrix_response_matches_authoritative_definition(
    prod_client: TestClient,
) -> None:
    """端点响应与单一权威定义逐字段一致（前端零硬编码的数据源）。"""
    from api.services.rbac_matrix import ROLE_CATEGORY_MATRIX, SCOPE_MATRIX, serialize_matrix

    resp = prod_client.get("/rbac/matrix", headers=_jwt_headers("admin-u", "admin"))
    assert resp.status_code == 200
    body = resp.json()

    assert body["matrix"] == ROLE_CATEGORY_MATRIX
    assert body["scope"] == SCOPE_MATRIX
    # roles/categories 顺序与 serialize_matrix 一致
    expected = serialize_matrix()
    assert [r["key"] for r in body["roles"]] == [r["key"] for r in expected["roles"]]
    assert [c["key"] for c in body["categories"]] == [c["key"] for c in expected["categories"]]
    # 语义对齐 multiuser-architecture §3.4：核心格断言
    assert body["matrix"]["dispatcher"]["session"] is True
    assert body["matrix"]["dispatcher"]["grayscale"] is False
    assert body["matrix"]["kb_admin"]["kb_write"] is True
    assert body["matrix"]["operator"]["system"] is True
    assert body["matrix"]["auditor"]["audit"] is True
    assert body["matrix"]["admin"]["kb_write"] is True
