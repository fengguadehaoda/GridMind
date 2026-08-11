"""V1.7.0 多用户地基 · T1 数据层测试（threads 表 + ThreadStore + backfill）。

**范围**（架构 multiuser-architecture Task 1 验收）：
1. ``init_db()`` 幂等建表 + ``idx_threads_owner_updated`` 索引；
2. ``ThreadStore`` 全方法：create / ensure（懒登记）/ get_owner / set_model /
   get_model / list_by_owner / count / thread_exists；
3. ``get_model_for_thread(NULL 行)`` 回退 ``get_current_model()``；
   ``set_model_for_thread`` 对未知模型抛 ValueError；
4. ``scripts/backfill_threads.py`` 可重复执行（INSERT OR IGNORE），
   存量 checkpoint thread_id 全部登记（owner=system）；
5. ``settings.threads_strict_mode`` 默认 False 且可配置。

**隔离**：所有用例通过 ``monkeypatch`` 把数据库路径切到 ``tmp_path``，
不污染全局 ``data/gridmind.db``（沿用 test_hitl_table_migration 手法）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from api.config import settings


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把主库路径切到 tmp_path，返回临时 DB 路径。

    直接 patch ``mcp_tools.db.database.get_connection``（与既有测试一致），
    并复位 ``settings.database_path``（frozen 属性用 setattr 兜底）。
    """
    import mcp_tools.db.database as db_module

    tmp_db = tmp_path / "threads_test.db"
    original = db_module.get_connection

    def patched() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(db_module, "get_connection", patched)
    try:
        monkeypatch.setattr(settings, "database_path", str(tmp_db))
    except Exception:  # noqa: BLE001 — frozen 属性可能拒绝 setattr，忽略即可
        pass
    return tmp_db


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """切库 + init_db 后返回临时 DB 路径。"""
    db = _patch_db_path(monkeypatch, tmp_path)
    import mcp_tools.db.database as db_module

    db_module.init_db()
    return db


# ═══════════════════════════════════════════════════════
# 1. init_db 幂等 + 索引
# ═══════════════════════════════════════════════════════


def _table_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _index_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_init_db_creates_threads_table_and_index(tmp_db: Path) -> None:
    """init_db 后 threads 表 + idx_threads_owner_updated 索引存在。"""
    tables = _table_names(tmp_db)
    assert "threads" in tables, f"threads 表缺失，实际表: {sorted(tables)}"
    indexes = _index_names(tmp_db)
    assert "idx_threads_owner_updated" in indexes, (
        f"idx_threads_owner_updated 索引缺失，实际索引: {sorted(indexes)}"
    )
    # 校验列结构（PRD §五 DDL）
    conn = sqlite3.connect(str(tmp_db))
    try:
        cols = {
            r[1]: r[2]
            for r in conn.execute("PRAGMA table_info(threads)").fetchall()
        }
    finally:
        conn.close()
    assert cols["thread_id"] == "TEXT"
    assert cols["owner_id"] == "TEXT"
    assert cols["model_id"] == "TEXT"
    assert cols["title"] == "TEXT"
    print("[PASS] threads 表 + 索引创建成功")


def test_init_db_repeated_is_idempotent(tmp_db: Path) -> None:
    """init_db 连续 3 次幂等：不报错、不重复建表。"""
    import mcp_tools.db.database as db_module

    for _ in range(3):
        db_module.init_db()
    tables = _table_names(tmp_db)
    assert "threads" in tables
    # 索引仍存在（不重复）
    indexes = _index_names(tmp_db)
    assert "idx_threads_owner_updated" in indexes
    print("[PASS] init_db 连续 3 次幂等")


def test_index_on_owner_updated(tmp_db: Path) -> None:
    """索引定义在 (owner_id, updated_at DESC) 上（PRD §五）。"""
    conn = sqlite3.connect(str(tmp_db))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_threads_owner_updated'"
        ).fetchone()
    finally:
        conn.close()
    sql = (row[0] if row else "").lower()
    assert "owner_id" in sql and "updated_at" in sql
    print("[PASS] 索引定义含 owner_id + updated_at")


# ═══════════════════════════════════════════════════════
# 2. ThreadStore CRUD
# ═══════════════════════════════════════════════════════


