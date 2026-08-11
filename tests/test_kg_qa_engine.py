"""M-4 · T02 后端图谱问答编排层测试。

覆盖（架构 §五 T02 验收标准）：
- GraphQAEngine.build（NetworkX 路径）：nodes/edges/paths 非空；seed hop=0 +
  1/2 跳可达节点；edges 每条含 relation_type 且 rule_id 恒 None；paths ≥ 1
  且与 nodes 同源；paths[].confidence 满足 max(0, 1-0.15*hops)；
  backend=networkx → degraded=true；
- 载荷剪枝（>50 节点 / >120 边 → 截断，seed 必保留；paths top_k=5）；
- 综合置信度口径（1/(hops+1) 加权；仅 seed → 1.0；全空 → 0.0）；
- RagEngine.answer() 正常路径产出带 graph_answer 的 KnowledgeAnswer
  （懒加载防循环 + 异常不阻断 + seed 空不 attach）；
- query_knowledge_base model_dump 含 graph_answer；_extract_knowledge_answer_from_results
  能反解（零额外管道）；
- mock 三剧本（油温/过载/停机检修）graph_answer 非空且 sources 与
  _MOCK_KNOWLEDGE_SOURCES 同源（US-5）；fallback 无 graph_answer；
- AGENT_TOOLS_MAP.knowledge_agent 含 2 个新工具；inference_engine_enabled=False
  时 kg_apply_rules 返回空（无规则边）。

运行：
    python3 -m pytest tests/test_kg_qa_engine.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.schemas import (
    GraphEntity,
    GraphRelation,
    KnowledgeAnswer,
    RetrievalResult,
    SourceRef,
)
from core.knowledge_graph import KnowledgeGraph
from core.kg_qa import GraphQAEngine
from core.rag_engine import RagEngine


# ─────────────────────────────────────────────────────────────
# 测试替身
# ─────────────────────────────────────────────────────────────

def _build_test_graph() -> KnowledgeGraph:
    """小型 NetworkX 测试图（过载 → 温度升高 → 绝缘老化 + 处置 + 设备）。"""
    kg = KnowledgeGraph(load_on_init=False)
    kg.add_entity(GraphEntity(id="e-transformer", name="变压器", type="设备", properties={}))
    kg.add_entity(GraphEntity(id="e-overload", name="过载", type="故障", properties={"severity": "high"}))
    kg.add_entity(GraphEntity(id="e-overtemp", name="温度升高", type="故障", properties={}))
    kg.add_entity(GraphEntity(id="e-insulation", name="绝缘老化", type="故障", properties={}))
    kg.add_entity(GraphEntity(id="e-derating", name="减载措施", type="处置", properties={}))
    kg.add_relation(GraphRelation(source_id="e-transformer", target_id="e-overload", relation_type="可能发生"))
    kg.add_relation(GraphRelation(source_id="e-overload", target_id="e-overtemp", relation_type="触发"))
    kg.add_relation(GraphRelation(source_id="e-overtemp", target_id="e-insulation", relation_type="加速"))
    kg.add_relation(GraphRelation(source_id="e-overload", target_id="e-derating", relation_type="处置"))
    return kg


class _FakeKGClient:
    """最小 KGClient 替身（NetworkX 行为：opt_paths 为空 → 走关系补全 + DFS）。"""

    current_backend_name = "networkx"

    def __init__(self, kg: KnowledgeGraph) -> None:
        self._kg = kg

    @staticmethod
    def _e2d(e: GraphEntity) -> dict:
        return {"id": e.id, "name": e.name, "type": e.type, "properties": e.properties or {}}

    def get_entity(self, entity_id: str) -> dict | None:
        e = self._kg.get_entity(entity_id)
        return self._e2d(e) if e else None

    def get_relations(self, entity_id: str) -> list[dict]:
        return [
            {"source_id": r.source_id, "target_id": r.target_id, "relation_type": r.relation_type}
            for r in self._kg.get_relations(entity_id)
        ]

    def expand_with_optimizer(self, seeds, hops=3, relation_types=None, limit=100):
        # 模拟 NetworkX 优化器现状：entities 完整、opt_paths 为空（占位）
        entities, _ = self._kg.expand_entities(list(seeds), hops=hops)
        return [self._e2d(e) for e in entities], []


class _FakeNeo4jClient(_FakeKGClient):
    """Neo4j 行为替身：返回真实 OptimizedPath（nodes/relations 完整）。"""

    current_backend_name = "neo4j"

    def expand_with_optimizer(self, seeds, hops=3, relation_types=None, limit=100):
        from core.kg_path_optimizer import OptimizedPath, PathCost

        entities, _ = self._kg.expand_entities(list(seeds), hops=hops)
        opt_paths = [
            OptimizedPath(
                nodes=["e-overload", "e-overtemp", "e-insulation"],
                relations=["触发", "加速"],
                cost=PathCost(hops=2, edge_count=2, estimated_latency_ms=1.0, confidence=0.7),
                backend="neo4j",
            ),
            OptimizedPath(
                nodes=["e-overload", "e-derating"],
                relations=["处置"],
                cost=PathCost(hops=1, edge_count=1, estimated_latency_ms=1.0, confidence=0.85),
                backend="neo4j",
            ),
        ]
        return [self._e2d(e) for e in entities], opt_paths


class _FakeRagEngine(RagEngine):
    """RagEngine 替身：retrieve/_generate 短路（不依赖 DB / LLM）。"""

    def __init__(self, result: RetrievalResult) -> None:
        super().__init__()
        self._stub_result = result

    def retrieve(self, query, top_k=3, thread_id="default"):  # type: ignore[override]
        return self._stub_result

    def _generate(self, query, context):  # type: ignore[override]
        return "模板答案"


# ─────────────────────────────────────────────────────────────
# 1. GraphQAEngine.build（NetworkX 路径）
# ─────────────────────────────────────────────────────────────

def test_build_networkx_nonempty() -> None:
    engine = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    ga = engine.build("变压器过载会影响哪些设备", seed_ids=["e-overload"])
    assert ga.nodes, "nodes 不应为空"
    assert ga.edges, "edges 不应为空"
    assert ga.paths, "paths 不应为空"
    assert ga.backend == "networkx"
    assert ga.degraded is True, "networkx 常态降级 → degraded=true（弱提示不阻断）"

    # seed 至少 1 个（hop=0）
    seeds = [n for n in ga.nodes if n.hop == 0]
    assert any(n.id == "e-overload" for n in seeds)
    # 1/2 跳可达节点
    assert any(n.hop == 1 for n in ga.nodes)
    assert any(n.hop == 2 for n in ga.nodes)

    # edges 每条含 relation_type；规则边恒空（决策 3）
    for e in ga.edges:
        assert e.relation_type, "edge.relation_type 不应为空"
        assert e.rule_id is None, "本批 rule_id 恒为 None"

    # paths ≥ 1；confidence 满足 max(0, 1-0.15*hops)；与 nodes 同源
    node_ids = {n.id for n in ga.nodes}
    for p in ga.paths:
        assert p.hops >= 1
        assert p.confidence == round(max(0.0, 1.0 - 0.15 * p.hops), 3)
        assert all(nid in node_ids for nid in p.nodes), "paths 与 nodes 必须同源"
        assert len(p.relations) == p.hops


def test_build_neo4j_path_uses_optimizer() -> None:
    """Neo4j 路径：优先使用 OptimizedPath（edges/paths 直接映射）。"""
    engine = GraphQAEngine(client=_FakeNeo4jClient(_build_test_graph()))
    ga = engine.build("变压器过载", seed_ids=["e-overload"])
    assert ga.backend == "neo4j"
    assert ga.degraded is False, "neo4j 非降级"
    assert ga.edges
    assert ga.paths
    # 路径直接来自 OptimizedPath（含 2 跳链）
    assert any(p.hops == 2 for p in ga.paths)
    assert any(p.hops == 1 for p in ga.paths)
    for e in ga.edges:
        assert e.relation_type
        assert e.rule_id is None


def test_build_no_seed_returns_empty() -> None:
    engine = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    ga = engine.build("", seed_ids=None)
    assert ga.nodes == []
    assert ga.edges == []
    assert ga.paths == []
    assert ga.seed_ids == []
    assert ga.confidence == 0.0


def test_build_exception_never_raises(monkeypatch) -> None:
    """组装异常 → 返回 degraded 空 GraphAnswer（不抛错，不阻断 RAG）。"""
    engine = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    monkeypatch.setattr(
        engine.client, "expand_with_optimizer",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ga = engine.build("变压器过载", seed_ids=["e-overload"])
    assert ga.nodes == []
    assert ga.degraded is True


def test_confidence_formula() -> None:
    engine = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    assert engine._confidence_for_hop(0) == 1.0
    assert engine._confidence_for_hop(1) == 0.85
    assert engine._confidence_for_hop(2) == 0.7
    assert engine._confidence_for_hop(3) == 0.55
    assert engine._confidence_for_hop(10) == 0.0
    assert engine._confidence_for_hop(None) == 0.0


# ─────────────────────────────────────────────────────────────
# 2. 载荷剪枝（决策 4）
# ─────────────────────────────────────────────────────────────

def test_prune_limits_nodes_edges_paths() -> None:
    """构造 >50 节点 / >120 边 → 截断且 seed 必保留。"""
    kg = KnowledgeGraph(load_on_init=False)
    kg.add_entity(GraphEntity(id="e-seed", name="seed", type="故障", properties={}))
    for i in range(60):
        kg.add_entity(GraphEntity(id=f"n{i}", name=f"节点{i}", type="设备", properties={}))
    # 边：seed→60 节点 + 节点间密集边（>120）
    for i in range(60):
        kg.add_relation(GraphRelation(source_id="e-seed", target_id=f"n{i}", relation_type="关联"))
    for i in range(59):
        kg.add_relation(GraphRelation(source_id=f"n{i}", target_id=f"n{i + 1}", relation_type="关联"))
    for i in range(58):
        kg.add_relation(GraphRelation(source_id=f"n{i}", target_id=f"n{i + 2}", relation_type="关联"))

    engine = GraphQAEngine(client=_FakeKGClient(kg))
    ga = engine.build("seed", seed_ids=["e-seed"])
    assert len(ga.nodes) <= GraphQAEngine.MAX_NODES
    assert len(ga.edges) <= GraphQAEngine.MAX_EDGES
    assert len(ga.paths) <= 5
    # seed 必保留
    assert any(n.id == "e-seed" for n in ga.nodes)
    # 剪枝后路径节点都在节点集内
    node_ids = {n.id for n in ga.nodes}
    for p in ga.paths:
        assert all(nid in node_ids for nid in p.nodes)


def test_overall_confidence_weighted() -> None:
    """综合置信度 = 路径按 1/(hops+1) 加权平均；仅 seed → 1.0；全空 → 0.0。"""
    from api.schemas import GraphAnswerNode, GraphPath

    engine = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    # 仅 seed
    assert engine._overall_confidence([], [GraphAnswerNode(id="s", name="s", type="故障", hop=0, confidence=1.0)]) == 1.0
    # 无路径但有非 seed 节点
    assert engine._overall_confidence([], [GraphAnswerNode(id="s", name="s", type="故障", hop=0, confidence=1.0),
                                           GraphAnswerNode(id="n", name="n", type="故障", hop=1, confidence=0.85)]) == 0.85
    # 全空
    assert engine._overall_confidence([], []) == 0.0
    # 加权：hops=1 (w=1/2, c=0.85) + hops=2 (w=1/3, c=0.7)
    paths = [
        GraphPath(nodes=["a", "b"], relations=["r"], hops=1, confidence=0.85),
        GraphPath(nodes=["a", "b", "c"], relations=["r", "r"], hops=2, confidence=0.7),
    ]
    expected = round((0.85 * 1 / 2 + 0.7 * 1 / 3) / (1 / 2 + 1 / 3), 3)
    assert engine._overall_confidence(paths, []) == expected


# ─────────────────────────────────────────────────────────────
# 3. RagEngine.answer() 组装 graph_answer
# ─────────────────────────────────────────────────────────────

def _sample_result(seed_ids: list[str]) -> RetrievalResult:
    return RetrievalResult(
        vector_chunks=["片段1"],
        graph_entities=[],
        graph_paths=[["e-overload", "e-overtemp"]],
        confidence=0.9,
        sources=[SourceRef(doc_id="d1", title="变压器运行规程", filename="变压器运行规程.md", score=0.9)],
        seed_ids=seed_ids,
    )


def test_answer_attaches_graph_answer(monkeypatch) -> None:
    """RagEngine.answer() 正常路径产出带 graph_answer 的 KnowledgeAnswer。"""
    from core.kg_qa import get_graph_qa_engine as _real_getter

    fake_gqe = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    monkeypatch.setattr("core.kg_qa.get_graph_qa_engine", lambda: fake_gqe)

    engine = _FakeRagEngine(_sample_result(seed_ids=["e-overload"]))
    ka = engine.answer("变压器过载会影响哪些设备")
    assert ka.refuse is False
    assert ka.graph_answer is not None
    assert ka.graph_answer.nodes
    assert ka.graph_answer.edges
    assert ka.graph_answer.paths
    # US-5：GraphAnswer.sources 与 KnowledgeAnswer.sources 同源
    assert ka.graph_answer.sources == ka.sources
    # 与检索同源：seed 来自 result.seed_ids
    assert ka.graph_answer.seed_ids == ["e-overload"]
    # 恢复真实 getter（防止污染其他用例）
    monkeypatch.setattr("core.kg_qa.get_graph_qa_engine", _real_getter)


def test_answer_no_seed_no_graph_answer(monkeypatch) -> None:
    """seed 空 → graph_answer=None（M-3 行为不变）。"""
    engine = _FakeRagEngine(_sample_result(seed_ids=[]))
    ka = engine.answer("随便问")
    assert ka.graph_answer is None


def test_answer_graph_assembly_exception_keeps_answer(monkeypatch) -> None:
    """图谱组装异常 → 不阻断 RAG，graph_answer=None。"""
    engine = _FakeRagEngine(_sample_result(seed_ids=["e-overload"]))

    def _boom(*a, **k):
        raise RuntimeError("gqe boom")

    monkeypatch.setattr("core.kg_qa.get_graph_qa_engine", _boom)
    ka = engine.answer("变压器过载")
    assert ka.answer == "模板答案"  # RAG 回答不受影响
    assert ka.graph_answer is None


def test_query_knowledge_base_dump_contains_graph_answer(monkeypatch) -> None:
    """query_knowledge_base 的 model_dump() 含 graph_answer（零额外管道）。"""
    from core.kg_qa import get_graph_qa_engine as _real_getter

    fake_gqe = GraphQAEngine(client=_FakeKGClient(_build_test_graph()))
    monkeypatch.setattr("core.kg_qa.get_graph_qa_engine", lambda: fake_gqe)

    engine = _FakeRagEngine(_sample_result(seed_ids=["e-overload"]))
    ka = engine.answer("变压器过载")
    dumped = ka.model_dump()
    assert "graph_answer" in dumped
    assert dumped["graph_answer"]["seed_ids"] == ["e-overload"]
    assert dumped["graph_answer"]["backend"] == "networkx"
    monkeypatch.setattr("core.kg_qa.get_graph_qa_engine", _real_getter)


def test_extract_knowledge_answer_with_graph_answer() -> None:
    """_extract_knowledge_answer_from_results 能反解含 graph_answer 的 JSON。"""
    from api.agents.agent_factory import _extract_knowledge_answer_from_results

    payload = {
        "answer": "答案",
        "citations": [],
        "graph_paths": [],
        "confidence": 0.9,
        "refuse": False,
        "refuse_reason": None,
        "sources": [{"doc_id": "d1", "title": "变压器运行规程", "score": 0.9}],
        "graph_answer": {
            "nodes": [{"id": "e-overload", "name": "过载", "type": "故障", "hop": 0, "confidence": 1.0}],
            "edges": [{"source": "e-overload", "target": "e-overtemp", "relation_type": "触发"}],
            "paths": [],
            "seed_ids": ["e-overload"],
            "confidence": 1.0,
            "backend": "networkx",
            "degraded": True,
            "latency_ms": 1.0,
            "sources": [{"doc_id": "d1", "title": "变压器运行规程", "score": 0.9}],
        },
    }
    res = f"【query_knowledge_base】结果：{json.dumps(payload, ensure_ascii=False)}"
    ka = _extract_knowledge_answer_from_results([res])
    assert ka is not None
    assert ka.graph_answer is not None
    assert ka.graph_answer.nodes[0].name == "过载"
    assert ka.graph_answer.edges[0].relation_type == "触发"
    assert ka.graph_answer.edges[0].rule_id is None


# ─────────────────────────────────────────────────────────────
# 4. mock 三剧本（P1-3，决策 7）
# ─────────────────────────────────────────────────────────────

async def test_mock_three_scripts_have_graph_answer() -> None:
    from api.agents.agent_factory import _build_mock_knowledge_answer

    cases = [
        ("变压器油温异常有哪些原因", "oil_temperature"),
        ("变压器过载会影响哪些设备", "overload"),
        ("变压器停机检修流程", "shutdown"),
    ]
    for q, _script in cases:
        ka = await _build_mock_knowledge_answer(q, "mock正文")
        assert ka is not None
        assert ka.graph_answer is not None, f"{q!r} 应携带 graph_answer"
        assert ka.graph_answer.nodes
        assert ka.graph_answer.edges
        assert ka.graph_answer.paths
        # US-5：graph_answer.sources 与 KnowledgeAnswer.sources 同一份
        assert ka.graph_answer.sources == ka.sources
        # 路径置信度公式
        for p in ka.graph_answer.paths:
            assert p.confidence == round(max(0.0, 1.0 - 0.15 * p.hops), 3)
        # 规则边恒空
        for e in ka.graph_answer.edges:
            assert e.rule_id is None
        # 降级标记
        assert ka.graph_answer.backend == "networkx"
        assert ka.graph_answer.degraded is True
        # seed 至少 1 个 hop=0
        assert any(n.hop == 0 for n in ka.graph_answer.nodes)


async def test_mock_fallback_no_graph_answer() -> None:
    from api.agents.agent_factory import _build_mock_knowledge_answer

    ka = await _build_mock_knowledge_answer("随便问一个知识问题", "正文")
    assert ka is not None
    assert ka.graph_answer is None, "fallback 剧本不 attach graph_answer（决策 7）"


# ─────────────────────────────────────────────────────────────
# 5. 工具注册 + 规则边（决策 3）
# ─────────────────────────────────────────────────────────────

def test_knowledge_agent_tools_registered() -> None:
    from api.agents.agent_factory import AGENT_TOOLS_MAP

    tools = AGENT_TOOLS_MAP["knowledge_agent"]
    assert "kg_multi_hop_reason" in tools
    assert "kg_apply_rules" in tools


async def test_kg_apply_rules_disabled_returns_empty() -> None:
    """inference_engine_enabled=False（默认）→ kg_apply_rules 返回空（无规则边）。"""
    from mcp_tools.tools.kg_reasoning_tools import kg_apply_rules

    result = await kg_apply_rules(entity_id="e-overload", ctx={"duration_min": 45})
    assert result["status"] == "ok"
    assert result["inferred_relations"] == []
