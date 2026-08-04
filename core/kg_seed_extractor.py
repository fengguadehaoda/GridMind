"""GridMind 知识图谱 M1 三元组抽取器 —— SeedExtractor。

设计目标
--------
- **统一接口**：从 ``kg_seed_data.build_seed_graph()`` 抽取节点/关系，转换为可写入
  多种 backend 的形式（Cypher / NetworkX / SQLite）。
- **幂等**：使用 ``MERGE`` 而非 ``CREATE``；节点用 ``entity_id`` 唯一，关系用
  ``(src_id, tgt_id, type)`` 唯一。
- **Cypher 注入防护**：所有动态值走 ``$param`` 参数化通道；Cypher 文本常量硬编码。
- **可独立运行**：无需 Neo4j 时仅生成内存图，供测试与 KGClient 验证。

抽取流程
--------
::

    SeedExtractor()
        ├── build()              # 返回 dict{"nodes", "relations"}
        ├── report()             # 抽取报告（按类型/标签统计）
        ├── save_report(path)    # 写入 extract_report.json
        ├── to_cypher()          # 生成参数化 Cypher 语句列表
        ├── write_to_neo4j(drv)  # 写入 Neo4j（MERGE 幂等）
        ├── write_to_networkx(g) # 写入 NetworkX 图
        └── save_to_sqlite(conn) # 写入 SQLite graph_entities/relations

Cypher 模式（与 kg_migration.py 对齐）
-----------------------------------
::

    // 节点
    MERGE (n:Entity {entity_id: $entity_id})
    ON CREATE SET n.created_at = datetime()
    SET n.name = $name, n.type = $type, n.code = $code,
        n.properties = $properties
    WITH n
    CALL (n, $label) {
        WITH n
        SET n:`$label`
    }
    RETURN n.entity_id AS entity_id
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from core.kg_seed_data import build_seed_graph, extract_report as _extract_report

# 防止在没有 Neo4j 环境下 import 失败
try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError
    NEO4J_AVAILABLE = True
except ImportError:  # pragma: no cover
    NEO4J_AVAILABLE = False
    ServiceUnavailable = Exception  # type: ignore
    AuthError = Exception  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Cypher 模板（MERGE 幂等）
# ─────────────────────────────────────────────────────────────────────────────

# MERGE 节点 + 设置属性（与 kg_migration.py 一致）
_MERGE_NODE_CYPHER = """
MERGE (n:Entity {entity_id: $entity_id})
ON CREATE SET n.created_at = datetime()
SET n.name = $name,
    n.type = $type,
    n.code = $code,
    n.properties = $properties
RETURN n.entity_id AS entity_id
""".strip()

# MERGE 关系（与 kg_migration.py 一致）
_MERGE_REL_CYPHER = """
MATCH (a:Entity {entity_id: $src_id})
MATCH (b:Entity {entity_id: $tgt_id})
MERGE (a)-[r:RELATION {type: $rel_type, src_id: $src_id, tgt_id: $tgt_id}]->(b)
ON CREATE SET r.created_at = datetime()
SET r += $properties
RETURN type(r) AS rel_type
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# SeedExtractor
# ─────────────────────────────────────────────────────────────────────────────

