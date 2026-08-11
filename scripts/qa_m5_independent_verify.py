"""M-5 独立回归验证（QA 严过关 · 独立于工程师测试文件）。

覆盖（架构 session-mgmt §1.3 + PRD US-1/AC1-1~4 + 共享知识 #1/#2）：
  1. 迁移幂等：存量库（无 archived 列）经 init_db 自动补列 + 索引；
  2. GET /sessions：dispatcher 只返本人活跃；管理员跨用户全量；?archived=0|1|2|all；
  3. rename/archive/restore/delete owner 正向闭环；
  4. 生产模式越权（他人会话 4 写端点 → 403）；
  5. 软删 404：DELETE 后 /thread/{id}、/chat/stream、/sessions/{id}/pause → 404
     （owner 与 admin 一致，防泄漏）；审计追溯 list_thread_ids_by_owner 不过滤；
  6. archived 语义：归档=1（列表消失但不 404，可 restore）；删除=2（404）；restore 1→0。

隔离：monkeypatch 风格手工切 tmp 库 + reload 鉴权栈（与工程师测试同手法，
但本脚本独立实现断言，不 import 其测试模块）。
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

# 确保项目根目录在 sys.path（脚本可能从 scripts/ 或根目录调用）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(_PROJECT_ROOT)

from fastapi.testclient import TestClient

TEST_JWT_SECRET = "qa-m5-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "qa-m5-admin-token"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} — {detail}")


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


def _token(user_id: str, *, role: str = "dispatcher") -> str:
    from api.services.auth import issue_test_token
    return issue_test_token(user_id=user_id, extra_claims={"role": role} if role else None)


def _seed(thread_id: str, owner: str, title: str = "新会话") -> None:
    from api.services.thread_store import ThreadStore
    ThreadStore().create_thread(thread_id, owner, title=title)


def _cols(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
    finally:
        conn.close()


def _idxs(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# 1. 迁移幂等
# ═══════════════════════════════════════════════════════
def verify_migration(tmp_path: Path, monkeypatch) -> None:
    print("\n== 1. 迁移幂等 ==")
    import mcp_tools.db.database as db_mod
    db = tmp_path / "migrate.db"
    monkeypatch.setattr(db_mod, "get_connection", _connect(db))
    db_mod.init_db()
    check("新库建全 archived/deleted_at 列", {"archived", "deleted_at"} <= _cols(db),
          f"cols={_cols(db)}")
    check("新库建 idx_threads_owner_archived_updated 索引",
          "idx_threads_owner_archived_updated" in _idxs(db), f"idxs={_idxs(db)}")
    for _ in range(3):
        db_mod.init_db()
    check("init_db 连续 3 次幂等不报错", True)

    # 存量库：无 archived 列的旧表 → init_db 自动补列（验收项 1 —— 已知 BUG 复现）
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    try:
        conn.execute("""
            CREATE TABLE threads (
                thread_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '新会话', model_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO threads (thread_id, owner_id) VALUES ('t-old', 'dev')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(db_mod, "get_connection", _connect(legacy))
    try:
        db_mod.init_db()
        init_db_ok = True
        init_db_err = ""
    except Exception as e:  # noqa: BLE001
        init_db_ok = False
        init_db_err = f"{type(e).__name__}: {e}"
    check(
        "存量库启动 init_db 不报错（自动补列）",
        init_db_ok,
        f"存量库（无 archived 列）init_db 抛异常 → 启动失败 BUG: {init_db_err}",
    )
    # 即便 init_db 抛异常，也要继续验证 _ensure_threads_schema 双保险路径
    try:
        from api.services.thread_store import ThreadStore
        ThreadStore().count()  # _open() → _ensure_threads_schema 触发迁移
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] _ensure_threads_schema 也失败: {e}")
    cols = _cols(legacy)
    check("存量库经 _ensure_threads_schema 补 archived 列", "archived" in cols, f"cols={cols}")
    check("存量库经 _ensure_threads_schema 补 deleted_at 列", "deleted_at" in cols, f"cols={cols}")
    check("存量库补侧栏索引",
          "idx_threads_owner_archived_updated" in _idxs(legacy), f"idxs={_idxs(legacy)}")
    # 存量行默认 archived=0
    conn = sqlite3.connect(str(legacy))
    try:
        row = conn.execute("SELECT archived FROM threads WHERE thread_id='t-old'").fetchone()
    finally:
        conn.close()
    check("存量行默认 archived=0", row and row[0] == 0, f"row={row}")


