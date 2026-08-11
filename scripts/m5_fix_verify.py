"""M-5 修复实证：QA Round 1 P0 —— 存量库 init_db 迁移崩溃。

原 bug：executescript 中 `CREATE INDEX idx_threads_owner_archived_updated
ON threads(owner_id, archived, ...)` 在 `_ensure_threads_columns` 补列之前
执行；存量库（threads 无 archived 列）启动即 `sqlite3.OperationalError:
no such column: archived`。

修复：从 executescript 移除该索引，仅由 `_ensure_threads_columns` 负责
（它先 PRAGMA 补列再建索引）。

本脚本：构造 M-4 旧表 → init_db() 不崩溃 → 补列 + 索引 → 幂等 3 次。
"""
import importlib, os, sqlite3, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = Path(tempfile.mkdtemp(prefix="m5-fix-verify-"))
legacy_db = tmp / "legacy_m4.db"

# ── 构造 M-4 时代旧库（threads 无 archived/deleted_at）──
conn = sqlite3.connect(str(legacy_db))
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
    conn.execute(
        "INSERT INTO threads (thread_id, owner_id, title) VALUES ('t-old', 'user-a', '存量会话')"
    )
    conn.commit()
finally:
    conn.close()

# ── 切库 + 执行 init_db（回归点：修复前在此崩溃）──
import mcp_tools.db.database as db_mod

def _connect(db_path: Path):
    def patched() -> sqlite3.Connection:
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c
    return patched

db_mod.get_connection = _connect(legacy_db)

try:
    db_mod.init_db()
    print("[PASS] init_db() 对 M-4 存量库不再崩溃")
except sqlite3.OperationalError as e:
    print(f"[FAIL] init_db() 仍崩溃: {e}")
    sys.exit(1)

# ── 补列 + 索引断言 ──
c = sqlite3.connect(str(legacy_db))
try:
    cols = {r[1] for r in c.execute("PRAGMA table_info(threads)").fetchall()}
    indexes = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    row = c.execute(
        "SELECT thread_id, archived, deleted_at FROM threads WHERE thread_id='t-old'"
    ).fetchone()
finally:
    c.close()

assert "archived" in cols, f"缺 archived 列: {sorted(cols)}"
assert "deleted_at" in cols, f"缺 deleted_at 列: {sorted(cols)}"
assert "idx_threads_owner_archived_updated" in indexes, f"缺归档索引: {sorted(indexes)}"
assert row[1] == 0 and row[2] is None, f"存量行 archived/deleted_at 异常: {row}"
print("[PASS] init_db 后自动补 archived/deleted_at 列 + 归档索引，存量行保留")

# ── 幂等 3 次（新库/存量库均不报错）──
for i in range(3):
    db_mod.init_db()
print("[PASS] init_db 幂等 3 次无异常")

# ── 新库（无旧表）init_db 幂等 ──
fresh_db = tmp / "fresh.db"
db_mod.get_connection = _connect(fresh_db)
for i in range(3):
    db_mod.init_db()
c = sqlite3.connect(str(fresh_db))
try:
    indexes = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
finally:
    c.close()
assert "idx_threads_owner_archived_updated" in indexes
print("[PASS] 新库 init_db 幂等 3 次 + 归档索引存在")

print("\n=== M-5 存量库迁移修复实证全部通过 ===")