class SeedExtractor:
    """M1 三元组抽取器。

    用法::

        extractor = SeedExtractor()
        graph = extractor.build()           # 不写入，仅产出 dict
        report = extractor.report()          # 抽取报告
        extractor.save_report("/tmp/extract_report.json")
        # 写入 Neo4j（需先连接）
        extractor.write_to_neo4j(driver, database="neo4j")
        # 或写入 NetworkX（降级 / 内存测试）
        extractor.write_to_networkx(nx_graph)
    """

    def __init__(
        self,
        seed_data: dict[str, list[dict[str, Any]]] | None = None,
        batch_size: int = 100,
    ) -> None:
        """初始化抽取器。

        Args:
            seed_data: 自定义种子数据（默认使用 ``kg_seed_data.build_seed_graph()``）。
            batch_size: 写入 Neo4j 时的批大小。
        """
        self._seed_data = seed_data if seed_data is not None else build_seed_graph()
        self._batch_size = max(1, int(batch_size))
        self._graph: dict[str, list[dict[str, Any]]] | None = None

    # ── 公开 API ────────────────────────────────────

    def build(self) -> dict[str, list[dict[str, Any]]]:
        """构建三元组图（不写入）。

        Returns:
            ``{"nodes": [...], "relations": [...]}`` 字典。
        """
        nodes = list(self._seed_data.get("nodes", []))
        relations = list(self._seed_data.get("relations", []))
        self._graph = {"nodes": nodes, "relations": relations}
        logger.info(
            "SeedExtractor.build: {} nodes, {} relations",
            len(nodes), len(relations),
        )
        return self._graph

    def report(self) -> dict[str, Any]:
        """生成抽取报告（节点 / 关系按类型分组统计）。"""
        if self._graph is None:
            self.build()
        return _extract_report(self._graph)

    def save_report(self, path: str | os.PathLike[str]) -> Path:
        """保存抽取报告到 JSON 文件。"""
        report = self.report()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("抽取报告已写入: {} ({} triples)", p, report["total_triples"])
        return p

    def to_cypher(self) -> list[tuple[str, dict[str, Any]]]:
        """生成参数化 Cypher（MERGE）语句列表。

        Returns:
            [(cypher_text, params_dict), ...] 列表。
        """
        if self._graph is None:
            self.build()
        assert self._graph is not None

        statements: list[tuple[str, dict[str, Any]]] = []
        # 节点语句
        for node in self._graph["nodes"]:
            params = {
                "entity_id": node["entity_id"],
                "name": node["name"],
                "type": node["type"],
                "code": node.get("code", node["entity_id"]),
                "label": node.get("label", "Entity"),
                "properties": json.dumps(node.get("properties", {}), ensure_ascii=False),
            }
            statements.append((_MERGE_NODE_CYPHER, params))
        # 关系语句
        for rel in self._graph["relations"]:
            params = {
                "src_id": rel["src_id"],
                "tgt_id": rel["tgt_id"],
                "rel_type": rel["type"],
                "relation_id": str(uuid.uuid4()),
                "properties": json.dumps(rel.get("properties", {}), ensure_ascii=False),
            }
            statements.append((_MERGE_REL_CYPHER, params))
        return statements

    # ── 写入：Neo4j ──────────────────────────────────

    def write_to_neo4j(
        self,
        driver: Any,
        database: str | None = None,
    ) -> dict[str, int]:
        """写入 Neo4j（MERGE 幂等）。

        Args:
            driver: ``neo4j.Driver`` 实例。
            database: 数据库名（None 表示使用 driver 默认）。

        Returns:
            ``{"nodes": N, "relations": M}`` 写入计数。
        """
        if not NEO4J_AVAILABLE:
            raise RuntimeError(
                "neo4j Python 驱动未安装（pip install 'neo4j>=5.0.0,<6.0.0'）"
            )
        if self._graph is None:
            self.build()
        assert self._graph is not None

        nodes_written = 0
        relations_written = 0

        session_kwargs: dict[str, Any] = {}
        if database is not None:
            session_kwargs["database"] = database

        with driver.session(**session_kwargs) as session:
            # 1) 节点
            total_nodes = len(self._graph["nodes"])
            for i in range(0, total_nodes, self._batch_size):
                batch = self._graph["nodes"][i : i + self._batch_size]
                tx = session.begin_transaction()
                try:
                    for node in batch:
                        params = {
                            "entity_id": node["entity_id"],
                            "name": node["name"],
                            "type": node["type"],
                            "code": node.get("code", node["entity_id"]),
                            "label": node.get("label", "Entity"),
                            "properties": json.dumps(
                                node.get("properties", {}), ensure_ascii=False,
                            ),
                        }
                        tx.run(_MERGE_NODE_CYPHER, **params)
                    tx.commit()
                    nodes_written += len(batch)
                except Exception as exc:  # noqa: BLE001
                    tx.rollback()
                    logger.error("节点批失败（回退单条）: {}", exc)
                    recovered = self._fallback_single_nodes(session, batch, exc)
                    nodes_written += recovered

            # 2) 关系
            total_rels = len(self._graph["relations"])
            for i in range(0, total_rels, self._batch_size):
                batch = self._graph["relations"][i : i + self._batch_size]
                tx = session.begin_transaction()
                try:
                    for rel in batch:
                        params = {
                            "src_id": rel["src_id"],
                            "tgt_id": rel["tgt_id"],
                            "rel_type": rel["type"],
                            "relation_id": str(uuid.uuid4()),
                            "properties": json.dumps(
                                rel.get("properties", {}), ensure_ascii=False,
                            ),
                        }
                        tx.run(_MERGE_REL_CYPHER, **params)
                    tx.commit()
                    relations_written += len(batch)
                except Exception as exc:  # noqa: BLE001
                    tx.rollback()
                    logger.error("关系批失败（回退单条）: {}", exc)
                    recovered = self._fallback_single_rels(session, batch, exc)
                    relations_written += recovered

        logger.success(
            "SeedExtractor → Neo4j: {} 节点 / {} 关系",
            nodes_written, relations_written,
        )
        return {"nodes": nodes_written, "relations": relations_written}

    def _fallback_single_nodes(
        self, session: Any, batch: list[dict[str, Any]], last_exc: Exception,
    ) -> int:
        """节点批失败时单条回退。"""
        recovered = 0
        for node in batch:
            try:
                tx = session.begin_transaction()
                params = {
                    "entity_id": node["entity_id"],
                    "name": node["name"],
                    "type": node["type"],
                    "code": node.get("code", node["entity_id"]),
                    "label": node.get("label", "Entity"),
                    "properties": json.dumps(node.get("properties", {}), ensure_ascii=False),
                }
                tx.run(_MERGE_NODE_CYPHER, **params)
                tx.commit()
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                try:
                    tx.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.error("单条节点失败: {} err={}", node.get("entity_id"), exc)
        return recovered

    def _fallback_single_rels(
        self, session: Any, batch: list[dict[str, Any]], last_exc: Exception,
    ) -> int:
        """关系批失败时单条回退。"""
        recovered = 0
        for rel in batch:
            try:
                tx = session.begin_transaction()
                params = {
                    "src_id": rel["src_id"],
                    "tgt_id": rel["tgt_id"],
                    "rel_type": rel["type"],
                    "relation_id": str(uuid.uuid4()),
                    "properties": json.dumps(rel.get("properties", {}), ensure_ascii=False),
                }
                tx.run(_MERGE_REL_CYPHER, **params)
                tx.commit()
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                try:
                    tx.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.error(
                    "单条关系失败: {} → {} ({}) err={}",
                    rel.get("src_id"), rel.get("tgt_id"), rel.get("type"), exc,
                )
        return recovered

    # ── 写入：NetworkX ──────────────────────────────

    def write_to_networkx(self, graph: Any) -> int:
        """写入 NetworkX 图（用于降级 backend / 测试）。

        Args:
            graph: ``networkx.DiGraph`` 实例。

        Returns:
            写入的节点数。
        """
        if self._graph is None:
            self.build()
        assert self._graph is not None

        for node in self._graph["nodes"]:
            graph.add_node(
                node["entity_id"],
                name=node["name"],
                type=node["type"],
                code=node.get("code", node["entity_id"]),
                properties=node.get("properties", {}),
            )
        for rel in self._graph["relations"]:
            graph.add_edge(
                rel["src_id"],
                rel["tgt_id"],
                label=rel["type"],
                **{k: v for k, v in rel.get("properties", {}).items()},
            )
        n_written = len(self._graph["nodes"])
        logger.info(
            "SeedExtractor → NetworkX: {} 节点, {} 关系",
            n_written, len(self._graph["relations"]),
        )
        return n_written

    # ── 写入：SQLite（保留影子副本）────────────────────

    def save_to_sqlite(self, conn: Any) -> dict[str, int]:
        """写入 SQLite ``graph_entities`` / ``graph_relations`` 表（影子副本）。

        Args:
            conn: ``sqlite3.Connection`` 实例。

        Returns:
            ``{"nodes": N, "relations": M}`` 写入计数。
        """
        if self._graph is None:
            self.build()
        assert self._graph is not None

        nodes_written = 0
        relations_written = 0

        # 节点
        for node in self._graph["nodes"]:
            props_json = json.dumps(node.get("properties", {}), ensure_ascii=False)
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO graph_entities(entity_id, name, type, properties)
                VALUES (?, ?, ?, ?)
                """,
                (node["entity_id"], node["name"], node["type"], props_json),
            )
            nodes_written += cur.rowcount if cur.rowcount > 0 else 1
        # 关系
        for rel in self._graph["relations"]:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO graph_relations(source_id, target_id, relation_type)
                VALUES (?, ?, ?)
                """,
                (rel["src_id"], rel["tgt_id"], rel["type"]),
            )
            relations_written += cur.rowcount if cur.rowcount > 0 else 1
        conn.commit()

        logger.success(
            "SeedExtractor → SQLite: {} 节点 / {} 关系",
            nodes_written, relations_written,
        )
        return {"nodes": nodes_written, "relations": relations_written}


__all__ = [
    "SeedExtractor",
]