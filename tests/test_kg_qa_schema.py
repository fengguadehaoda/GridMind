"""M-4 · T01 数据契约与基础设施测试。

覆盖：
- GraphAnswerNode / GraphAnswerEdge / GraphPath / GraphAnswer 字段与默认值
  与架构 §3.1 完全一致（snake_case，rule_id 默认 None，backend 默认 networkx）；
- KnowledgeAnswer 向后兼容：旧数据（无 graph_answer 键）反解/序列化不变
  （model_dump() 不包含 graph_answer）；有 graph_answer 时往返一致；
- GraphAnswer 序列化往返（Pydantic v2）；
- core.rag_engine.extract_entity_ids 公开 util 返回非空 seed；
- RagEngine._extract_entity_ids 委托行为与 M-3 一致；
- 前端类型镜像静态断言（vue-tsc 兼容性由 web 目录 npx vue-tsc 验证，
  此处仅检查字段存在性，防镜像漂移）。

运行：
    python3 -m pytest tests/test_kg_qa_schema.py -q -p no:cacheprovider
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.schemas import (
    GraphAnswer,
    GraphAnswerEdge,
    GraphAnswerNode,
    GraphPath,
    KnowledgeAnswer,
    SourceRef,
)


# ─────────────────────────────────────────────────────────────
# 1. Schema 字段一致性（架构 §3.1）
# ─────────────────────────────────────────────────────────────

def test_graph_answer_node_fields() -> None:
    node = GraphAnswerNode(id="e-1", name="过载", type="故障")
    assert node.id == "e-1"
    assert node.name == "过载"
    assert node.type == "故障"
    # 默认值
    assert node.properties == {}
    assert node.hop is None
    assert node.doc_ids == []
    assert node.confidence is None
    # 显式赋值
    node2 = GraphAnswerNode(
        id="e-1", name="过载", type="故障",
        properties={"k": "v"}, hop=1, doc_ids=["d1"], confidence=0.85,
    )
    assert node2.hop == 1
    assert node2.doc_ids == ["d1"]
    assert node2.confidence == 0.85


def test_graph_answer_edge_fields() -> None:
    edge = GraphAnswerEdge(source="e-1", target="e-2", relation_type="触发")
    assert edge.source == "e-1"
    assert edge.target == "e-2"
    assert edge.relation_type == "触发"
    # 默认值：rule_id 恒 None（决策 3：规则推导边不启用）
    assert edge.confidence is None
    assert edge.rule_id is None


def test_graph_path_fields() -> None:
    path = GraphPath(nodes=["e-1", "e-2"], relations=["触发"], hops=1, confidence=0.85)
    assert path.nodes == ["e-1", "e-2"]
    assert path.relations == ["触发"]
    assert path.hops == 1
    assert path.confidence == 0.85
    # 默认值
    assert GraphPath().nodes == []
    assert GraphPath().relations == []


def test_graph_answer_fields() -> None:
    ga = GraphAnswer()
    assert ga.nodes == []
    assert ga.edges == []
    assert ga.paths == []
    assert ga.seed_ids == []
    assert ga.confidence == 0.0
    assert ga.backend == "networkx"
    assert ga.degraded is False
    assert ga.latency_ms == 0.0
    assert ga.sources == []


def test_graph_answer_roundtrip() -> None:
    """GraphAnswer 序列化往返一致（Pydantic v2）。"""
    ga = GraphAnswer(
        nodes=[GraphAnswerNode(id="e-1", name="过载", type="故障", hop=0, confidence=1.0)],
        edges=[GraphAnswerEdge(source="e-1", target="e-2", relation_type="触发")],
        paths=[GraphPath(nodes=["e-1", "e-2"], relations=["触发"], hops=1, confidence=0.85)],
        seed_ids=["e-1"],
        confidence=0.85,
        backend="networkx",
        degraded=True,
        latency_ms=12.3,
        sources=[SourceRef(doc_id="d1", title="规程")],
    )
    dumped = ga.model_dump()
    assert dumped["backend"] == "networkx"
    assert dumped["degraded"] is True
    assert dumped["nodes"][0]["hop"] == 0
    assert dumped["edges"][0]["rule_id"] is None
    assert dumped["paths"][0]["confidence"] == 0.85
    # 往返
    restored = GraphAnswer(**dumped)
    assert restored == ga


# ─────────────────────────────────────────────────────────────
# 2. KnowledgeAnswer 向后兼容（旧数据无 graph_answer）
# ─────────────────────────────────────────────────────────────

def test_knowledge_answer_backward_compat_without_graph_answer() -> None:
    """旧数据（无 graph_answer 键）反解正常，model_dump() 不含该键。"""
    old = {
        "answer": "测试答案",
        "citations": ["片段1"],
        "graph_paths": [["e-1", "e-2"]],
        "confidence": 0.8,
        "refuse": False,
        "refuse_reason": None,
        "sources": [{"doc_id": "d1", "title": "规程", "score": 0.9}],
    }
    ka = KnowledgeAnswer(**old)
    assert ka.answer == "测试答案"
    assert ka.graph_answer is None  # 默认值
    dumped = ka.model_dump()
    assert "graph_answer" not in dumped  # 向后兼容：旧字段集合不变


def test_knowledge_answer_with_graph_answer_roundtrip() -> None:
    """有 graph_answer 时序列化往返一致。"""
    ka = KnowledgeAnswer(
        answer="答案",
        citations=[],
        graph_paths=[],
        confidence=0.9,
        refuse=False,
        sources=[],
        graph_answer=GraphAnswer(
            nodes=[GraphAnswerNode(id="e-1", name="过载", type="故障", hop=0, confidence=1.0)],
            edges=[],
            paths=[],
            seed_ids=["e-1"],
            confidence=1.0,
            backend="networkx",
            degraded=True,
        ),
    )
    dumped = ka.model_dump()
    assert "graph_answer" in dumped
    assert dumped["graph_answer"]["seed_ids"] == ["e-1"]
    restored = KnowledgeAnswer(**dumped)
    assert restored.graph_answer is not None
    assert restored.graph_answer.nodes[0].hop == 0
    assert restored == ka


def test_knowledge_answer_snake_case_contract() -> None:
    """字段命名全链路 snake_case 契约（K-1：relation_type/seed_ids/doc_ids…）。"""
    ga = GraphAnswer(
        nodes=[GraphAnswerNode(id="e-1", name="过载", type="故障", hop=0, doc_ids=["d1"])],
        edges=[GraphAnswerEdge(source="e-1", target="e-2", relation_type="触发")],
        paths=[GraphPath(nodes=["e-1", "e-2"], relations=["触发"], hops=1, confidence=0.85)],
        seed_ids=["e-1"],
    )
    dumped = ga.model_dump()
    assert "relation_type" in dumped["edges"][0]
    assert "doc_ids" in dumped["nodes"][0]
    assert "seed_ids" in dumped
    assert "latency_ms" in dumped


# ─────────────────────────────────────────────────────────────
# 3. extract_entity_ids 公开 util
# ─────────────────────────────────────────────────────────────

def test_extract_entity_ids_nonempty() -> None:
    """公开 util 从「变压器过载」文本返回非空 seed。"""
    from core.rag_engine import extract_entity_ids

    seeds = extract_entity_ids("变压器过载会影响哪些设备")
    assert isinstance(seeds, list)
    assert len(seeds) > 0


def test_rag_engine_extract_entity_ids_delegates() -> None:
    """RagEngine._extract_entity_ids 委托公开 util（行为与 M-3 一致）。"""
    from core.rag_engine import RagEngine

    engine = RagEngine()
    seeds = engine._extract_entity_ids("变压器过载会影响哪些设备")
    assert isinstance(seeds, list)
    assert len(seeds) > 0
    # 委托后结果与模块级 util 一致（同源）
    from core.rag_engine import extract_entity_ids as module_util

    assert seeds == module_util("变压器过载会影响哪些设备", engine.knowledge_graph)


def test_extract_entity_ids_empty_text() -> None:
    """空文本返回空列表（不抛错）。"""
    from core.rag_engine import extract_entity_ids

    assert extract_entity_ids("") == []
    assert extract_entity_ids("   ") == []


# ─────────────────────────────────────────────────────────────
# 4. 前端类型镜像静态断言（防 snake_case 漂移；vue-tsc 由 web 验证）
# ─────────────────────────────────────────────────────────────

def test_frontend_type_mirror_present() -> None:
    """web/src/types/index.ts 含 M-4 镜像类型与 graph_answer 字段。"""
    ts_path = Path(__file__).resolve().parents[1] / "web" / "src" / "types" / "index.ts"
    assert ts_path.exists(), "web/src/types/index.ts 不存在"
    content = ts_path.read_text(encoding="utf-8")
    for token in [
        "GraphAnswerNode",
        "GraphAnswerEdge",
        "GraphPath",
        "GraphAnswer",
        "ForceGraphNodeInput",
        "ForceGraphEdgeInput",
        "graph_answer",
        "relation_type",
        "seed_ids",
        "doc_ids",
        "latency_ms",
    ]:
        assert token in content, f"web/src/types/index.ts 缺少 {token!r}"
