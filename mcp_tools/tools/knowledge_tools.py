"""知识库类 MCP 工具——基于混合 RAG（向量 + 图谱）。"""

from __future__ import annotations

from typing import Any

from core.rag_engine import RagEngine

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
    answer = engine.answer(query)
    return answer.model_dump()


async def search_knowledge_chunks(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """纯向量检索知识库片段（不带生成）。"""
    engine = _get_engine()
    result = engine.retrieve(query, top_k=top_k)
    return [
        {"content": chunk, "score": 0.0}
        for chunk in result.vector_chunks
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
