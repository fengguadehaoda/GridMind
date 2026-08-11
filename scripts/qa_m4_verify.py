# -*- coding: utf-8 -*-
"""QA 独立回归验证 · M-4 图谱问答 UI 链路实证（只读验证，不改产品代码）。

验证点（架构 §五 T05 + 全局一致性审查）：
1. import api.main 通过（含 GraphAnswer* schema）
2. 真实链路：query_knowledge_base("变压器过载会影响哪些设备")
   → knowledge_answer.graph_answer 非空（nodes/edges/paths）
   → backend=networkx / degraded=true（常态降级）
   → 载荷剪枝 nodes≤50 / edges≤120 / paths top_k=5；seed 必保留
   → 边含 relation_type 且 rule_id 恒 None；路径置信度 max(0,1-0.15*hops)
   → graph_answer.sources 与 KnowledgeAnswer.sources 同源（US-5）
3. 真实链路：油温 / 停机检修 同样非空
4. 降级实证：图谱组装返回空 → graph_answer=None，RAG 回答不受影响
5. 向后兼容实证：旧数据（无 graph_answer）KnowledgeAnswer 反解不报错
6. mock 三剧本（过载/油温/停机检修）graph_answer 非空且 sources 同源；fallback 无

运行：
    python scripts/qa_m4_verify.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
# 使 `python scripts/qa_m4_verify.py` 能 import 项目根下的 api/core/mcp_tools
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} -- {detail}")


def _summarize_ga(ga) -> str:
    if not ga:
        return "None"
    return (
        f"nodes={len(ga.get('nodes', []))} edges={len(ga.get('edges', []))} "
        f"paths={len(ga.get('paths', []))} seeds={len(ga.get('seed_ids', []))} "
        f"backend={ga.get('backend')} degraded={ga.get('degraded')}"
    )


async def _query(query: str) -> dict:
    from mcp_tools.tools.knowledge_tools import query_knowledge_base
    return await query_knowledge_base(query)


async def main():
    print("=" * 70)
    print("[1] import api.main / schema 契约")
    import api.main  # noqa: F401
    from api.schemas import (
        GraphAnswer, GraphAnswerEdge, GraphAnswerNode, GraphPath, KnowledgeAnswer,
    )
    check("api.main import ok", True)
    check("GraphAnswer* schema 存在", all(
        cls.__name__ in {"GraphAnswer", "GraphAnswerNode", "GraphAnswerEdge", "GraphPath"}
        for cls in (GraphAnswer, GraphAnswerNode, GraphAnswerEdge, GraphPath)
    ))
    # 旧数据反解
    old = {
        "answer": "旧答案", "citations": [], "graph_paths": [],
        "confidence": 0.8, "refuse": False, "refuse_reason": None, "sources": [],
    }
    ka = KnowledgeAnswer(**old)
    check("旧数据无 graph_answer 反解不报错", ka.graph_answer is None)
    check("旧数据 model_dump 不含 graph_answer", "graph_answer" not in ka.model_dump())

    print("=" * 70)
    print("[2] 真实链路：变压器过载会影响哪些设备")
    res = await _query("变压器过载会影响哪些设备")
    ga = res.get("graph_answer")
    print(f"    graph_answer: {_summarize_ga(ga)}")
    check("真实链路 graph_answer 非空", ga is not None)
    if ga:
        check("nodes 非空", len(ga["nodes"]) > 0)
        check("edges 非空", len(ga["edges"]) > 0)
        check("paths 非空", len(ga["paths"]) > 0)
        check("载荷剪枝 nodes≤50", len(ga["nodes"]) <= 50, f"got {len(ga['nodes'])}")
        check("载荷剪枝 edges≤120", len(ga["edges"]) <= 120, f"got {len(ga['edges'])}")
        check("paths top_k=5", len(ga["paths"]) <= 5, f"got {len(ga['paths'])}")
        check("seed 必保留（hop=0 存在）", any(n["hop"] == 0 for n in ga["nodes"]))
        check("backend=networkx（常态降级）", ga["backend"] == "networkx")
        check("degraded=true（弱提示不阻断）", ga["degraded"] is True)
        check("边均含 relation_type", all(e.get("relation_type") for e in ga["edges"]))
        check("规则边恒空 rule_id=None", all(e.get("rule_id") is None for e in ga["edges"]))
        check(
            "路径置信度公式 max(0,1-0.15*hops)",
            all(
                abs(p["confidence"] - max(0.0, 1.0 - 0.15 * p["hops"])) < 1e-6
                for p in ga["paths"]
            ),
        )
        check(
            "graph_answer.sources 与 KnowledgeAnswer.sources 同源（US-5）",
            (ga.get("sources") or []) == (res.get("sources") or []),
        )

    print("=" * 70)
    print("[3] 真实链路：变压器油温异常有哪些原因 / 变压器停机检修流程")
    for q in ["变压器油温异常有哪些原因", "变压器停机检修流程"]:
        r = await _query(q)
        g = r.get("graph_answer")
        print(f"    {q[:18]}... → {_summarize_ga(g)}")
        check(f"真实链路 graph_answer 非空: {q[:12]}", g is not None)
        if g:
            check(f"nodes/edges/paths 非空: {q[:12]}",
                  bool(g["nodes"]) and bool(g["edges"]) and bool(g["paths"]))

    print("=" * 70)
    print("[4] 降级实证：图谱组装返回空 → graph_answer=None，RAG 不受影响")
    from api.agents import agent_factory  # noqa: F401  (确保 get_graph_qa_engine 可 patch)
    from core import kg_qa

    class _EmptyGQE:
        def build(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            from api.schemas import GraphAnswer
            return GraphAnswer()

    original = kg_qa.get_graph_qa_engine
    kg_qa.get_graph_qa_engine = lambda: _EmptyGQE()  # type: ignore[assignment]
    try:
        from core.rag_engine import RagEngine
        degraded_ka = RagEngine().answer("变压器过载会影响哪些设备")
        check("空图 → graph_answer=None", degraded_ka.graph_answer is None)
        check("RAG 回答不受影响", bool(degraded_ka.answer))
    finally:
        kg_qa.get_graph_qa_engine = original

    print("=" * 70)
    print("[5] mock 三剧本（决策 7）：过载 / 油温 / 停机检修")
    from api.agents.agent_factory import _build_mock_knowledge_answer

    for q in ["变压器过载会影响哪些设备", "变压器油温异常有哪些原因", "变压器停机检修流程"]:
        ka = await _build_mock_knowledge_answer(q, "mock正文")
        print(f"    {q[:18]}... → graph_answer={'非空' if ka and ka.graph_answer else 'None'}")
        check(f"mock graph_answer 非空: {q[:12]}", ka is not None and ka.graph_answer is not None)
        if ka and ka.graph_answer:
            check(f"mock sources 同源: {q[:12]}", ka.graph_answer.sources == ka.sources)
            check(
                f"mock 路径置信度公式: {q[:12]}",
                all(
                    abs(p.confidence - max(0.0, 1.0 - 0.15 * p.hops)) < 1e-6
                    for p in ka.graph_answer.paths
                ),
            )
    ka_fb = await _build_mock_knowledge_answer("随便问一个知识问题", "正文")
    check("mock fallback 无 graph_answer", ka_fb is not None and ka_fb.graph_answer is None)

    print("=" * 70)
    print(f"结果：PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("M-4 图谱问答链路实证通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