def test_create_and_get_thread(tmp_db: Path) -> None:
    """create_thread → get_thread / get_owner 读回一致。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-1", "user-a", title="调度 A")
    row = store.get_thread("t-1")
    assert row is not None
    assert row["owner_id"] == "user-a"
    assert row["title"] == "调度 A"
    assert row["model_id"] is None
    assert store.get_owner("t-1") == "user-a"
    assert store.get_thread("t-missing") is None
    print("[PASS] create/get 一致")


def test_create_thread_idempotent_keep_first_owner(tmp_db: Path) -> None:
    """重复 create 同一 thread：INSERT OR IGNORE，保留首个 owner。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-1", "user-a")
    store.create_thread("t-1", "user-b")  # 不应覆盖
    assert store.get_owner("t-1") == "user-a"
    assert store.count() == 1
    print("[PASS] create 幂等保留首 owner")


def test_ensure_thread_lazy_registers(tmp_db: Path) -> None:
    """ensure_thread：无行则懒登记，有行则不覆盖 owner。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    row = store.ensure_thread("t-legacy", "user-b")
    assert row["owner_id"] == "user-b"
    # 再次 ensure 由别人访问 → 不覆盖
    row2 = store.ensure_thread("t-legacy", "user-c")
    assert row2["owner_id"] == "user-b"
    print("[PASS] ensure_thread 懒登记 + 不覆盖")


def test_set_and_get_model(tmp_db: Path) -> None:
    """set_model UPSERT：无行时也写入（owner 兜底 system），可重复更新。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.set_model("t-1", "deepseek-chat")
    assert store.get_model("t-1") == "deepseek-chat"
    store.set_model("t-1", "qwen-plus")
    assert store.get_model("t-1") == "qwen-plus"
    # 未设置 → None
    store.create_thread("t-2", "user-a")
    assert store.get_model("t-2") is None
    print("[PASS] set_model UPSERT + get_model NULL 语义")


def test_list_by_owner_and_count(tmp_db: Path) -> None:
    """list_by_owner 只返回该 owner 的会话，count 为总数。"""
    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    store.create_thread("t-a1", "user-a")
    store.create_thread("t-a2", "user-a")
    store.create_thread("t-b1", "user-b")

    ids_a = {r["thread_id"] for r in store.list_by_owner("user-a")}
    assert ids_a == {"t-a1", "t-a2"}
    assert store.list_thread_ids_by_owner("user-a") == sorted(ids_a)
    assert store.count() == 3
    print("[PASS] list_by_owner 过滤 + count")


# ═══════════════════════════════════════════════════════
# 3. 统一模型读写接口
# ═══════════════════════════════════════════════════════


def test_get_model_for_thread_falls_back_to_global(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NULL 行回退 ``get_current_model()``；有值则返回会话模型。"""
    from api.services import thread_store as ts

    # 重置进程级全局模型为默认
    import core.llm_client as llm

    monkeypatch.setattr(llm, "_current_model", None)
    default_model = llm.get_default_model()

    store = ts.ThreadStore()
    store.create_thread("t-none", "user-a")          # model_id NULL
    store.set_model("t-set", "deepseek-chat")        # 显式设置

    assert ts.get_model_for_thread("t-none") == default_model
    assert ts.get_model_for_thread("t-set") == "deepseek-chat"
    # 未登记 thread → 也回退全局
    assert ts.get_model_for_thread("t-unknown") == default_model
    print("[PASS] get_model_for_thread NULL 回退全局")


def test_set_model_for_thread_validates_unknown_model(
    tmp_db: Path,
) -> None:
    """set_model_for_thread 对未知模型抛 ValueError。"""
    from api.services.thread_store import set_model_for_thread

    with pytest.raises(ValueError):
        set_model_for_thread("t-1", "not-a-real-model")
    print("[PASS] set_model_for_thread 校验未知模型")


def test_resolve_model(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_model：thread_id ? 会话模型 : 全局模型。"""
    from api.services import thread_store as ts
    import core.llm_client as llm

    monkeypatch.setattr(llm, "_current_model", None)
    default_model = llm.get_default_model()
    ts.ThreadStore().set_model("t-set", "qwen-turbo")
    assert ts.resolve_model("t-set") == "qwen-turbo"
    assert ts.resolve_model(None) == default_model
    assert ts.resolve_model("t-none") == default_model
    print("[PASS] resolve_model 双路径")


# ═══════════════════════════════════════════════════════
# 4. backfill 脚本幂等
# ═══════════════════════════════════════════════════════


@pytest.fixture
def checkpoint_db(tmp_path: Path) -> Path:
    """构造一个含 3 个 thread 的 checkpoint 库（模拟 v1.6 存量）。"""
    db = tmp_path / "checkpoints.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            """
        )
        rows = [
            ("t-old-1", "", "c1"),
            ("t-old-1", "", "c2"),   # 同 thread 多 checkpoint → 去重
            ("t-old-2", "", "c3"),
            ("t-old-3", "", "c4"),
        ]
        for tid, ns, cid in rows:
            conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id) "
                "VALUES (?, ?, ?)",
                (tid, ns, cid),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def test_backfill_registers_legacy_threads(tmp_db: Path, checkpoint_db: Path) -> None:
    """backfill 把 3 个去重 thread_id 登记（owner=system，title=存量会话）。"""
    from scripts.backfill_threads import backfill

    result = backfill(tmp_db, checkpoint_db)
    assert result["total"] == 3
    assert result["registered"] == 3
    assert result["ignored"] == 0

    from api.services.thread_store import ThreadStore

    store = ThreadStore()
    assert store.count() == 3
    for tid in ("t-old-1", "t-old-2", "t-old-3"):
        row = store.get_thread(tid)
        assert row is not None
        assert row["owner_id"] == "system"
        assert row["title"] == "存量会话"
        assert row["model_id"] is None
    print("[PASS] backfill 登记存量线程 owner=system")


