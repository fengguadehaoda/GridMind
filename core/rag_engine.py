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

import json
import re
import time
from typing import Any

from loguru import logger

from api.config import settings
from api.schemas import GraphEntity, KnowledgeAnswer, RetrievalResult
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

        Args:
            query:     用户问题
            top_k:     向量检索 top_k
            thread_id: 会话 ID（用于 GrayscaleRouter 哈希取模）
        """
        # Step 1: 向量检索
        vec_results = self.vector_store.search(query, top_k=top_k)
        vector_chunks = [r["content"] for r in vec_results]

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
        )

    def answer(
        self,
        query: str,
        top_k: int = 3,
        thread_id: str = "default",
    ) -> KnowledgeAnswer:
        """检索 + 生成完整链路。"""
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
            )

        # 构建 LLM 上下文
        context = self._build_context(result)
        answer_text = self._generate(query, context)

        return KnowledgeAnswer(
            answer=answer_text,
            citations=result.vector_chunks.copy(),
            graph_paths=result.graph_paths.copy(),
            confidence=result.confidence,
            refuse=False,
        )

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
        """从文本中提取图谱实体 ID。"""
        found_ids: list[str] = []
        for pattern in ENTITY_PATTERNS:
            for m in re.finditer(pattern, text):
                keyword = m.group(1).strip()
                entities = self.knowledge_graph.search_entities(keyword)
                for e in entities:
                    if e.id not in found_ids:
                        found_ids.append(e.id)
        # 对特殊设备名直接匹配
        device_map = {
            "一号主变": "e-TR001",
            "二号主变": "e-TR002",
            "35kv母线": "e-BB002",
            "10kv母线": "e-BB001",
        }
        for alias, eid in device_map.items():
            if alias in text.lower() and eid not in found_ids:
                found_ids.append(eid)
        return found_ids

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
    - 如果当前有 event loop 在运行，则用 loop.run_until_complete
    - 否则用 asyncio.run
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # 在 event loop 中：返回 future（让调用方 await）
        # 但 RAG 是同步接口，这里使用 nest_asyncio 不安全，故采用线程池 fallback
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=10.0)
    else:
        return asyncio.run(coro)
