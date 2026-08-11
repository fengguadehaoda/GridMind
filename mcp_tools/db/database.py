"""SQLite 数据库管理——创建表、获取连接、初始化种子数据。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from api.config import settings


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接（自动创建父目录）。"""
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_devices_columns(conn: sqlite3.Connection) -> None:
    """为 devices 表补齐 P0 机理校验所需铭牌字段（幂等迁移）。

    新增列：
    - ``rated_current``    REAL — 额定电流 (A)
    - ``short_impedance``  REAL — 短路阻抗百分比 (%)
    - ``rated_voltage``    REAL — 额定电压 (kV)

    这些列对应架构 §3.5 规则库 JSON 中 ``ratio_field: device.rated_current`` 的取值源。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
    migrations = {
        "rated_current": "REAL",
        "short_impedance": "REAL",
        "rated_voltage": "REAL",
    }
    for col, decl in migrations.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {decl}")
            logger.info("Migration: devices.{} added ({})", col, decl)


def _ensure_hitl_columns(conn: sqlite3.Connection) -> None:
    """为 hitl_audit_log 表补齐 V1.5.1 pause/rewind 所需 3 列（幂等迁移）。

    V1.5.1 新增列（架构 §2.4.1 + §6 T01 主理人决策 #5）：
    - ``risk_level``  TEXT    — HITL 风险分级（low/normal/high/critical）
    - ``pause_count`` INTEGER — 该 audit 行对应的 session 触发 pause 的次数
    - ``edit_count``  INTEGER — 该 audit 行对应的 session 触发 edit 的次数

    幂等实现：先 ``PRAGMA table_info`` 查已有列，仅 ALTER 缺失列；
    SQLite ``ALTER TABLE ADD COLUMN`` 重复执行会抛 "duplicate column name"，
    旧库升级时**必须**走这条 PRAGMA 路径，否则启动失败。
    """
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(hitl_audit_log)").fetchall()
    }
    # 列迁移：(列名, SQL 声明) — 注意 CHECK 约束在 SQLite ALTER 中不支持，
    # 故 3 列均**不带** CHECK；CHECK 约束由应用层 Pydantic ``RiskLevel`` 枚举保证。
    migrations: list[tuple[str, str]] = [
        ("risk_level", "TEXT NOT NULL DEFAULT 'normal'"),
        ("pause_count", "INTEGER NOT NULL DEFAULT 0"),
        ("edit_count", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, decl in migrations:
        if col not in existing:
            try:
                conn.execute(
                    f"ALTER TABLE hitl_audit_log ADD COLUMN {col} {decl}"
                )
                logger.info(
                    "V1.5.1 migration: hitl_audit_log.{} added ({})", col, decl
                )
            except sqlite3.OperationalError as e:
                # 双保险：万一 PRAGMA 与 ALTER 竞态，仍兜底捕获"重复列"错误
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    logger.debug(
                        "V1.5.1 migration: hitl_audit_log.{} already exists, skip",
                        col,
                    )
                else:
                    raise

    # 新增索引（CREATE INDEX IF NOT EXISTS 自身幂等）
    # 1. risk_level 索引（hitl_audit_service.query_by_risk_level 用，T02 新增方法）
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hitl_risk_level "
        "ON hitl_audit_log(risk_level)"
    )
    # 2 & 3. pause_count / edit_count 部分索引（T01 创建，T02 写入后才有用）
    #    部分索引 (WHERE col > 0) 在大表上更省空间（多数 audit 行 count=0）
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hitl_pause_count "
        "ON hitl_audit_log(pause_count) WHERE pause_count > 0"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hitl_edit_count "
        "ON hitl_audit_log(edit_count) WHERE edit_count > 0"
    )


def _ensure_knowledge_chunks_columns(conn: sqlite3.Connection) -> None:
    """为 knowledge_chunks 表补齐「功能介绍知识库化」所需元信息列（幂等迁移）。

    新增列（全部可空 / 带默认值，旧数据零影响）：
    - ``tags``           TEXT — 逗号分隔标签串（如 ``feature-intro,scenario:monitor-overview``）
    - ``icon``           TEXT — Element Plus 图标名（场景卡 / wizard 要点卡渲染用）
    - ``starter_message`` TEXT — 场景卡种子问题（仅 ``scenario:*`` 分片有值）
    - ``meta``           TEXT — JSON 字符串，承载 tour steps / bullets / cta 等结构化内容
    - ``updated_at``     TEXT — 最近一次入仓时间（本地时间字符串）

    幂等实现：先 ``PRAGMA table_info`` 查已有列，仅 ALTER 缺失列；
    并额外 catch "duplicate column" 做双保险（防 PRAGMA 与 ALTER 竞态）。
    """
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(knowledge_chunks)").fetchall()
    }
    migrations: list[tuple[str, str]] = [
        ("tags", "TEXT NOT NULL DEFAULT ''"),
        ("icon", "TEXT"),
        ("starter_message", "TEXT"),
        ("meta", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for col, decl in migrations:
        if col in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {col} {decl}")
            logger.info("Feature-intro migration: knowledge_chunks.{} added ({})", col, decl)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                logger.debug(
                    "Feature-intro migration: knowledge_chunks.{} already exists, skip", col
                )
            else:
                raise

    # doc_id 索引：upsert_chunks 按 doc_id 覆盖式删除，需要索引避免全表扫
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc "
        "ON knowledge_chunks(doc_id)"
    )


def _ensure_threads_columns(conn: sqlite3.Connection) -> None:
    """为 threads 表补齐 M-5 会话管理所需 archived/deleted_at 列（幂等迁移）。

    新增列（架构 session-mgmt §3.1 + 主理人决策 Q1/Q2）：
    - ``archived``   INTEGER NOT NULL DEFAULT 0 — 0=活跃 1=归档 2=删除（软删）
    - ``deleted_at`` TEXT                      — 软删时间戳（UTC ISO 串）；NULL=未删

    幂等实现：先 ``PRAGMA table_info`` 查已有列，仅 ALTER 缺失列；
    SQLite ``ALTER TABLE ADD COLUMN`` 重复执行会抛 "duplicate column name"，
    存量库升级时必须走这条 PRAGMA 路径，否则启动失败（沿用既有迁移模式）。

    同时补建侧栏查询索引 ``idx_threads_owner_archived_updated``（CREATE INDEX
    IF NOT EXISTS 自身幂等），加速 ``GET /sessions`` 的 owner + 状态 + 时间查询。
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

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_threads_owner_archived_updated "
        "ON threads(owner_id, archived, updated_at DESC)"
    )