# ═══════════════════════════════════════════════════════
# 2. GET /sessions 列表语义
# ═══════════════════════════════════════════════════════
def verify_sessions_list(prod_client: TestClient) -> None:
    print("\n== 2. GET /sessions 列表语义（生产）==")
    _seed("t-zhang-1", "zhangsan", title="会话A")
    _seed("t-zhang-2", "zhangsan", title="会话B")
    _seed("t-zhang-3", "zhangsan", title="会话C")
    _seed("t-li-1", "lisi", title="李四会话")
    from api.services.thread_store import ThreadStore, delete_thread
    ThreadStore().set_archived("t-zhang-2", 1)
    delete_thread("t-zhang-3")

    # dispatcher 只返本人活跃
    r = prod_client.get("/sessions", headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("GET /sessions 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    ids = [s["thread_id"] for s in r.json()["sessions"]]
    check("dispatcher 只返本人活跃（不含归档/删除/他人）", ids == ["t-zhang-1"], f"ids={ids}")
    check("响应 total=1", r.json()["total"] == 1, f"total={r.json()['total']}")
    check("archived 为 int 0", r.json()["sessions"][0]["archived"] == 0, "archived 非 int 0")
    check("响应不含 owner_id（不泄漏内部字段）", "owner_id" not in r.json()["sessions"][0],
          "响应泄漏 owner_id")

    # ?archived=1 → 归档；?archived=2 → 删除；all → 全部
    r1 = prod_client.get("/sessions", params={"archived": "1"},
                         headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("?archived=1 返归档", [s["thread_id"] for s in r1.json()["sessions"]] == ["t-zhang-2"],
          f"{r1.json()['sessions']}")
    r2 = prod_client.get("/sessions", params={"archived": "2"},
                         headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("?archived=2 返删除", [s["thread_id"] for s in r2.json()["sessions"]] == ["t-zhang-3"],
          f"{r2.json()['sessions']}")
    r3 = prod_client.get("/sessions", params={"archived": "all"},
                         headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    all_ids = {s["thread_id"] for s in r3.json()["sessions"]}
    check("?archived=all 返全状态（本人）", all_ids == {"t-zhang-1", "t-zhang-2", "t-zhang-3"},
          f"{all_ids}")
    bad = prod_client.get("/sessions", params={"archived": "9"},
                          headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("?archived=9 → 422", bad.status_code == 422, f"{bad.status_code}")

    # 管理员跨用户全量
    ra = prod_client.get("/sessions", headers={"Authorization": f"Bearer {_token('admin-x', role='admin')}"})
    check("管理员 GET /sessions 200", ra.status_code == 200, f"{ra.status_code}")
    admin_ids = {s["thread_id"] for s in ra.json()["sessions"]}
    check("管理员跨用户全量（含李四活跃）", admin_ids == {"t-zhang-1", "t-li-1"},
          f"admin_ids={admin_ids}")

    # admin token（X-Admin-Token）叠加在 JWT 之上等效管理员（verify_jwt_if_prod 仍强制 JWT）
    rb = prod_client.get("/sessions", headers={
        "Authorization": f"Bearer {_token('zhangsan')}",
        "X-Admin-Token": TEST_ADMIN_TOKEN,
    })
    check("JWT + X-Admin-Token 跨用户全量",
          rb.status_code == 200 and {s["thread_id"] for s in rb.json()["sessions"]} == {"t-zhang-1", "t-li-1"},
          f"{rb.status_code}: {rb.text[:200]}")
    # 仅 X-Admin-Token 无 JWT → 401（生产强制 JWT，符合 verify_jwt_if_prod 契约）
    rb2 = prod_client.get("/sessions", headers={"X-Admin-Token": TEST_ADMIN_TOKEN})
    check("仅 X-Admin-Token 无 JWT → 401（预期）", rb2.status_code == 401, f"{rb2.status_code}")

    # 未带 token 生产 → 401
    rn = prod_client.get("/sessions")
    check("生产无 token → 401", rn.status_code == 401, f"{rn.status_code}")


# ═══════════════════════════════════════════════════════
# 3. rename/archive/restore/delete 正向闭环 + archived 语义
# ═══════════════════════════════════════════════════════
def verify_write_cycle(prod_client: TestClient) -> None:
    print("\n== 3. 写端点正向闭环 + archived 语义（生产）==")
    _seed("t-cycle", "zhangsan", title="旧标题")

    # rename
    r = prod_client.patch("/sessions/t-cycle", json={"title": "#1 主变异常处置"},
                          headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("PATCH rename 200 + 标题更新", r.status_code == 200 and r.json()["title"] == "#1 主变异常处置",
          f"{r.status_code}: {r.text[:200]}")

    # archive → 1
    r = prod_client.post("/sessions/t-cycle/archive",
                         headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("POST archive → 200 archived=1", r.status_code == 200 and r.json()["archived"] == 1,
          f"{r.status_code}: {r.text[:200]}")
    # 归档后列表消失但不 404：GET /thread 仍可访问（archived=1 非软删）
    rt = prod_client.get("/thread/t-cycle", headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("归档=1 后 /thread 不 404（可 restore）", rt.status_code != 404,
          f"/thread 归档后 {rt.status_code}")

    # restore → 0（从 1→0）
    r = prod_client.post("/sessions/t-cycle/restore",
                         headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("POST restore → 200 archived=0（1→0）",
          r.status_code == 200 and r.json()["archived"] == 0, f"{r.status_code}: {r.text[:200]}")

    # delete → 2（软删）
    r = prod_client.delete("/sessions/t-cycle",
                           headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("DELETE → 200 archived=2", r.status_code == 200 and r.json()["archived"] == 2,
          f"{r.status_code}: {r.text[:200]}")
    from api.services.thread_store import ThreadStore
    row = ThreadStore().get_thread("t-cycle")
    check("软删后 deleted_at 非空", row and row["deleted_at"] is not None, f"deleted_at={row and row['deleted_at']}")

    # rename 标题校验
    e1 = prod_client.patch("/sessions/t-cycle", json={"title": ""},
                           headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    e2 = prod_client.patch("/sessions/t-cycle", json={"title": "x" * 101},
                           headers={"Authorization": f"Bearer {_token('zhangsan')}"})
    check("rename 空标题 → 422", e1.status_code == 422, f"{e1.status_code}")
    check("rename 超长标题 → 422", e2.status_code == 422, f"{e2.status_code}")


# ═══════════════════════════════════════════════════════
# 4. 生产越权（他人会话 → 403）
# ═══════════════════════════════════════════════════════
def verify_cross_user_403(prod_client: TestClient) -> None:
    print("\n== 4. 生产越权（他人会话 4 写端点 → 403）==")
    _seed("t-other", "zhangsan")
    h = {"Authorization": f"Bearer {_token('lisi')}"}

    r1 = prod_client.patch("/sessions/t-other", json={"title": "越权改名"}, headers=h)
    check("他人 rename → 403", r1.status_code == 403, f"{r1.status_code}: {r1.text[:200]}")

    r2 = prod_client.post("/sessions/t-other/archive", headers=h)
    check("他人 archive → 403", r2.status_code == 403, f"{r2.status_code}: {r2.text[:200]}")

    r3 = prod_client.post("/sessions/t-other/restore", headers=h)
    check("他人 restore → 403", r3.status_code == 403, f"{r3.status_code}: {r3.text[:200]}")

    r4 = prod_client.delete("/sessions/t-other", headers=h)
    check("他人 delete → 403", r4.status_code == 403, f"{r4.status_code}: {r4.text[:200]}")

    # 404 文案不泄漏存在性（403 而非 404）
    for name, resp in [("rename", r1), ("archive", r2), ("restore", r3), ("delete", r4)]:
        check(f"{name} 不泄漏存在性（统一 403）", resp.status_code == 403, "")


# ═══════════════════════════════════════════════════════
# 5. 软删 404（所有 thread 入口；owner 与 admin 一致；审计追溯不受影响）
# ═══════════════════════════════════════════════════════
def verify_soft_delete_404(prod_client: TestClient) -> None:
    print("\n== 5. 软删 404（所有 thread 入口；owner/admin 一致；审计追溯）==")
    _seed("t-del", "zhangsan")
    prod_client.delete("/sessions/t-del", headers={"Authorization": f"Bearer {_token('zhangsan')}"})

    owner_h = {"Authorization": f"Bearer {_token('zhangsan')}"}
    admin_h = {"Authorization": f"Bearer {_token('admin-x', role='admin')}"}

    # /thread/{id}
    r = prod_client.get("/thread/t-del", headers=owner_h)
    check("软删后 GET /thread/{id} → 404（owner）", r.status_code == 404, f"{r.status_code}")
    r = prod_client.get("/thread/t-del", headers=admin_h)
    check("软删后 GET /thread/{id} → 404（admin）", r.status_code == 404, f"{r.status_code}")

    # /chat/stream/{id}
    r = prod_client.get("/chat/stream/t-del", headers=owner_h)
    check("软删后 GET /chat/stream/{id} → 404（owner）", r.status_code == 404, f"{r.status_code}")
    r = prod_client.get("/chat/stream/t-del", headers=admin_h)
    check("软删后 GET /chat/stream/{id} → 404（admin）", r.status_code == 404, f"{r.status_code}")

    # /sessions/{id}/pause（路径型依赖 verify_thread_ownership_if_prod）
    r = prod_client.post("/sessions/t-del/pause", headers=owner_h)
    check("软删后 POST /sessions/{id}/pause → 404（owner）", r.status_code == 404, f"{r.status_code}")
    r = prod_client.post("/sessions/t-del/pause", headers=admin_h)
    check("软删后 POST /sessions/{id}/pause → 404（admin）", r.status_code == 404, f"{r.status_code}")

    # /sessions/{id}/events（verify_thread_ownership → _raise_if_thread_deleted）
    r = prod_client.get("/sessions/t-del/events", headers=owner_h)
    check("软删后 GET /sessions/{id}/events → 404（owner）", r.status_code == 404, f"{r.status_code}")

    # /chat body thread_id → 404（ensure_thread_owned 内联）
    r = prod_client.post("/chat", json={"message": "hi", "thread_id": "t-del"}, headers=owner_h)
    check("软删后 POST /chat body thread_id → 404", r.status_code == 404, f"{r.status_code}")

    # 审计追溯：list_thread_ids_by_owner 不过滤（含软删会话）
    from api.services.thread_store import ThreadStore
    ids = set(ThreadStore().list_thread_ids_by_owner("zhangsan"))
    check("审计 list_thread_ids_by_owner 含软删会话", "t-del" in ids, f"ids={ids}")


# ═══════════════════════════════════════════════════════
# 6. dev 放行回归（不回归基线）
# ═══════════════════════════════════════════════════════
def verify_dev_regression(dev_client: TestClient) -> None:
    print("\n== 6. dev 放行回归 ==")
    _seed("t-dev", "dev")
    r = dev_client.get("/sessions")
    check("dev GET /sessions 200", r.status_code == 200, f"{r.status_code}")
    ids = [s["thread_id"] for s in r.json()["sessions"]]
    check("dev 只返 dev 活跃会话", ids == ["t-dev"], f"ids={ids}")


def main() -> None:
    import tempfile
    import sys
    from types import SimpleNamespace

    tmp = Path(tempfile.mkdtemp(prefix="qa_m5_"))
    # 简易 monkeypatch 容器
    class MP:
        def __init__(self):
            self._saved = []
        def setattr(self, obj, name, value):
            old = getattr(obj, name, None)
            self._saved.append((obj, name, old))
            setattr(obj, name, value)
        def setenv(self, k, v):
            import os
            old = os.environ.get(k)
            self._saved.append(("env", k, old))
            os.environ[k] = v
        def delenv(self, k, raising=False):
            import os
            old = os.environ.get(k)
            self._saved.append(("env", k, old))
            if k in os.environ:
                del os.environ[k]
            elif raising:
                raise KeyError(k)
        def teardown(self):
            import os
            for obj, name, old in reversed(self._saved):
                if obj == "env":
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old
                else:
                    setattr(obj, name, old)
    mp = MP()

    try:
        # ── 迁移（dev 环境即可，不 reload）──
        verify_migration(tmp / "m", mp)

        # ── 生产客户端 ──
        import mcp_tools.db.database as db_mod
        import api.services.hitl_audit_service as has_mod
        prod_db = tmp / "prod.db"
        mp.setattr(db_mod, "get_connection", _connect(prod_db))
        mp.setattr(has_mod, "get_connection", _connect(prod_db))
        db_mod.init_db()
        mp.setenv("APP_ENV", "production")
        mp.setenv("JWT_SECRET", TEST_JWT_SECRET)
        mp.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
        _reload_stack()
        import api.main as main_mod
        prod_client = TestClient(main_mod.app, raise_server_exceptions=False)
        verify_sessions_list(prod_client)
        verify_write_cycle(prod_client)
        verify_cross_user_403(prod_client)
        verify_soft_delete_404(prod_client)
        prod_client.close()

        # ── dev 客户端 ──
        dev_db = tmp / "dev.db"
        mp.setattr(db_mod, "get_connection", _connect(dev_db))
        mp.setattr(has_mod, "get_connection", _connect(dev_db))
        db_mod.init_db()
        mp.delenv("APP_ENV")
        mp.delenv("PRODUCTION")
        mp.setenv("JWT_SECRET", TEST_JWT_SECRET)
        mp.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
        _reload_stack()
        import api.main as main_mod2
        dev_client = TestClient(main_mod2.app, raise_server_exceptions=False)
        verify_dev_regression(dev_client)
        dev_client.close()
    finally:
        mp.teardown()
        _reload_stack()

    print("\n" + "=" * 60)
    print(f"QA M-5 独立验证汇总: PASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("失败项:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("全部通过 [OK]")


if __name__ == "__main__":
    main()
