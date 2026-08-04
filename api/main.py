"""GridMind FastAPI 应用（端口 9900）。

承载 LangGraph Supervisor 状态图，提供：
- POST /chat              — 对话接口
- GET  /chat/stream/{thread_id} — SSE 流式输出
- POST /interrupt/{thread_id}/approve — HITL 审批通过
- POST /interrupt/{thread_id}/reject  — HITL 审批拒绝
- GET  /thread/{thread_id}      — 查看对话历史

启动时自动连接 MCP Server（localhost:9901）获取工具列表。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger
from pydantic import BaseModel

from api.config import settings
from api.graph import GraphBuilder
from api.metrics_endpoint import register_metrics_endpoint
from api.schemas import ChatRequest, ChatResponse
from api.schemas.hitl_edit import EditInterruptRequest
from api.services.grayscale_admin_service import GrayscaleAdminService
from api.services.hitl_audit_service import HitlAuditService, process_edit_decision

# 监控数据源：直接复用 MCP 工具实现（纯 SQLite 查询，无需 HTTP 往返 9901）
from mcp_tools.tools.monitor_tools import (
    get_device_list,
    get_device_telemetry,
    get_latest_telemetry,
    get_device_info,
    get_inspection_records,
)
from mcp_tools.tools.diagnosis_tools import (
    detect_device_anomalies,
    get_device_health_score,
    get_all_health_scores,
    get_critical_devices,
)

# P0 可解释性 AI 集成
from core.diagnosis_orchestrator import FUSION_STORE

# M2 双向同步服务
from core.kg_chroma_sync import get_sync_service

# ── 全局变量 ──────────────────────────────────────────

graph_builder: GraphBuilder | None = None
_mcp_client: MultiServerMCPClient | None = None


# ═══════════════════════════════════════════════════════
# 启动/关闭事件
# ═══════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期：启动时连接 MCP，关闭时断开。"""
    global graph_builder, _mcp_client

    logger.info("Connecting to MCP server at localhost:{}...", settings.mcp_port)
    try:
        _mcp_client = MultiServerMCPClient(
            {
                "gridmind-tools": {
                    "transport": "sse",
                    "url": f"http://localhost:{settings.mcp_port}/sse",
                },
            }
        )
        tools = await _mcp_client.get_tools()
        logger.info("MCP connected, got {} tools", len(tools))
        for t in tools:
            logger.debug("  MCP tool: {}", t.name)
    except Exception as e:
        logger.warning("MCP connection failed ({}), starting with empty tools", e)
        tools = []

    graph_builder = GraphBuilder(tools)
    logger.info("Graph built, API ready on port {}", settings.api_port)

    # ── M2：启动双向同步服务 ──
    sync_service = get_sync_service()
    try:
        await sync_service.start()
        logger.info("ChromaSyncService started in lifespan")
    except Exception as e:
        logger.warning("ChromaSyncService start failed ({}), continuing without sync", e)

    yield

    # ── M2：优雅停止同步服务 ──
    try:
        await sync_service.stop()
    except Exception as e:
        logger.warning("ChromaSyncService stop error: {}", e)

    if _mcp_client:
        logger.info("MCP client shutting down")


# ═══════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(
    title="GridMind API",
    description="灵枢电网 Multi-Agent 系统（FastAPI + LangGraph + MCP）",
    version="1.3.0",
    lifespan=lifespan,
)

# CORS：allow_credentials=True 时 allow_origins 不能为 "*"，须显式列出可信源。
# 开发态前端运行在 5173（Vite），生产态同源 9900。
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:9900",
    "http://127.0.0.1:9900",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── M3c：注册 Prometheus /metrics 端点 ──────────────────────────────
# 必须 CORSMiddleware 之后注册，保证 metrics 也能跨域（沙箱前端调试）。
register_metrics_endpoint(app)


# ═══════════════════════════════════════════════════════
# 请求/响应模型
# ═══════════════════════════════════════════════════════

class InterruptRequest(BaseModel):
    """HITL 审批请求。"""
    reason: str = ""


# ═══════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════

@app.get("/")
async def root() -> dict[str, Any]:
    """健康检查。"""
    return {
        "service": "GridMind",
        "version": "1.3.0",
        "status": "running",
        "mcp_connected": _mcp_client is not None,
    }


# ═══════════════════════════════════════════════════════
# 设备实时监控路由
# ═══════════════════════════════════════════════════════