def _ensure_kb_meta_table(conn: sqlite3.Connection) -> None:
    """创建/迁移「知识库元信息」轻量表（V1.6 · P0-5 跨进程热更新 · 增补件 §3.2）。

    用途：
    - ``kb_revision`` —— 单调递增整数，每次 ``upsert_chunks`` 成功后写入。
    - ``VectorStore.ensure_fresh()`` 每次 ``search_by_tag`` 前惰性 SELECT 比对，
      跨进程（API 9900 / MCP 9901）共享同一份 SQLite，使 MCP 进程无需重启即可
      看到运营热更新后的分片。

    幂等实现：``CREATE TABLE IF NOT EXISTS`` 自身幂等；``updated_at`` 用本地时间。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_meta (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    # 兜底：老库若没 updated_at 列则补齐
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(kb_meta)").fetchall()}
    if "updated_at" not in existing:
        try:
            conn.execute(
                "ALTER TABLE kb_meta ADD COLUMN updated_at "
                "TEXT NOT NULL DEFAULT (datetime('now','localtime'))"
            )
            logger.info("Feature-intro migration: kb_meta.updated_at added")
        except sqlite3.OperationalError as e:  # noqa: PERF203
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                logger.debug("kb_meta.updated_at already exists, skip")
            else:
                raise


def init_db() -> None:
    """初始化数据库表结构。"""
    conn = get_connection()
    try:
        conn.executescript("""
            -- 设备表
            CREATE TABLE IF NOT EXISTS devices (
                device_id   TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL CHECK(device_type IN ('transformer','breaker','cable','busbar')),
                location    TEXT NOT NULL,
                install_date TEXT,
                status      TEXT NOT NULL DEFAULT 'normal'
            );

            -- 遥测表
            CREATE TABLE IF NOT EXISTS telemetry (
                reading_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id    TEXT NOT NULL REFERENCES devices(device_id),
                timestamp    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                temperature  REAL,
                voltage      REAL,
                current_load REAL,
                humidity     REAL,
                pressure     REAL
            );

            -- 巡检记录
            CREATE TABLE IF NOT EXISTS inspections (
                inspection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id     TEXT NOT NULL REFERENCES devices(device_id),
                inspector     TEXT,
                inspect_time  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                result        TEXT NOT NULL CHECK(result IN ('normal','abnormal','critical')),
                notes         TEXT
            );

            -- 安规条款
            CREATE TABLE IF NOT EXISTS safety_rules (
                rule_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_code TEXT NOT NULL UNIQUE,
                category  TEXT NOT NULL,
                content   TEXT NOT NULL,
                severity  TEXT NOT NULL DEFAULT 'mandatory'
            );

            -- 知识库文档片段
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id    TEXT NOT NULL,
                title     TEXT NOT NULL,
                content   TEXT NOT NULL,
                source    TEXT
            );

            -- 图谱实体（持久化层，运行时主要用内存 NetworkX）
            CREATE TABLE IF NOT EXISTS graph_entities (
                entity_id   TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                type        TEXT NOT NULL,
                properties  TEXT DEFAULT '{}'
            );

            -- 图谱关系
            CREATE TABLE IF NOT EXISTS graph_relations (
                relation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id     TEXT NOT NULL REFERENCES graph_entities(entity_id),
                target_id     TEXT NOT NULL REFERENCES graph_entities(entity_id),
                relation_type TEXT NOT NULL,
                UNIQUE(source_id, target_id, relation_type)
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_device ON telemetry(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_inspections_device ON inspections(device_id);

            -- HITL 审计日志（P0：Edit & Continue 改造）
            -- 保留期 3 年（Q3 决策方案 A），SQLite 直接保留，无冷归档。
            CREATE TABLE IF NOT EXISTS hitl_audit_log (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id                TEXT    NOT NULL,
                interrupt_node           TEXT    NOT NULL,
                tool_name                TEXT    NOT NULL,
                user_id                  TEXT    NOT NULL DEFAULT 'anonymous',
                user_name                TEXT,
                user_role                TEXT,
                decision                 TEXT    NOT NULL CHECK(decision IN ('approve','reject','edit_approve')),
                original_args            TEXT    NOT NULL,
                edited_args              TEXT,
                edit_reason              TEXT,
                safety_recheck_result    TEXT,
                reason                   TEXT,
                ip_address               TEXT,
                user_agent               TEXT,
                created_at               TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_hitl_audit_thread  ON hitl_audit_log(thread_id);
            CREATE INDEX IF NOT EXISTS idx_hitl_audit_created ON hitl_audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_hitl_audit_user    ON hitl_audit_log(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_hitl_audit_decision ON hitl_audit_log(decision);

            -- P1-4: 可解释性 AI 三层融合结果持久化（独立表，不破坏 hitl_audit_log）
            -- 用途：保存 DiagnosisFusionResult 完整快照（含 reasoning_chain），
            -- 供事后追溯、QA 复核、回归测试使用。
            -- 保留期：与 hitl_audit_log 一致（3 年）
            CREATE TABLE IF NOT EXISTS diagnosis_fusion_log (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id              TEXT    NOT NULL,
                fusion_result          TEXT    NOT NULL,    -- JSON 序列化 DiagnosisFusionResult
                llm_confidence         REAL,
                final_severity         TEXT,
                requires_human_review  INTEGER NOT NULL DEFAULT 0,  -- 0/1 布尔
                created_at             TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_fusion_thread   ON diagnosis_fusion_log(thread_id);
            CREATE INDEX IF NOT EXISTS idx_fusion_created  ON diagnosis_fusion_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_fusion_severity ON diagnosis_fusion_log(final_severity);

            -- M0：知识图谱 Neo4j 迁移历史日志
            -- 用途：记录每次 KGMigrator 执行的元数据（时间、来源、节点/关系数、耗时、状态）
            -- 幂等迁移支持：相同 source 的多次执行会生成多行，便于审计与回滚分析
            CREATE TABLE IF NOT EXISTS kg_migration_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                source            TEXT    NOT NULL,                    -- 'sqlite' / 'networkx'
                started_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                finished_at       TEXT,
                entity_count      INTEGER NOT NULL DEFAULT 0,          -- 实际写入/合并的节点数
                relation_count    INTEGER NOT NULL DEFAULT 0,          -- 实际写入/合并的关系数
                source_entity_cnt INTEGER NOT NULL DEFAULT 0,          -- 源数据理论节点数
                source_rel_cnt    INTEGER NOT NULL DEFAULT 0,          -- 源数据理论关系数
                status            TEXT    NOT NULL DEFAULT 'running'   -- 'running' / 'success' / 'failed'
                        CHECK(status IN ('running','success','failed','verify_only')),
                error_message     TEXT,
                target_uri        TEXT,                                -- Neo4j bolt URI（便于多环境）
                duration_ms       INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_kg_mig_source  ON kg_migration_log(source);
            CREATE INDEX IF NOT EXISTS idx_kg_mig_started ON kg_migration_log(started_at);
            CREATE INDEX IF NOT EXISTS idx_kg_mig_status  ON kg_migration_log(status);

            -- M2：双向同步审计日志（Neo4j ↔ Chroma）
            -- 用途：
            -- 1. 持久化同步事件队列（asyncio.Queue 内存队列 + SQLite 持久化双写）
            -- 2. 进程崩溃重启时可从 pending 状态恢复任务
            -- 3. 冲突解决审计（Neo4j 权威源覆盖 Chroma）
            -- 4. 自动重试（retry_count++，最大 3 次后标记 failed）
            CREATE TABLE IF NOT EXISTS sync_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type         TEXT    NOT NULL CHECK(sync_type IN ('graph_to_vector','vector_to_graph','event','rollback')),
                entity_id         TEXT    NOT NULL,
                chunk_id          TEXT,
                status            TEXT    NOT NULL DEFAULT 'pending'
                                        CHECK(status IN ('pending','success','failed','conflict')),
                retry_count       INTEGER NOT NULL DEFAULT 0,
                neo4j_updated_at  REAL,
                chroma_updated_at REAL,
                payload           TEXT,                  -- JSON
                started_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                finished_at       TEXT,
                error_message     TEXT,
                thread_id         TEXT,                  -- 关联会话
                duration_ms       INTEGER,
                reason            TEXT                   -- 回滚/事件原因
            );
            CREATE INDEX IF NOT EXISTS idx_sync_status  ON sync_log(status);
            CREATE INDEX IF NOT EXISTS idx_sync_type    ON sync_log(sync_type);
            CREATE INDEX IF NOT EXISTS idx_sync_thread  ON sync_log(thread_id);
            CREATE INDEX IF NOT EXISTS idx_sync_started ON sync_log(started_at);
            CREATE INDEX IF NOT EXISTS idx_sync_entity  ON sync_log(entity_id);

            -- V1.7.0 多用户地基（P0-1）：会话归属表 + 按 owner 的会话列表索引
            -- thread_id 直接复用 LangGraph checkpoint 主键，不加代理主键；
            -- model_id 为 M-2 per-session 模型偏好（NULL = 用全局默认）；
            -- owner_id 取自 JWT sub / user_id（管理员视角可跨用户）。
            -- M-5 增量（主理人决策 Q1/Q2）：archived 0/1/2 + deleted_at 软删。
            CREATE TABLE IF NOT EXISTS threads (
                thread_id   TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '新会话',
                model_id    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                archived    INTEGER NOT NULL DEFAULT 0,
                deleted_at  TEXT
            );

            -- 按 owner 的会话列表查询（前端会话侧栏 / /audit/hitl 角色过滤）
            CREATE INDEX IF NOT EXISTS idx_threads_owner_updated
                ON threads(owner_id, updated_at DESC);

            -- M-5：侧栏查询索引（owner + 状态 + 时间）——**不在此处创建**，
            -- 由末尾 `_ensure_threads_columns(conn)` 负责（它先 PRAGMA 补
            -- archived/deleted_at 列再建索引）。若在此创建，存量库（threads
            -- 表已存在、跳过 CREATE TABLE）会直接引用不存在的 archived 列
            -- 导致 `sqlite3.OperationalError: no such column: archived`
            -- 启动崩溃（QA Round 1 P0）。
        """)
        conn.commit()
        logger.info("Database initialized: {}", settings.database_path)

        # P0 可解释性 AI 迁移：补齐设备铭牌字段
        _ensure_devices_columns(conn)
        # V1.5.1 迁移：补齐 HITL 表 pause/rewind 3 列（架构 §2.4.1 + §6 T01）
        _ensure_hitl_columns(conn)
        # 功能介绍知识库化迁移：补齐 knowledge_chunks 元信息 5 列
        _ensure_knowledge_chunks_columns(conn)
        # V1.6 P0-5 增补件 §3.2：补齐 kb_meta 表（跨进程热更新 revision 戳）
        _ensure_kb_meta_table(conn)
        # M-5：补齐 threads 表 archived/deleted_at 列 + 侧栏索引（存量库升级）
        _ensure_threads_columns(conn)
        conn.commit()
    finally:
        conn.close()
