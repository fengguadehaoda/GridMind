"""MCP 工具服务（MCPServer + SSE 传输，端口 9901）。

注册所有工具到 MCPServer，通过 SSE 暴露给 LangGraph Agent 调用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from api.config import settings
from mcp_tools.db.database import init_db
from mcp_tools.db.seed_data import seed_all

# ── 创建 MCP Server ──────────────────────────────────
mcp = FastMCP(
    "GridMind Tools",
    instructions="GridMind 灵枢电网 MCP 工具服务",
    host=settings.mcp_host,
    port=settings.mcp_port,
)


# ═══════════════════════════════════════════════════════
# 监控类工具
# ═══════════════════════════════════════════════════════

@mcp.tool(description="获取所有设备列表（真实 SQLite 数据）")
async def get_device_list() -> str:
    from mcp_tools.tools.monitor_tools import get_device_list as _fn
    result = await _fn()
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="查询设备最新遥测数据（真实 SQLite 数据），参数：device_id-设备ID, hours-查询小时数")
async def get_device_telemetry(device_id: str, hours: int = 24) -> str:
    from mcp_tools.tools.monitor_tools import get_device_telemetry as _fn
    result = await _fn(device_id, hours)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="查询设备最新一条遥测数据，参数：device_id-设备ID")
async def get_latest_telemetry(device_id: str) -> str:
    from mcp_tools.tools.monitor_tools import get_latest_telemetry as _fn
    result = await _fn(device_id)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="查询设备详细信息，参数：device_id-设备ID")
async def get_device_info(device_id: str) -> str:
    from mcp_tools.tools.monitor_tools import get_device_info as _fn
    result = await _fn(device_id)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="查询设备巡检记录，参数：device_id-设备ID, limit-返回条数")
async def get_inspection_records(device_id: str, limit: int = 10) -> str:
    from mcp_tools.tools.monitor_tools import get_inspection_records as _fn
    result = await _fn(device_id, limit)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════
# 安规类工具
# ═══════════════════════════════════════════════════════

@mcp.tool(description="查询安规条款，参数：category-分类(操作票/验电/安全距离等), keyword-关键词")
async def get_safety_rules(category: str | None = None, keyword: str | None = None) -> str:
    from mcp_tools.tools.safety_tools import get_safety_rules as _fn
    result = await _fn(category, keyword)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="按编号查询安规条款，参数：rule_code-条款编号(如 DL/T-572-2010-1)")
async def get_safety_rule_by_code(rule_code: str) -> str:
    from mcp_tools.tools.safety_tools import get_safety_rule_by_code as _fn
    result = await _fn(rule_code)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="检查操作是否符合安规（关键词匹配），参数：operation-操作内容, device_type-设备类型(可选)")
async def check_safety_compliance(operation: str, device_type: str | None = None) -> str:
    from mcp_tools.tools.safety_tools import check_safety_compliance as _fn
    result = await _fn(operation, device_type)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════
# 诊断类工具
# ═══════════════════════════════════════════════════════

@mcp.tool(description="检测设备异常（z-score + 规则评分），参数：device_id-设备ID")
async def detect_device_anomalies(device_id: str) -> str:
    """FR-6 异常检测：z-score 滚动窗口 + 规则评分。"""
    from mcp_tools.tools.diagnosis_tools import detect_device_anomalies as _fn
    result = await _fn(device_id)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="获取设备健康评分（0-100），参数：device_id-设备ID")
async def get_device_health_score(device_id: str) -> str:
    from mcp_tools.tools.diagnosis_tools import get_device_health_score as _fn
    result = await _fn(device_id)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="获取全部设备健康评分")
async def get_all_health_scores() -> str:
    from mcp_tools.tools.diagnosis_tools import get_all_health_scores as _fn
    result = await _fn()
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="获取所有严重（critical/warning）设备列表")
async def get_critical_devices() -> str:
    from mcp_tools.tools.diagnosis_tools import get_critical_devices as _fn
    result = await _fn()
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════
# 知识库类工具
# ═══════════════════════════════════════════════════════

@mcp.tool(description="知识库问答（混合 RAG 检索 + LLM 生成），参数：query-问题")
async def query_knowledge_base(query: str) -> str:
    """FR-3 混合 RAG：向量 + 图谱联合检索。"""
    from mcp_tools.tools.knowledge_tools import query_knowledge_base as _fn
    result = await _fn(query)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="纯向量检索知识库片段，参数：query-查询词, top_k-返回条数")
async def search_knowledge_chunks(query: str, top_k: int = 3) -> str:
    from mcp_tools.tools.knowledge_tools import search_knowledge_chunks as _fn
    result = await _fn(query, top_k)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="搜索图谱实体，参数：keyword-关键词")
async def search_graph_entities(keyword: str) -> str:
    from mcp_tools.tools.knowledge_tools import search_graph_entities as _fn
    result = await _fn(keyword)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="获取图谱实体关系，参数：entity_id-实体ID")
async def get_entity_relations(entity_id: str) -> str:
    from mcp_tools.tools.knowledge_tools import get_entity_relations as _fn
    result = await _fn(entity_id)
    import json
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════
@mcp.tool(description="执行参数化 Cypher 查询（仅只读：MATCH/RETURN/WITH/WHERE，禁止写操作），参数：query-Cypher语句, params-参数化字典")
async def cypher_query(query: str, params: dict | None = None) -> str:
    """M1 新增 · 只读 Cypher 查询（白名单 + 参数化注入防护）。"""
    import json
    from mcp_tools.tools.neo4j_tools import cypher_query as _fn
    result = await _fn(query, params or {})
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="从指定实体多跳扩展（默认 3 跳，可按关系类型过滤），参数：entity_id-起始实体ID, hops-跳数(1-5), relation_types-关系类型白名单(可选)")
async def multi_hop_expand(entity_id: str, hops: int = 3, relation_types: list[str] | None = None) -> str:
    """M1 新增 · 多跳图谱扩展。"""
    import json
    from mcp_tools.tools.neo4j_tools import multi_hop_expand as _fn
    result = await _fn(entity_id, hops, relation_types)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="按变电站查询设备实例，可选设备类型过滤，参数：substation_id-变电站ID, device_type-设备类型(Transformer/CircuitBreaker/Busbar/Line,可选)")
async def find_devices_by_substation(substation_id: str, device_type: str | None = None) -> str:
    """M1 新增 · 按变电站/类型查询设备。"""
    import json
    from mcp_tools.tools.neo4j_tools import find_devices_by_substation as _fn
    result = await _fn(substation_id, device_type)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="获取故障因果链（沿 CAUSES 关系多跳推理），参数：fault_id-起始故障ID, max_hops-最大跳数(1-5)")
async def get_fault_chain(fault_id: str, max_hops: int = 3) -> str:
    """M1 新增 · 故障因果链推理。"""
    import json
    from mcp_tools.tools.neo4j_tools import get_fault_chain as _fn
    result = await _fn(fault_id, max_hops)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="查询适用规程（按设备ID或故障类型过滤），参数：device_id-设备ID(可选), fault_type-故障ID(可选)")
async def get_applicable_regulations(device_id: str | None = None, fault_type: str | None = None) -> str:
    """M1 新增 · 适用规程查询。"""
    import json
    from mcp_tools.tools.neo4j_tools import get_applicable_regulations as _fn
    result = await _fn(device_id, fault_type)
    return json.dumps(result, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════
# M3a 推理类工具
# ═══════════════════════════════════════════════════════

@mcp.tool(description="【M3a】多跳图谱推理（路径优化 + top_k 剪枝 + LRU 缓存），用于探索图谱关联、上下文召回；不适用于单跳查询（用 get_entity_relations）或全文检索（用 search_knowledge_chunks）。参数：seed_ids-起始实体ID列表(1-10个), hops-跳数(1-5), relation_types-关系类型白名单(可选), top_k-返回路径上限(默认5), use_optimizer-是否启用路径优化(默认True)")
async def kg_multi_hop_reason(
    seed_ids: list[str],
    hops: int = 3,
    relation_types: list[str] | None = None,
    top_k: int = 5,
    min_confidence: float = 0.0,
    use_optimizer: bool = True,
) -> str:
    """M3a 新增 · 多跳推理（替代 M2 multi_hop_expand 的高性能版本）。"""
    import json
    from mcp_tools.tools.kg_reasoning_tools import kg_multi_hop_reason as _fn
    result = await _fn(
        seed_ids, hops, relation_types, top_k, min_confidence, use_optimizer,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool(description="【M3a】推理规则匹配（IF-THEN DSL + 5s 超时守护），用于故障推理与应急响应建议；不适用于多跳查询（用 kg_multi_hop_reason）。参数：entity_id-目标实体ID, ctx-业务上下文(如duration_min/temp_c), rule_ids-规则白名单(可选), min_confidence-置信度下限(默认0)")
async def kg_apply_rules(
    entity_id: str,
    ctx: dict | None = None,
    rule_ids: list[str] | None = None,
    min_confidence: float = 0.0,
) -> str:
    """M3a 新增 · 推理规则匹配（需 enable_inference_engine=True）。"""
    import json
    from mcp_tools.tools.kg_reasoning_tools import kg_apply_rules as _fn
    result = await _fn(entity_id, ctx, rule_ids, min_confidence)
    return json.dumps(result, ensure_ascii=False, default=str)


# 高风险工具（需 HITL）
# ═══════════════════════════════════════════════════════

@mcp.tool(description="【高危】派发检修工单，参数：device_id-设备ID, description-故障描述, priority-优先级(high/medium/low)")
async def dispatch_work_order(device_id: str, description: str, priority: str = "medium") -> str:
    """高危工具——执行前需经 LangGraph interrupt() 人工确认。"""
    import json
    # 此工具仅返回占位数据，实际调用由 LangGraph HITL 拦截
    return json.dumps({
        "status": "pending_approval",
        "device_id": device_id,
        "description": description,
        "priority": priority,
        "message": "工单已提交待人工确认",
    }, ensure_ascii=False)


@mcp.tool(description="【高危】建议设备停运，参数：device_id-设备ID, reason-停运原因")
async def suggest_shutdown(device_id: str, reason: str) -> str:
    """高危工具——需人工确认后执行。"""
    import json
    return json.dumps({
        "status": "pending_approval",
        "device_id": device_id,
        "reason": reason,
        "message": "停运建议已提交待人工确认",
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════

def start() -> None:
    """初始化数据库 + 种子数据，启动 MCP SSE 服务。"""
    logger.info("Initializing database...")
    init_db()
    seed_all()
    logger.info("Starting MCP tool service on port {}...", settings.mcp_port)
    mcp.run(transport="sse")


if __name__ == "__main__":
    start()