def test_backfill_is_idempotent(tmp_db: Path, checkpoint_db: Path) -> None:
    """backfill 重复执行幂等：第二次 registered=0 ignored=3。"""
    from scripts.backfill_threads import backfill

    r1 = backfill(tmp_db, checkpoint_db)
    r2 = backfill(tmp_db, checkpoint_db)
    assert r1["registered"] == 3
    assert r2["registered"] == 0
    assert r2["ignored"] == 3
    assert r2["total"] == 3

    from api.services.thread_store import ThreadStore

    assert ThreadStore().count() == 3
    print("[PASS] backfill 幂等（INSERT OR IGNORE）")


def test_backfill_missing_checkpoint_db(tmp_db: Path, tmp_path: Path) -> None:
    """checkpoint 库不存在 → 不报错，total=0。"""
    from scripts.backfill_threads import backfill

    result = backfill(tmp_db, tmp_path / "no-such.db")
    assert result == {"registered": 0, "ignored": 0, "total": 0}
    print("[PASS] backfill 空库安全")


def test_backfill_preserves_lazy_registered_owner(
    tmp_db: Path, checkpoint_db: Path,
) -> None:
    """已被懒登记（owner=user-a）的行不被 backfill 覆盖为 system。"""
    from scripts.backfill_threads import backfill
    from api.services.thread_store import ThreadStore

    # 用户先访问过 t-old-1 → 懒登记为 user-a
    ThreadStore().ensure_thread("t-old-1", "user-a")
    result = backfill(tmp_db, checkpoint_db)
    # t-old-1 已存在被忽略；t-old-2/t-old-3 新登记
    assert result["registered"] == 2
    assert result["ignored"] == 1
    assert ThreadStore().get_owner("t-old-1") == "user-a"
    assert ThreadStore().get_owner("t-old-2") == "system"
    print("[PASS] backfill 不覆盖懒登记 owner")


# ═══════════════════════════════════════════════════════
# 5. threads_strict_mode 配置
# ═══════════════════════════════════════════════════════


def test_threads_strict_mode_default_false() -> None:
    """settings.threads_strict_mode 默认 False。"""
    assert settings.threads_strict_mode is False
    print("[PASS] threads_strict_mode 默认 False")


def test_threads_strict_mode_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量 THREADS_STRICT_MODE=true 可配置为 True。"""
    import importlib

    monkeypatch.setenv("THREADS_STRICT_MODE", "true")
    import api.config as config_mod

    importlib.reload(config_mod)
    try:
        assert config_mod.settings.threads_strict_mode is True
    finally:
        # 复位，避免泄漏到其它用例
        monkeypatch.delenv("THREADS_STRICT_MODE", raising=False)
        importlib.reload(config_mod)
    assert config_mod.settings.threads_strict_mode is False
    print("[PASS] threads_strict_mode 可通过 env 配置")
