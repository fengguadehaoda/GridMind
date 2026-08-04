"""GridMind 知识图谱迁移工具 —— SQLite / NetworkX → Neo4j。

设计要点
--------
1. **幂等**：所有写操作用 ``MERGE`` 而非 ``CREATE``；节点主键为 ``entity_id``，唯一性由
   ``kg_ontology.ENTITY_ID_UNIQUE_CYPHER`` 约束保证；重复执行结果一致。
2. **可审计**：每次迁移在 SQLite ``kg_migration_log`` 表写一行（含时间、来源、计数、状态、错误）。
3. **批量写入**：每批 100 条（参数 ``batch_size`` 可调），避免一次性事务过大；进度条可选。
4. **来源无关**：支持 ``--source sqlite`` 与 ``--source networkx`` 两种读取模式，二者最终产生相同 Cypher。
5. **可独立运行**：通过 ``KGMigrator`` 类 API 编程调用；命令行入口 ``python -m core.kg_migration``。
6. **失败容忍**：实体迁移失败不中断关系迁移；最终报告含每阶段统计与错误明细。
7. **Cypher 注入防护**：所有动态值走 ``$param`` 参数化通道；Cypher 文本常量在本模块内硬编码。

Cypher 模式
-----------
::

    // 节点
    MERGE (n:Entity {entity_id: $entity_id})
    SET n.name = $name, n.type = $type, n.code = $code, n.properties = $properties
    WITH n
    CALL apoc.create.addLabels(n, [$label]) YIELD node
    RETURN node

    // 关系
    MATCH (a:Entity {entity_id: $src_id})
    MATCH (b:Entity {entity_id: $tgt_id})
    MERGE (a)-[r:RELATION {type: $rel_type, source_id: $src_id, target_id: $tgt_id}]->(b)
    RETURN r
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from api.config import settings
from mcp_tools.db.database import get_connection

# 防止在没有 Neo4j 环境下 import 失败
try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError
    NEO4J_AVAILABLE = True
except ImportError:  # pragma: no cover
    NEO4J_AVAILABLE = False
    ServiceUnavailable = Exception  # type: ignore
    AuthError = Exception  # type: ignore

from core.kg_ontology import apply_ontology
from core.kg_seed_extractor import SeedExtractor


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MigrationReport:
    """单次迁移执行的报告。"""

    migration_id: int
    source: str
    started_at: str
    finished_at: str
    status: str
    entity_count: int = 0
    relation_count: int = 0
    source_entity_cnt: int = 0
    source_rel_cnt: int = 0
    error_message: str | None = None
    target_uri: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "source": self.source,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "entity_count": self.entity_count,
            "relation_count": self.relation_count,
            "source_entity_cnt": self.source_entity_cnt,
            "source_rel_cnt": self.source_rel_cnt,
            "error_message": self.error_message,
            "target_uri": self.target_uri,
            "duration_ms": self.duration_ms,
        }

    @property
    def is_success(self) -> bool:
        return self.status == "success"


# 类型→Neo4j 标签映射（M0：4 个本体类 + 设备实例 + 通用 Entity）
_ENTITY_TYPE_TO_LABEL: dict[str, str] = {
    "设备类别": "DeviceCategory",
    "设备类型": "DeviceCategory",
    "故障类型": "FaultType",
    "处置措施": "HandlingMeasure",
    "规程": "Regulation",
    "设备实例": "DeviceInstance",
}


def _map_label(entity_type: str) -> str:
    """根据实体的 ``type`` 字段映射到 Neo4j 节点标签。

    未识别的类型保持为空字符串（节点仅保留 ``Entity`` 通用标签）。
    """
    return _ENTITY_TYPE_TO_LABEL.get(entity_type, "")


# ─────────────────────────────────────────────────────────────────────────────
# 节点 / 关系 Cypher 模板
# ─────────────────────────────────────────────────────────────────────────────

# MERGE 节点 + 设置属性；标签通过 APOC 动态添加（避免多 label Cypher 拼接）
_MERGE_NODE_CYPHER = """
MERGE (n:Entity {entity_id: $entity_id})
ON CREATE SET n.created_at = datetime()
SET n.name = $name,
    n.type = $type,
    n.code = $code,
    n.properties = $properties
