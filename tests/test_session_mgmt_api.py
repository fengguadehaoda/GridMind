"""M-5 会话管理 · T1 测试（archived 迁移 + ThreadStore 方法 + /sessions 端点）。

**范围**（架构 session-mgmt-architecture Task 1 验收 + PRD US-1）：
1. ``init_db()`` 幂等迁移：存量库补 archived/deleted_at 列 + 新索引，重复执行不报错；
2. ``ThreadStore`` 新方法：rename_thread / set_archived / delete_thread /
   list_by_owner(archived=) / list_all(archived=)；``list_thread_ids_by_owner``
   保持全量（审计追溯不丢已删会话）；
3. ``ensure_thread_owned`` 与 ``auth.verify_thread_ownership``：archived=2 → 404
   （dev/prod 一致；管理员同样 404）；
4. ``GET /sessions`` 默认只返本人活跃会话（updated_at DESC）；管理员跨用户全量；
   ``?archived=1/2/all`` 过滤正确；
5. ``PATCH /sessions/{id}`` 重命名 / ``POST .../archive|restore`` /
   ``DELETE .../archive=2`` 正确；生产模式他人会话写操作 → 403；
6. 软删后 ``GET /thread/{id}`` 404；dev 放行回归不回归基线。

**隔离**：monkeypatch 把主库切到 ``tmp_path``（database + hitl_audit_service
两处 ``get_connection`` 引用），并 reload 鉴权栈让 ``APP_ENV=production`` 生效
（沿用 test_multiuser_ownership 手法）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "session-mgmt-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "session-mgmt-admin-token"


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


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
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """数据层用例：切库 + init_db 后返回临时 DB 路径。"""
    db = tmp_path / "session_mgmt.db"
    import mcp_tools.db.database as db_mod

    monkeypatch.setattr(db_mod, "get_connection", _connect(db))
    db_mod.init_db()
    return db


@pytest.fixture
def prod_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产模式客户端（主库切 tmp；鉴权栈 reload；teardown 复位 dev）。"""
    tmp_db = tmp_path / "session_mgmt_prod.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    monkeypatch.setattr(db_mod, "get_connection", _connect(tmp_db))
    monkeypatch.setattr(has_mod, "get_connection", _connect(tmp_db))
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
    """dev 模式客户端（基线回归：全部放行）。"""
    tmp_db = tmp_path / "session_mgmt_dev.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    monkeypatch.setattr(db_mod, "get_connection", _connect(tmp_db))
    monkeypatch.setattr(has_mod, "get_connection", _connect(tmp_db))
    db_mod.init_db()

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)

    _reload_stack()


def _token(user_id: str, *, role: str = "dispatcher") -> str:
    """签发测试 JWT（role claim 可选）。"""
    from api.services.auth import issue_test_token

    return issue_test_token(
        user_id=user_id,
        extra_claims={"role": role} if role else None,
    )


def _seed_thread(thread_id: str, owner: str, title: str = "新会话") -> None:
    """在 tmp 主库登记一个 thread。"""
    from api.services.thread_store import ThreadStore

    ThreadStore().create_thread(thread_id, owner, title=title)