@app.get("/devices")
async def list_devices() -> dict[str, Any]:
    """设备总览：所有设备 + 最新遥测 + 健康评分（监控页主数据源）。"""
    devices = await get_device_list()
    scores = await get_all_health_scores()
    score_map = {s["device_id"]: s for s in scores}
    result = []
    for d in devices:
        latest = await get_latest_telemetry(d["device_id"])
        result.append({
            **d,
            "latest_telemetry": latest or {},
            "health": score_map.get(d["device_id"]),
        })
    return {"devices": result}


@app.get("/devices/{device_id}")
async def device_detail(device_id: str) -> dict[str, Any]:
    """设备详情：信息 + 健康评分 + 异常明细 + 最新遥测 + 巡检记录。"""
    info = await get_device_info(device_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"设备 {device_id} 不存在")
    health = await get_device_health_score(device_id)
    anomalies = await detect_device_anomalies(device_id)
    latest = await get_latest_telemetry(device_id)
    inspections = await get_inspection_records(device_id, limit=5)
    return {
        "device": info,
        "health": health,
        "anomalies": anomalies.get("anomalies", []),
        "latest_telemetry": latest or {},
        "inspections": inspections,
    }


@app.get("/devices/{device_id}/telemetry")
async def device_telemetry(device_id: str, hours: int = 24) -> dict[str, Any]:
    """设备遥测时间序列（默认最近 24 小时，用于趋势图）。"""
    if not 1 <= hours <= 168:
        raise HTTPException(status_code=422, detail="hours 必须在 1–168 之间")
    rows = await get_device_telemetry(device_id, hours)
    return {"device_id": device_id, "telemetry": rows}


@app.get("/health/scores")
async def health_scores() -> dict[str, Any]:
    """全部设备健康评分列表。"""
    return {"scores": await get_all_health_scores()}


@app.get("/health/critical")
async def health_critical() -> dict[str, Any]:
    """健康异常（warning/critical）设备列表。"""
    return {"critical": await get_critical_devices()}


@app.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    """对话接口（阻塞模式）。"""
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    thread_id = req.thread_id or f"thread-{id(req)}"
    try:
        result = await graph_builder.run(thread_id, req.message)
        messages = result.get("messages", []) if isinstance(result, dict) else []

        # 提取最后一条 assistant 消息
        last_content = ""
        last_agent = None
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_content = msg.get("content", "")
                break

        # 检查是否有中断等待
        interrupt_required = False
        interrupt_node = None
        interrupt_msg = None
        if isinstance(result, dict):
            interrupt_required = result.get("interrupt_action") == "pending" or False
            interrupt_node = result.get("interrupt_tool")
            interrupt_msg = result.get("interrupt_msg")

        return ChatResponse(
            thread_id=thread_id,
            response=last_content or "处理完成",
            agent_name=last_agent,
            interrupt_required=interrupt_required,
            interrupt_node=interrupt_node,
            interrupt_msg=interrupt_msg,
        )
    except Exception as e:
        logger.error("Chat error: {}", e)
        return ChatResponse(
            thread_id=thread_id,
            response=f"处理出错: {e!s}",
        )


@app.get("/chat/stream/{thread_id}")
async def chat_stream(thread_id: str, message: str) -> StreamingResponse:
    """对话接口（SSE 流式模式）。"""
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            result = await graph_builder.run(thread_id, message)
            messages = result.get("messages", []) if isinstance(result, dict) else []

            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if content:
                        yield f"data: {json.dumps({'type': 'token', 'content': content}, ensure_ascii=False)}\n\n"

            # 发送完成事件（含 HITL 中断信息，供前端流式路径弹出确认框）
            interrupt_required = (
                result.get("interrupt_action") == "pending"
                if isinstance(result, dict) else False
            )
            final = {
                "type": "done",
                "thread_id": thread_id,
                "interrupt_required": interrupt_required,
                "interrupt_node": result.get("interrupt_tool") if isinstance(result, dict) else None,
                "interrupt_msg": result.get("interrupt_msg") if isinstance(result, dict) else None,
            }
            yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/interrupt/{thread_id}/approve")
