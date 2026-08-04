"""KRAG / GraphRAG 知识图谱——基于 NetworkX 内存图。

从 SQLite 持久化层加载实体-关系，构建内存 NetworkX 有向图，
提供实体/关系查询、多跳（1–2 hop）扩展与路径检索能力。

M0 升级说明（知识图谱 Neo4j 升级 · M0 阶段）
--------------------------------------------
本文件 **保留不变** 作为降级 backend 兼容垫片。M0 新增能力通过
``core/kg_client.py`` 暴露（``KGClient`` / ``NetworkXBackend`` / ``Neo4jBackend``），
调用方通过 ``get_kg_client()`` 获取统一客户端，无需修改任何 import。
"""

from __future__ import annotations

import json
from typing import Any

import networkx as nx
from loguru import logger

from api.schemas import GraphEntity, GraphRelation
from mcp_tools.db.database import get_connection


class KnowledgeGraph:
    """电力知识图谱（NetworkX 内存有向图）。"""

    def __init__(self, load_on_init: bool = True) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        if load_on_init:
            self._load_from_db()

    # ── 公开查询 ─────────────────────────────────────

    def get_entity(self, entity_id: str) -> GraphEntity | None:
        """按 ID 查实体。"""
        if entity_id not in self.graph:
            return None
        data = self.graph.nodes[entity_id]
        return GraphEntity(
            id=entity_id,
            name=data.get("name", entity_id),
            type=data.get("type", "unknown"),
            properties=data.get("properties", {}),
        )

    def search_entities(self, query: str, type_filter: str | None = None) -> list[GraphEntity]:
        """按名称模糊搜索实体。"""
        results: list[GraphEntity] = []
        q = query.lower()
        for node_id, data in self.graph.nodes(data=True):
            name: str = data.get("name", "")
            if q in name.lower():
                if type_filter and data.get("type") != type_filter:
                    continue
                results.append(GraphEntity(
                    id=node_id,
                    name=name,
                    type=data.get("type", "unknown"),
                    properties=data.get("properties", {}),
                ))
        return results

    def get_relations(self, entity_id: str) -> list[GraphRelation]:
        """获取实体的所有出边关系。"""
        if entity_id not in self.graph:
            return []
        rels: list[GraphRelation] = []
        for _, tgt, data in self.graph.out_edges(entity_id, data=True):
            rels.append(GraphRelation(
                source_id=entity_id,
                target_id=tgt,
                relation_type=data.get("label", "关联"),
            ))
        return rels

    def expand_entities(
        self, seed_entity_ids: list[str], hops: int = 2,
    ) -> tuple[list[GraphEntity], list[list[str]]]:
        """沿边做 1–2 跳扩展，返回扩展实体集与路径列表。"""
        visited: set[str] = set(seed_entity_ids)
        current = set(seed_entity_ids)
        paths: list[list[str]] = [[e] for e in seed_entity_ids]

        for _ in range(hops):
            next_set: set[str] = set()
            new_paths: list[list[str]] = []
            for node_id in current:
                for _, tgt in self.graph.out_edges(node_id):
                    if tgt not in visited:
                        next_set.add(tgt)
                        visited.add(tgt)
                        # 追加路径
                        base = [p for p in paths if p[-1] == node_id]
                        for p in base:
                            new_paths.append(p + [tgt])
                for src, _ in self.graph.in_edges(node_id):
                    if src not in visited:
                        next_set.add(src)
                        visited.add(src)
                        base = [p for p in paths if p[-1] == node_id]
                        for p in base:
                            new_paths.append([src] + p)
            current = next_set
            paths.extend(new_paths)
            if not current:
                break

        entities = [GraphEntity(
            id=nid,
            name=self.graph.nodes[nid].get("name", nid),
            type=self.graph.nodes[nid].get("type", "unknown"),
            properties=self.graph.nodes[nid].get("properties", {}),
        ) for nid in visited]

        path_strs: list[list[str]] = []
        for p in paths:
            labeled = []
            for i in range(len(p) - 1):
                src, tgt = p[i], p[i + 1]
                label = "关联"
                if self.graph.has_edge(src, tgt):
                    label = self.graph.edges[src, tgt].get("label", "→")
                elif self.graph.has_edge(tgt, src):
                    label = self.graph.edges[tgt, src].get("label", "←")
                labeled.append(f"{self._name(src)}--[{label}]-->")
            labeled.append(self._name(p[-1]))
            path_strs.append(labeled)

        return entities, path_strs

    def get_all_entities(self) -> list[GraphEntity]:
        """返回图中全部实体。"""
        return [GraphEntity(
            id=nid,
            name=data.get("name", nid),
            type=data.get("type", "unknown"),
            properties=data.get("properties", {}),
        ) for nid, data in self.graph.nodes(data=True)]

    # ── 构建 ─────────────────────────────────────────

    def add_entity(self, entity: GraphEntity) -> None:
        self.graph.add_node(
            entity.id,
            name=entity.name,
            type=entity.type,
            properties=entity.properties,
        )

    def add_relation(self, relation: GraphRelation) -> None:
        self.graph.add_edge(
            relation.source_id,
            relation.target_id,
            label=relation.relation_type,
        )

    # ── 内部 ─────────────────────────────────────────

    def _load_from_db(self) -> None:
        """从 SQLite 加载实体与关系到内存 NetworkX 图。"""
        conn = get_connection()
        try:
            # 加载实体
            rows = conn.execute(
                "SELECT entity_id, name, type, properties FROM graph_entities"
            ).fetchall()
            for r in rows:
                props = json.loads(r["properties"]) if r["properties"] else {}
                self.graph.add_node(
                    r["entity_id"],
                    name=r["name"],
                    type=r["type"],
                    properties=props,
                )

            # 加载关系
            rels = conn.execute(
                "SELECT source_id, target_id, relation_type FROM graph_relations"
            ).fetchall()
            for r in rels:
                self.graph.add_edge(
                    r["source_id"],
                    r["target_id"],
                    label=r["relation_type"],
                )

            logger.info("KnowledgeGraph loaded: {} entities, {} relations",
                         self.graph.number_of_nodes(), self.graph.number_of_edges())
        finally:
            conn.close()

    def _name(self, entity_id: str) -> str:
        return self.graph.nodes[entity_id].get("name", entity_id) if entity_id in self.graph else entity_id

    # ── M0 兼容垫片：委托给 NetworkXBackend ─────────────
    # 允许现有调用方 `from core.knowledge_graph import KnowledgeGraph` 继续工作；
    # 同时支持 `from core.kg_client import get_kg_client` 走统一接口。
    # 注意：本类行为完全未变；新增的自动 backend 切换 / 降级能力在 kg_client 中实现。

    def to_backend(self) -> "Any":
        """转换为 NetworkXBackend 适配器（用于统一接口调用）。

        Returns:
            ``core.kg_client.NetworkXBackend`` 实例。
        """
        from core.kg_client import NetworkXBackend

        return NetworkXBackend()


# 模块级导出：允许 `from core.knowledge_graph import KnowledgeGraph` 不变
# 同时也允许 `from core.knowledge_graph import get_kg_client` 走统一入口
def get_kg_client() -> "Any":
    """兼容垫片：获取 KGClient 单例（M0 升级后推荐入口）。

    等价于 ``core.kg_client.get_kg_client``。
    """
    from core.kg_client import get_kg_client as _get

    return _get()
