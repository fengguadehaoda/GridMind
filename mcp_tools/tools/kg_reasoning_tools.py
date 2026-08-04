"""GridMind 知识图谱 M3a · 推理类 MCP 工具（kg_multi_hop_reason + kg_apply_rules）。

工具清单（M3a 新增 · AC-4）
--------------------------------
1. ``kg_multi_hop_reason(seed_ids, hops, ...)`` — 多跳推理（路径优化 + 模板 + 注入防护）
2. ``kg_apply_rules(entity_id, ctx, ...)``     — 规则匹配（IF-THEN DSL + 5s 超时守护）

工具描述（MCP 规范）
--------------------
- 入参/返回严格走 Pydantic v2 校验；
- 描述中明确"何时用 / 何时不用"（AC-22）；
- 失败时返回 ``{"status": "error", "error_code": "...", "message": "..."}``。

Cypher 注入防护
----------------
- 所有 Cypher 走 ``CypherTemplateRegistry.render``（$param 参数化 + 正则黑名单）。
- 规则执行受 ``threading.Timer(timeout_s=5s)`` 守护。

降级路径
--------
- ``neo4j_enabled=False`` → 调用 ``KGClient.expand_entities()``（M2 NetworkX 行为）；
- ``enable_kg_path_optimizer=False`` → 跳过 ``KGPathOptimizer`` 直接走 M2 硬编码 3 跳；
- ``enable_inference_engine=False`` → ``kg_apply_rules`` 返回 ``{"inferred_relations": []}``。
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pydantic 入参 / 返回 model（M3a 严格校验）
# ─────────────────────────────────────────────────────────────────────────────

class KGMultiHopReasonInput(BaseModel):
    """``kg_multi_hop_reason`` 入参。"""
    seed_ids: list[str] = Field(
        ..., min_length=1, max_length=10,
        description="起始实体 ID 列表（1-10 个）",
    )
    hops: int = Field(3, ge=1, le=5, description="跳数（1-5）")
    relation_types: list[str] | None = Field(
        None, max_length=20,
        description="关系类型白名单（可选，最多 20 个）",
    )
    top_k: int = Field(5, ge=1, le=20, description="返回路径上限（1-20）")
    min_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="置信度下限（0-1）",
    )
    use_optimizer: bool = Field(
        True, description="是否启用路径优化（默认 True）",
    )


class KGMultiHopReasonOutput(BaseModel):
    """``kg_multi_hop_reason`` 返回。"""
    entities: list[dict[str, Any]] = Field(default_factory=list)
    paths: list[dict[str, Any]] = Field(default_factory=list)
    backend: str = "networkx"
    latency_ms: float = 0.0
    cache_hit: bool = False
    status: str = "ok"
    error_code: str | None = None
    message: str | None = None


class KGApplyRulesInput(BaseModel):
    """``kg_apply_rules`` 入参。"""
    entity_id: str = Field(..., min_length=1, max_length=128, description="目标实体 ID")
    ctx: dict[str, Any] = Field(
        default_factory=dict,
        description="业务上下文（如 duration_min / temp_c / load_pct）",
    )
    rule_ids: list[str] | None = Field(
        None, max_length=20,
        description="规则 ID 白名单（None = 全部启用规则）",
    )
    min_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="置信度下限（0-1）",
    )


class KGApplyRulesOutput(BaseModel):
    """``kg_apply_rules`` 返回。"""
    inferred_relations: list[dict[str, Any]] = Field(default_factory=list)
    rules_fired: list[str] = Field(default_factory=list)
    rules_total: int = 0
    backend: str = "networkx"
    latency_ms: float = 0.0
    status: str = "ok"
    error_code: str | None = None
    message: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 2. 工具函数：kg_multi_hop_reason
# ─────────────────────────────────────────────────────────────────────────────

async def kg_multi_hop_reason(
    seed_ids: list[str],
    hops: int = 3,
    relation_types: list[str] | None = None,
    top_k: int = 5,
    min_confidence: float = 0.0,
    use_optimizer: bool = True,
) -> dict[str, Any]:
    """多跳推理（MCP 工具 · AC-4）。

    用于：
        - 探索图谱关联（多跳扩展 + top_k 剪枝 + LRU 缓存）。
        - 与 Knowledge Agent 配合：在 LangGraph 中按需调用。

    不适用于：
        - 单跳关系查询（用 get_entity_relations / get_fault_chain）。
        - 全文 / 向量检索（用 search_knowledge_chunks）。
        - 设备清单查询（用 find_devices_v1 模板或 find_devices_by_substation）。

    参数:
        seed_ids:       起始实体 ID 列表（1-10 个）。
        hops:           跳数（1-5）。
        relation_types: 关系类型白名单（可选，最多 20 个）。
        top_k:          返回路径上限（1-20，默认 5）。
        min_confidence: 置信度下限（0-1，默认 0）。
        use_optimizer:  是否启用 KGPathOptimizer（默认 True；False 时走 M2 硬编码 3 跳）。

    返回:
        ``{"status": "ok"|"error", "entities": [...], "paths": [...],
          "backend": "neo4j"|"networkx", "latency_ms": float,
          "cache_hit": bool, ...}``

        失败时 ``{"status": "error", "error_code": "TEMPLATE_NOT_FOUND" / ...}``
    """
    start = time.perf_counter()
    try:
        # Pydantic 校验（直接传 seed_ids；勿 list() 包装——否则错误类型会被强制转 list 旁路校验）
        params = KGMultiHopReasonInput(
            seed_ids=seed_ids,
            hops=int(hops),
            relation_types=relation_types,
            top_k=int(top_k),
            min_confidence=float(min_confidence),
            use_optimizer=bool(use_optimizer),
        )
    except Exception as exc:  # noqa: BLE001
        return KGMultiHopReasonOutput(
            status="error",
            error_code="INVALID_PARAM",
            message=f"参数校验失败: {exc}",
        ).model_dump()

    try:
        from core.kg_client import get_kg_client

        client = get_kg_client()
        backend = client.current_backend_name

        # 优先尝试模板渲染（multi_hop_v1）
        from core.kg_cypher_templates import (
            TemplateNotFound, TemplateDisabled, MissingParamError,
            CypherInjectionRisk,
        )
        template_used: str | None = None
        if backend == "neo4j":
            try:
                rows = client.execute_template(
                    "multi_hop_v1",
                    {
                        "seed_ids": params.seed_ids,
                        "hops": params.hops,
                        "relation_types": params.relation_types,
                        "limit": params.top_k * 20,
                    },
                )
                entities = [
                    {
                        "id": r.get("tgt_id"),
                        "name": r.get("target_name"),
                        "type": "Entity",
                        "properties": {},
                    }
                    for r in rows
                ]
                template_used = "multi_hop_v1"
            except (TemplateNotFound, TemplateDisabled):
                # fallback 到 optimizer 路径
                pass
            except (MissingParamError, CypherInjectionRisk) as exc:
                return KGMultiHopReasonOutput(
                    status="error",
                    error_code=type(exc).__name__,
                    message=str(exc),
                    backend=backend,
                ).model_dump()

        # 路径优化（默认开启；M2 fallback 已内置）
        if params.use_optimizer and not template_used:
            ents, opt_paths = client.expand_with_optimizer(
                params.seed_ids,
                hops=params.hops,
                relation_types=params.relation_types,
                limit=max(100, params.top_k * 20),
            )
            # min_confidence 过滤（路径 cost.confidence）
            if params.min_confidence > 0:
                opt_paths = [
                    p for p in opt_paths
                    if p.cost.confidence >= params.min_confidence
                ]
            paths_dicts = [
                {
                    "nodes": p.nodes,
                    "relations": p.relations,
                    "hops": p.cost.hops,
                    "confidence": p.cost.confidence,
                    "estimated_latency_ms": p.cost.estimated_latency_ms,
                    "backend": p.backend,
                }
                for p in opt_paths[: params.top_k]
            ]
            entities = ents
        elif not template_used:
            # use_optimizer=False → 走 M2 硬编码 multi_hop_expand
            from mcp_tools.tools.neo4j_tools import multi_hop_expand
            hop_result = await multi_hop_expand(
                params.seed_ids[0],
                hops=params.hops,
                relation_types=params.relation_types,
                limit=params.top_k * 20,
            )
            entities = hop_result.get("entities", []) if isinstance(hop_result, dict) else []
            paths_dicts = []

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.info(json.dumps({
            "event": "kg_multi_hop_reason",
            "seed_count": len(params.seed_ids),
            "hops": params.hops,
            "top_k": params.top_k,
            "use_optimizer": params.use_optimizer,
            "backend": backend,
            "latency_ms": round(latency_ms, 2),
            "entities_count": len(entities),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        return KGMultiHopReasonOutput(
            entities=entities[: params.top_k * 5],
            paths=paths_dicts,
            backend=backend,
            latency_ms=round(latency_ms, 2),
            cache_hit=False,
            status="ok",
        ).model_dump()

    except Exception as exc:  # noqa: BLE001
        logger.error("kg_multi_hop_reason 失败: {}", exc)
        return KGMultiHopReasonOutput(
            status="error",
            error_code="INTERNAL_ERROR",
            message=str(exc),
            latency_ms=(time.perf_counter() - start) * 1000.0,
        ).model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# 3. 工具函数：kg_apply_rules
# ─────────────────────────────────────────────────────────────────────────────

async def kg_apply_rules(
    entity_id: str,
    ctx: dict[str, Any] | None = None,
    rule_ids: list[str] | None = None,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    """规则匹配（MCP 工具 · AC-4）。

    用于：
        - 故障推理（IF-THEN 规则 + 5s 超时守护）。
        - 应急响应建议（基于已注册的内置规则）。

    不适用于：
        - 多跳扩展（用 kg_multi_hop_reason）。
        - 全文检索（用 search_knowledge_chunks）。
        - 单规则手动执行（直接调用 ``ReasoningRulesEngine.infer``）。

    参数:
        entity_id:      目标实体 ID（1-128 字符）。
        ctx:            业务上下文（如 ``{"duration_min": 45, "temp_c": 105}``）。
        rule_ids:       规则 ID 白名单（None = 全部启用规则，最多 20 个）。
        min_confidence: 置信度下限（0-1，默认 0）。

    返回:
        ``{"status": "ok"|"error", "inferred_relations": [...],
          "rules_fired": [...], "rules_total": N, "backend": "...",
          "latency_ms": float}``

        失败时 ``{"status": "error", "error_code": "INVALID_PARAM" / ...}``
    """
    start = time.perf_counter()
    try:
        params = KGApplyRulesInput(
            entity_id=str(entity_id),
            ctx=ctx or {},
            rule_ids=rule_ids,
            min_confidence=float(min_confidence),
        )
    except Exception as exc:  # noqa: BLE001
        return KGApplyRulesOutput(
            status="error",
            error_code="INVALID_PARAM",
            message=f"参数校验失败: {exc}",
        ).model_dump()

    try:
        from core.kg_client import get_kg_client
        from api.config import settings

        client = get_kg_client()
        backend = client.current_backend_name

        # feature flag 检查（默认关闭 → 返回空）
        if not getattr(settings, "inference_engine_enabled", False):
            return KGApplyRulesOutput(
                inferred_relations=[],
                rules_fired=[],
                rules_total=0,
                backend=backend,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                status="ok",
                message="inference_engine_enabled=False, 跳过规则推理",
            ).model_dump()

        relations = client.apply_rules(
            entity_id=params.entity_id,
            ctx=params.ctx,
            rule_ids=params.rule_ids,
            min_confidence=params.min_confidence,
        )

        rules_fired = sorted({r.rule_id for r in relations if hasattr(r, "rule_id")})

        from core.kg_reasoning_rules import get_rules_engine
        engine = get_rules_engine(client=client)
        rules_total = len(engine.list_rules(enabled_only=True))

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.info(json.dumps({
            "event": "kg_apply_rules",
            "entity_id": params.entity_id,
            "rules_total": rules_total,
            "rules_fired": rules_fired,
            "inferred_count": len(relations),
            "latency_ms": round(latency_ms, 2),
            "backend": backend,
            "timestamp": time.time(),
        }, ensure_ascii=False))

        return KGApplyRulesOutput(
            inferred_relations=[
                {
                    "src_id": r.src_id,
                    "tgt_id": r.tgt_id,
                    "relation_type": r.relation_type,
                    "confidence": r.confidence,
                    "rule_id": r.rule_id,
                    "evidence_path": r.evidence_path,
                }
                for r in relations
            ],
            rules_fired=rules_fired,
            rules_total=rules_total,
            backend=backend,
            latency_ms=round(latency_ms, 2),
            status="ok",
        ).model_dump()

    except Exception as exc:  # noqa: BLE001
        logger.error("kg_apply_rules 失败: {}", exc)
        return KGApplyRulesOutput(
            status="error",
            error_code="INTERNAL_ERROR",
            message=str(exc),
            latency_ms=(time.perf_counter() - start) * 1000.0,
        ).model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# 4. 工具注册辅助（MCP server.py 调用）
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "KGMultiHopReasonInput",
    "KGMultiHopReasonOutput",
    "KGApplyRulesInput",
    "KGApplyRulesOutput",
    "kg_multi_hop_reason",
    "kg_apply_rules",
]