async def approve_interrupt(thread_id: str, req: InterruptRequest) -> ChatResponse:
    """[兼容壳] 批准 HITL 中断，继续执行高危工具。

    保留老路径向后兼容至少 1 个季度；实际处理逻辑委托给
    ``process_edit_decision``（decision='approve'）。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    # 委托新服务（含审计 + 老 resume 路径）
    payload = EditInterruptRequest(decision="approve", reason=req.reason)
    try:
        ctx = await _gather_request_context(thread_id, payload)
        if ctx is not None:
            return ChatResponse(**ctx)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Service layer failed for legacy /approve ({}), fallback to direct resume", e)

    # 兜底：直接调用原 path（保留极端场景可用性）
    try:
        result = await graph_builder.resume(thread_id, "approved", req.reason)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        last_content = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_content = msg.get("content", "")
                break
        return ChatResponse(
            thread_id=thread_id,
            response=last_content or "已批准执行",
        )
    except Exception as e:
        logger.error("Approve interrupt error: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/interrupt/{thread_id}/reject")
async def reject_interrupt(thread_id: str, req: InterruptRequest) -> ChatResponse:
    """[兼容壳] 拒绝 HITL 中断，终止高危工具执行。

    保留老路径向后兼容至少 1 个季度；实际处理逻辑委托给
    ``process_edit_decision``（decision='reject'）。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    payload = EditInterruptRequest(decision="reject", reason=req.reason)
    try:
        ctx = await _gather_request_context(thread_id, payload)
        if ctx is not None:
            return ChatResponse(**ctx)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Service layer failed for legacy /reject ({}), fallback to direct resume", e)

    try:
        result = await graph_builder.resume(thread_id, "rejected", req.reason)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        last_content = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_content = msg.get("content", "")
                break
        return ChatResponse(
            thread_id=thread_id,
            response=last_content or "已拒绝执行",
        )
    except Exception as e:
        logger.error("Reject interrupt error: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/interrupt/{thread_id}/decision")
