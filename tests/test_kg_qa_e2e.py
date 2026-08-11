"""M-4 · T05 端到端联调测试。

覆盖（架构 §五 T05 验收标准）：
- 真实链路：``query_knowledge_base`` → KnowledgeAnswer.graph_answer 非空
  （nodes/edges/paths、backend=networkx、degraded=true、载荷剪枝 ≤50/≤120/top_k=5）；
- SSE done 事件 `knowledge_answer.graph_answer` 透传（零新端点、SSE 管道零改动）；
- 阻塞 /chat 路径 `ChatResponse.knowledge_answer.graph_answer` 透传；
- mock 链路：``_attach_knowledge_answer``（mock 分支）→ KnowledgeAnswer 含
  graph_answer（三剧本）；fallback 无；
- 降级路径：图谱组装返回空 → graph_answer=None，RAG 回答不受影响（M-3 行为一致）；
- 旧数据兼容：无 graph_answer 键的 KnowledgeAnswer 反解/序列化不变。

运行：
    python3 -m pytest tests/test_kg_qa_e2e.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

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
# 1. 真实链路（query_knowledge_base → graph_answer）
# ─────────────────────────────────────────────────────────────

def test_query_knowledge_base_real_graph_answer() -> None:
    """真实链路：query_knowledge_base 返回含 graph_answer 的 KnowledgeAnswer。"""
    from mcp_tools.tools.knowledge_tools import query_knowledge_base

    result = asyncio.run(query_knowledge_base("变压器过载会影响哪些设备"))
    assert isinstance(result, dict)
    assert "graph_answer" in result, "model_dump 应含 graph_answer（零额外管道）"
    ga = result["graph_answer"]
    assert ga is not None, "图谱类问题应组装 graph_answer"
    assert ga["nodes"], "nodes 不应为空"
    assert ga["edges"], "edges 不应为空"
    assert ga["paths"], "paths 不应为空"
    # 载荷剪枝（决策 4）
    assert len(ga["nodes"]) <= 50
    assert len(ga["edges"]) <= 120
    assert len(ga["paths"]) <= 5
    # 常态降级（当前环境 Neo4j 未启用）
    assert ga["backend"] == "networkx"
    assert ga["degraded"] is True
    # seed 至少 1 个 hop=0；边含 relation_type；规则边恒空；路径置信度公式
    assert any(n["hop"] == 0 for n in ga["nodes"])
    for e in ga["edges"]:
        assert e["relation_type"]
        assert e["rule_id"] is None
    for p in ga["paths"]:
        assert p["confidence"] == round(max(0.0, 1.0 - 0.15 * p["hops"]), 3)
    # US-5：graph_answer.sources 与 KnowledgeAnswer.sources 同源/子集
    ka_sources = result.get("sources") or []
    ga_sources = ga.get("sources") or []
    assert len(ga_sources) == len(ka_sources)
    assert ga_sources == ka_sources


# ─────────────────────────────────────────────────────────────
# 2. SSE done 事件透传（零新端点 / SSE 管道零改动）
# ─────────────────────────────────────────────────────────────

def _make_ga() -> GraphAnswer:
    return GraphAnswer(
        nodes=[
            GraphAnswerNode(id="e-overload", name="过载", type="故障", hop=0, confidence=1.0),
            GraphAnswerNode(id="e-derating", name="减载措施", type="处置", hop=1, confidence=0.85),
        ],
        edges=[GraphAnswerEdge(source="e-overload", target="e-derating", relation_type="处置")],
        paths=[GraphPath(nodes=["e-overload", "e-derating"], relations=["处置"], hops=1, confidence=0.85)],
        seed_ids=["e-overload"],
        confidence=0.85,
        backend="networkx",
        degraded=True,
        latency_ms=3.2,
        sources=[SourceRef(doc_id="user-upload:mock-transformer-rules", title="变压器运行规程", score=0.85)],
    )


def _done_payload(client, path: str, params: dict) -> dict:
    resp = client.get(path, params=params)
    assert resp.status_code == 200, f"status={resp.status_code} text={resp.text[:300]}"
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        evt = json.loads(payload)
        if evt.get("type") == "done":
            return evt
    raise AssertionError("未找到 done 事件")


def test_sse_done_carries_graph_answer(monkeypatch) -> None:
    """SSE done 事件 knowledge_answer.graph_answer 透传（M-4 零新端点）。"""
    from fastapi.testclient import TestClient
    import api.main as main_mod

    class FakeBuilder:
        async def run(self, thread_id, message, display_mode=None, model_id=None):
            return {
                "messages": [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": "【知识库 Agent】测试回答",
                     "metadata": {"agent_name": "knowledge_agent"}},
                ],
                "knowledge_answer": KnowledgeAnswer(
                    answer="测试回答",
                    citations=[],
                    graph_paths=[],
                    confidence=0.85,
                    refuse=False,
                    sources=[SourceRef(doc_id="user-upload:mock-transformer-rules",
                                       title="变压器运行规程", score=0.85)],
                    graph_answer=_make_ga(),
                ),
            }

    monkeypatch.setattr(main_mod, "graph_builder", FakeBuilder())
    monkeypatch.setattr(main_mod, "resolve_model", lambda thread_id: None)
    client = TestClient(main_mod.app)
    evt = _done_payload(client, "/chat/stream/thread-m4-e2e-1", {"message": "变压器过载"})
    assert "knowledge_answer" in evt
    ga = evt["knowledge_answer"]["graph_answer"]
    assert ga is not None, "done 事件应携带 graph_answer"
    assert ga["nodes"][0]["name"] == "过载"
    assert ga["edges"][0]["relation_type"] == "处置"
    assert ga["paths"][0]["hops"] == 1
    assert ga["seed_ids"] == ["e-overload"]
    assert ga["backend"] == "networkx"
    assert ga["degraded"] is True


def test_chat_blocking_carries_graph_answer(monkeypatch) -> None:
    """阻塞 /chat 路径 ChatResponse.knowledge_answer.graph_answer 透传。"""
    from fastapi.testclient import TestClient
    import api.main as main_mod

    class FakeBuilder:
        async def run(self, thread_id, message, display_mode=None, model_id=None):
            return {
                "messages": [
                    {"role": "assistant", "content": "测试回答",
                     "metadata": {"agent_name": "knowledge_agent"}},
                ],
                "knowledge_answer": KnowledgeAnswer(
                    answer="测试回答", citations=[], graph_paths=[], confidence=0.8,
                    refuse=False,
                    sources=[SourceRef(doc_id="user-upload:mock-transformer-rules",
                                       title="变压器运行规程", score=0.87)],
                    graph_answer=_make_ga(),
                ),
            }

    monkeypatch.setattr(main_mod, "graph_builder", FakeBuilder())
    monkeypatch.setattr(main_mod, "resolve_model", lambda thread_id: None)
    client = TestClient(main_mod.app)
    resp = client.post("/chat", json={"thread_id": "thread-m4-e2e-2", "message": "变压器过载"})
    assert resp.status_code == 200, f"status={resp.status_code} text={resp.text[:300]}"
    body = resp.json()
    assert body["knowledge_answer"] is not None
    ga = body["knowledge_answer"]["graph_answer"]
    assert ga is not None
    assert ga["nodes"][0]["id"] == "e-overload"


def test_sse_done_old_data_without_graph_answer(monkeypatch) -> None:
    """旧数据（无 graph_answer）→ done 事件无该键（向后兼容，M-3 行为不变）。"""
    from fastapi.testclient import TestClient
    import api.main as main_mod

    class FakeBuilder:
        async def run(self, thread_id, message, display_mode=None, model_id=None):
            return {
                "messages": [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": "【知识库 Agent】测试回答",
                     "metadata": {"agent_name": "knowledge_agent"}},
                ],
                "knowledge_answer": KnowledgeAnswer(
                    answer="测试回答", citations=[], graph_paths=[], confidence=0.85,
                    refuse=False,
                    sources=[SourceRef(doc_id="user-upload:mock-transformer-rules",
                                       title="变压器运行规程", score=0.85)],
                ),
            }

    monkeypatch.setattr(main_mod, "graph_builder", FakeBuilder())
    monkeypatch.setattr(main_mod, "resolve_model", lambda thread_id: None)
    client = TestClient(main_mod.app)
    evt = _done_payload(client, "/chat/stream/thread-m4-e2e-3", {"message": "变压器油温"})
    ka = evt["knowledge_answer"]
    assert "graph_answer" not in ka, "旧数据序列化不应新增 graph_answer 键（向后兼容）"
    assert ka["sources"][0]["title"] == "变压器运行规程"


# ─────────────────────────────────────────────────────────────
# 3. mock 链路（_attach_knowledge_answer mock 分支）
# ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_attach_mock_knowledge_answer_graph_answer() -> None:
    """mock 分支：_attach_knowledge_answer 注入含 graph_answer 的 KnowledgeAnswer。"""
    from api.agents.agent_factory import _attach_knowledge_answer

    update = {"messages": []}
    out = _run(_attach_knowledge_answer(
        "knowledge_agent", update,
        last_user="变压器过载会影响哪些设备", answer_text="mock正文",
    ))
    assert "knowledge_answer" in out
    ka = out["knowledge_answer"]
    assert isinstance(ka, KnowledgeAnswer)
    assert ka.graph_answer is not None
    assert ka.graph_answer.nodes
    assert ka.graph_answer.edges
    assert ka.graph_answer.paths
    assert ka.graph_answer.sources == ka.sources  # US-5 同源
    assert ka.graph_answer.backend == "networkx"
    assert ka.graph_answer.degraded is True


def test_attach_mock_fallback_no_graph_answer() -> None:
    """mock 分支：fallback 剧本不注入 graph_answer（决策 7）。"""
    from api.agents.agent_factory import _attach_knowledge_answer

    update = {"messages": []}
    out = _run(_attach_knowledge_answer(
        "knowledge_agent", update,
        last_user="随便问一个知识问题", answer_text="正文",
    ))
    assert "knowledge_answer" in out
    assert out["knowledge_answer"].graph_answer is None


# ─────────────────────────────────────────────────────────────
# 4. 降级路径（图谱组装空 → graph_answer=None，RAG 不受影响）
# ─────────────────────────────────────────────────────────────

def test_degraded_empty_graph_answer_none(monkeypatch) -> None:
    """图谱组装返回空 → graph_answer=None，问答本身不受影响（US-4）。"""
    from core.rag_engine import RagEngine

    class _EmptyGQE:
        def build(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return GraphAnswer()  # 全空 → 不 attach

    monkeypatch.setattr("core.kg_qa.get_graph_qa_engine", lambda: _EmptyGQE())
    engine = RagEngine()
    ka = engine.answer("变压器过载会影响哪些设备")
    assert ka.graph_answer is None, "全空 graph_answer 不 attach（M-3 行为）"
    assert ka.refuse is False
    assert ka.answer, "RAG 回答不受图谱降级影响"


def test_knowledge_answer_old_data_backward_compat() -> None:
    """旧数据（无 graph_answer）反解不报错，序列化不含该键。"""
    old = {
        "answer": "旧答案",
        "citations": ["c1"],
        "graph_paths": [],
        "confidence": 0.8,
        "refuse": False,
        "refuse_reason": None,
        "sources": [],
    }
    ka = KnowledgeAnswer(**old)
    assert ka.graph_answer is None
    dumped = ka.model_dump()
    assert "graph_answer" not in dumped
