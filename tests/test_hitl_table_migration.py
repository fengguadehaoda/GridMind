"""V1.5.1 LangGraph 后端改造 · T05 · HITL 表迁移幂等性测试（架构 §2.4.2 + §10.3）。

**T05 范围**：验证 ``mcp_tools.db.database._ensure_hitl_columns`` 把 v1.5.0
的 16 列旧库**幂等**升级到 v1.5.1 的 19 列（含 ``risk_level`` / ``pause_count`` /
``edit_count`` 3 列 + 3 个新索引）。

**测试策略**（**5 个场景**，≥ 3 PASS 必达）：

- **构造隔离**：每个测试用 ``tmp_path`` 临时建 SQLite 文件（**不**污染全局
  ``data/gridmind.db``，避免与其他测试冲突）
- **直调内部函数**：测试通过 ``mcp_tools.db.database._ensure_hitl_columns(conn)``
  直接验证幂等逻辑，比启动服务更纯粹（也无需 MCP/LLM）
- **PRAGMA 校验**：用 ``PRAGMA table_info(hitl_audit_log)`` 读实际 schema；
  用 ``PRAGMA index_list`` 读索引列表
- **真实启动校验**：1 个测试用全局 ``init_db()`` 连跑 3 次（模拟"重复启动"），
  验证不会 ALTER 错或锁住

**关键验收点**（架构 §10.3）：

- ✅ 旧 16 列升级后变 19 列
- ✅ ``risk_level`` 默认 ``'normal'``、``pause_count`` 默认 0、``edit_count`` 默认 0
- ✅ ``idx_hitl_risk_level`` / ``idx_hitl_pause_count`` / ``idx_hitl_edit_count``
  3 个新索引存在
- ✅ 16 列库 INSERT 的旧数据保留
- ✅ 升级操作可重复执行（幂等），不报 "duplicate column" 错误

**运行**::

    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_hitl_table_migration.py -v
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


#: v1.5.0 基线 16 列 DDL（架构 §2.4.1 + 主理人决策 #5）
#: 不含 V1.5.1 新增的 risk_level / pause_count / edit_count 3 列
OLD_16_COLUMNS_DDL: str = """
CREATE TABLE hitl_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    interrupt_node TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    user_name TEXT,
    user_role TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject','edit_approve')),
    original_args TEXT NOT NULL,
    edited_args TEXT,
    edit_reason TEXT,
    safety_recheck_result TEXT,
    reason TEXT,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

#: v1.5.1 完整 19 列 DDL（_ensure_hitl_columns 升级后的目标状态）
NEW_19_COLUMNS_DDL: str = """
CREATE TABLE hitl_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    interrupt_node TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    user_name TEXT,
    user_role TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject','edit_approve')),
    original_args TEXT NOT NULL,
    edited_args TEXT,
    edit_reason TEXT,
    safety_recheck_result TEXT,
    reason TEXT,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    risk_level TEXT NOT NULL DEFAULT 'normal',
    pause_count INTEGER NOT NULL DEFAULT 0,
    edit_count INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
def old_db(tmp_path: Path) -> Path:
    """构造一个 16 列旧库（v1.5.0 升级前的状态）+ 1 条测试数据。"""
    db_path = tmp_path / "old_hitl.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_16_COLUMNS_DDL)
    # 插入 1 条老数据（不带新增 3 列）
    conn.execute(
        """
        INSERT INTO hitl_audit_log
        (thread_id, interrupt_node, tool_name, decision, original_args)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "t-old-001", "supervisor", "mock_tool",
            "approve", '{"k": "v"}',
        ),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def new_db(tmp_path: Path) -> Path:
    """构造一个 19 列新库（v1.5.1 完整状态），用于测幂等性。"""
    db_path = tmp_path / "new_hitl.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(NEW_19_COLUMNS_DDL)
    conn.commit()
    conn.close()
    return db_path


# ═══════════════════════════════════════════════════════
# 工具函数：拿到 column 列表
# ═══════════════════════════════════════════════════════


