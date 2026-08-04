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
        """)
        conn.commit()
        logger.info("Database initialized: {}", settings.database_path)

        # P0 可解释性 AI 迁移：补齐设备铭牌字段
        _ensure_devices_columns(conn)
        conn.commit()
    finally:
        conn.close()
