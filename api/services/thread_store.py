"""V1.7.0 多用户地基 · 会话归属表（threads）数据访问层。

**职责**（架构 multiuser-architecture §3.2 + PRD P0-1/P0-3）：

- ``threads`` 表 CRUD：``create / get / ensure(懒登记) / set_model / get_model /
  list_by_owner / count``；
- 模块级统一模型读写接口（跨文件命名规范 §7.4）：
  ``get_model_for_thread`` / ``set_model_for_thread`` / ``resolve_model``；
- 越权判定 helper ``ensure_thread_owned``（供 /chat、/models/switch 等
  body 内 thread_id 的 handler 内联调用）。

**懒登记语义**（PRD §五 设计说明 4 + 架构 §1.3）：
- 新会话：``POST /chat`` 无 thread_id → 服务端生成 thread_id 并
  ``create_thread(owner=当前用户)``；
- 存量会话：首次成功访问时 ``ensure_thread`` 把该 thread 登记给当前已认证
  用户（Q2 决策：backfill 归 system + 懒登记双保险）；
- 严格模式（``settings.threads_strict_mode=True``）：未知 thread 一律 404。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from loguru import logger

from api.services.rbac import Role

# ═══════════════════════════════════════════════════════
# threads 表 DDL（与 mcp_tools/db/database.py::init_db 保持一致，双保险幂等）
# M-5：新增 archived（0=活跃 1=归档 2=删除软删）+ deleted_at（软删时间戳）
# ═══════════════════════════════════════════════════════

_THREADS_DDL = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '新会话',
    model_id    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    archived    INTEGER NOT NULL DEFAULT 0,
    deleted_at  TEXT
)
"""

_THREADS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_threads_owner_updated
    ON threads(owner_id, updated_at DESC)
"""

#: M-5 侧栏查询索引（owner + 状态 + 时间）
_THREADS_ARCHIVED_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_threads_owner_archived_updated
    ON threads(owner_id, archived, updated_at DESC)
"""

#: 越权响应文案（统一口径，不泄漏内部值）
_MSG_FORBIDDEN = "无权访问该会话"
_MSG_NOT_FOUND = "会话不存在"


def _ensure_threads_columns(conn: sqlite3.Connection) -> None:
    """幂等补齐 threads 表 M-5 archived/deleted_at 列 + 侧栏索引（双保险）。

    与 ``mcp_tools/db/database.py::_ensure_threads_columns`` 语义一致，供
    ``_ensure_threads_schema`` 在未走 ``init_db`` 的调用路径下兜底执行。
    """
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(threads)").fetchall()
    }
    migrations: list[tuple[str, str]] = [
        ("archived", "INTEGER NOT NULL DEFAULT 0"),
        ("deleted_at", "TEXT"),
    ]
    for col, decl in migrations:
        if col in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE threads ADD COLUMN {col} {decl}")
            logger.info("M-5 migration: threads.{} added ({})", col, decl)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                logger.debug("M-5 migration: threads.{} already exists, skip", col)
            else:
                raise
    conn.execute(_THREADS_ARCHIVED_INDEX_DDL)


def _ensure_threads_schema(conn: sqlite3.Connection) -> None:
    """幂等确保 threads 表 + 索引存在（兼容未走 init_db 的调用路径）。"""
    conn.execute(_THREADS_DDL)
    conn.execute(_THREADS_INDEX_DDL)
    # M-5：存量库补列 + 侧栏索引（双保险，幂等）
    _ensure_threads_columns(conn)