def _get_columns(db_path: Path, table: str = "hitl_audit_log") -> list[str]:
    """返回 ``PRAGMA table_info`` 中的 column 列表（按 sqlite master 序）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
        return [row[1] for row in rows]  # row[1] = name
    finally:
        conn.close()


def _has_index(
    db_path: Path, table: str, index_name: str
) -> bool:
    """检查 ``PRAGMA index_list(table)`` 中是否存在 ``index_name``。"""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            f"PRAGMA index_list({table})"
        ).fetchall()
        # row[1] = name
        return any(row[1] == index_name for row in rows)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# 1. 旧 16 列库升级到 19 列（核心场景）
# ═══════════════════════════════════════════════════════


def test_old_16_column_db_upgrades_to_19(old_db: Path) -> None:
    """旧 16 列库跑 ``_ensure_hitl_columns`` 后变 19 列，旧数据保留 + 默认值正确。

    这是 T05 迁移幂等的**核心场景**（架构 §10.3 通过门槛）：
    - 旧库状态 → ALTER → 19 列
    - 旧数据自动填默认值
    - 3 个新索引建立
    """
    from mcp_tools.db.database import _ensure_hitl_columns

    # 升级前：16 列
    cols_before = _get_columns(old_db)
    assert len(cols_before) == 16, f"升级前应是 16 列，实际 {len(cols_before)}"
    assert "risk_level" not in cols_before
    assert "pause_count" not in cols_before
    assert "edit_count" not in cols_before
    print(f"[OK] 旧库 16 列确认（无 risk_level/pause_count/edit_count）")

    # 升级（直接调内部函数）
    conn = sqlite3.connect(str(old_db))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_hitl_columns(conn)
        conn.commit()
    finally:
        conn.close()

    # 升级后：19 列
    cols_after = _get_columns(old_db)
    assert len(cols_after) == 19, f"升级后应是 19 列，实际 {len(cols_after)}: {cols_after}"
    assert "risk_level" in cols_after
    assert "pause_count" in cols_after
    assert "edit_count" in cols_after
    print(f"[PASS] 旧库升级为 19 列：{cols_after[-3:]}")

    # 旧数据保留 + 默认值正确
    conn = sqlite3.connect(str(old_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM hitl_audit_log WHERE thread_id = 't-old-001'"
        ).fetchone()
        assert row is not None, "旧数据丢失！"
        assert row["risk_level"] == "normal", (
            f"risk_level 默认值应为 'normal', 实际 '{row['risk_level']}'"
        )
        assert row["pause_count"] == 0, f"pause_count 默认值应为 0, 实际 {row['pause_count']}"
        assert row["edit_count"] == 0, f"edit_count 默认值应为 0, 实际 {row['edit_count']}"
        print(
            f"[PASS] 旧数据保留 + 默认值正确: "
            f"risk_level='{row['risk_level']}', "
            f"pause_count={row['pause_count']}, "
            f"edit_count={row['edit_count']}"
        )
    finally:
        conn.close()

    # 3 个新索引都已创建
    assert _has_index(old_db, "hitl_audit_log", "idx_hitl_risk_level"), (
        "缺少 idx_hitl_risk_level 索引"
    )
    assert _has_index(old_db, "hitl_audit_log", "idx_hitl_pause_count"), (
        "缺少 idx_hitl_pause_count 索引"
    )
    assert _has_index(old_db, "hitl_audit_log", "idx_hitl_edit_count"), (
        "缺少 idx_hitl_edit_count 索引"
    )
    print("[PASS] 3 个新索引已建立（idx_hitl_risk_level/pause_count/edit_count）")


# ═══════════════════════════════════════════════════════
# 2. 19 列库二次跑迁移函数无变更
# ═══════════════════════════════════════════════════════


def test_already_19_column_db_is_idempotent(new_db: Path) -> None:
    """19 列库调 ``_ensure_hitl_columns`` 不报错、不重复加列（幂等性核心）。"""
    from mcp_tools.db.database import _ensure_hitl_columns

    cols_before = _get_columns(new_db)
    assert len(cols_before) == 19

    # 再次跑迁移（幂等测试）
    conn = sqlite3.connect(str(new_db))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_hitl_columns(conn)
        conn.commit()
    finally:
        conn.close()

    cols_after = _get_columns(new_db)
    assert len(cols_after) == 19, (
        f"19 列库跑迁移后应仍 19 列，实际 {len(cols_after)}: {cols_after}"
    )
    # 关键：列没有重复
    assert len(cols_after) == len(set(cols_after)), (
        f"列名重复了！{cols_after}"
    )
    print("[PASS] 19 列库二次迁移无变更（幂等性 ✓）")


# ═══════════════════════════════════════════════════════
# 3. 连续 init_db() 多次不报错
# ═══════════════════════════════════════════════════════


def test_repeated_init_db_calls_are_idempotent(tmp_path: Path, monkeypatch) -> None:
    """连续多次调 ``init_db()``（真实启动路径）不报错，hitl_audit_log 仍 19 列。

    关键：通过 ``monkeypatch.setattr(settings, 'database_path', ...)`` 临时把
    数据库路径切到 ``tmp_path``，避免污染全局 ``data/gridmind.db``。
    """
    from api.config import settings
    from mcp_tools.db import database as db_module

    # 把全局 DB 路径切到 tmp_path（不影响 settings，因 model_config frozen=True；
    # 直接 monkeypatch db_module.get_connection 即可）
    tmp_db = tmp_path / "init_db_test.db"

    original_get_connection = db_module.get_connection

    def patched_get_connection() -> sqlite3.Connection:
        # 用tmp_db替换原 connection
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(db_module, "get_connection", patched_get_connection)

    try:
        # 第 1 次 init_db
        db_module.init_db()
        cols1 = _get_columns(tmp_db)
        assert len(cols1) == 19, f"第 1 次 init_db 后应 19 列，实际 {len(cols1)}"

        # 第 2 次 init_db（幂等）
        db_module.init_db()
        cols2 = _get_columns(tmp_db)
        assert len(cols2) == 19, f"第 2 次 init_db 后应仍 19 列，实际 {len(cols2)}"

        # 第 3 次 init_db（幂等）
        db_module.init_db()
        cols3 = _get_columns(tmp_db)
        assert len(cols3) == 19, f"第 3 次 init_db 后应仍 19 列，实际 {len(cols3)}"

        # 列无重复
        assert len(cols3) == len(set(cols3)), f"列名重复了！{cols3}"

        print(
            f"[PASS] init_db() 连续 3 次均幂等，hitl_audit_log 持续 19 列"
        )
    finally:
        monkeypatch.setattr(db_module, "get_connection", original_get_connection)


# ═══════════════════════════════════════════════════════
# 4. （额外）旧库通过 init_db() 完整启动路径升级
# ═══════════════════════════════════════════════════════


def test_old_db_through_init_db_full_path(old_db: Path, monkeypatch) -> None:
    """旧 16 列库**走完整 ``init_db()`` 路径**升级（生产场景模拟）。

    对比 test 1 直接调 ``_ensure_hitl_columns``，本测试验证完整
    ``init_db() → _ensure_hitl_columns`` 链路（含表创建脚本）。
    """
    from mcp_tools.db import database as db_module

    # 把 get_connection 替换成连到 old_db 的版本
    original_get_connection = db_module.get_connection

    def patched_get_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(str(old_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(db_module, "get_connection", patched_get_connection)

    try:
        # 跑完整 init_db（关键：CREATE TABLE IF NOT EXISTS 不会重复创建；
        # _ensure_hitl_columns 走 ALTER 路径升级）
        db_module.init_db()

        cols = _get_columns(old_db)
        assert len(cols) == 19, f"完整 init_db 后应是 19 列，实际 {len(cols)}"
        assert "risk_level" in cols
        assert "pause_count" in cols
        assert "edit_count" in cols

        # 旧数据保留（关键！）
        conn = sqlite3.connect(str(old_db))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM hitl_audit_log WHERE thread_id = 't-old-001'"
            ).fetchone()
            assert row is not None, "旧数据丢失！"
            assert row["risk_level"] == "normal"
            assert row["pause_count"] == 0
            assert row["edit_count"] == 0
        finally:
            conn.close()
        print(
            "[PASS] 旧库走完整 init_db() 路径升级成功 + 旧数据保留 + 默认值正确"
        )
    finally:
        monkeypatch.setattr(db_module, "get_connection", original_get_connection)


# ═══════════════════════════════════════════════════════
# 5. （额外）现有 19 列库走 init_db 不破坏既有数据
# ═══════════════════════════════════════════════════════


def test_init_db_preserves_existing_data_with_new_columns(new_db: Path, monkeypatch) -> None:
    """19 列库已有 5 条审计数据，``init_db()`` 不应丢失任何一行。

    模拟"重启服务但数据库已有数据"的场景。
    """
    from mcp_tools.db import database as db_module

    # 在 19 列新库上插 5 条审计数据（含新列显式值）
    conn = sqlite3.connect(str(new_db))
    conn.row_factory = sqlite3.Row
    for i in range(5):
        conn.execute(
            """
            INSERT INTO hitl_audit_log
            (thread_id, interrupt_node, tool_name, decision, original_args,
             risk_level, pause_count, edit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"t-existing-{i}", "supervisor", "mock_tool", "approve",
             '{"x": "y"}', "high", i + 1, i * 2),
        )
    conn.commit()
    conn.close()

    # monkeypatch get_connection
    original_get_connection = db_module.get_connection

    def patched_get_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(str(new_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(db_module, "get_connection", patched_get_connection)

    try:
        db_module.init_db()  # 应无破坏
        conn = sqlite3.connect(str(new_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM hitl_audit_log ORDER BY id"
            ).fetchall()
            assert len(rows) == 5, (
                f"init_db 应保留 5 条数据，实际 {len(rows)}"
            )
            # 第 1 行 risk_level='high', pause_count=1, edit_count=0
            assert rows[0]["risk_level"] == "high"
            assert rows[0]["pause_count"] == 1
            assert rows[0]["edit_count"] == 0
            # 第 2 行 risk_level='high', pause_count=2, edit_count=2
            assert rows[1]["pause_count"] == 2
            assert rows[1]["edit_count"] == 2
            print("[PASS] init_db() 不破坏既有 19 列数据（含自定义 risk_level/计数）")
        finally:
            conn.close()
    finally:
        monkeypatch.setattr(db_module, "get_connection", original_get_connection)


# ═══════════════════════════════════════════════════════
# Runner（兼容 ``python tests/test_hitl_table_migration.py``）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口。"""
    import traceback
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    tests: list[tuple[str, Any]] = [
        (
            "test_old_16_column_db_upgrades_to_19",
            lambda: test_old_16_column_db_upgrades_to_19(_make_old_db(tmp)),
        ),
        (
            "test_already_19_column_db_is_idempotent",
            lambda: test_already_19_column_db_is_idempotent(_make_new_db(tmp)),
        ),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"[PASS] {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()

    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)


def _make_old_db(tmp: Path) -> Path:
    db = tmp / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(OLD_16_COLUMNS_DDL)
    conn.execute(
        "INSERT INTO hitl_audit_log "
        "(thread_id, interrupt_node, tool_name, decision, original_args) "
        "VALUES (?, ?, ?, ?, ?)",
        ("t-old-001", "supervisor", "mock_tool", "approve", '{"k":"v"}'),
    )
    conn.commit()
    conn.close()
    return db


def _make_new_db(tmp: Path) -> Path:
    db = tmp / "new.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(NEW_19_COLUMNS_DDL)
    conn.commit()
    conn.close()
    return db


from typing import Any  # noqa: E402  (在文件尾部导入，避免污染 pytest 头部)


if __name__ == "__main__":
    _run_all()
