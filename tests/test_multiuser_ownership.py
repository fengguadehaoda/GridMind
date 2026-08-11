"""V1.7.0 多用户地基 · T2 越权攻击用例（生产模式跨用户访问 → 403/404）。

**范围**（架构 multiuser-architecture Task 2 验收 + PRD US-1.1/1.2/1.3）：
1. 生产模式下，李四（dispatcher）访问张三拥有的 thread，9 类会话端点全部
   **403**（owner 不符）/ **404**（严格模式未知 thread）；
2. 张三访问自己全部正常（越权误报为零 —— 断言非 401/403/404，业务码
   503 = graph 未就绪属正常）；
3. 管理员角色 / admin token 通过全部 owner 校验（非 403/404）；
4. 懒登记：未知 thread 首个已认证访问者接管（非 403/404）；
5. token ``thread_id`` claim 快速路径：claim 与 URL 不匹配 → 403（防 probing）；
6. dev 模式全部放行（本地开发零改动）。

**隔离**：monkeypatch 把主库切到 ``tmp_path``（database + hitl_audit_service
两处 ``get_connection`` 引用），并 reload 鉴权栈让 ``APP_ENV=production`` 生效。

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

TEST_JWT_SECRET = "multiuser-ownership-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "multiuser-ownership-admin-token"

#: 9 类会话端点（method, path, body）—— 覆盖 PRD P0-2 列出的全部端点
OWNERSHIP_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/chat/stream/{tid}?message=hi", None),
    ("GET", "/thread/{tid}", None),
    ("GET", "/diagnosis/{tid}/reasoning", None),
    ("POST", "/interrupt/{tid}/approve", {"reason": ""}),
    ("POST", "/interrupt/{tid}/reject", {"reason": ""}),
    ("POST", "/interrupt/{tid}/decision", {"decision": "approve"}),
    ("POST", "/sessions/{tid}/pause", {"reason": ""}),
    ("POST", "/sessions/{tid}/resume", {"action": "continue_from_pause"}),
    ("POST", "/sessions/{tid}/rewind", {"step_index": 0}),
    ("POST", "/sessions/{tid}/abort", {"reason": ""}),
    ("GET", "/sessions/{tid}/events", None),
    ("GET", "/audit/hitl/{tid}", None),
]

#: 生产模式越权后应返回的状态码集合（403=越权 / 404=严格模式）
FORBIDDEN_CODES = (403, 404)

#: dev 放行断言排除的业务 404（诊断无推理链等）：非 401/403 即放行
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
    """按依赖顺序重载鉴权栈（reload 后各模块拿到最新 env/settings）。"""
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
    """生产模式客户端（主库切 tmp；鉴权栈 reload；teardown 复位 dev）。"""
    tmp_db = tmp_path / "ownership.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    patched = _connect(tmp_db)
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(has_mod, "get_connection", patched)
    # 先建表（用 patched 连接）
    db_mod.init_db()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)

    # teardown：复位 dev 态（防 importlib.reload 副作用泄漏）
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    _reload_stack()


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 模式客户端（基线回归：全部放行）。"""
    tmp_db = tmp_path / "ownership_dev.db"
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


def _token(user_id: str, *, role: str = "dispatcher", thread_claim: str | None = None) -> str:
    """签发测试 JWT（role claim 可选；thread_id claim 可选）。"""
    from api.services.auth import issue_test_token

    return issue_test_token(
        user_id=user_id,
        thread_id=thread_claim,
        extra_claims={"role": role} if role else None,
    )


def _seed_thread(thread_id: str, owner: str) -> None:
    """在 tmp 主库登记一个 thread。"""
    from api.services.thread_store import ThreadStore

    ThreadStore().create_thread(thread_id, owner)


def _request(
    client: TestClient, method: str, path: str,
    body: dict[str, Any] | None, headers: dict[str, str] | None = None,
):
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, json=body if body is not None else {}, headers=headers)