class ThreadStore:
    """threads 归属表数据访问服务（无状态，可复用单例/直接实例化）。"""

    # ── 基础查询 ──────────────────────────────────────

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        """按主键查一行；不存在返回 ``None``（含 M-5 archived/deleted_at）。"""
        conn = _open()
        try:
            row = conn.execute(
                "SELECT thread_id, owner_id, title, model_id, created_at, updated_at, "
                "       archived, deleted_at "
                "  FROM threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def get_owner(self, thread_id: str) -> str | None:
        """返回 thread 的 owner_id；不存在返回 ``None``。"""
        row = self.get_thread(thread_id)
        return row["owner_id"] if row else None

    def thread_exists(self, thread_id: str) -> bool:
        """thread 是否已在 threads 表登记。"""
        conn = _open()
        try:
            row = conn.execute(
                "SELECT 1 FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # ── 写操作 ────────────────────────────────────────

    def create_thread(
        self,
        thread_id: str,
        owner_id: str,
        title: str = "新会话",
        model_id: str | None = None,
    ) -> None:
        """登记一个新会话归属行（幂等：已存在则不覆盖）。"""
        conn = _open()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO threads
                    (thread_id, owner_id, title, model_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (thread_id, owner_id, title, model_id),
            )
            conn.commit()
            logger.debug(
                "thread create: thread_id={} owner={} title={} model_id={}",
                thread_id, owner_id, title, model_id,
            )
        finally:
            conn.close()

    def ensure_thread(
        self,
        thread_id: str,
        owner_id: str,
        title: str = "新会话",
    ) -> dict[str, Any]:
        """懒登记：INSERT OR IGNORE 后返回该行（首个访问者接管）。

        Returns:
            登记后的 threads 行 dict（``get_thread`` 同构）。
        """
        self.create_thread(thread_id, owner_id, title=title)
        row = self.get_thread(thread_id)
        if row is None:  # pragma: no cover — create_thread 刚写入不可能 None
            raise RuntimeError(f"thread row missing after ensure: {thread_id}")
        logger.info(
            "thread lazy-registered: thread_id={} owner={}",
            thread_id, owner_id,
        )
        return row

    def set_model(self, thread_id: str, model_id: str) -> None:
        """UPSERT 写入会话模型偏好（NULL 语义 = 全局默认由调用方控制）。"""
        conn = _open()
        try:
            conn.execute(
                """
                INSERT INTO threads (thread_id, owner_id, title, model_id,
                                     created_at, updated_at)
                VALUES (?, 'system', '存量会话', ?, datetime('now'), datetime('now'))
                ON CONFLICT(thread_id) DO UPDATE SET
                    model_id = excluded.model_id,
                    updated_at = datetime('now')
                """,
                (thread_id, model_id),
            )
            conn.commit()
            logger.debug(
                "thread set_model: thread_id={} model_id={}", thread_id, model_id,
            )
        finally:
            conn.close()

    def get_model(self, thread_id: str) -> str | None:
        """返回会话模型偏好；未登记 / 未设置返回 ``None``（= 用全局默认）。"""
        row = self.get_thread(thread_id)
        if row is None:
            return None
        return row.get("model_id")

    # ── M-5 会话管理写操作 ──────────────────────────────

    def rename_thread(self, thread_id: str, title: str) -> bool:
        """重命名会话：UPDATE title + updated_at；返回是否命中。"""
        conn = _open()
        try:
            cur = conn.execute(
                "UPDATE threads SET title = ?, updated_at = datetime('now') "
                " WHERE thread_id = ?",
                (title, thread_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_archived(
        self,
        thread_id: str,
        archived: int,
        deleted_at: str | None = None,
    ) -> bool:
        """设置会话归档态：``archived ∈ {0,1,2}``；返回是否命中。

        - ``archived=1`` 归档、``0`` 恢复：不改 ``deleted_at``；
        - ``archived=2`` 删除（软删）：写入 ``deleted_at``（UTC ISO 串）。
        """
        if archived not in (0, 1, 2):
            raise ValueError(f"archived must be 0/1/2, got {archived}")
        conn = _open()
        try:
            cur = conn.execute(
                """
                UPDATE threads
                   SET archived = ?,
                       deleted_at = COALESCE(?, deleted_at),
                       updated_at = datetime('now')
                 WHERE thread_id = ?
                """,
                (archived, deleted_at, thread_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── 列表 / 统计 ────────────────────────────────────

    def list_by_owner(
        self,
        owner_id: str,
        archived: int | None = None,
    ) -> list[dict[str, Any]]:
        """按 owner 列出会话（updated_at 倒序，供会话侧栏 / 审计过滤）。

        Args:
            owner_id: 所有者（JWT user_id / dev）。
            archived: ``None``=全量（既有行为不变，供审计过滤）；
                ``0/1/2``=按状态过滤（0=活跃 1=归档 2=删除）。
        """
        conn = _open()
        try:
            if archived is None:
                rows = conn.execute(
                    "SELECT thread_id, owner_id, title, model_id, created_at, "
                    "       updated_at, archived, deleted_at "
                    "  FROM threads WHERE owner_id = ? "
                    " ORDER BY updated_at DESC, thread_id ASC",
                    (owner_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT thread_id, owner_id, title, model_id, created_at, "
                    "       updated_at, archived, deleted_at "
                    "  FROM threads WHERE owner_id = ? AND archived = ? "
                    " ORDER BY updated_at DESC, thread_id ASC",
                    (owner_id, archived),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_all(self, archived: int | None = None) -> list[dict[str, Any]]:
        """列出全部会话（管理员跨用户视角；archived 过滤同 list_by_owner）。"""
        conn = _open()
        try:
            if archived is None:
                rows = conn.execute(
                    "SELECT thread_id, owner_id, title, model_id, created_at, "
                    "       updated_at, archived, deleted_at "
                    "  FROM threads "
                    " ORDER BY updated_at DESC, thread_id ASC",
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT thread_id, owner_id, title, model_id, created_at, "
                    "       updated_at, archived, deleted_at "
                    "  FROM threads WHERE archived = ? "
                    " ORDER BY updated_at DESC, thread_id ASC",
                    (archived,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_thread_ids_by_owner(self, owner_id: str) -> list[str]:
        """仅返回 owner 的 thread_id 列表（审计列表角色过滤用，性能更轻）。

        注意（M-5 共享知识 #2）：**保持全量不过滤 archived** —— 审计页需看到
        已删会话的 HITL 记录，供追溯；软删不改变该语义。
        """
        return [row["thread_id"] for row in self.list_by_owner(owner_id)]

    def count(self) -> int:
        """threads 表总行数。"""
        conn = _open()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM threads").fetchone()
            return int(row["c"]) if row else 0
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════
# 模块级统一模型读写接口（架构 §7.4 命名规范，唯一入口）
# ═══════════════════════════════════════════════════════


def get_model_for_thread(thread_id: str) -> str:
    """返回会话生效模型：``threads.model_id`` 非 NULL 用之，否则回退全局。

    Returns:
        ``threads.model_id`` → ``core.llm_client.get_current_model()``
        （进程级全局，初始即默认模型）。
    """
    # 延迟导入避免模块级循环（thread_store ← llm_client 单向无环，仍显式化）
    from core.llm_client import get_current_model

    store = ThreadStore()
    model_id = store.get_model(thread_id)
    if model_id:
        return model_id
    return get_current_model()


def set_model_for_thread(thread_id: str, model_id: str) -> None:
    """设置会话模型偏好（校验在 AVAILABLE_MODELS 内，UPSERT 写入）。

    Raises:
        ValueError: ``model_id`` 不在 ``AVAILABLE_MODELS`` 内。
    """
    from core.llm_client import AVAILABLE_MODELS

    if model_id not in {m["id"] for m in AVAILABLE_MODELS}:
        raise ValueError(f"Unknown model: {model_id}")
    ThreadStore().set_model(thread_id, model_id)


def resolve_model(thread_id: str | None) -> str:
    """解析会话/全局生效模型：``thread_id ? get_model_for_thread : get_current_model``。"""
    from core.llm_client import get_current_model

    if thread_id:
        return get_model_for_thread(thread_id)
    return get_current_model()


# ═══════════════════════════════════════════════════════
# 越权判定 helper（handler 内联调用：/chat、/models/switch 等 body 内 thread_id）
# ═══════════════════════════════════════════════════════


def delete_thread(thread_id: str) -> bool:
    """软删封装（M-5）：``archived=2`` + ``deleted_at``（UTC ISO 串）。

    保留 checkpoint 数据供审计追溯（TTL 自然清理）；软删后所有 thread 入口
    端点一律 404（防泄漏「会话曾存在」）。
    """
    deleted_at = datetime.now(timezone.utc).isoformat()
    return ThreadStore().set_archived(thread_id, 2, deleted_at=deleted_at)


def ensure_thread_owned(
    thread_id: str,
    user_id: str,
    role: str | Role,
    strict: bool | None = None,
) -> None:
    """生产模式下的 owner 校验 + 懒登记（供 body 内 thread_id 的端点内联调用）。

    语义（PRD P0-2 + 架构 §1.3 步骤 5/6 + M-5 升级）：
    1. **软删（archived=2）→ 404**（无论 dev/prod、无论角色；管理员同样 404，
       已删会话不可复活访问，防泄漏「会话曾存在」）；
    2. 管理员角色（或 admin token 等效已映射为 ``admin``）→ 放行；
    3. threads 表无行 → 懒登记：首个已认证访问者接管（owner=user_id）；
    4. owner 不符 → 403（不泄漏具体值）；
    5. 严格模式（``strict=True`` 或 ``settings.threads_strict_mode``）下
       未知 thread 一律 404（即使 checkpoint 存在也拒绝）；
    6. dev 模式（非 production）：已有行不做 owner 校验（放行），
       无行仍懒登记（保证 dev 模型偏好可持久，架构 §7.3）。

    Args:
        thread_id: 会话 ID。
        user_id:   当前身份（JWT sub/user_id；dev 下为 "dev"）。
        role:      当前角色（Role 枚举或字符串；生产从 JWT role claim 解析）。
        strict:    严格模式开关；None 时取 ``settings.threads_strict_mode``。

    Raises:
        HTTPException 403: owner 不符（越权）。
        HTTPException 404: 严格模式下未知 thread / 软删会话。
    """
    # lazy import：reload api.config 后取到最新 settings（避免模块级引用过期）
    from api.config import settings

    if strict is None:
        strict = settings.threads_strict_mode

    store = ThreadStore()
    row = store.get_thread(thread_id)
    if row is None:
        if strict:
            raise HTTPException(status_code=404, detail=_MSG_NOT_FOUND)
        # 懒登记：首个已认证访问者接管（backfill 之外的第二道保险）
        store.ensure_thread(thread_id, user_id)
        return

    # M-5：软删会话（archived=2）= 资源不存在，dev/prod 一致、管理员同样 404
    if int(row.get("archived") or 0) == 2:
        raise HTTPException(status_code=404, detail=_MSG_NOT_FOUND)

    if not settings.is_production:
        # dev：不校验 owner（本地开发零改动），已有行直接放行
        return

    role_value = role.value if isinstance(role, Role) else str(role)
    if role_value == Role.ADMIN.value:
        # 管理员角色跨用户放行（US-1.2）
        return

    if row["owner_id"] != user_id:
        raise HTTPException(status_code=403, detail=_MSG_FORBIDDEN)


def _open() -> sqlite3.Connection:
    """打开主库连接（复用 mcp_tools.db.database 的 get_connection）。"""
    from mcp_tools.db.database import get_connection

    conn = get_connection()
    _ensure_threads_schema(conn)
    return conn
