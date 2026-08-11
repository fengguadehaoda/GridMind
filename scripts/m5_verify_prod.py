"""M-5 生产模式实证：GET /sessions 本人/管理员全量 + 越权 403 + 软删 /thread 404。"""
import importlib, os, sqlite3, sys, tempfile
from pathlib import Path

# 确保项目根目录在 sys.path（scripts/ 子目录运行时）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = Path(tempfile.mkdtemp(prefix="m5-verify-"))
db = tmp / "verify.db"

def _connect():
    def patched():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn
    return patched

import mcp_tools.db.database as db_mod
import api.services.hitl_audit_service as has_mod
db_mod.get_connection = _connect()
has_mod.get_connection = _connect()
db_mod.init_db()

os.environ["APP_ENV"] = "production"
os.environ["JWT_SECRET"] = "m5-verify-secret-0123456789abcdef"
os.environ["ADMIN_TOKEN"] = "m5-verify-admin-token"

import api.config as c; importlib.reload(c)
import api.services.rbac as r; importlib.reload(r)
import api.services.thread_store as t; importlib.reload(t)
import api.services.auth as a; importlib.reload(a)
import api.main as m; importlib.reload(m)

from fastapi.testclient import TestClient
from api.services.thread_store import ThreadStore, delete_thread
from api.services.auth import issue_test_token

client = TestClient(m.app, raise_server_exceptions=False)

def tok(uid, role="dispatcher"):
    return {"Authorization": f"Bearer {issue_test_token(uid, extra_claims={'role': role})}"}

# seed
ThreadStore().create_thread("t-zhang", "zhangsan", title="主变异常")
ThreadStore().create_thread("t-li", "lisi", title="母线过载")

# 1) dispatcher only own
r = client.get("/sessions", headers=tok("zhangsan"))
ids = [s["thread_id"] for s in r.json()["sessions"]]
assert ids == ["t-zhang"], f"dispatcher 应只返本人: {ids}"
print("[PASS] 生产 dispatcher GET /sessions 只返本人活跃:", ids)

# 2) admin full
r = client.get("/sessions", headers=tok("admin-x", role="admin"))
ids = {s["thread_id"] for s in r.json()["sessions"]}
assert ids == {"t-zhang", "t-li"}, f"admin 应全量: {ids}"
print("[PASS] 生产 admin GET /sessions 跨用户全量:", sorted(ids))

# 3) cross-user write 403
for method, path, body in [
    ("PATCH", "/sessions/t-zhang", {"title": "越权"}),
    ("POST", "/sessions/t-zhang/archive", None),
    ("POST", "/sessions/t-zhang/restore", None),
    ("DELETE", "/sessions/t-zhang", None),
]:
    if method == "PATCH":
        resp = client.patch(path, json=body, headers=tok("lisi"))
    elif method == "DELETE":
        resp = client.delete(path, headers=tok("lisi"))
    else:
        resp = client.post(path, json=body, headers=tok("lisi"))
    assert resp.status_code == 403, f"{method} {path} 应 403, 实际 {resp.status_code}"
print("[PASS] 生产 越权 rename/archive/restore/delete → 403（4 端点）")

# 4) soft-delete → /thread 404 (owner + admin)
ThreadStore().create_thread("t-del", "zhangsan")
delete_thread("t-del")
r1 = client.get("/thread/t-del", headers=tok("zhangsan"))
assert r1.status_code == 404, f"owner 软删 /thread 应 404, 实际 {r1.status_code}"
r2 = client.get("/thread/t-del", headers=tok("admin-x", role="admin"))
assert r2.status_code == 404, f"admin 软删 /thread 应 404, 实际 {r2.status_code}"
print("[PASS] 生产 软删后 /thread/{id} → 404（owner + admin 一致）")

# 5) rename/archive/restore happy path
ThreadStore().create_thread("t-own", "zhangsan")
resp = client.patch("/sessions/t-own", json={"title": "新标题"}, headers=tok("zhangsan"))
assert resp.status_code == 200 and resp.json()["title"] == "新标题"
resp = client.post("/sessions/t-own/archive", headers=tok("zhangsan"))
assert resp.json()["archived"] == 1
resp = client.post("/sessions/t-own/restore", headers=tok("zhangsan"))
assert resp.json()["archived"] == 0
resp = client.delete("/sessions/t-own", headers=tok("zhangsan"))
assert resp.json()["archived"] == 2
print("[PASS] 生产 rename/archive/restore/delete 正向闭环")

os.environ.pop("APP_ENV", None)
os.environ.pop("PRODUCTION", None)
print("\n=== M-5 后端实证全部通过 ===")