def _column_names(db: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _index_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# 1. 迁移幂等：archived/deleted_at 列 + 索引
# ═══════════════════════════════════════════════════════


def test_init_db_adds_archived_columns_and_index(tmp_db: Path) -> None:
    """init_db 后 threads 表含 archived/deleted_at + 新索引。"""
    cols = _column_names(tmp_db, "threads")
    assert "archived" in cols, f"threads 缺 archived 列，实际: {sorted(cols)}"
    assert "deleted_at" in cols, f"threads 缺 deleted_at 列，实际: {sorted(cols)}"
    indexes = _index_names(tmp_db)
    assert "idx_threads_owner_archived_updated" in indexes, (
        f"idx_threads_owner_archived_updated 缺失，实际: {sorted(indexes)}"
    )
    print("[PASS] init_db 补 archived/deleted_at 列 + 新索引")


def test_init_db_archived_migration_idempotent(tmp_db: Path) -> None:
    """init_db 连续 3 次幂等：不报错、列/索引不重复。"""
    import mcp_tools.db.database as db_module

    for _ in range(3):
        db_module.init_db()
    cols = _column_names(tmp_db, "threads")
    assert "archived" in cols
    assert "deleted_at" in cols
    indexes = _index_names(tmp_db)
    assert "idx_threads_owner_archived_updated" in indexes
    print("[PASS] init_db archived 迁移幂等")


def test_legacy_table_gets_columns_via_ensure_threads_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存量库（无 archived 列）经 _ensure_threads_schema 也能补列（双保险）。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE threads (
                thread_id   TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '新会话',
                model_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    import mcp_tools.db.database as db_mod

    monkeypatch.setattr(db_mod, "get_connection", _connect(db))
    from api.services.thread_store import ThreadStore

    ThreadStore().count()  # _open() → _ensure_threads_schema 触发迁移
    cols = _column_names(db, "threads")
    assert "archived" in cols, f"存量库补列失败，实际: {sorted(cols)}"
    assert "deleted_at" in cols
    print("[PASS] 存量库经 _ensure_threads_schema 补列")


def test_init_db_legacy_threads_table_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA Round 1 P0 回归：**init_db 本体**对存量库（threads 无 archived 列）
    不再崩溃（原 bug：executescript 先建归档索引引用不存在的 archived 列 →
    sqlite3.OperationalError），且自动补列 + 索引。"""
    db = tmp_path / "legacy_init_db.db"
    conn = sqlite3.connect(str(db))
    try:
        # M-4 时代旧表：无 archived / deleted_at
        conn.execute(
            """
            CREATE TABLE threads (
                thread_id   TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '新会话',
                model_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO threads (thread_id, owner_id, title) VALUES ('t-old', 'user-a', '存量会话')"
        )
        conn.commit()
    finally:
        conn.close()

    import mcp_tools.db.database as db_mod

    monkeypatch.setattr(db_mod, "get_connection", _connect(db))
    # 不应抛 sqlite3.OperationalError（回归点）
    db_mod.init_db()

    cols = _column_names(db, "threads")
    assert "archived" in cols, f"init_db 后 threads 缺 archived 列，实际: {sorted(cols)}"
    assert "deleted_at" in cols
    indexes = _index_names(db)
    assert "idx_threads_owner_archived_updated" in indexes, (
        f"init_db 后归档索引缺失，实际: {sorted(indexes)}"
    )
    # 存量行保留且 archived 默认 0
    conn2 = sqlite3.connect(str(db))
    try:
        row = conn2.execute(
            "SELECT thread_id, archived, deleted_at FROM threads WHERE thread_id='t-old'"
        ).fetchone()
    finally:
        conn2.close()
    assert row is not None
    assert row[1] == 0
    assert row[2] is None
    # 再次 init_db 幂等（补列/索引不重复报错）
    db_mod.init_db()
    print("[PASS] init_db 对存量库不再崩溃且自动补列 + 索引（QA P0 回归）")


# ═══════════════════════════════════════════════════════
# 2. ThreadStore 新方法
# ═══════════════════════════════════════════════════════


def test_rename_thread(tmp_db: Path) -> None:
    """rename_thread 更新 title + updated_at；未知 thread 返回 False。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-1", "user-a", title="旧标题")
    assert store.rename_thread("t-1", "#1 主变异常处置") is True
    row = store.get_thread("t-1")
    assert row["title"] == "#1 主变异常处置"
    assert store.rename_thread("t-missing", "x") is False
    print("[PASS] rename_thread")


def test_set_archived_and_restore(tmp_db: Path) -> None:
    """set_archived 0/1/2；归档/恢复不改 deleted_at，删除写 deleted_at。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-1", "user-a")
    # 归档
    assert store.set_archived("t-1", 1) is True
    row = store.get_thread("t-1")
    assert row["archived"] == 1
    assert row["deleted_at"] is None
    # 恢复
    assert store.set_archived("t-1", 0) is True
    row = store.get_thread("t-1")
    assert row["archived"] == 0
    assert row["deleted_at"] is None  # 恢复不改 deleted_at
    # 删除（软删）
    assert store.set_archived("t-1", 2, deleted_at="2026-08-10T12:00:00+00:00") is True
    row = store.get_thread("t-1")
    assert row["archived"] == 2
    assert row["deleted_at"] == "2026-08-10T12:00:00+00:00"
    # 非法值
    with pytest.raises(ValueError):
        store.set_archived("t-1", 3)
    print("[PASS] set_archived 0/1/2 + deleted_at 语义")


def test_delete_thread_soft_delete(tmp_db: Path) -> None:
    """delete_thread：archived=2 + deleted_at 非空；checkpoint 数据保留（表行仍在）。"""
    from api.services.thread_store import ThreadStore, delete_thread

    store = ThreadStore()
    store.create_thread("t-1", "user-a")
    assert delete_thread("t-1") is True
    row = store.get_thread("t-1")
    assert row["archived"] == 2
    assert row["deleted_at"] is not None  # UTC ISO 串
    assert delete_thread("t-missing") is False
    print("[PASS] delete_thread 软删")


def test_list_by_owner_archived_filter(tmp_db: Path) -> None:
    """list_by_owner(archived=) 过滤；None=全量（既有行为不变）。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-a", "user-a")
    store.create_thread("t-b", "user-a")
    store.create_thread("t-c", "user-a")
    store.set_archived("t-b", 1)
    store.set_archived("t-c", 2)

    active = {r["thread_id"] for r in store.list_by_owner("user-a", 0)}
    assert active == {"t-a"}
    archived = {r["thread_id"] for r in store.list_by_owner("user-a", 1)}
    assert archived == {"t-b"}
    deleted = {r["thread_id"] for r in store.list_by_owner("user-a", 2)}
    assert deleted == {"t-c"}
    all_rows = {r["thread_id"] for r in store.list_by_owner("user-a", None)}
    assert all_rows == {"t-a", "t-b", "t-c"}
    # 他人会话不可见
    store.create_thread("t-other", "user-b")
    assert store.list_by_owner("user-a", None)  # 不报错
    assert {r["thread_id"] for r in store.list_by_owner("user-a", 0)} == {"t-a"}
    print("[PASS] list_by_owner archived 过滤")


def test_list_all_cross_user(tmp_db: Path) -> None:
    """list_all 跨用户全量（管理员视角）。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-a", "user-a")
    store.create_thread("t-b", "user-b")
    store.set_archived("t-b", 1)
    assert {r["thread_id"] for r in store.list_all(None)} == {"t-a", "t-b"}
    assert {r["thread_id"] for r in store.list_all(0)} == {"t-a"}
    print("[PASS] list_all 跨用户全量")


def test_list_thread_ids_by_owner_keeps_deleted(tmp_db: Path) -> None:
    """审计追溯：list_thread_ids_by_owner 保持全量（含软删会话）。"""
    from api.services.thread_store import ThreadStore, delete_thread

    store = ThreadStore()
    store.create_thread("t-a", "user-a")
    store.create_thread("t-b", "user-a")
    delete_thread("t-b")
    ids = set(store.list_thread_ids_by_owner("user-a"))
    assert ids == {"t-a", "t-b"}, "软删会话仍应被审计列表返回（供追溯）"
    print("[PASS] list_thread_ids_by_owner 不过滤软删")


# ═══════════════════════════════════════════════════════
# 3. ensure_thread_owned：archived=2 → 404（dev/prod 一致、管理员同样 404）
# ═══════════════════════════════════════════════════════


def test_ensure_thread_owned_deleted_404(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 模式下软删会话 ensure_thread_owned → 404（软删=资源不存在）。"""
    from api.services.thread_store import ThreadStore, delete_thread, ensure_thread_owned
    from fastapi import HTTPException

    store = ThreadStore()
    store.create_thread("t-del", "user-a")
    delete_thread("t-del")
    with pytest.raises(HTTPException) as ei:
        ensure_thread_owned("t-del", "user-a", "dispatcher")
    assert ei.value.status_code == 404
    # 管理员同样 404
    with pytest.raises(HTTPException) as ei2:
        ensure_thread_owned("t-del", "admin-x", "admin")
    assert ei2.value.status_code == 404
    print("[PASS] ensure_thread_owned 软删 → 404（dev + admin 一致）")


def test_verify_thread_ownership_deleted_404(
    prod_client: TestClient,
) -> None:
    """生产路径 verify_thread_ownership（events 端点）软删会话 → 404。"""
    _seed_thread("t-del-prod", "zhangsan")
    from api.services.thread_store import delete_thread

    delete_thread("t-del-prod")
    resp = prod_client.get(
        "/sessions/t-del-prod/events",
        headers={"Authorization": f"Bearer {_token('zhangsan')}"},
    )
    assert resp.status_code == 404, (
        f"软删会话 events 应 404，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print("[PASS] 生产 verify_thread_ownership 软删 → 404")


def test_verify_audit_access_deleted_404(
    prod_client: TestClient,
) -> None:
    """生产审计单条（/audit/hitl/{id}）软删会话 → 404（防泄漏，审计/管理员同样）。"""
    _seed_thread("t-del-audit", "zhangsan")
    from api.services.thread_store import delete_thread

    delete_thread("t-del-audit")
    # 管理员角色访问软删会话 → 404（已删会话不可复活访问）
    resp = prod_client.get(
        "/audit/hitl/t-del-audit",
        headers={"Authorization": f"Bearer {_token('admin-x', role='admin')}"},
    )
    assert resp.status_code == 404, (
        f"管理员访问软删审计应 404，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print("[PASS] 生产审计软删 → 404（admin 同样）")


# ═══════════════════════════════════════════════════════
# 4. GET /sessions 端点
# ═══════════════════════════════════════════════════════


def test_get_sessions_default_own_active_only(dev_client: TestClient) -> None:
    """dev 模式 GET /sessions 默认只返本人（dev）活跃会话，按 updated_at 倒序。"""
    _seed_thread("t-a", "dev", title="会话 A")
    _seed_thread("t-b", "dev", title="会话 B")
    _seed_thread("t-other", "other-user", title="他人会话")
    from api.services.thread_store import ThreadStore

    ThreadStore().set_archived("t-b", 1)

    resp = dev_client.get("/sessions")
    assert resp.status_code == 200, resp.text[:200]
    data = resp.json()
    sessions = data["sessions"]
    ids = [s["thread_id"] for s in sessions]
    assert ids == ["t-a"], f"默认只返本人活跃会话，实际 {ids}"
    assert data["total"] == 1
    assert sessions[0]["archived"] == 0  # int 非 boolean
    assert sessions[0]["title"] == "会话 A"
    print("[PASS] GET /sessions 默认本人活跃")


def test_get_sessions_archived_filter(dev_client: TestClient) -> None:
    """GET /sessions?archived=1|2|all 过滤正确。"""
    _seed_thread("t-a", "dev")
    _seed_thread("t-b", "dev")
    _seed_thread("t-c", "dev")
    from api.services.thread_store import ThreadStore, delete_thread

    ThreadStore().set_archived("t-b", 1)
    delete_thread("t-c")

    r1 = dev_client.get("/sessions", params={"archived": "1"}).json()
    assert [s["thread_id"] for s in r1["sessions"]] == ["t-b"]

    r2 = dev_client.get("/sessions", params={"archived": "2"}).json()
    assert [s["thread_id"] for s in r2["sessions"]] == ["t-c"]
    assert r2["sessions"][0]["archived"] == 2

    r3 = dev_client.get("/sessions", params={"archived": "all"}).json()
    assert {s["thread_id"] for s in r3["sessions"]} == {"t-a", "t-b", "t-c"}

    # 非法值 → 422
    bad = dev_client.get("/sessions", params={"archived": "9"})
    assert bad.status_code == 422
    print("[PASS] GET /sessions archived 过滤 + 422")


def test_get_sessions_admin_full_cross_user(prod_client: TestClient) -> None:
    """生产管理员角色 GET /sessions → 跨用户全量（含他人会话）。"""
    _seed_thread("t-zhang", "zhangsan")
    _seed_thread("t-li", "lisi")
    _seed_thread("t-archived", "zhangsan")
    from api.services.thread_store import ThreadStore

    ThreadStore().set_archived("t-archived", 1)

    resp = prod_client.get(
        "/sessions",
        headers={"Authorization": f"Bearer {_token('admin-x', role='admin')}"},
    )
    assert resp.status_code == 200, resp.text[:200]
    ids = {s["thread_id"] for s in resp.json()["sessions"]}
    assert ids == {"t-zhang", "t-li"}, f"管理员应跨用户全量活跃，实际 {ids}"
    print("[PASS] 管理员 GET /sessions 跨用户全量")


def test_get_sessions_owner_only_prod(prod_client: TestClient) -> None:
    """生产调度员 GET /sessions 只返本人活跃会话（不含他人）。"""
    _seed_thread("t-zhang", "zhangsan")
    _seed_thread("t-li", "lisi")

    resp = prod_client.get(
        "/sessions",
        headers={"Authorization": f"Bearer {_token('zhangsan')}"},
    )
    assert resp.status_code == 200, resp.text[:200]
    ids = [s["thread_id"] for s in resp.json()["sessions"]]
    assert ids == ["t-zhang"], f"调度员只应见本人会话，实际 {ids}"
    print("[PASS] 调度员 GET /sessions 仅本人")


# ═══════════════════════════════════════════════════════
# 5. rename / archive / restore / delete 端点 + 越权
# ═══════════════════════════════════════════════════════


def test_rename_archive_restore_delete_endpoints(dev_client: TestClient) -> None:
    """dev 模式写端点全链路：rename → archive → restore → delete。"""
    _seed_thread("t-mgmt", "dev", title="旧标题")

    # rename
    r = dev_client.patch("/sessions/t-mgmt", json={"title": "#1 主变异常处置"})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["title"] == "#1 主变异常处置"
    assert r.json()["thread_id"] == "t-mgmt"

    # archive
    r = dev_client.post("/sessions/t-mgmt/archive")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["archived"] == 1

    # restore
    r = dev_client.post("/sessions/t-mgmt/restore")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["archived"] == 0

    # delete（软删）
    r = dev_client.delete("/sessions/t-mgmt")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["archived"] == 2
    print("[PASS] rename/archive/restore/delete 端点")


def test_rename_invalid_title_422(dev_client: TestClient) -> None:
    """重命名空标题 / 超长标题 → 422。"""
    _seed_thread("t-mgmt", "dev")
    empty = dev_client.patch("/sessions/t-mgmt", json={"title": ""})
    assert empty.status_code == 422
    long = dev_client.patch("/sessions/t-mgmt", json={"title": "x" * 101})
    assert long.status_code == 422
    print("[PASS] 重命名标题校验 422")


def test_soft_deleted_thread_404_after_delete(prod_client: TestClient) -> None:
    """生产模式：软删后 GET /thread/{id} → 404（防泄漏「会话曾存在」）。"""
    _seed_thread("t-gone", "dev")
    resp = prod_client.delete(
        "/sessions/t-gone",
        headers={"Authorization": f"Bearer {_token('dev')}"},
    )
    assert resp.status_code == 200, resp.text[:200]

    r = prod_client.get(
        "/thread/t-gone",
        headers={"Authorization": f"Bearer {_token('dev')}"},
    )
    # 安全校验（verify_thread_ownership_if_prod → _raise_if_thread_deleted）
    # 先于 graph-ready / 业务逻辑抛 404（架构 §1.3 安全优先）。
    assert r.status_code == 404, (
        f"软删后 /thread 应 404，实际 {r.status_code}: {r.text[:200]}"
    )
    print("[PASS] 软删后 /thread/{id} → 404")


def test_soft_deleted_chat_body_404(dev_client: TestClient) -> None:
    """软删后 /chat body 携带该 thread_id → 404（ensure_thread_owned 内联）。"""
    _seed_thread("t-gone", "dev")
    dev_client.delete("/sessions/t-gone")
    r = dev_client.post("/chat", json={"message": "hi", "thread_id": "t-gone"})
    assert r.status_code == 404, (
        f"软删后 /chat body thread_id 应 404，实际 {r.status_code}: {r.text[:200]}"
    )
    print("[PASS] 软删后 /chat body → 404")


# ═══════════════════════════════════════════════════════
# 6. 生产越权攻击用例（他人会话 → 403 / 软删 → 404）
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/sessions/{tid}", {"title": "越权改名"}),
        ("POST", "/sessions/{tid}/archive", None),
        ("POST", "/sessions/{tid}/restore", None),
        ("DELETE", "/sessions/{tid}", None),
    ],
)
def test_cross_user_write_forbidden_prod(
    prod_client: TestClient, method: str, path: str, body: dict[str, Any] | None,
) -> None:
    """生产模式：李四对张三的活跃会话 rename/archive/restore/delete → 403。"""
    _seed_thread("t-owner-zhang", "zhangsan")
    headers = {"Authorization": f"Bearer {_token('lisi')}"}
    url = path.format(tid="t-owner-zhang")
    if method == "PATCH":
        resp = prod_client.patch(url, json=body, headers=headers)
    elif method == "DELETE":
        resp = prod_client.delete(url, headers=headers)
    else:
        resp = prod_client.post(url, json=body, headers=headers)
    assert resp.status_code == 403, (
        f"{method} {url} 他人活跃会话应 403，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print(f"[PASS] {method} {url} → 403（跨用户越权）")


def test_soft_deleted_access_404_prod_even_admin(
    prod_client: TestClient,
) -> None:
    """生产模式：软删会话对 owner / 管理员一律 404（不泄漏存在性）。"""
    _seed_thread("t-del", "zhangsan")
    from api.services.thread_store import delete_thread

    delete_thread("t-del")

    # owner 本人访问 /thread → 404
    r1 = prod_client.get(
        "/thread/t-del",
        headers={"Authorization": f"Bearer {_token('zhangsan')}"},
    )
    assert r1.status_code == 404, (
        f"owner 访问软删 /thread 应 404，实际 {r1.status_code}: {r1.text[:200]}"
    )

    # 管理员角色 rename 软删会话 → 404
    r2 = prod_client.patch(
        "/sessions/t-del",
        json={"title": "复活"},
        headers={"Authorization": f"Bearer {_token('admin-x', role='admin')}"},
    )
    assert r2.status_code == 404, (
        f"管理员 rename 软删会话应 404，实际 {r2.status_code}: {r2.text[:200]}"
    )
    print("[PASS] 软删会话 owner/管理员 一律 404")
