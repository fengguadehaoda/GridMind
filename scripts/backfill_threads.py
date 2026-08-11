"""V1.7.0 多用户地基 · 存量会话归属 backfill 脚本（P0-1 迁移策略）。

**用途**：把 v1.6 及更早的 LangGraph checkpoint 会话登记进主库 ``threads``
归属表（owner=``system``，title=``存量会话``，model_id=NULL），使存量数据
在多用户上线后依然可访问（PRD §五 设计说明 4 + 架构 §1.3）。

**幂等性**：``INSERT OR IGNORE``——重复执行不产生重复行、不报错；已由
懒登记接管（owner=具体用户）的行不会被覆盖。

**运行**::

    python scripts/backfill_threads.py
    python scripts/backfill_threads.py --main-db data/gridmind.db --checkpoint-db data/checkpoints.db

**输出**：登记的 thread 数（含新增 / 已存在忽略）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 允许以 ``python scripts/backfill_threads.py`` 直接运行（项目根目录在 sys.path）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger  # noqa: E402

from api.config import settings  # noqa: E402

#: 存量会话默认归属（管理员视角可跨用户访问）
SYSTEM_OWNER = "system"
#: 存量会话默认标题
SYSTEM_TITLE = "存量会话"

#: LangGraph AsyncSqliteSaver checkpoint 表名（checkpoint_service 同源）
CHECKPOINT_TABLE = "checkpoints"


def collect_thread_ids(checkpoint_db_path: str | Path) -> list[str]:
    """从 checkpoint 库读取去重后的 thread_id 列表。

    Args:
        checkpoint_db_path: ``data/checkpoints.db`` 路径。

    Returns:
        按 thread_id 升序去重列表（空库返回空列表）。
    """
    db_path = Path(checkpoint_db_path)
    if not db_path.exists():
        logger.warning("checkpoint db not found: {}, skip backfill", db_path)
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        # 先确认表存在（首次初始化前可能无表）
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (CHECKPOINT_TABLE,),
        ).fetchone()
        if table is None:
            logger.warning(
                "checkpoint table {!r} not found in {}, skip backfill",
                CHECKPOINT_TABLE, db_path,
            )
            return []
        rows = conn.execute(
            f"SELECT DISTINCT thread_id FROM {CHECKPOINT_TABLE} "
            "WHERE thread_id IS NOT NULL AND thread_id != '' "
            "ORDER BY thread_id ASC"
        ).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        conn.close()


def backfill(
    main_db_path: str | Path,
    checkpoint_db_path: str | Path,
    owner: str = SYSTEM_OWNER,
    title: str = SYSTEM_TITLE,
) -> dict[str, Any]:
    """幂等 backfill：把 checkpoint thread_id 登记进主库 threads 表。

    Args:
        main_db_path: 主库 ``data/gridmind.db`` 路径。
        checkpoint_db_path: checkpoint 库 ``data/checkpoints.db`` 路径。
        owner: 存量会话归属（默认 ``system``）。
        title: 存量会话标题（默认 ``存量会话``）。

    Returns:
        ``{"registered": int, "ignored": int, "total": int}`` ——
        registered=本次新登记数；ignored=已存在被忽略数；total=checkpoint 去重总数。
    """
    thread_ids = collect_thread_ids(checkpoint_db_path)
    registered = 0
    ignored = 0

    conn = sqlite3.connect(str(main_db_path))
    try:
        # 幂等建表（脚本独立可运行，不依赖 init_db 已被调用）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS threads (
                thread_id   TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '新会话',
                model_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_threads_owner_updated
                ON threads(owner_id, updated_at DESC);
            """
        )
        for thread_id in thread_ids:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO threads
                    (thread_id, owner_id, title, model_id, created_at, updated_at)
                VALUES (?, ?, ?, NULL, datetime('now'), datetime('now'))
                """,
                (thread_id, owner, title),
            )
            if cur.rowcount and cur.rowcount > 0:
                registered += 1
            else:
                ignored += 1
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "backfill done: total={} registered={} ignored={} owner={}",
        len(thread_ids), registered, ignored, owner,
    )
    return {"registered": registered, "ignored": ignored, "total": len(thread_ids)}


def main() -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="GridMind 存量会话归属 backfill（threads 表幂等登记）"
    )
    parser.add_argument(
        "--main-db", default=None,
        help=f"主库路径（默认 settings.database_path = {settings.database_path}）",
    )
    parser.add_argument(
        "--checkpoint-db", default=None,
        help="checkpoint 库路径（默认 data/checkpoints.db）",
    )
    parser.add_argument(
        "--owner", default=SYSTEM_OWNER,
        help=f"存量会话归属（默认 {SYSTEM_OWNER!r}）",
    )
    parser.add_argument(
        "--title", default=SYSTEM_TITLE,
        help=f"存量会话标题（默认 {SYSTEM_TITLE!r}）",
    )
    args = parser.parse_args()

    main_db = Path(args.main_db) if args.main_db else Path(settings.database_path)
    checkpoint_db = (
        Path(args.checkpoint_db)
        if args.checkpoint_db
        else PROJECT_ROOT / "data" / "checkpoints.db"
    )

    result = backfill(main_db, checkpoint_db, owner=args.owner, title=args.title)
    print(
        f"[backfill] total={result['total']} registered={result['registered']} "
        f"ignored={result['ignored']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
