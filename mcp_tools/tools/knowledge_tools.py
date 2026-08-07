"""知识库类 MCP 工具——基于混合 RAG（向量 + 图谱）。"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from core.rag_engine import RagEngine
from core.vector_store import get_vector_store

_rag_engine: RagEngine | None = None


def _get_engine() -> RagEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RagEngine()
    return _rag_engine


async def query_knowledge_base(query: str) -> dict[str, Any]:
    """知识库问答（混合 RAG 检索 + 生成）。"""
    engine = _get_engine()
    from api.config import settings
    engine.set_llm_api_key(settings.dashscope_api_key)
    # B1：engine.answer() 是同步链路（向量检索 + 图谱扩展 + LLM 生成，最坏
    # 10-60s），直接在 async MCP 工具内调用会阻塞事件循环 → to_thread 移出。
    answer = await asyncio.to_thread(engine.answer, query)
    return answer.model_dump()


async def search_knowledge_chunks(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """纯向量检索知识库片段（不带生成）。

    V1.6 P0-5 增补件 §1.7 修复：透传真实分值（不再恒为 0）。
    """
    engine = _get_engine()
    # 直接走 VectorStore 而非 retrieve()，避免 RAG 全链路开销（实体抽取 / 图谱扩展）
    store = get_vector_store()
    store.ensure_fresh()  # V1.6 P0-5 §3.2 跨进程热更新自检
    vec_results = store.search(query, top_k=top_k)
    return [
        {"content": r.get("content", ""), "score": float(r.get("score", 0.0))}
        for r in vec_results
    ]


async def search_graph_entities(keyword: str) -> list[dict[str, Any]]:
    """搜索图谱实体。"""
    engine = _get_engine()
    entities = engine.knowledge_graph.search_entities(keyword)
    return [e.model_dump() for e in entities]


async def get_entity_relations(entity_id: str) -> list[dict[str, Any]]:
    """获取图谱实体关系。"""
    engine = _get_engine()
    relations = engine.knowledge_graph.get_relations(entity_id)
    result = []
    for rel in relations:
        src = engine.knowledge_graph.get_entity(rel.source_id)
        tgt = engine.knowledge_graph.get_entity(rel.target_id)
        result.append({
            "source": src.name if src else rel.source_id,
            "target": tgt.name if tgt else rel.target_id,
            "relation": rel.relation_type,
        })
    return result


# ═══════════════════════════════════════════════════════
# V1.6 P0-5 增补件 §1.7 · 对话 grounding 优先通道
# ═══════════════════════════════════════════════════════


async def search_feature_intro(
    query: str,
    top_k: int = 5,
    tag: str | None = None,
) -> dict[str, Any]:
    """检索 GridMind 功能介绍文档（产品功能/视图/引导类问题的权威来源）。

    **何时优先用本工具**（架构增补件 §1.7 + K-A1）：
        用户提问属于「产品功能介绍 / 视图结构 / 引导流程 / 演示场景」
        类问题时，应优先调用本工具而非通用 ``query_knowledge_base``。
        通用 RAG 链路会被 25 条电力规程分片同池竞争，本工具走 tag
        优先通道（``feature-intro`` 命名空间），结果更精准、可溯源。

    流程（架构增补件 §1.2 + §3.2）：
        1. ``core.feature_intro.intent.detect(query)`` 判定是否属于功能介绍
           ——若不属于则返回空 ``count=0``，让上层走普通 RAG（K-A1 单一判定点）。
        2. ``get_vector_store().ensure_fresh()`` 惰性自检跨进程热更新。
        3. ``search_by_tag`` 在 ``feature-intro`` 命名空间内检索 top_k 条。
        4. 用 :class:`RagEngine` 的 ``answer()`` rerank（保留引用追溯）。

    Args:
        query: 用户问题（任意大小写 / 空白）。
        top_k: 返回条数上限（默认 5）。
        tag: 可选子 tag 过滤（如 ``kind:view`` / ``kind:scenario``）；
            缺省时用 intent 推导的 tags，意图不明则用 ``"feature-intro"`` 全空间。

    Returns:
        形如::

            {
                "count": int,
                "chunks": [
                    {
                        "doc_id": "feature-intro:view-chat",
                        "section": "2.1",       # 来自 meta.section（增补件 §1.6）
                        "title": "对话视图 chat",
                        "kind": "view",        # 来自 meta.kind
                        "content": "……",
                        "score": 0.92,
                    },
                    ...
                ],
                "source": "docs/gridmind-feature-introduction.md",
                "intent": {
                    "hit": True,
                    "score": 0.6,
                    "intent": "view",
                    "tags": ["kind:view", "kind:overview"],
                    "matched": ["product:gridmind", "view:核心视图"],
                },
            }
    """
    # 1. 意图门控（K-A1：单一判定点）
    try:
        from core.feature_intro.intent import detect as _detect
        intent = _detect(query)
    except Exception as e:  # noqa: BLE001 — 意图模块自身故障不应阻断工具
        logger.warning("search_feature_intro: 意图检测失败，按通用 RAG 兜底：{}", e)
        intent = None

    if intent is None or not intent.hit:
        return {
            "count": 0,
            "chunks": [],
            "source": "",
            "intent": {
                "hit": False, "score": getattr(intent, "score", 0.0),
                "intent": getattr(intent, "intent", ""),
                "tags": list(getattr(intent, "tags", ())),
                "matched": list(getattr(intent, "matched", ())),
            },
        }

    # 2. ensure_fresh（节流内开销可忽略）
    wanted_tag = (tag or "").strip() or (intent.tags[0] if intent.tags else "feature-intro")
    try:
        store = get_vector_store()
        store.ensure_fresh()
    except Exception as e:  # noqa: BLE001 — 自检失败不阻断
        logger.warning("search_feature_intro: ensure_fresh 失败，继续使用当前内存：{}", e)
        store = get_vector_store()

    # 3. tag 优先召回
    try:
        items = store.search_by_tag(wanted_tag, top_k=max(1, int(top_k)))
        # 若子 tag 命中不足，回落到全 feature-intro 命名空间补位
        if len(items) < top_k and wanted_tag != "feature-intro":
            extra = store.search_by_tag("feature-intro", top_k=top_k)
            seen = {i["doc_id"] for i in items}
            for it in extra:
                if it["doc_id"] not in seen:
                    items.append(it)
                    seen.add(it["doc_id"])
                if len(items) >= top_k:
                    break
    except Exception as e:  # noqa: BLE001 — 检索异常返回空
        logger.warning("search_feature_intro: search_by_tag 失败：{}", e)
        return {
            "count": 0,
            "chunks": [],
            "source": "docs/gridmind-feature-introduction.md",
            "intent": {
                "hit": True,
                "score": intent.score,
                "intent": intent.intent,
                "tags": list(intent.tags),
                "matched": list(intent.matched),
            },
            "error": str(e),
        }

    # 4. 用 query 在子集内做关键词 rerank（K-A1 单点判定后这里只做排序，不重判）
    query_norm = " ".join(str(query or "").split()).lower()
    reranked: list[tuple[float, dict[str, Any]]] = []
    for it in items:
        text = (it.get("content") or "").lower()
        title = (it.get("title") or "").lower()
        if query_norm:
            score = 0.0
            for token in query_norm.split():
                if not token:
                    continue
                if token in text:
                    score += 0.6
                if token in title:
                    score += 0.4
            # 关键词 rerank 退化时给一个保底分
            if score == 0.0 and query_norm:
                score = 0.05
        else:
            score = 0.5
        reranked.append((score, it))
    reranked.sort(key=lambda x: -x[0])

    chunks: list[dict[str, Any]] = []
    for score, it in reranked[: max(1, int(top_k))]:
        meta = it.get("meta") or {}
        chunks.append({
            "doc_id": it.get("doc_id", ""),
            "section": str(meta.get("section") or ""),
            "title": it.get("title", ""),
            "kind": str(meta.get("kind") or ""),
            "content": it.get("content", ""),
            "score": round(float(score), 3),
        })

    return {
        "count": len(chunks),
        "chunks": chunks,
        "source": "docs/gridmind-feature-introduction.md",
        "intent": {
            "hit": True,
            "score": intent.score,
            "intent": intent.intent,
            "tags": list(intent.tags),
            "matched": list(intent.matched),
        },
    }
