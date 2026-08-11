"""Round 2 · M-4 存量库独立复现（QA 严过关 · 自写，不依赖工程师脚本）。

场景：构造 M-4 时代 threads 表（无 archived/deleted_at 列）+ 存量数据，
直接调 `mcp_tools.db.database.init_db()` 本体 —— 验证修复后：
1. 不再崩溃（no such column: archived 已消除）；
2. 自动补 archived/deleted_at 列 + idx_threads_owner_archived_updated 索引；
3. 存量行保留且 archived 默认 0；
4. 连续 3 次 init_db 幂等。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qa_m5_r2_legacy_"))
    db = tmp / "legacy_m4.db"

    # ── 构造 M-4 旧表（无 archived/deleted_at）+ 存量数据 ──
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("""
            CREATE TABLE threads (
                thread_id   TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '新会话',
                model_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO threads (thread_id, owner_id, title, model_id)
            VALUES ('t-legacy-1', 'zhangsan', '主变异常', 'qwen-plus'),
                   ('t-legacy-2', 'lisi', '母线过载', NULL)
        """)
        conn.commit()
    finally:
        conn.close()

    # 前置断言：确实是存量库（无 archived 列）
    conn = sqlite3.connect(str(db))
    pre_cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
    conn.close()
    check("前置：M-4 旧表确实无 archived 列", "archived" not in pre_cols, f"cols={pre_cols}")
    check("前置：M-4 旧表确实无 deleted_at 列", "deleted_at" not in pre_cols, f"cols={pre_cols}")

    # ── 切库 + 直接调 init_db() 本体（与生产启动同路径）──
    import mcp_tools.db.database as db_mod

    def _connect():
        def patched() -> sqlite3.Connection:
            c = sqlite3.connect(str(db))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            return c
        return patched

    orig = db_mod.get_connection
    db_mod.get_connection = _connect()
    try:
        db_mod.init_db()
        ok = True
        err = ""
    except Exception as e:  # noqa: BLE001
        ok = False
        err = f"{type(e).__name__}: {e}"
    finally:
        db_mod.get_connection = orig
    check("修复后：存量库 init_db() 本体不再崩溃", ok, err)

    # ── 补列 + 索引 + 存量行保留 ──
    conn = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
        idxs = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        rows = conn.execute(
            "SELECT thread_id, title, model_id, archived, deleted_at FROM threads "
            "ORDER BY thread_id"
        ).fetchall()
    finally:
        conn.close()

    check("补 archived 列", "archived" in cols, f"cols={sorted(cols)}")
    check("补 deleted_at 列", "deleted_at" in cols, f"cols={sorted(cols)}")
    check("建 idx_threads_owner_archived_updated 索引",
          "idx_threads_owner_archived_updated" in idxs, f"idxs={sorted(idxs)}")
    check("存量行保留（2 行）", len(rows) == 2, f"rows={[r[0] for r in rows]}")
    check("存量行数据未丢（title/model_id 保留）",
          rows[0][1] == "主变异常" and rows[0][2] == "qwen-plus", str(rows))
    check("存量行 archived 默认 0", all(r[3] == 0 for r in rows), f"archived={[r[3] for r in rows]}")
    check("存量行 deleted_at 为 NULL", all(r[4] is None for r in rows), f"deleted_at={[r[4] for r in rows]}")

    # ── 幂等 3 次 ──
    db_mod.get_connection = _connect()
    try:
        for i in range(3):
            db_mod.init_db()
        idem_ok = True
        idem_err = ""
    except Exception as e:  # noqa: BLE001
        idem_ok = False
        idem_err = f"{type(e).__name__}: {e}"
    finally:
        db_mod.get_connection = orig
    check("连续 3 次 init_db 幂等不报错", idem_ok, idem_err)

    conn = sqlite3.connect(str(db))
    try:
        cols2 = {r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()}
        idxs2 = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        conn.close()
    check("幂等后列不重复", len(cols2 & {"archived", "deleted_at"}) == 2, f"cols={sorted(cols2)}")
    check("幂等后索引不重复", "idx_threads_owner_archived_updated" in idxs2, f"idxs={sorted(idxs2)}")

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n=== M-4 存量库独立复现: PASS={passed} FAIL={failed} ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
