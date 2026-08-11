"""M-4 图谱问答编排层（GraphQAEngine）。

职责
----
把「实体抽取 → 图谱多跳扩展 → 结构化组装」编排成可直接下发给前端的
:class:`GraphAnswer`（节点/边/路径/置信度/backend/degraded/sources）。

数据流（架构 §1.2）::

    用户提问
      → RagEngine.answer()
      → GraphQAEngine.build(query, seed_ids=result.seed_ids, sources=answer.sources, hops=3)
          ├─ KGClient.expand_with_optimizer(seed_ids, hops=3, limit=100)   # 双 backend 自动降级
          ├─ 组装 GraphAnswerNode/Edge/Path（hop=最短距离、置信度公式）
          ├─ 载荷剪枝（nodes≤50 / edges≤120 / paths top_k=5）
          └─ GraphAnswer{backend, degraded, confidence, latency_ms, sources}

关键口径（架构 §7 共享知识 #2/#3/#4，禁止第二种口径）：
- ``degraded = (backend == "networkx") or 组装异常``——本环境 Neo4j 未启用，
  networkx 是**常态降级**，仅作为前端弱提示，不阻断问答；
- 置信度：seed=1.0；节点/路径 = ``max(0, 1 - 0.15*hop)``（与
  ``KGPathOptimizer.estimate_cost`` 一致）；边 = ``min(端点节点置信度)``；
  ``GraphAnswer.confidence`` = 路径置信度按 ``1/(hops+1)`` 加权平均
  （无路径有节点 → 0.85；仅 seed → 1.0；全空 → 0.0）；
- 载荷：nodes ≤ 50 / edges ≤ 120 / paths top_k=5；seed 节点必保留；
- 规则边：``rule_id`` 恒为 None（决策 3：规则推导边不启用）。

永不抛错：任何异常 → 返回空/degraded 的 :class:`GraphAnswer`（由调用方决定
是否 attach；``RagEngine.answer()`` 对全空结果不 attach → M-3 行为不变）。
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from api.schemas import (
    GraphAnswer,
    GraphAnswerEdge,
    GraphAnswerNode,
    GraphPath,
    SourceRef,
)
from core.rag_engine import extract_entity_ids


class GraphQAEngine:
    """图谱问答编排引擎（进程级单例，见 :func:`get_graph_qa_engine`）。"""

    #: 载荷上限（架构决策 4）
    MAX_NODES = 50
    MAX_EDGES = 120
    #: 默认扩展跳数（前端默认 3，超出截断并在面板标注）
    DEFAULT_HOPS = 3
    #: 每跳置信度惩罚（与 KGPathOptimizer.estimate_cost 一致）
    HOP_PENALTY = 0.15
    #: 扩展候选上限（传给 KGClient.expand_with_optimizer）
    EXPAND_LIMIT = 100
    #: DFS 重建路径的候选上限（防止稠密图爆炸）
    _MAX_DFS_PATHS = 40

    def __init__(self, client: Any | None = None) -> None:
        """初始化。

        Args:
            client: ``KGClient`` 实例；None 时懒加载进程级单例。
        """
        if client is None:
            from core.kg_client import get_kg_client

            client = get_kg_client()
        self.client = client

    # ── 主入口 ─────────────────────────────────────────

    def build(
        self,
        query: str,
        seed_ids: list[str] | None = None,
        sources: list[SourceRef] | None = None,
        hops: int = DEFAULT_HOPS,
        top_k: int = 5,
    ) -> GraphAnswer:
        """组装 :class:`GraphAnswer`。**永不抛错**。

        Args:
            query: 用户问题（seed_ids 为空时用于实体抽取）。
            seed_ids: 本轮实体抽取的 seed（``RagEngine.answer()`` 显式传入
                ``result.seed_ids`` 保证与检索同源，US-1「同源」）。
            sources: 同轮 :class:`KnowledgeAnswer` 的 sources（US-5 同源/子集）。
            hops: 扩展跳数（上限 3，超出截断）。
            top_k: 路径返回上限（默认 5）。

        Returns:
            :class:`GraphAnswer`；无 seed / 异常 → 空或 degraded 的结果
            （调用方据此决定是否 attach，全空 → ``graph_answer=None``）。
        """
        start = time.perf_counter()
        sources = list(sources or [])
        backend = self._backend_name()
        degraded = backend == "networkx"
        try:
            seeds = self._resolve_seeds(query, seed_ids)
            if not seeds:
                # 无 seed → 空 GraphAnswer（不 attach）
                return GraphAnswer(
                    backend=backend,
                    degraded=degraded,
                    latency_ms=round((time.perf_counter() - start) * 1000, 2),
                    sources=sources,
                )

            entities, opt_paths = self.client.expand_with_optimizer(
                list(seeds), hops=int(hops), limit=self.EXPAND_LIMIT,
            )
            # 收集实体（含 seed 补全：NetworkX expand 已含 seed；Neo4j 兜底补）
            nodes_by_id = self._collect_entities(entities, seeds)

            # 组装边（含 NetworkX 真实关系补全）
            answer_edges = self._assemble_edges(opt_paths, nodes_by_id)

            # 邻接（用于 hop BFS 与路径重建）
            adjacency = self._edges_to_adjacency(answer_edges)

            # hop = 距任一 seed 的最短距离（BFS；seed=0）
            hops_by_id = self._compute_hops(seeds, adjacency)

            # 边置信度 = min(端点节点置信度)——hop 已知后回填
            for eid, raw in nodes_by_id.items():
                raw["_hop"] = hops_by_id.get(eid)
            for e in answer_edges:
                e.confidence = self._edge_confidence(e.source, e.target, nodes_by_id)

            # 组装节点（hop + confidence + doc_ids）
            answer_nodes = self._assemble_nodes(nodes_by_id, hops_by_id, sources)

            # 组装路径（优先 OptimizedPath；NetworkX 占位路径则 DFS 重建）
            answer_paths = self._assemble_paths(
                opt_paths, adjacency, seeds, hops, answer_nodes, top_k,
            )

            # 载荷剪枝（nodes≤50 / edges≤120 / paths top_k=5，seed 必保留）
            answer_nodes, answer_edges, answer_paths = self._prune(
                answer_nodes, answer_edges, answer_paths, top_k,
            )

            confidence = self._overall_confidence(answer_paths, answer_nodes)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            return GraphAnswer(
                nodes=answer_nodes,
                edges=answer_edges,
                paths=answer_paths,
                seed_ids=list(seeds),
                confidence=confidence,
                backend=backend,
                degraded=degraded,
                latency_ms=latency_ms,
                sources=sources,
            )
        except Exception as exc:  # noqa: BLE001 — 异常不阻断 RAG
            logger.warning("GraphQAEngine.build 异常，返回降级空 GraphAnswer: {}", exc)
            return GraphAnswer(
                backend=backend,
                degraded=True,
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
                sources=sources,
            )

    # ── 组装规则 ────────────────────────────────────────

    def _resolve_seeds(self, query: str, seed_ids: list[str] | None) -> list[str]:
        """seed 解析：显式传入优先；否则用公开 util ``extract_entity_ids``。"""
        if seed_ids:
            return [s for s in seed_ids if s]
        if query:
            return extract_entity_ids(query)
        return []

    def _backend_name(self) -> str:
        try:
            return str(getattr(self.client, "current_backend_name", "networkx"))
        except Exception:  # noqa: BLE001
            return "networkx"

    def _collect_entities(
        self, entities: list[dict[str, Any]], seeds: list[str],
    ) -> dict[str, dict[str, Any]]:
        """实体收集：去重 + seed 补全（seed 不存在于扩展结果时按需查询）。"""
        nodes_by_id: dict[str, dict[str, Any]] = {}
        for e in entities or []:
            eid = e.get("id") or e.get("entity_id")
            if not eid:
                continue
            nodes_by_id[eid] = {
                "id": eid,
                "name": e.get("name") or eid,
                "type": e.get("type") or "unknown",
                "properties": e.get("properties") or {},
            }
        for sid in seeds:
            if sid in nodes_by_id:
                continue
            try:
                ent = self.client.get_entity(sid)
            except Exception:  # noqa: BLE001
                ent = None
            if ent:
                nodes_by_id[sid] = {
                    "id": sid,
                    "name": ent.get("name") or sid,
                    "type": ent.get("type") or "unknown",
                    "properties": ent.get("properties") or {},
                }
        return nodes_by_id

    def _assemble_edges(
        self, opt_paths: list[Any], nodes_by_id: dict[str, dict[str, Any]],
    ) -> list[GraphAnswerEdge]:
        """从 :class:`OptimizedPath` 重建边（架构 §1.2 #4）。

        - 优先从 ``nodes[i]→nodes[i+1] + relations[i]`` 重建；
        - 对 NetworkX（optimizer 仅返回占位路径，见 ``KGPathOptimizer`` 现状），
          额外用 ``client.get_relations`` 补全**真实关系边**（仅保留两端都在
          节点集内的边，保证 NetworkX 下图谱图正常渲染）；
        - 按 ``(source, target, relation_type)`` 去重；
        - ``confidence = min(端点节点置信度)``。
        """
        edges: list[GraphAnswerEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add(src: str, tgt: str, label: str) -> None:
            if not src or not tgt or src == tgt:
                return
            key = (src, tgt, label or "关联")
            if key in seen:
                return
            seen.add(key)
            edges.append(GraphAnswerEdge(
                source=src,
                target=tgt,
                relation_type=label or "关联",
                confidence=self._edge_confidence(src, tgt, nodes_by_id),
            ))

        # 1) OptimizedPath 重建
        for p in opt_paths or []:
            p_nodes = list(getattr(p, "nodes", None) or [])
            p_rels = list(getattr(p, "relations", None) or [])
            if len(p_nodes) >= 2:
                for i in range(len(p_nodes) - 1):
                    label = p_rels[i] if i < len(p_rels) and p_rels[i] else "关联"
                    add(p_nodes[i], p_nodes[i + 1], label)

        # 2) NetworkX 补全：真实关系边（两端都在节点集内）
        # ⚠️ A3 遗留修复（N+1 风险说明）：
        #   当前默认走 NetworkX 内存图（``KGClient.get_relations`` → 内存 dict 查边），
        #   节点集通常 ≤ 数十个，单次 O(1) 查询，整体无感，保持现状即可。
        #   若后续 NEO4J_ENABLED=true 启用 Bolt 后端，此处对每个节点一次
        #   ``get_relations`` 会退化为 N+1 次 Cypher 往返 —— 优化建议：
        #   在 KGClient/Neo4jBackend 增加一个批量方法（如 ``get_relations_bulk``），
        #   一次 Cypher ``MATCH (n)-[r]->(m) WHERE n.id IN $ids RETURN ...``
        #   取回节点集内全部关系，再在本地按 ``source_id/target_id`` 过滤，
        #   可把复杂度从 O(N) 次往返降为 O(1) 次往返。当前 NetworkX 路径无需改动。
        for nid in nodes_by_id:
            try:
                rels = self.client.get_relations(nid)
            except Exception:  # noqa: BLE001
                rels = []
            for r in rels or []:
                src = r.get("source_id") or r.get("source")
                tgt = r.get("target_id") or r.get("target")
                if src in nodes_by_id and tgt in nodes_by_id:
                    add(src, tgt, r.get("relation_type") or "关联")
        return edges

    @staticmethod
    def _edge_confidence(
        src: str, tgt: str, nodes_by_id: dict[str, dict[str, Any]],
    ) -> float | None:
        """边置信度 = min(端点节点置信度)（节点置信度按 hop 计算）。"""
        confs: list[float] = []
        for nid in (src, tgt):
            node = nodes_by_id.get(nid)
            if not node:
                continue
            hop = node.get("_hop")
            if hop is None:
                continue
            confs.append(max(0.0, 1.0 - 0.15 * hop) if hop > 0 else 1.0)
        if not confs:
            return None
        return round(min(confs), 3)

    @staticmethod
    def _edges_to_adjacency(
        edges: list[GraphAnswerEdge],
    ) -> dict[str, list[tuple[str, str]]]:
        """边集 → 邻接表 {node: [(target, relation_type), ...]}。"""
        adj: dict[str, list[tuple[str, str]]] = {}
        for e in edges:
            adj.setdefault(e.source, []).append((e.target, e.relation_type))
        return adj

    @staticmethod
    def _compute_hops(
        seeds: list[str], adjacency: dict[str, list[tuple[str, str]]],
    ) -> dict[str, int]:
        """BFS 最短距离：seed=0；不可达节点 hop=None（组装时转 None）。"""
        hops: dict[str, int] = {}
        queue: list[tuple[str, int]] = [(s, 0) for s in seeds]
        for nid, h in queue:
            if nid in hops and hops[nid] <= h:
                continue
            hops[nid] = h
            for tgt, _ in adjacency.get(nid, []):
                if tgt not in hops or hops[tgt] > h + 1:
                    queue.append((tgt, h + 1))
        return hops

    def _assemble_nodes(
        self,
        nodes_by_id: dict[str, dict[str, Any]],
        hops_by_id: dict[str, int],
        sources: list[SourceRef],
    ) -> list[GraphAnswerNode]:
        """组装节点：hop / confidence / doc_ids。"""
        nodes: list[GraphAnswerNode] = []
        for eid, raw in nodes_by_id.items():
            hop = hops_by_id.get(eid)
            nodes.append(GraphAnswerNode(
                id=eid,
                name=raw.get("name") or eid,
                type=raw.get("type") or "unknown",
                properties=raw.get("properties") or {},
                hop=hop,
                doc_ids=self._resolve_doc_ids(
                    raw.get("name") or eid, raw.get("type") or "", sources,
                ),
                confidence=self._confidence_for_hop(hop),
            ))
        return nodes

    def _assemble_paths(
        self,
        opt_paths: list[Any],
        adjacency: dict[str, list[tuple[str, str]]],
        seeds: list[str],
        hops: int,
        nodes: list[GraphAnswerNode],
        top_k: int,
    ) -> list[GraphPath]:
        """组装路径：优先 OptimizedPath；不足时 DFS 重建（NetworkX 常态）。"""
        node_ids = {n.id for n in nodes}
        paths: list[GraphPath] = []
        seen_chains: set[tuple[str, ...]] = set()

        def add_chain(chain: list[str], rels: list[str]) -> None:
            if len(chain) < 2:
                return
            key = tuple(chain)
            if key in seen_chains:
                return
            seen_chains.add(key)
            p_hops = len(chain) - 1
            paths.append(GraphPath(
                nodes=list(chain),
                relations=list(rels),
                hops=p_hops,
                confidence=self._confidence_for_hop(p_hops),
            ))

        # 1) OptimizedPath（NetworkX 占位路径通常只有 1 个节点，跳过）
        for p in opt_paths or []:
            p_nodes = list(getattr(p, "nodes", None) or [])
            p_rels = list(getattr(p, "relations", None) or [])
            if len(p_nodes) >= 2 and all(nid in node_ids for nid in p_nodes):
                add_chain(p_nodes, p_rels[: len(p_nodes) - 1])

        # 2) DFS 重建（从每个 seed 出发，≤hops 跳，防环 + 候选上限）
        if len(paths) < max(1, int(top_k)) or not seen_chains:
            for seed in seeds:
                if len(seen_chains) >= self._MAX_DFS_PATHS:
                    break
                self._dfs_paths(
                    seed, adjacency, int(hops),
                    lambda chain, rels: add_chain(chain, rels),
                )

        # 3) 按置信度降序 + 跳数升序，截断 top_k
        paths.sort(
            key=lambda p: (-p.confidence, p.hops),
        )
        return paths[: max(1, int(top_k))]

    def _dfs_paths(
        self,
        seed: str,
        adjacency: dict[str, list[tuple[str, str]]],
        hops: int,
        emit: Any,
    ) -> None:
        """DFS 枚举从 seed 出发 ≤hops 跳的简单路径（无环；交给 emit 回调）。"""
        collected: list[tuple[list[str], list[str]]] = []

        def dfs(node: str, chain: list[str], rels: list[str]) -> None:
            if len(chain) > 1:
                collected.append((list(chain), list(rels)))
            if len(chain) > hops or len(collected) >= self._MAX_DFS_PATHS:
                return
            for nxt, label in adjacency.get(node, []):
                if nxt in chain:
                    continue
                chain.append(nxt)
                rels.append(label)
                dfs(nxt, chain, rels)
                rels.pop()
                chain.pop()

        dfs(seed, [seed], [])
        for chain, rels in collected:
            emit(chain, rels)

    # ── 剪枝 / 置信度（架构 §7 共享知识 #3/#4）────────────

    def _prune(
        self,
        nodes: list[GraphAnswerNode],
        edges: list[GraphAnswerEdge],
        paths: list[GraphPath],
        top_k: int,
    ) -> tuple[list[GraphAnswerNode], list[GraphAnswerEdge], list[GraphPath]]:
        """载荷剪枝：paths 按 confidence 降序取 top_k；nodes ≤ 50（seed 必保留 +
        小 hop 优先）；edges ≤ 120（仅保留两端在保活节点集内的边，高置信度优先）。"""
        # paths
        sorted_paths = sorted(paths, key=lambda p: p.confidence, reverse=True)
        sorted_paths = sorted_paths[: max(1, int(top_k))]

        # nodes：seed（hop==0）必保留，其余 hop 升序 + confidence 降序
        seed_ids = [n.id for n in nodes if n.hop == 0]
        kept_ids: list[str] = []
        for nid in seed_ids:
            if nid not in kept_ids:
                kept_ids.append(nid)
        others = sorted(
            [n for n in nodes if n.id not in kept_ids],
            key=lambda n: (
                n.hop if n.hop is not None else 999,
                -(n.confidence if n.confidence is not None else 0.0),
            ),
        )
        for n in others:
            if len(kept_ids) >= self.MAX_NODES:
                break
            kept_ids.append(n.id)
        kept_set = set(kept_ids)
        kept_nodes = [n for n in nodes if n.id in kept_set]

        # edges：两端都在 kept 节点内，高置信度优先
        kept_edges = [e for e in edges if e.source in kept_set and e.target in kept_set]
        kept_edges.sort(
            key=lambda e: e.confidence if e.confidence is not None else -1.0,
            reverse=True,
        )
        kept_edges = kept_edges[: self.MAX_EDGES]

        # paths：节点都在 kept 节点内
        kept_paths = [
            p for p in sorted_paths if all(nid in kept_set for nid in p.nodes)
        ]
        return kept_nodes, kept_edges, kept_paths

    def _confidence_for_hop(self, hop: int | None) -> float:
        """seed=1.0；其余 max(0, 1 - 0.15*hop)（与 KGPathOptimizer 一致）。"""
        if hop is None or hop < 0:
            return 0.0
        if hop == 0:
            return 1.0
        return round(max(0.0, 1.0 - self.HOP_PENALTY * hop), 3)

    def _resolve_doc_ids(
        self, entity_name: str, entity_type: str, sources: list[SourceRef],
    ) -> list[str]:
        """实体 → doc_id：名称/类型与 sources[].title/filename/source 子串匹配。

        无匹配返回空列表（P1-4 协同，US-5 仅要求 GraphAnswer.sources 同源，
        节点级 doc_ids 可为空——前端据此降级展示）。
        """
        doc_ids: list[str] = []
        name_norm = (entity_name or "").strip().lower()
        type_norm = (entity_type or "").strip().lower()
        for s in sources:
            if not s.doc_id:
                continue
            haystack = " ".join([
                str(s.title or ""),
                str(s.filename or ""),
                str(s.source or ""),
            ]).lower()
            matched = False
            if name_norm and name_norm in haystack:
                matched = True
            elif type_norm and len(type_norm) >= 2 and type_norm in haystack:
                matched = True
            if matched and s.doc_id not in doc_ids:
                doc_ids.append(s.doc_id)
        return doc_ids

    @staticmethod
    def _overall_confidence(
        paths: list[GraphPath], nodes: list[GraphAnswerNode],
    ) -> float:
        """综合置信度：路径按 1/(hops+1) 加权平均；无路径有节点 → 0.85；
        仅 seed → 1.0；全空 → 0.0（架构决策 2 / §7 #4）。"""
        if paths:
            total_w = 0.0
            weighted = 0.0
            for p in paths:
                w = 1.0 / (p.hops + 1)
                weighted += w * p.confidence
                total_w += w
            if total_w <= 0:
                return 0.0
            return round(weighted / total_w, 3)
        if nodes:
            if all(n.hop == 0 for n in nodes):
                return 1.0
            return 0.85
        return 0.0


# ─────────────────────────────────────────────────────────────
# 单例工厂（与 get_kg_client 同模式）
# ─────────────────────────────────────────────────────────────

_graph_qa_engine: GraphQAEngine | None = None


def get_graph_qa_engine() -> GraphQAEngine:
    """获取进程级 :class:`GraphQAEngine` 单例。"""
    global _graph_qa_engine
    if _graph_qa_engine is None:
        _graph_qa_engine = GraphQAEngine()
    return _graph_qa_engine


def reset_graph_qa_engine() -> None:
    """重置单例（仅测试用）。"""
    global _graph_qa_engine
    _graph_qa_engine = None


__all__ = [
    "GraphQAEngine",
    "get_graph_qa_engine",
    "reset_graph_qa_engine",
]