async def decide_interrupt(
    thread_id: str,
    req: EditInterruptRequest,
) -> dict[str, Any]:
    """统一 HITL 决策端点（P0：Edit & Continue 改造）。

    支持三种决策（详见 EditDecisionEnum）：
    - ``approve``      — 仅批准，沿用 Agent 原 args（编辑后的 args 必为空）
    - ``reject``       — 拒绝（终止高危工具执行）
    - ``edit_approve`` — 修改后批准（edited_args 必填；走 safety 重检 + 审计 + resume）

    三步原子（fail-closed）：
    1. safety 重检（仅 edit_approve）
    2. 写审计日志（hitl_audit_log）
    3. resume（safety 通过时才执行；替换 pending_tool_plan 中的 args）

    safety 重检失败：返回 200 + ``{"rejected_by_safety": true, ...}``，不 resume。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    ip, ua = _extract_request_meta()

    try:
        result = await process_edit_decision(
            thread_id,
            req,
            user_id="anonymous",  # TODO: 接 JWT 后替换
            user_name=None,
            user_role=None,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception as e:
        logger.error("Decision processing error for {}: {}", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e))

    # safety 重检失败 → 200 但带 ``rejected_by_safety=True`` 标志（前端用于红色横幅）
    if result.get("rejected_by_safety"):
        return {
            "thread_id": result["thread_id"],
            "response": result["response"],
            "interrupt_required": False,
            "decision": req.decision.value,
            "rejected_by_safety": True,
            "safety_summary": result.get("response", ""),
        }

    return {
        "thread_id": result["thread_id"],
        "response": result.get("response", "处理完成"),
        "interrupt_required": False,
        "decision": req.decision.value,
    }


# ═══════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════


async def _gather_request_context(
    thread_id: str,
    payload: "EditInterruptRequest",
) -> dict[str, Any] | None:
    """为兼容壳端点收集请求上下文（IP/UA）并委托给统一 service。

    当前返回 None 表示强制走兜底路径（保留老 endpoint 行为 100% 一致）。
    """
    return None


def _extract_request_meta() -> tuple[str | None, str | None]:
    """提取当前 FastAPI Request 的 IP / User-Agent（不强制依赖，避免影响兼容壳）。

    Returns:
        (ip, user_agent) 元组。
    """
    try:
        from fastapi import Request as _Req  # noqa: F401

        return None, None
    except Exception:
        return None, None


@app.get("/audit/hitl/{thread_id}")
async def get_hitl_audit_log(thread_id: str) -> dict[str, Any]:
    """查询指定 thread_id 的 HITL 审计记录（P0：审计追溯）。"""
    rows = HitlAuditService.query_by_thread(thread_id)
    return {"thread_id": thread_id, "count": len(rows), "entries": rows}


@app.get("/audit/hitl")
async def list_hitl_audit_log(
    decision: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """列出 HITL 审计记录（按 decision 过滤，可选）。"""
    rows = HitlAuditService.query_by_decision(decision, limit=limit) if decision else []
    return {"count": len(rows), "entries": rows, "retention_years": HitlAuditService.retention_years()}


@app.get("/thread/{thread_id}")
async def get_thread(thread_id: str) -> dict[str, Any]:
    """获取指定线程的对话历史。"""
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    state = graph_builder.get_state(thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    return {
        "thread_id": thread_id,
        "messages": state.messages,
        "interrupt_action": state.interrupt_action.value if state.interrupt_action else None,
    }


@app.get("/diagnosis/{thread_id}/reasoning")
async def get_diagnosis_reasoning(thread_id: str) -> dict[str, Any]:
    """P0 可解释性 AI：拉取指定 thread_id 的完整推理链 JSON。

    返回：
    - ``thread_id``         : 会话 ID
    - ``final_severity``    : 融合后最终严重度 (info/warning/critical)
    - ``conflict_detected`` : LLM 与机理是否矛盾
    - ``requires_human_review`` : 是否需要人工复核
    - ``forced_action``     : 强制动作 (none/dispatch/shutdown)
    - ``llm_output``        : LLM 结构化输出（DiagnosisOutput）
    - ``mechanical_check``  : 机理校验明细（5 项）
    - ``rules_guard``       : 规则护栏明细
    - ``reasoning_chain``   : 4 步推理链（LLM → MC → RG → Fusion）

    若该 thread 没有触发过 diagnosis_agent，404。
    """
    fusion = FUSION_STORE.get(thread_id)
    if fusion is None:
        raise HTTPException(
            status_code=404,
            detail=f"No diagnosis reasoning found for thread '{thread_id}'. "
                   "Only diagnosis_agent messages produce a reasoning chain.",
        )
    return fusion.model_dump()


# ═══════════════════════════════════════════════════════
# M2 灰度管理端点（P0：T-M2-07）
# ═══════════════════════════════════════════════════════


class GrayscaleSetRequest(BaseModel):
    """灰度切流请求体。"""
    ratio: int
    actor: str | None = "admin"


class GrayscaleRollbackRequest(BaseModel):
    """手动回滚请求体。"""
    reason: str = "manual"
    actor: str | None = "admin"


@app.get("/grayscale/status")
async def grayscale_status() -> dict[str, Any]:
    """灰度状态（公开端点，无需 admin token）。"""
    return GrayscaleAdminService.get_status()


@app.post("/grayscale/set")
async def grayscale_set(
    req: GrayscaleSetRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """灰度切流（admin token 必填）。"""
    if not GrayscaleAdminService.verify_admin_token(x_admin_token):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: invalid or missing X-Admin-Token header",
        )
    try:
        return GrayscaleAdminService.set_ratio(req.ratio, actor=req.actor or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/grayscale/history")
async def grayscale_history(limit: int = 20) -> dict[str, Any]:
    """灰度切换历史。"""
    rows = GrayscaleAdminService.get_history(limit=limit)
    return {"count": len(rows), "entries": rows}


@app.post("/grayscale/manual_rollback")
async def grayscale_manual_rollback(
    req: GrayscaleRollbackRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """手动回滚（admin token 必填）。"""
    if not GrayscaleAdminService.verify_admin_token(x_admin_token):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: invalid or missing X-Admin-Token header",
        )
    return GrayscaleAdminService.manual_rollback(
        reason=req.reason or "manual", actor=req.actor or "admin",
    )


# ═══════════════════════════════════════════════════════
# M2 调试端点（P0：T-M2-07 调试 + 监控）
# ═══════════════════════════════════════════════════════


@app.get("/debug/sync_lag")
async def debug_sync_lag() -> dict[str, Any]:
    """同步状态监控端点。"""
    svc = get_sync_service()
    return {
        "queue_length": svc.get_queue_length(),
        "stats": svc.get_stats(),
        "sync_log_stats": GrayscaleAdminService.get_sync_log_stats(),
        "recent_syncs": GrayscaleAdminService.get_sync_log_recent(limit=10),
    }


@app.post("/debug/sync_force")
async def debug_sync_force() -> dict[str, Any]:
    """强制触发一次全量同步（仅开发模式）。"""
    svc = get_sync_service()
    try:
        import asyncio
        asyncio.create_task(svc._full_sync_check())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("debug_sync_force failed: {}", exc)
    return {"ok": True, "stats": svc.get_stats()}