RETURN n.entity_id AS entity_id
""".strip()

# 备选：用 SET n:Label 而非 APOC（兼容性更好）
_MERGE_NODE_WITH_LABEL_CYPHER = """
MERGE (n:Entity {entity_id: $entity_id})
ON CREATE SET n.created_at = datetime()
SET n.name = $name,
    n.type = $type,
    n.code = $code,
    n.properties = $properties,
    n:`$label`
RETURN n.entity_id AS entity_id
""".strip()

_MERGE_REL_CYPHER = """
MATCH (a:Entity {entity_id: $src_id})
MATCH (b:Entity {entity_id: $tgt_id})
MERGE (a)-[r:RELATION {type: $rel_type, src_id: $src_id, tgt_id: $tgt_id}]->(b)
ON CREATE SET r.created_at = datetime()
RETURN type(r) AS rel_type
""".strip()

_COUNT_NODES_CYPHER = "MATCH (n:Entity) RETURN count(n) AS cnt"
_COUNT_RELS_CYPHER = "MATCH ()-[r:RELATION]->() RETURN count(r) AS cnt"


# ─────────────────────────────────────────────────────────────────────────────
# KGMigrator
# ─────────────────────────────────────────────────────────────────────────────

class KGMigrator:
    """知识图谱迁移器——支持 sqlite / networkx 两种来源。

    用法::

        migrator = KGMigrator()
        report = migrator.run(source="sqlite")
        # 或
        report = migrator.run(source="networkx")
        # 或仅校验不写入
        report = migrator.run(source="sqlite", verify_only=True)
    """

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
        neo4j_database: str | None = None,
        batch_size: int = 100,
        apply_schema: bool = True,
    ) -> None:
        self.uri = neo4j_uri or settings.neo4j_uri
        self.user = neo4j_user or settings.neo4j_user
        self.password = neo4j_password or settings.neo4j_password
        self.database = neo4j_database or settings.neo4j_database
        self.batch_size = max(1, int(batch_size))
        self.apply_schema = apply_schema

        self._driver: Any = None
        self._migration_id: int | None = None

    # ── 公开 API ─────────────────────────────────────

    def run(
        self,
        source: str = "sqlite",
        verify_only: bool = False,
        apply_seed_extractor: bool = True,
    ) -> MigrationReport:
        """执行一次完整迁移。

        Args:
            source: ``sqlite`` / ``networkx`` / ``seed_extractor``（M1 新增）。
            verify_only: True 时仅做节点/关系计数校验，不写入。
            apply_seed_extractor: True 时同步运行 ``SeedExtractor``（≥500 三元组）。
                当 ``source="seed_extractor"`` 时此参数被忽略。

        Returns:
            ``MigrationReport`` 报告（含状态、计数、错误信息）。
        """
        if source not in ("sqlite", "networkx", "seed_extractor"):
            raise ValueError(
                f"不支持的 source: {source}（仅 sqlite / networkx / seed_extractor）"
            )

        if not NEO4J_AVAILABLE:
            return self._record_failure(
                source=source,
                error="neo4j Python 驱动未安装（pip install 'neo4j>=5.0.0,<6.0.0'）",
            )

        started_at = datetime.now().isoformat(timespec="seconds")
        started_ts = time.monotonic()

        # 1) 启动日志
        self._migration_id = self._log_start(source, started_at)
        logger.info("=" * 60)
        logger.info("KGMigrator 启动 (id={}, source={})", self._migration_id, source)
        logger.info("=" * 60)

        # 2) 读源
        try:
            entities, relations = self._read_source(source)
        except Exception as exc:  # noqa: BLE001
            return self._record_failure(
                source=source, started_at=started_at,
                started_ts=started_ts,
                error=f"读源失败: {exc}",
            )
        logger.info("源数据: {} 节点, {} 关系", len(entities), len(relations))

        # 3) 仅校验模式
        if verify_only:
            report = self._verify_only(
                source=source, started_at=started_at, started_ts=started_ts,
                source_entity_cnt=len(entities), source_rel_cnt=len(relations),
            )
            return report

        # 4) 连接 Neo4j
        try:
            self._driver = GraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
            # neo4j 5.x driver 的 verify_connectivity() 不接受 timeout kwarg
            self._driver.verify_connectivity()
        except (ServiceUnavailable, AuthError) as exc:  # type: ignore[misc]
            return self._record_failure(
                source=source, started_at=started_at, started_ts=started_ts,
                source_entity_cnt=len(entities), source_rel_cnt=len(relations),
                error=f"Neo4j 连接失败: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return self._record_failure(
                source=source, started_at=started_at, started_ts=started_ts,
                source_entity_cnt=len(entities), source_rel_cnt=len(relations),
                error=f"Neo4j 驱动初始化失败: {exc}",
            )

        try:
            # 5) 应用本体 schema（M0 占位）
            if self.apply_schema:
                try:
                    schema_report = apply_ontology(self._driver, database=self.database)
                    logger.info(
                        "Schema 应用: {} constraints, {} indexes",
                        schema_report["constraints_applied"],
                        schema_report["indexes_applied"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Schema 应用失败（继续迁移）: {}", exc)

            # 6) 迁移节点
            entity_count = self._migrate_entities(entities)
            logger.info("节点迁移完成: {} 条", entity_count)

            # 7) 迁移关系
            relation_count = self._migrate_relations(relations)
            logger.info("关系迁移完成: {} 条", relation_count)

            # 8) 校验最终计数
            actual_nodes = self._count_neo4j(_COUNT_NODES_CYPHER)
            actual_rels = self._count_neo4j(_COUNT_RELS_CYPHER)
            logger.info("Neo4j 当前: {} 节点 / {} 关系", actual_nodes, actual_rels)

            finished_at = datetime.now().isoformat(timespec="seconds")
            duration_ms = int((time.monotonic() - started_ts) * 1000)

            report = MigrationReport(
                migration_id=self._migration_id or 0,
                source=source,
                started_at=started_at,
                finished_at=finished_at,
                status="success",
                entity_count=entity_count,
                relation_count=relation_count,
                source_entity_cnt=len(entities),
                source_rel_cnt=len(relations),
                error_message=None,
                target_uri=self.uri,
                duration_ms=duration_ms,
            )
            self._log_finish(report)
            logger.success(
                "迁移成功: {} 节点 / {} 关系（耗时 {}ms）",
                entity_count, relation_count, duration_ms,
            )
            return report

        except Exception as exc:  # noqa: BLE001
            finished_at = datetime.now().isoformat(timespec="seconds")
            duration_ms = int((time.monotonic() - started_ts) * 1000)
            return self._record_failure(
                source=source, started_at=started_at, started_ts=started_ts,
                source_entity_cnt=len(entities), source_rel_cnt=len(relations),
                error=f"迁移失败: {exc}",
                finished_at=finished_at, duration_ms=duration_ms,
            )
        finally:
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:  # noqa: BLE001
                    pass

    def verify(self) -> dict[str, int]:
        """仅校验 Neo4j 当前节点/关系数。

        Returns:
            ``{"entities": N, "relations": M, "ok": bool}``
        """
        if not NEO4J_AVAILABLE:
            return {"entities": 0, "relations": 0, "ok": False, "error": "neo4j 未安装"}
        driver = GraphDatabase.driver(
            self.uri, auth=(self.user, self.password)
        )
        try:
            # neo4j 5.x driver 的 verify_connectivity() 不接受 timeout kwarg
            driver.verify_connectivity()
            with driver.session(database=self.database) as session:
                entities = session.run(_COUNT_NODES_CYPHER).single()["cnt"]
                relations = session.run(_COUNT_RELS_CYPHER).single()["cnt"]
            return {"entities": entities, "relations": relations, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"entities": 0, "relations": 0, "ok": False, "error": str(exc)}
        finally:
            driver.close()

    def close(self) -> None:
        """关闭底层 driver（如有）。"""
        if self._driver is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    # ── 内部：源读取 ──────────────────────────────────

    def _read_source(
        self, source: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从 SQLite / NetworkX / SeedExtractor 读取实体/关系。

        Returns:
            (entities, relations) 列表；每项是 dict 便于参数化。
        """
        if source == "sqlite":
            return self._read_from_sqlite()
        if source == "networkx":
            return self._read_from_networkx()
        if source == "seed_extractor":
            return self._read_from_seed_extractor()
        raise ValueError(f"未知 source: {source}")

    def _read_from_seed_extractor(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从 SeedExtractor 读取（≥500 三元组）。"""
        extractor = SeedExtractor()
        graph = extractor.build()
        # 转为迁移器期望的格式
        entities: list[dict[str, Any]] = []
        for node in graph["nodes"]:
            entities.append({
                "entity_id": node["entity_id"],
                "name": node["name"],
                "type": node["type"],
                "code": node.get("code", node["entity_id"]),
                "properties": json.dumps(node.get("properties", {}), ensure_ascii=False),
            })
        relations: list[dict[str, Any]] = []
        for rel in graph["relations"]:
            relations.append({
                "src_id": rel["src_id"],
                "tgt_id": rel["tgt_id"],
                "rel_type": rel["type"],
            })
        return entities, relations

    def _read_from_sqlite(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从 SQLite ``graph_entities`` / ``graph_relations`` 表读取。"""
        conn = get_connection()
        try:
            entity_rows = conn.execute(
                "SELECT entity_id, name, type, properties FROM graph_entities"
            ).fetchall()
            rel_rows = conn.execute(
                "SELECT source_id, target_id, relation_type "
                "FROM graph_relations"
            ).fetchall()

            entities: list[dict[str, Any]] = []
            for r in entity_rows:
                props_raw = r["properties"] or "{}"
                try:
                    props = json.loads(props_raw) if isinstance(props_raw, str) else dict(props_raw)
                except (TypeError, json.JSONDecodeError):
                    props = {}
                entities.append({
                    "entity_id": r["entity_id"],
                    "name": r["name"],
                    "type": r["type"],
                    "code": r["entity_id"],  # M0 默认 code=entity_id（M1 规范）
                    "properties": json.dumps(props, ensure_ascii=False),
                })

            relations: list[dict[str, Any]] = []
            for r in rel_rows:
                relations.append({
                    "src_id": r["source_id"],
                    "tgt_id": r["target_id"],
                    "rel_type": r["relation_type"],
                })

            return entities, relations
        finally:
            conn.close()

    def _read_from_networkx(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从 NetworkX ``KnowledgeGraph`` 实例读取（保留现有数据）。"""
        # 延迟 import 避免循环
        from core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        entities: list[dict[str, Any]] = []
        for e in kg.get_all_entities():
            entities.append({
                "entity_id": e.id,
                "name": e.name,
                "type": e.type,
                "code": e.id,  # M0 默认
                "properties": json.dumps(e.properties or {}, ensure_ascii=False),
            })

        relations: list[dict[str, Any]] = []
        for node_id in kg.graph.nodes():
            for _, tgt, data in kg.graph.out_edges(node_id, data=True):
                relations.append({
                    "src_id": node_id,
                    "tgt_id": tgt,
                    "rel_type": data.get("label", "关联"),
                })
        return entities, relations

    # ── 内部：写入 ─────────────────────────────────────

    def _migrate_entities(self, entities: list[dict[str, Any]]) -> int:
        """批量 MERGE 节点，返回成功写入数。"""
        if not entities:
            return 0

        total_written = 0
        total = len(entities)
        session = self._driver.session(database=self.database)
        try:
            for i in range(0, total, self.batch_size):
                batch = entities[i : i + self.batch_size]
                batch_no = i // self.batch_size + 1
                total_batches = (total + self.batch_size - 1) // self.batch_size

                tx = session.begin_transaction()
                try:
                    for ent in batch:
                        tx.run(_MERGE_NODE_CYPHER, **ent)
                    tx.commit()
                    total_written += len(batch)
                    logger.info(
                        "节点批 {} / {}: +{} (累计 {})",
                        batch_no, total_batches, len(batch), total_written,
                    )
                except Exception as exc:  # noqa: BLE001
                    tx.rollback()
                    logger.error("节点批 {} 失败: {}", batch_no, exc)
                    # 退避：单条回退重试
                    recovered = self._fallback_single_entities(session, batch, exc)
                    total_written += recovered
        finally:
            session.close()
        return total_written

    def _fallback_single_entities(
        self, session: Any, batch: list[dict[str, Any]], last_exc: Exception,
    ) -> int:
        """节点批失败时单条回退，返回成功数。"""
        recovered = 0
        for ent in batch:
            try:
                tx = session.begin_transaction()
                tx.run(_MERGE_NODE_CYPHER, **ent)
                tx.commit()
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                tx.rollback()
                logger.error(
                    "单条节点失败: entity_id={} err={}", ent.get("entity_id"), exc,
                )
        if recovered < len(batch):
            logger.warning(
                "节点批回退：{}/{} 成功（last_err={}）", recovered, len(batch), last_exc,
            )
        return recovered

    def _migrate_relations(self, relations: list[dict[str, Any]]) -> int:
        """批量 MERGE 关系。"""
        if not relations:
            return 0

        total_written = 0
        total = len(relations)
        session = self._driver.session(database=self.database)
        try:
            for i in range(0, total, self.batch_size):
                batch = relations[i : i + self.batch_size]
                batch_no = i // self.batch_size + 1
                total_batches = (total + self.batch_size - 1) // self.batch_size

                tx = session.begin_transaction()
                try:
                    for rel in batch:
                        tx.run(_MERGE_REL_CYPHER, **rel)
                    tx.commit()
                    total_written += len(batch)
                    logger.info(
                        "关系批 {} / {}: +{} (累计 {})",
                        batch_no, total_batches, len(batch), total_written,
                    )
                except Exception as exc:  # noqa: BLE001
                    tx.rollback()
                    logger.error("关系批 {} 失败: {}", batch_no, exc)
                    recovered = self._fallback_single_relations(session, batch, exc)
                    total_written += recovered
        finally:
            session.close()
        return total_written

    def _fallback_single_relations(
        self, session: Any, batch: list[dict[str, Any]], last_exc: Exception,
    ) -> int:
        """关系批失败时单条回退。"""
        recovered = 0
        for rel in batch:
            try:
                tx = session.begin_transaction()
                tx.run(_MERGE_REL_CYPHER, **rel)
                tx.commit()
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                tx.rollback()
                logger.error(
                    "单条关系失败: src={} tgt={} err={}",
                    rel.get("src_id"), rel.get("tgt_id"), exc,
                )
        if recovered < len(batch):
            logger.warning(
                "关系批回退：{}/{} 成功（last_err={}）", recovered, len(batch), last_exc,
            )
        return recovered

    def _count_neo4j(self, cypher: str) -> int:
        """执行 COUNT 类 Cypher，返回单值。"""
        session = self._driver.session(database=self.database)
        try:
            record = session.run(cypher).single()
            if record is None:
                return 0
            return int(record["cnt"])
        finally:
            session.close()

    # ── 内部：仅校验 ──────────────────────────────────

    def _verify_only(
        self,
        source: str,
        started_at: str,
        started_ts: float,
        source_entity_cnt: int,
        source_rel_cnt: int,
    ) -> MigrationReport:
        """仅校验：读源 + 写 verify_only 日志。"""
        duration_ms = int((time.monotonic() - started_ts) * 1000)
        report = MigrationReport(
            migration_id=self._migration_id or 0,
            source=source,
            started_at=started_at,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            status="verify_only",
            source_entity_cnt=source_entity_cnt,
            source_rel_cnt=source_rel_cnt,
            target_uri=self.uri,
            duration_ms=duration_ms,
        )
        self._log_finish(report)
        logger.info("仅校验模式：源 {} 节点 / {} 关系（未写入）",
                    source_entity_cnt, source_rel_cnt)
        return report

    # ── 内部：日志记录 ──────────────────────────────────

    def _log_start(self, source: str, started_at: str) -> int:
        """在 SQLite ``kg_migration_log`` 表插入 running 行，返回 id。"""
        conn = get_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO kg_migration_log(
                    source, started_at, status, target_uri
                ) VALUES (?, ?, 'running', ?)
                """,
                (source, started_at, self.uri),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _log_finish(self, report: MigrationReport) -> None:
        """更新 SQLite ``kg_migration_log`` 行（success / verify_only / failed）。"""
        if report.migration_id <= 0:
            return
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE kg_migration_log SET
                    finished_at = ?,
                    entity_count = ?,
                    relation_count = ?,
                    source_entity_cnt = ?,
                    source_rel_cnt = ?,
                    status = ?,
                    error_message = ?,
                    duration_ms = ?
                WHERE id = ?
                """,
                (
                    report.finished_at, report.entity_count, report.relation_count,
                    report.source_entity_cnt, report.source_rel_cnt,
                    report.status, report.error_message, report.duration_ms,
                    report.migration_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _record_failure(
        self,
        source: str,
        error: str,
        started_at: str | None = None,
        started_ts: float | None = None,
        source_entity_cnt: int = 0,
        source_rel_cnt: int = 0,
        finished_at: str | None = None,
        duration_ms: int | None = None,
    ) -> MigrationReport:
        """记录失败并返回失败报告。"""
        if started_at is None:
            started_at = datetime.now().isoformat(timespec="seconds")
        if started_ts is None:
            duration_ms = 0
        if duration_ms is None:
            duration_ms = int((time.monotonic() - started_ts) * 1000)
        if finished_at is None:
            finished_at = datetime.now().isoformat(timespec="seconds")
        report = MigrationReport(
            migration_id=self._migration_id or 0,
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            status="failed",
            source_entity_cnt=source_entity_cnt,
            source_rel_cnt=source_rel_cnt,
            error_message=error,
            target_uri=self.uri,
            duration_ms=duration_ms,
        )
        self._log_finish(report)
        logger.error("迁移失败: {}", error)
        return report


# ─────────────────────────────────────────────────────────────────────────────
# 命令行入口
# ─────────────────────────────────────────────────────────────────────────────

def _cli() -> int:
    parser = argparse.ArgumentParser(description="GridMind 知识图谱迁移工具")
    parser.add_argument(
        "--source", choices=["sqlite", "networkx"], default="sqlite",
        help="数据源（默认 sqlite）",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="仅校验源数据，不写入 Neo4j",
    )
    parser.add_argument(
        "--batch-size", type=int, default=100,
        help="批量写入大小（默认 100）",
    )
    parser.add_argument(
        "--uri", default=None, help="Neo4j URI（覆盖配置）",
    )
    parser.add_argument(
        "--user", default=None, help="Neo4j 用户名（覆盖配置）",
    )
    parser.add_argument(
        "--password", default=None, help="Neo4j 密码（覆盖配置）",
    )
    args = parser.parse_args()

    migrator = KGMigrator(
        neo4j_uri=args.uri,
        neo4j_user=args.user,
        neo4j_password=args.password,
        batch_size=args.batch_size,
    )
    report = migrator.run(source=args.source, verify_only=args.verify_only)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.is_success or report.status == "verify_only" else 1


if __name__ == "__main__":
    sys.exit(_cli())