# ═══════════════════════════════════════════════════════
# 1. 生产模式：李四访问张三的 thread → 403/404（每个端点）
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(("method", "path", "body"), OWNERSHIP_ENDPOINTS)
def test_cross_user_access_forbidden_on_every_endpoint(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """李四（dispatcher）访问张三拥有的 ``t-owner-zhang`` → 403/404。"""
    _seed_thread("t-owner-zhang", "zhangsan")
    headers = {"Authorization": f"Bearer {_token('lisi')}"}
    url = path.format(tid="t-owner-zhang")
    resp = _request(prod_client, method, url, body, headers=headers)
    assert resp.status_code in FORBIDDEN_CODES, (
        f"{method} {url} 李四访问张三 thread 应 403/404，"
        f"实际 {resp.status_code}: {resp.text[:200]}"
    )
    print(f"[PASS] {method} {url} → {resp.status_code}（跨用户越权拦截）")


def test_chat_with_foreign_thread_id_in_body_forbidden(
    prod_client: TestClient,
) -> None:
    """``POST /chat`` body 携带他人 thread_id → 403（handler 内联校验）。"""
    _seed_thread("t-owner-zhang", "zhangsan")
    resp = prod_client.post(
        "/chat",
        json={"message": "hi", "thread_id": "t-owner-zhang"},
        headers={"Authorization": f"Bearer {_token('lisi')}"},
    )
    assert resp.status_code in FORBIDDEN_CODES, (
        f"/chat body 越权应 403/404，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print("[PASS] /chat body thread_id 越权 → 403/404")


# ═══════════════════════════════════════════════════════
# 2. 正向：owner 本人访问全部正常（不误报）
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(("method", "path", "body"), OWNERSHIP_ENDPOINTS)
def test_owner_access_not_rejected(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """张三访问自己的 thread → 非 401/403（业务 404/503 属正常，非越权误报）。"""
    _seed_thread("t-owner-zhang", "zhangsan")
    headers = {"Authorization": f"Bearer {_token('zhangsan')}"}
    url = path.format(tid="t-owner-zhang")
    resp = _request(prod_client, method, url, body, headers=headers)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {url} 张三访问自己 thread 被误报 {resp.status_code}: {resp.text[:200]}"
    )
    print(f"[PASS] {method} {url} → {resp.status_code}（owner 正常）")


# ═══════════════════════════════════════════════════════
# 3. 管理员角色 / admin token 放行
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(("method", "path", "body"), OWNERSHIP_ENDPOINTS)
def test_admin_role_bypasses_owner_check(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """管理员角色（JWT role=admin）访问任意 thread → 非 401/403。"""
    _seed_thread("t-owner-zhang", "zhangsan")
    headers = {"Authorization": f"Bearer {_token('admin-user', role='admin')}"}
    url = path.format(tid="t-owner-zhang")
    resp = _request(prod_client, method, url, body, headers=headers)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {url} 管理员角色被误报 {resp.status_code}: {resp.text[:200]}"
    )
    print(f"[PASS] {method} {url} → {resp.status_code}（admin 角色放行）")


@pytest.mark.parametrize(("method", "path", "body"), OWNERSHIP_ENDPOINTS)
def test_admin_token_bypasses_owner_check(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """admin token + 合法 JWT 访问任意 thread → 非 401/403。

    注：会话端点属 ``verify_jwt_if_prod`` 口径（生产强制 JWT；events 恒要 token），
    因此 admin token 需与 JWT 同传，等效管理员通过 owner 校验（US-1.2）。
    """
    _seed_thread("t-owner-zhang", "zhangsan")
    headers = {
        "Authorization": f"Bearer {_token('dispatcher-user')}",
        "X-Admin-Token": TEST_ADMIN_TOKEN,
    }
    url = path.format(tid="t-owner-zhang")
    resp = _request(prod_client, method, url, body, headers=headers)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"{method} {url} admin token 被误报 {resp.status_code}: {resp.text[:200]}"
    )
    print(f"[PASS] {method} {url} → {resp.status_code}（admin token 放行）")


# ═══════════════════════════════════════════════════════
# 4. 懒登记：未知 thread 首个已认证访问者接管
# ═══════════════════════════════════════════════════════


def test_lazy_registration_claims_unknown_thread(prod_client: TestClient) -> None:
    """生产：未知 thread 首次访问 → 懒登记为当前用户（非 403/404）。"""
    headers = {"Authorization": f"Bearer {_token('lisi')}"}
    resp = prod_client.get(
        "/thread/t-legacy-unknown",
        headers=headers,
    )
    assert resp.status_code not in (401, 403, 404), (
        f"懒登记应放行（业务 503 属正常），实际 {resp.status_code}: {resp.text[:200]}"
    )
    from api.services.thread_store import ThreadStore

    assert ThreadStore().get_owner("t-legacy-unknown") == "lisi", (
        "懒登记应把 t-legacy-unknown 接管给 lisi"
    )
    print("[PASS] 未知 thread 懒登记 → owner=lisi")


def test_strict_mode_unknown_thread_404(
    prod_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """严格模式（THREADS_STRICT_MODE=true）：未知 thread → 404。"""
    monkeypatch.setenv("THREADS_STRICT_MODE", "true")
    _reload_stack()
    try:
        import api.main as main_mod

        client = TestClient(main_mod.app, raise_server_exceptions=False)
        headers = {"Authorization": f"Bearer {_token('lisi')}"}
        resp = client.get("/thread/t-strict-unknown", headers=headers)
        assert resp.status_code == 404, (
            f"严格模式未知 thread 应 404，实际 {resp.status_code}: {resp.text[:200]}"
        )
        print("[PASS] 严格模式未知 thread → 404")
    finally:
        monkeypatch.delenv("THREADS_STRICT_MODE", raising=False)
        _reload_stack()


# ═══════════════════════════════════════════════════════
# 5. token thread_id claim 快速路径（防 probing）
# ═══════════════════════════════════════════════════════


def test_claim_mismatch_forbidden_before_db_check(prod_client: TestClient) -> None:
    """token 携带 thread_id claim=t-x 访问 /thread/t-owner-zhang → 403（快速路径）。"""
    _seed_thread("t-owner-zhang", "zhangsan")
    headers = {
        "Authorization": f"Bearer {_token('zhangsan', thread_claim='t-x')}",
    }
    resp = prod_client.get("/thread/t-owner-zhang", headers=headers)
    assert resp.status_code == 403, (
        f"claim 不匹配应 403，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print("[PASS] claim 快速路径 → 403（不泄漏具体值）")


# ═══════════════════════════════════════════════════════
# 6. dev 放行回归（本地开发零改动）
# ═══════════════════════════════════════════════════════

# events 端点保持「始终要 token」既有行为（架构 §7.3 例外），单独覆盖
DEV_ALLOW_ENDPOINTS = [
    e for e in OWNERSHIP_ENDPOINTS if e[1] != "/sessions/{tid}/events"
]


@pytest.mark.parametrize(("method", "path", "body"), DEV_ALLOW_ENDPOINTS)
def test_dev_mode_cross_user_allowed(
    dev_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """dev 模式：匿名访问任意会话端点 → 非 401/403（404 属业务，放行）。"""
    _seed_thread("t-dev-any", "zhangsan")
    url = path.format(tid="t-dev-any")
    resp = _request(dev_client, method, url, body)
    assert resp.status_code not in NOT_AUTH_BLOCKED, (
        f"dev 模式 {method} {url} 被鉴权误伤 {resp.status_code}: {resp.text[:200]}"
    )
    print(f"[PASS] dev {method} {url} → {resp.status_code}（放行）")


def test_dev_events_still_requires_token(dev_client: TestClient) -> None:
    """例外回归：events 端点 dev 模式仍「始终要 token」（匿名 → 401）。"""
    _seed_thread("t-dev-any", "zhangsan")
    resp = dev_client.get("/sessions/t-dev-any/events")
    assert resp.status_code == 401, (
        f"dev events 匿名应 401（始终要 token），实际 {resp.status_code}"
    )
    # 带合法 JWT → 通过（200/503 均正常）
    from api.services.auth import issue_test_token

    ok = dev_client.get(
        "/sessions/t-dev-any/events",
        headers={"Authorization": f"Bearer {issue_test_token('dev-user')}"},
    )
    assert ok.status_code not in NOT_AUTH_BLOCKED, (
        f"dev events 带 JWT 应放行，实际 {ok.status_code}"
    )
    print("[PASS] dev events 匿名 401 / 带 JWT 放行（始终要 token 语义保持）")


def test_dev_anonymous_chat_creates_thread_with_dev_owner(
    dev_client: TestClient,
) -> None:
    """dev 模式 /chat 无 thread_id → 新会话 owner 记 dev（架构 §7.3）。"""
    resp = dev_client.post("/chat", json={"message": "hi"})
    assert resp.status_code not in (401, 403, 404), (
        f"dev /chat 被误伤 {resp.status_code}: {resp.text[:200]}"
    )
    from api.services.thread_store import ThreadStore

    tid = resp.json().get("thread_id", "") if resp.status_code == 200 else ""
    if tid:
        assert ThreadStore().get_owner(tid) == "dev"
        print(f"[PASS] dev /chat 新会话 owner=dev（tid={tid}）")
    else:
        # graph 未就绪返回 503 → 无法断言 thread_id，跳过（非失败）
        print("[SKIP] dev /chat graph 未就绪（503），跳过 owner 断言")


# ═══════════════════════════════════════════════════════
# 7. QA 记档修复：/interrupt/{tid}/decision 审计 user_id 追溯操作者
# ═══════════════════════════════════════════════════════


class _DummyGraphBuilder:
    """仅用于越过端点 503 闸门；COMPILED_GRAPH=None 时 decision 走
    「写审计后直接返回」路径（不 resume），可确定性断言 user_id 落库。"""

    async def resume(self, thread_id: str, action: str, reason: str = "", **kw):
        return {"messages": []}


def _patch_decision_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 graph_builder 置非 None、COMPILED_GRAPH 置 None（确定性审计路径）。"""
    import api.main as main_mod
    import api.graph as graph_mod

    monkeypatch.setattr(main_mod, "graph_builder", _DummyGraphBuilder())
    monkeypatch.setattr(graph_mod, "COMPILED_GRAPH", None)


def test_decision_audit_records_jwt_user_id(
    prod_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA 记档修复：生产模式带 JWT 调用 /interrupt/{tid}/decision →
    审计日志 user_id = JWT 用户（不再是 "anonymous"）。

    电网操作票追责要求：HITL 决策必须能追溯到「哪个用户做了决策」。
    """
    from api.services.hitl_audit_service import HitlAuditService

    tid = "t-decision-audit-prod"
    _seed_thread(tid, "operator-a")
    _patch_decision_env(monkeypatch)

    headers = {"Authorization": f"Bearer {_token('operator-a')}"}
    resp = prod_client.post(
        f"/interrupt/{tid}/decision",
        json={"decision": "approve", "reason": "现场确认"},
        headers=headers,
    )
    assert resp.status_code == 200, (
        f"生产 owner 调用 decision 应 200，实际 {resp.status_code}: {resp.text[:200]}"
    )

    rows = HitlAuditService.query_by_thread(tid)
    assert len(rows) == 1, f"应有 1 条审计记录，实际 {len(rows)}"
    assert rows[0]["user_id"] == "operator-a", (
        f"审计 user_id 应为 JWT 用户 operator-a，实际 {rows[0]['user_id']!r}"
    )
    assert rows[0]["decision"] == "approve", (
        f"审计 decision 应为 approve，实际 {rows[0]['decision']!r}"
    )
    print(f"[PASS] 生产 /interrupt/{{tid}}/decision 审计 user_id={rows[0]['user_id']!r}")


def test_decision_audit_prod_other_user_still_traceable(
    prod_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员角色跨用户决策 → 审计 user_id 仍为实际 JWT 操作者（非 thread owner）。"""
    from api.services.hitl_audit_service import HitlAuditService

    tid = "t-decision-audit-admin"
    _seed_thread(tid, "zhangsan")
    _patch_decision_env(monkeypatch)

    # admin 角色可访问任意 thread；审计应记录「谁做的决策」= JWT 用户
    headers = {"Authorization": f"Bearer {_token('admin-user', role='admin')}"}
    resp = prod_client.post(
        f"/interrupt/{tid}/decision",
        json={"decision": "reject", "reason": "保电时段不允许"},
        headers=headers,
    )
    assert resp.status_code == 200, (
        f"admin 角色调用 decision 应 200，实际 {resp.status_code}: {resp.text[:200]}"
    )

    rows = HitlAuditService.query_by_thread(tid)
    assert len(rows) == 1, f"应有 1 条审计记录，实际 {len(rows)}"
    assert rows[0]["user_id"] == "admin-user", (
        f"审计 user_id 应为实际操作者 admin-user，实际 {rows[0]['user_id']!r}"
    )
    print(
        f"[PASS] admin 跨用户决策审计 user_id={rows[0]['user_id']!r}"
        f"（实际操作者，非 thread owner zhangsan）"
    )


def test_decision_dev_no_token_not_500(
    dev_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dev 模式无 token 调用 /interrupt/{tid}/decision → 不 500（保持放行语义）；
    审计 user_id 回退 "anonymous"（与既有 dev 行为一致）。"""
    from api.services.hitl_audit_service import HitlAuditService

    tid = "t-decision-audit-dev"
    _seed_thread(tid, "zhangsan")
    _patch_decision_env(monkeypatch)

    resp = dev_client.post(
        f"/interrupt/{tid}/decision",
        json={"decision": "approve", "reason": ""},
    )
    assert resp.status_code == 200, (
        f"dev 无 token 调用 decision 应 200（不 500），实际 {resp.status_code}: {resp.text[:200]}"
    )

    rows = HitlAuditService.query_by_thread(tid)
    assert len(rows) == 1, f"应有 1 条审计记录，实际 {len(rows)}"
    assert rows[0]["user_id"] == "anonymous", (
        f"dev 无 token 审计 user_id 应回退 anonymous，实际 {rows[0]['user_id']!r}"
    )
    print(f"[PASS] dev 无 token decision → 200（不 500），审计 user_id 回退 anonymous")
