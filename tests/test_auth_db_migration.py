"""V1.8.0 认证（T01）· 三表幂等迁移测试（users / refresh_tokens / auth_audit_log）。

**验收**（架构 auth-architecture Task 1 + PRD §4.1-4.3）：
1. ``init_db()`` 后三表 + 索引存在，字段与 DDL 逐列一致；
2. 重复 ``init_db()`` 幂等（零副作用，不抛错）；
3. 既有表（threads / telemetry / hitl_audit_log 等）零影响；
4. 主键/外键/默认值语义正确（username UNIQUE、refresh_tokens.token_hash UNIQUE、
   refresh_tokens.user_id REFERENCES users(id)）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import mcp_tools.db.database as db_mod

#: users 表预期字段（与架构 §3.1 DDL 逐列对齐）
USERS_COLUMNS = {
    "id", "username", "email", "password_hash", "role", "disabled",
    "must_change_password", "password_changed_at", "password_history",
    "failed_attempts", "locked_until", "last_login_at", "created_at", "updated_at",
}

#: refresh_tokens 表预期字段
REFRESH_COLUMNS = {
    "id", "user_id", "token_hash", "expires_at", "created_at",
    "revoked_at", "replaced_by", "user_agent", "ip_address",
}

#: auth_audit_log 表预期字段
AUDIT_COLUMNS = {
    "id", "event_type", "user_id", "username",
    "ip_address", "user_agent", "detail", "created_at",
}


def _connect(tmp_db: Path):
    """生成指向 tmp DB 的 get_connection 替代函数（沿用既有测试模式）。"""

    def patched() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return patched


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 db_mod.get_connection 切到 tmp 库并返回路径。"""
    db = tmp_path / "auth_migration.db"
    monkeypatch.setattr(db_mod, "get_connection", _connect(db))
    return db


def _table_columns(db: Path, table: str) -> set[str]:
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _index_names(db: Path) -> set[str]:
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    return {r[0] for r in rows}


def test_init_db_creates_auth_tables_with_expected_columns(tmp_db: Path) -> None:
    """T01 AC3：三表建表成功，字段与 DDL 逐列一致。"""
    db_mod.init_db()

    assert _table_columns(tmp_db, "users") == USERS_COLUMNS
    assert _table_columns(tmp_db, "refresh_tokens") == REFRESH_COLUMNS
    assert _table_columns(tmp_db, "auth_audit_log") == AUDIT_COLUMNS


def test_init_db_idempotent(tmp_db: Path) -> None:
    """T01 AC3：重复 init_db() 零副作用、不抛错（幂等迁移）。"""
    db_mod.init_db()
    db_mod.init_db()  # 第二次（存量库升级路径）

    assert _table_columns(tmp_db, "users") == USERS_COLUMNS
    assert _table_columns(tmp_db, "refresh_tokens") == REFRESH_COLUMNS
    assert _table_columns(tmp_db, "auth_audit_log") == AUDIT_COLUMNS


def test_auth_indexes_created(tmp_db: Path) -> None:
    """T01 AC3：三表索引创建成功（username/role/user/hash/audit 时间）。"""
    db_mod.init_db()
    indexes = _index_names(tmp_db)

    assert {"idx_users_username", "idx_users_role"} <= indexes
    assert {"idx_refresh_user", "idx_refresh_hash"} <= indexes
    assert {"idx_auth_audit_user", "idx_auth_audit_time"} <= indexes


def test_existing_tables_unaffected(tmp_db: Path) -> None:
    """T01 AC3：既有表（threads/telemetry/hitl_audit_log 等）零影响。"""
    db_mod.init_db()

    # threads 表（M-5 会话管理）+ archived/deleted_at 列迁移仍在
    assert _table_columns(tmp_db, "threads") >= {
        "thread_id", "owner_id", "title", "archived", "deleted_at",
    }
    # 既有业务表仍存在
    for table in (
        "devices", "telemetry", "inspections", "safety_rules",
        "knowledge_chunks", "graph_entities", "hitl_audit_log",
        "diagnosis_fusion_log", "kb_meta", "sync_log",
    ):
        with sqlite3.connect(str(tmp_db)) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        assert row is not None, f"既有表 {table} 应存在"


def test_auth_table_constraints(tmp_db: Path) -> None:
    """T01 语义：username UNIQUE / token_hash UNIQUE / FK 引用 users(id)。"""
    db_mod.init_db()

    # 1) users.username UNIQUE（独立连接事务，rollback 不影响其它断言）
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
            "VALUES ('u1', 'alice', 'x', 'dispatcher', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
                "VALUES ('u2', 'alice', 'x', 'dispatcher', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )

    # 2) refresh_tokens.token_hash UNIQUE
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) "
            "VALUES ('u1', 'hash1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) "
                "VALUES ('u1', 'hash1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )

    # 3) refresh_tokens.user_id 外键：不存在 user → IntegrityError
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, expires_at, created_at) "
                "VALUES ('no-such-user', 'hash2', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
