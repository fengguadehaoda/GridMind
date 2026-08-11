"""FR-3 真 RAG 知识库——「图谱检索增强生成（GraphRAG/KRAG）+ 向量检索」混合架构。

流程（M2 升级后）：
1. 用户 query → 向量检索（Chroma）召回 top-k 候选片段
2. 对候选片段提取关键实体 → 图谱检索（**灰度路由**：GrayscaleRouter 决定 Neo4j 或 NetworkX）
3. **Neo4j 模式**：multi_hop_expand 3 跳 + get_fault_chain + get_applicable_regulations
4. **NetworkX 降级**：保留 M0 行为（2 跳扩展，零回归）
5. 融合：向量候选 + 图谱关联子图 → 拼接上下文
6. 调用 DashScope LLM 生成答案（带原文引用与图谱路径）
7. 低置信度拒答/转人工

M2 关键改造（架构 §3.4）：
- 第 60-67 行 NetworkX 2 跳 → GrayscaleRouter 路由
- 保留 NetworkX fallback（neo4j_enabled=False 或 router 拒绝时）
- 监控埋点：RAG 耗时 + 命中 backend 自动上报 RollbackMonitor
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from loguru import logger

from api.config import settings
from api.schemas import GraphEntity, KnowledgeAnswer, RetrievalResult, SourceRef
from core.knowledge_graph import KnowledgeGraph
from core.metrics_collector import (
    get_metrics_collector,
    is_metrics_enabled,
)
from core.vector_store import VectorStore


# 用于实体提取的正则（简单启发式）
ENTITY_PATTERNS = [
    r"(变压器|断路器|电缆|母线|避雷器)",
    r"(过载|油温异常|SF6泄漏|局部放电|接地故障|绝缘降低)",
    r"(减载|停运|检修|更换|加强监测)",
    r"(DL/T[\s\-]?\d+[\s\-]?\d*|Q/GDW[\s\-]?\d+|GB/T[\s\-]?\d+[\s\-]?\d*)",
    r"(一号主变|二号主变|35kV母线|10kV母线)",
]

# 故障关键词（用于触发 get_fault_chain）
FAULT_KEYWORDS = ("过载", "故障", "异常", "跳闸", "报警")

# 特殊设备名 → 实体 ID 兜底映射（M0 保留，M-4 公开 util 复用）
_DEVICE_ID_MAP: dict[str, str] = {
    "一号主变": "e-TR001",
    "二号主变": "e-TR002",
    "35kv母线": "e-BB002",
    "10kv母线": "e-BB001",
}


def extract_entity_ids(text: str, knowledge_graph: Any | None = None) -> list[str]:
    """从文本中提取图谱实体 ID（M-4 P0-1 公开 util）。

    由原 ``RagEngine._extract_entity_ids`` 逻辑提升为模块级函数，供
    ``RagEngine``（委托）与 ``GraphQAEngine``（core/kg_qa.py）复用，保证
    检索与图谱问答的 seed 同源（US-1「同源」）。

    Args:
        text: 待抽取文本（用户问题 + 检索上下文拼接）。
        knowledge_graph: ``KnowledgeGraph`` 实例（模糊搜索用）；None 时
            懒加载默认图（兼容 RagEngine 传入自身图）。

    Returns:
        去重后的实体 ID 列表（正则 ENTITY_PATTERNS + device_map 兜底）。
    """
    if knowledge_graph is None:
        from core.knowledge_graph import KnowledgeGraph
        knowledge_graph = KnowledgeGraph()

    found_ids: list[str] = []
    for pattern in ENTITY_PATTERNS:
        for m in re.finditer(pattern, text):
            keyword = m.group(1).strip()
            entities = knowledge_graph.search_entities(keyword)
            for e in entities:
                if e.id not in found_ids:
                    found_ids.append(e.id)
    # 对特殊设备名直接匹配
    lowered = (text or "").lower()
    for alias, eid in _DEVICE_ID_MAP.items():
        if alias in lowered and eid not in found_ids:
            found_ids.append(eid)
    return found_ids


class RagEngine:
    """混合 RAG 引擎（向量 + 图谱 + M2 灰度路由）。"""

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        knowledge_graph: KnowledgeGraph | None = None,
    ) -> None:
        self.vector_store = vector_store or VectorStore()
        self.knowledge_graph = knowledge_graph or KnowledgeGraph()
        self._api_key: str = settings.dashscope_api_key
        # M2 灰度路由器（懒加载）
        self._router: Any = None

    def set_llm_api_key(self, api_key: str) -> None:
        """运行时注入 DashScope API Key。"""
        self._api_key = api_key

    # ── 主入口（M2 改造：灰度路由）─────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        thread_id: str = "default",
    ) -> RetrievalResult:
        """混合检索：向量召回 → 实体抽取 → 灰度路由 → 图谱扩展。

        V1.6 P0-5 · 对话 grounding 优先通道（架构增补件 §1.3）
            入口处先做意图门控：若用户问「5 个核心视图/功能介绍/引导/演示」
            等产品类问题，**直接**调用 ``search_feature_intro`` 的 tag 通道
            召回 5 条功能介绍 chunks，不再走通用 RAG（避免被 25 条电力规程分片
            挤占）。任何异常 fallback 到原有通用 RAG，不破坏既有行为。

        Args:
            query:     用户问题
            top_k:     向量检索 top_k
            thread_id: 会话 ID（用于 GrayscaleRouter 哈希取模）
        """
        # ── V1.6 P0-5 · 意图门控（功能介绍优先 grounding 通道）──
        # 双保险第一层：即便上层 routing 把问题错配给 knowledge_agent 通用
        # RAG，也能在 retrieve() 入口拦截、跳转到 feature-intro 通道。
        _fi_intent: Any = None
        try:
            from core.feature_intro.intent import detect as _fi_detect
            _fi_intent = _fi_detect(query)
        except Exception as _fi_exc:  # noqa: BLE001 — 意图模块故障不应阻断主流程
            logger.debug("feature_intro intent detect unavailable: {}", _fi_exc)

        if _fi_intent is not None and _fi_intent.hit:
            try:
                from mcp_tools.tools.knowledge_tools import search_feature_intro
                fi_result = asyncio_run_sync(
                    search_feature_intro(query, top_k=int(top_k), tag=None),
                )
                fi_chunks: list[dict[str, Any]] = (
                    list(fi_result.get("chunks") or [])
                    if isinstance(fi_result, dict) else []
                )
                fi_text: list[str] = [
                    (c.get("content") or "").strip()
                    for c in fi_chunks
                    if (c.get("content") or "").strip()
                ]
                # 结构化 JSON 日志（便于后续统计「新手问题被路由到功能介绍」）
                logger.info(
                    json.dumps(
                        {
                            "event": "feature_intro_gate",
                            "thread_id": thread_id,
                            "query": (query or "")[:80],
                            "intent_hit": True,
                            "intent_score": _fi_intent.score,
                            "intent_kind": _fi_intent.intent,
                            "intent_matched": list(_fi_intent.matched),
                            "chunks": len(fi_text),
                            "timestamp": time.time(),
                        },
                        ensure_ascii=False,
                    ),
                )
                if fi_text:
                    # 把 chunks 注入 vector_chunks，让下游 answer() 的
                    # _build_context + _generate 走同一条拼接链路。
                    # M-3：feature-intro 通道纳入 sources（主理人已拍板）。
                    fi_sources = [
                        self._make_source_ref({
                            "content": c.get("content") or "",
                            "metadata": {
                                "doc_id": c.get("doc_id") or "",
                                "title": c.get("title") or "",
                                "section": c.get("section") or None,
                            },
                            "score": c.get("score"),
                        })
                        for c in fi_chunks
                        if (c.get("content") or "").strip()
                    ]
                    return RetrievalResult(
                        vector_chunks=fi_text,
                        graph_entities=[],
                        graph_paths=[],
                        confidence=0.95,
                        sources=fi_sources,
                    )
            except Exception as exc:  # noqa: BLE001 — 降级到通用 RAG（不抛错）
                logger.warning(
                    "feature_intro gate fallback to normal RAG: {}", exc,
                )

        # Step 1: 向量检索（业务查询，不召回 feature-intro 命名空间避免 RAG 反向污染）
        vec_results = self.vector_store.search(query, top_k=top_k, exclude_tags=["feature-intro"])
        vector_chunks = [r["content"] for r in vec_results]
        # M-3：从向量结果构建结构化 sources（与 vector_chunks 并行，K-3）
        sources = [self._make_source_ref(r) for r in vec_results]

        # Step 2: 从向量结果中提取实体（保留 M0 正则 + device_map 兜底）
        all_text = " ".join(vector_chunks) + " " + query
        seed_ids = self._extract_entity_ids(all_text)
        logger.debug("RAG extracted seed entities: {}", seed_ids)

        # Step 3: 灰度路由（图谱扩展 — M2 改造）
        router = self._get_router()
        use_neo4j = router.should_use_neo4j(thread_id) and bool(settings.neo4j_enabled)

        start_perf = time.perf_counter()
        error = False
        try:
            if use_neo4j:
                neo4j_entities, neo4j_paths = self._expand_via_neo4j(seed_ids, query)
                # Neo4j 返回 dict 列表，转为 GraphEntity
                graph_entities = [
                    GraphEntity(
                        id=e.get("id"),
                        name=e.get("name"),
                        type=e.get("type"),
                        properties=e.get("properties", {}),
                    )
                    for e in neo4j_entities
                ]
                graph_paths = neo4j_paths
            else:
                graph_entities, graph_paths = self._expand_via_networkx(seed_ids)
        except Exception as exc:  # noqa: BLE001
            error = True
            logger.warning("RAG expansion failed ({}), fallback to NetworkX", exc)
            # 降级到 NetworkX（保留 M0 行为，零回归）
            graph_entities, graph_paths = self._expand_via_networkx(seed_ids)
        finally:
            latency_ms = (time.perf_counter() - start_perf) * 1000
            # 监控埋点 + 自动回滚
            backend = "neo4j" if use_neo4j else "networkx"
            try:
                router.record_request(
                    error=error,
                    latency_ms=latency_ms,
                    backend=backend,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("RAG router.record_request failed: {}", exc)
            # M3c：Prometheus 指标埋点（feature flag 关闭时 no-op）
            if is_metrics_enabled():
                try:
                    metrics = get_metrics_collector()
                    status = "error" if error else "ok"
                    metrics.record_cypher(
                        backend=backend,
                        status=status,
                        latency_ms=latency_ms,
                    )
                    metrics.rag_total_latency_ms.observe(
                        float(latency_ms), backend=backend,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("RAG metrics hook failed: {}", exc)
            # JSON 日志埋点（M2 6 指标之一：rag_query）
            logger.info(
                json.dumps(
                    {
                        "event": "rag_query",
                        "thread_id": thread_id,
                        "backend": backend,
                        "latency_ms": round(latency_ms, 1),
                        "error": error,
                        "seed_count": len(seed_ids),
                        "entities_count": len(graph_entities),
                        "paths_count": len(graph_paths),
                        "timestamp": time.time(),
                    },
                    ensure_ascii=False,
                )
            )

        return RetrievalResult(
            vector_chunks=vector_chunks,
            graph_entities=graph_entities,
            graph_paths=graph_paths,
            confidence=self._calc_confidence(vector_chunks, graph_entities),
            sources=sources,
            # M-4：本轮实体抽取的 seed（图谱问答组装用，保证与检索同源）
            seed_ids=seed_ids,
        )

    def answer(
        self,
        query: str,
        top_k: int = 3,
        thread_id: str = "default",
    ) -> KnowledgeAnswer:
        """检索 + 生成完整链路。

        M-3 增量：``KnowledgeAnswer.sources`` 由 ``retrieve()`` 的 sources 经
        ``citation_min_score`` 过滤 + ``citation_top_n`` 截断 + score 降序后写入
        （K-2）；``citations`` 仍为全部 vector_chunks 副本（K-3 并行构建）。
        """
        result = self.retrieve(query, top_k=top_k, thread_id=thread_id)

        # 低置信度拒答
        if result.confidence < 0.25:
            return KnowledgeAnswer(
                answer="",
                citations=[],
                graph_paths=[],
                confidence=result.confidence,
                refuse=True,
                refuse_reason="检索结果置信度过低（{:.0f}%），无法生成可靠答案，建议转人工处理".format(
                    result.confidence * 100
                ),
                sources=[],
            )

        # 构建 LLM 上下文
        context = self._build_context(result)
        answer_text = self._generate(query, context)

        answer = KnowledgeAnswer(
            answer=answer_text,
            citations=result.vector_chunks.copy(),
            graph_paths=result.graph_paths.copy(),
            confidence=result.confidence,
            refuse=False,
            sources=self._filter_sources(result.sources),
        )

        # M-4：图谱问答组装（P0-4）——懒加载防循环；异常不阻断 RAG；
        # seed 为空或组装结果全空 → 不 attach（graph_answer=None，M-3 行为不变）。
        if result.seed_ids:
            try:
                from core.kg_qa import get_graph_qa_engine  # 懒加载防循环
                ga = get_graph_qa_engine().build(
                    query=query,
                    seed_ids=result.seed_ids,
                    sources=answer.sources,
                    hops=3,
                )
                if ga.nodes or ga.edges or ga.paths:
                    answer.graph_answer = ga
            except Exception as exc:  # noqa: BLE001 — 图谱组装故障不阻断 RAG
                logger.debug("graph_answer assembly skipped: {}", exc)

        return answer

    # ── M2 扩展方法 ─────────────────────────────────────────

    def _expand_via_neo4j(
        self,
        seed_ids: list[str],
        query: str,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        """Neo4j 多跳扩展 + 故障链 + 适用规程（M3a 增强：路径优化 + 模板）。

        通过 mcp_tools.tools.neo4j_tools 间接调用 KGClient，
        这样 Neo4j 不可用时自动降级 NetworkX 实现（已有 fallback 逻辑）。
        """
        if not seed_ids:
            return [], []
        entities: list[dict[str, Any]] = []
        paths: list[list[str]] = []
        seed_id = seed_ids[0]

        # 1) M3a：优先用 KGClient.expand_with_optimizer（路径优化 + top_k + LRU）
        #    feature flag 关闭或 NetworkX 模式自动 fallback 到 M2 硬编码 3 跳。
        try:
            from core.kg_client import get_kg_client
            client = get_kg_client()
            if client.current_backend_name == "neo4j":
                opt_entities, opt_paths = client.expand_with_optimizer(
                    seed_ids, hops=3, limit=50,
                )
                for e in opt_entities:
                    entities.append({
                        "id": e.get("id"),
                        "name": e.get("name"),
                        "type": e.get("type"),
                        "properties": e.get("properties", {}),
                    })
                for p in opt_paths:
                    if hasattr(p, "nodes") and hasattr(p, "relations"):
                        if p.nodes:
                            paths.append(p.nodes)
        except Exception as exc:  # noqa: BLE001
            logger.debug("RAG expand_with_optimizer failed: {}", exc)

        # 2) 故障链（仅当 query 含故障关键词）
        if any(kw in query for kw in FAULT_KEYWORDS):
            try:
                from mcp_tools.tools.neo4j_tools import get_fault_chain
                chain_result = asyncio_run_sync(
                    get_fault_chain(seed_id, max_hops=3, limit=5)
                )
                if chain_result and chain_result.get("status") == "ok":
                    for chain in chain_result.get("chains", []) or []:
                        nodes = chain.get("chain", [])
                        for node in nodes:
                            if not any(e["id"] == node.get("id") for e in entities):
                                entities.append({
                                    "id": node.get("id"),
                                    "name": node.get("name"),
                                    "type": node.get("type"),
                                    "properties": {},
                                })
                        # 构造路径
                        if len(nodes) >= 2:
                            path_ids = [n.get("id") for n in nodes]
                            if path_ids[0] != path_ids[-1]:
                                paths.append(path_ids)
            except Exception as exc:  # noqa: BLE001
                logger.debug("RAG get_fault_chain failed: {}", exc)

        # 3) 适用规程
        try:
            from mcp_tools.tools.neo4j_tools import get_applicable_regulations
            regs_result = asyncio_run_sync(
                get_applicable_regulations(device_id=seed_id, limit=10)
            )
            if regs_result and regs_result.get("status") == "ok":
                for reg in regs_result.get("regulations", []) or []:
                    reg_id = reg.get("id")
                    if reg_id and not any(e["id"] == reg_id for e in entities):
                        entities.append({
                            "id": reg_id,
                            "name": reg.get("name") or reg.get("code") or reg_id,
                            "type": "规程",
                            "properties": reg.get("properties", {}),
                        })
        except Exception as exc:  # noqa: BLE001
            logger.debug("RAG get_applicable_regulations failed: {}", exc)

        # 构造基础路径（seed → 扩展实体）
        for e in entities[:5]:
            if e.get("id") and e["id"] != seed_id:
                paths.append([seed_id, e["id"]])

        return entities, paths

    def _apply_inference_rules(
        self,
        entity_id: str,
        ctx: dict[str, Any],
        *,
        rule_ids: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        """M3a 包装 ``KGClient.apply_rules``，返回 dict 列表（便于 RAG 拼接）。

        ``enable_inference_engine=False``（默认）时返回空 list（与 M2 行为一致）。
        """
        try:
            from core.kg_client import get_kg_client
            client = get_kg_client()
            relations = client.apply_rules(
                entity_id=entity_id,
                ctx=ctx,
                rule_ids=rule_ids,
                min_confidence=min_confidence,
            )
            return [
                {
                    "src_id": r.src_id,
                    "tgt_id": r.tgt_id,
                    "relation_type": r.relation_type,
                    "confidence": r.confidence,
                    "rule_id": r.rule_id,
                    "evidence_path": r.evidence_path,
                }
                for r in relations
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("RAG _apply_inference_rules failed: {}", exc)
            return []

    def _expand_via_networkx(
        self,
        seed_ids: list[str],
    ) -> tuple[list[Any], list[list[str]]]:
        """NetworkX 2 跳扩展（M0 行为保留）。"""
        return self.knowledge_graph.expand_entities(seed_ids, hops=2)

    def _get_router(self) -> Any:
        """懒加载 GrayscaleRouter 单例。"""
        if self._router is None:
            from core.grayscale_router import get_grayscale_router
            self._router = get_grayscale_router()
        return self._router

    # ── 内部方法 ────────────────────────────────────────

    def _extract_entity_ids(self, text: str) -> list[str]:
        """从文本中提取图谱实体 ID（M-4 起委托模块级公开 util，行为不变）。"""
        return extract_entity_ids(text, self.knowledge_graph)

    @staticmethod
    def _calc_confidence(
        vector_chunks: list[str], graph_entities: list[Any],
    ) -> float:
        """综合计算检索置信度（0-1）。"""
        score = 0.0
        if vector_chunks:
            score += 0.5 * min(1.0, len(vector_chunks) / 3.0)
        if graph_entities:
            score += 0.3 * min(1.0, len(graph_entities) / 5.0)
        if vector_chunks and graph_entities:
            score += 0.2
        return min(1.0, score)

    # ── M-3：来源引用构建 helper（K-1/K-2/K-9）────────────

    @staticmethod
    def _strip_title_prefix(content: str, title: str | None = None) -> str:
        """去除 chunk 内容开头的《标题》前缀（K-9）。

        user-upload 分片形如 ``《主变运行规程》\\n\\n正文...``；feature-intro
        分片无前缀。去前缀后用于 snippet / content_excerpt，避免引用卡片
        重复显示文档名。
        """
        text = (content or "").strip()
        if not title:
            return text
        prefix = f"《{title}》"
        if text.startswith(prefix):
            return text[len(prefix):].strip()
        return text

    @staticmethod
    def _make_excerpt(text: str, max_len: int) -> str:
        """截断为最多 ``max_len`` 字符的摘要；超长追加 ``…``（K-9）。"""
        text = (text or "").strip().replace("\n", " ")
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "…"

    @staticmethod
    def _sort_sources(sources: list[SourceRef]) -> list[SourceRef]:
        """按 score 降序（score 为 None 排最后），稳定排序。"""
        return sorted(
            sources,
            key=lambda s: (s.score is None, -(s.score if s.score is not None else 0.0)),
        )

    def _filter_sources(self, sources: list[SourceRef]) -> list[SourceRef]:
        """``citation_min_score`` 过滤 + ``citation_top_n`` 截断（K-2）。

        规则：
        - ``score is None`` 的来源**保留**——前端按 K-5 降级展示「未提供匹配度」，
          避免 feature-intro 等无 score 数据被误杀；
        - ``score < citation_min_score`` 剔除；
        - 过滤后按 score 降序并截断 top_n。
        """
        min_score = float(getattr(settings, "citation_min_score", 0.25))
        top_n = int(getattr(settings, "citation_top_n", 5))
        filtered = [
            s for s in sources
            if s.score is None or s.score >= min_score
        ]
        return self._sort_sources(filtered)[:top_n]

    def _make_source_ref(self, vec_result: dict[str, Any]) -> SourceRef:
        """从 ``VectorStore.search()`` 返回项构建 :class:`SourceRef`。

        元数据来源：``metadata``（doc_id/title/source/chunk_id/filename/
        chunk_index/total_chunks/section）+ ``content`` + ``score``。文件名
        缺省时从 ``source``（形如 ``user-upload/<原名>``）反解；score 越界
        clamp 到 [0, 1]（K-2）。所有缺失字段降级为 None，绝不抛错。
        """
        meta = vec_result.get("metadata") or {}
        content = str(vec_result.get("content") or "").strip()
        score_raw = vec_result.get("score")
        score: float | None = None
        if score_raw is not None:
            try:
                score = round(min(1.0, max(0.0, float(score_raw))), 3)
            except (TypeError, ValueError):
                score = None

        title = str(meta.get("title") or "").strip()
        content_body = self._strip_title_prefix(content, title)
        source_raw = str(meta.get("source") or "").strip()
        filename = str(meta.get("filename") or "").strip() or self._filename_from_source(source_raw)

        return SourceRef(
            chunk_id=self._as_int_or_none(meta.get("chunk_id")),
            doc_id=str(meta.get("doc_id") or "").strip() or None,
            filename=filename or None,
            title=title or None,
            source=source_raw or None,
            section=str(meta.get("section") or "").strip() or None,
            score=score,
            snippet=self._make_excerpt(content_body, 120),
            content_excerpt=content_body or content,
            chunk_index=self._as_int_or_none(meta.get("chunk_index")),
            total_chunks=self._as_int_or_none(meta.get("total_chunks")),
        )

    @staticmethod
    def _filename_from_source(source: str) -> str:
        """从 ``source``（``user-upload/<原始文件名>``）反解原始文件名。"""
        raw = (source or "").strip()
        if "/" in raw:
            return raw.split("/", 1)[-1]
        return raw

    @staticmethod
    def _as_int_or_none(value: Any) -> int | None:
        """安全转 int；None / 非法值返回 None（缺失字段降级语义）。"""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_context(result: RetrievalResult) -> str:
        """融合向量候选片段 + 图谱关联子图 → 上下文。"""
        parts: list[str] = ["## 知识库片段"]

        for i, chunk in enumerate(result.vector_chunks):
            parts.append(f"[片段 {i + 1}] {chunk}")

        if result.graph_entities:
            parts.append("\n## 关联图谱实体")
            for e in result.graph_entities:
                parts.append(f"- {e.name}（{e.type}）")

        if result.graph_paths:
            parts.append("\n## 图谱检索路径")
            for path in result.graph_paths:
                parts.append(" → ".join(path))

        return "\n".join(parts)

    def _generate(self, query: str, context: str) -> str:
        """调用 DashScope LLM 生成答案（含 fallback 模版）。"""
        try:
            from dashscope import Generation

            response = Generation.call(
                model="qwen-plus",
                messages=[
                    {"role": "system", "content": (
                        "你是一个电力运维知识库助手。请基于以下检索上下文回答用户问题。\n"
                        "要求：\n"
                        "1. 只使用上下文中包含的信息作答，不要编造\n"
                        "2. 引用来源片段时标注[片段N]\n"
                        "3. 如有图谱路径，在回答末尾列出\n"
                        "4. 若上下文不足以回答，诚实地拒绝并建议转人工"
                    )},
                    {"role": "user", "content": f"上下文：\n{context}\n\n问题：{query}"},
                ],
                api_key=self._api_key,
                temperature=0.3,
                result_format="message",
            )

            if response.status_code == 200 and response.output.choices:
                choice = response.output.choices[0]
                if choice and hasattr(choice.message, "content"):
                    return choice.message.content

            logger.warning("LLM call returned status {}", response.status_code)
        except Exception as e:
            logger.warning("LLM call failed ({}), using template fallback", e)

        # Fallback: 模版答案
        return self._template_answer(query, context)

    @staticmethod
    def _template_answer(query: str, context: str) -> str:
        """无 LLM 时的模版 fallback 答案（演示用）。"""
        citations = re.findall(r"\[片段 \d+\]", context)
        cite_str = "、".join(citations) if citations else "知识库片段"

        snippets = re.findall(r"\[片段 \d+\](.*?)(?=\[片段|\Z)", context, re.DOTALL)
        top_snippet = snippets[0].strip()[:200] if snippets else ""

        graph_info = ""
        path_match = re.search(r"## 图谱检索路径\n(.*?)(?=\n##|\Z)", context, re.DOTALL)
        if path_match:
            graph_info = f"\n\n关联知识路径：\n{path_match.group(1).strip()}"

        return (
            f"根据{cite_str}中的相关内容，{top_snippet}"
            f"{graph_info}\n\n"
            f"（注：当前为模版生成答案，配置真实 DashScope Key 后可获得 LLM 增强回答）"
        )


# ── 辅助函数 ────────────────────────────────────────

def asyncio_run_sync(coro: Any) -> Any:
    """同步执行 async coroutine（兼容无 event loop 场景）。

    RAG 引擎本身是同步方法，但 mcp_tools.neo4j_tools 是 async。
    这里提供一个简单的 sync wrapper：
    - 当前**没有**运行中的事件循环 → ``asyncio.run``（正常路径）
    - 当前**有**运行中的事件循环 → 同步等待必然阻塞事件循环（B1）：
      生产 async 调用方必须改走 :func:`asyncio_run_sync_async` 并 ``await``；
      此处**快速失败**并给出指引，避免 ``future.result(timeout=10)``
      把整个服务冻结 10s。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        raise RuntimeError(
            "asyncio_run_sync 被在运行中的事件循环内调用（会阻塞事件循环，B1）。"
            "请改用 asyncio_run_sync_async(coro) 并在 async 函数中 await。"
        )
    return asyncio.run(coro)


async def asyncio_run_sync_async(coro: Any) -> Any:
    """async 版 ``asyncio_run_sync``——协程在工作线程中运行，不阻塞事件循环。

    B1 修复：RAG 同步链路（``retrieve`` / ``answer``）由 async 入口调用时，
    若直接在事件循环内 ``asyncio.run(coro)`` 或 ``future.result(timeout)`` 会
    冻结整个服务；本函数把协程放到线程池执行（等价
    ``await asyncio.wrap_future(loop.run_in_executor(...))``），事件循环保持可响应。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: asyncio.run(coro))
