"""GridMind FastAPI 应用（端口 9900）。

承载 LangGraph Supervisor 状态图，提供：
- POST /chat              — 对话接口
- GET  /chat/stream/{thread_id} — SSE 流式输出
- POST /interrupt/{thread_id}/approve — HITL 审批通过
- POST /interrupt/{thread_id}/reject  — HITL 审批拒绝
- GET  /thread/{thread_id}      — 查看对话历史
- GET  /admin/checkpoint-stats  — V1.5.1 Checkpoint 统计（admin 鉴权）

启动时自动连接 MCP Server（localhost:9901）获取工具列表。

**V1.5.1 T02 改动**（架构 §6 T02 · §2.1）：
- ✅ ``lifespan`` 钩子增加：``await checkpoint_service.async_init()`` +
  ``await graph_builder.async_init()`` + 后台 cleanup task
- ✅ Shutdown 增加：cancel cleanup task + ``await checkpoint_service.aclose()``
- ✅ 新增 ``GET /admin/checkpoint-stats`` 端点
- ✅ ``/thread/{id}`` 改用 ``await graph_builder.aget_state(...)``
  （兼容 AsyncSqliteSaver 的异步访问，T01 的 sync ``get_state`` 保留为测试兜底）
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncGenerator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.config import APP_VERSION, settings
from api.graph import GraphBuilder
from api.metrics_endpoint import register_metrics_endpoint
from api.schemas import (
    AbortRequest,
    ChatRequest,
    ChatResponse,
    KnowledgeAnswer,
    ModelSwitchRequest,
    PauseRequest,
    ResumeRequest,
    RewindRequest,
    SessionActionResponse,
    SessionListResponse,
    SessionRenameRequest,
    ThreadSummary,
)
from api.routers import feature_intro_router, knowledge_upload_router
from api.schemas.hitl_edit import EditInterruptRequest
from api.services.auth import (
    verify_audit_thread_access,
    verify_jwt_if_prod,
    verify_thread_ownership,
    verify_thread_ownership_if_prod,
)
from api.services.error_handler import safe_endpoint
from api.services.grayscale_admin_service import GrayscaleAdminService
from api.services.hitl_audit_service import HitlAuditService, process_edit_decision
from api.services.rbac import (
    AUDIT_FULL_ACCESS_ROLES,
    Role,
    get_role,
    require_role,
)
from api.services.session_lock import SessionLockTimeout, session_lock_manager
from api.services.sse_event_emitter import sse_event_emitter
from api.services.thread_store import (
    ThreadStore,
    delete_thread,
    ensure_thread_owned,
    get_model_for_thread,
    resolve_model,
    set_model_for_thread,
)


# ── Admin Token 鉴权依赖（T05 新增，与 GrayscaleAdminService 同源）──────────
# 注意（V1.7.0）：灰度/管理端点已统一改用 ``require_role(OPERATOR, ADMIN)``
# （其内部已内置 admin token 等效管理员逻辑），本依赖仅保留定义以兼容
# 外部直接引用，新端点请勿再使用。
#: - 无 X-Admin-Token header → **401 Unauthorized**（让客户端知道"需要鉴权"）
#: - 有 header 但 token 不对 → **403 Forbidden**（让客户端知道"鉴权已失败"）
async def verify_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> bool:
    """校验 admin token（T05 新增，与既有 GrayscaleAdminService 一致 token 来源）。

    Returns:
        True — token 校验通过。

    Raises:
        HTTPException 401 — header 缺失（``X-Admin-Token header missing``）。
        HTTPException 403 — token 不匹配（``Invalid admin token``）。
    """
    from api.services.grayscale_admin_service import GrayscaleAdminService

    if x_admin_token is None or x_admin_token == "":
        raise HTTPException(
            status_code=401,
            detail="X-Admin-Token header missing",
            headers={"WWW-Authenticate": "X-Admin-Token"},
        )
    if not GrayscaleAdminService.verify_admin_token(x_admin_token):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin token",
        )
    return True


from core.llm_client import (
    AVAILABLE_MODELS,
    get_current_model,
    get_default_model,
    set_current_model,
)

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
_checkpoint_cleanup_task: asyncio.Task[None] | None = None
# B4：MCP 工具真实数量（lifespan 连接后更新；健康检查用，非恒 true）
_mcp_tools_count: int = 0
# B4：MCP 连接重试参数（3 次退避 1s/2s/4s）
_MCP_RETRY_ATTEMPTS: int = 4
_MCP_RETRY_BACKOFF_S: tuple[int, ...] = (1, 2, 4)


# ═══════════════════════════════════════════════════════
# 启动/关闭事件
# ═══════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期（V1.5.1 T02 改造）。

    启动顺序：
        1. 连接 MCP server（拉工具列表）
        2. 调 ChromaSyncService 启动（M2 已有）
        3. ``await checkpoint_service.async_init()`` —— 拿到 AsyncSqliteSaver
        4. 构造 ``GraphBuilder(tools)``（仅构建框架，不 compile）
        5. ``await graph_builder.async_init()`` —— compile + 设 COMPILED_GRAPH
        6. ``register_cleanup_task()`` —— 后台每 5 分钟扫 TTL 过期

    关闭顺序：
        1. cancel cleanup task + await 退出
        2. ``await checkpoint_service.aclose()`` —— 关闭 aiosqlite 连接
        3. ChromaSyncService 停止
        4. MCP client 断开
    """
    global graph_builder, _mcp_client, _checkpoint_cleanup_task, _mcp_tools_count

    # ── B2：幂等初始化数据库表结构（纯 API 部署首次 RAG 不 500）──
    # init_db() 内部全部 CREATE TABLE IF NOT EXISTS / ALTER 幂等，可安全重复调用。
    try:
        from mcp_tools.db.database import init_db
        init_db()
        logger.info("Database schema ensured (init_db)")
    except Exception as e:  # noqa: BLE001 — DB 初始化失败不阻断 API 启动
        logger.warning("init_db failed ({}), continuing (RAG may degrade)", e)

    # ── B4：连接 MCP server（重试 3 次退避 1s/2s/4s，失败不丢工具能力于静默）──
    logger.info("Connecting to MCP server at localhost:{}...", settings.mcp_port)
    tools: list[Any] = []
    for attempt in range(1, _MCP_RETRY_ATTEMPTS + 1):
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
            break
        except Exception as e:
            _mcp_client = None
            if attempt < _MCP_RETRY_ATTEMPTS:
                backoff = _MCP_RETRY_BACKOFF_S[attempt - 1]
                logger.warning(
                    "MCP connection attempt {}/{} failed ({}), retrying in {}s",
                    attempt, _MCP_RETRY_ATTEMPTS, e, backoff,
                )
                await asyncio.sleep(backoff)
            else:
                logger.warning(
                    "MCP connection failed after {} attempts ({}), "
                    "starting with empty tools",
                    _MCP_RETRY_ATTEMPTS, e,
                )
                tools = []
    _mcp_tools_count = len(tools)

    # ── V1.5.1 T02: 初始化 CheckpointService（AsyncSqliteSaver）──
    from api.services.checkpoint_service import get_checkpoint_service
    checkpoint_svc = get_checkpoint_service()
    try:
        await checkpoint_svc.async_init()
        logger.info(
            "CheckpointService ready: db={}, ttl={}s",
            checkpoint_svc.get_db_path(),
            checkpoint_svc.get_ttl_seconds(),
        )
    except Exception as e:
        logger.error(
            "CheckpointService async_init FAILED ({}), "
            "falling back to MemorySaver (NO persistence)",
            e,
        )
        # 失败 → 切到 memory 模式重试一次（架构 §2.1.3 降级）
        import os
        os.environ["GRIDMIND_CHECKPOINTER"] = "memory"
        await checkpoint_svc.async_init()

    # ── 构建图（V1.5.1 T02: 拆分 __init__ + async_init）──
    graph_builder = GraphBuilder(tools)
    await graph_builder.async_init()
    logger.info("Graph built + compiled, API ready on port {}", settings.api_port)

    # ── 启动后台 TTL 清理 task（每 5 分钟）──
    _checkpoint_cleanup_task = checkpoint_svc.register_cleanup_task()
    logger.info("Checkpoint cleanup task registered")

    # ── M2：启动双向同步服务 ──
    sync_service = get_sync_service()
    try:
        await sync_service.start()
        logger.info("ChromaSyncService started in lifespan")
    except Exception as e:
        logger.warning("ChromaSyncService start failed ({}), continuing without sync", e)

    yield

    # ── Shutdown 顺序 ───────────────────────────────────

    # 1. cancel cleanup task（T05 改造：复用 CheckpointService.stop_cleanup_task）
    #    该方法幂等：未注册 / 已 done / 多次调用都不抛错
    try:
        if _checkpoint_cleanup_task is not None:
            await checkpoint_svc.stop_cleanup_task()
        _checkpoint_cleanup_task = None
    except Exception as e:
        logger.warning("Checkpoint cleanup task stop error: {}", e)

    # 2. 关闭 CheckpointService（释放 aiosqlite 连接）
    try:
        await checkpoint_svc.aclose()
    except Exception as e:
        logger.warning("CheckpointService aclose error: {}", e)

    # 3. M2：优雅停止同步服务
    try:
        await sync_service.stop()
    except Exception as e:
        logger.warning("ChromaSyncService stop error: {}", e)

    # 4. MCP client 断开
    if _mcp_client:
        logger.info("MCP client shutting down")


# ═══════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(
    title="GridMind API",
    description="灵枢电网 Multi-Agent 系统（FastAPI + LangGraph + MCP）",
    version=APP_VERSION,
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


# ═══════════════════════════════════════════════════════
# V1.5.1 T06：slowapi IP 维度限流（QA R-X1 修复）
# ═══════════════════════════════════════════════════════
# 默认 60 次/分钟/IP，超限返回 429 Too Many Requests。
# 通过 ``settings.rate_limit_per_minute`` 可调；生产建议根据实际流量配置。
# 用例见 ``@limiter.limit`` 装饰的端点（如 ``/admin/checkpoint-stats``）。
limiter: Limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── M3c：注册 Prometheus /metrics 端点 ──────────────────────────────
# 必须 CORSMiddleware 之后注册，保证 metrics 也能跨域（沙箱前端调试）。
register_metrics_endpoint(app)


# ── V1.6：功能介绍知识库路由（GET/POST /api/knowledge/feature-intro）─────
# 前端 onboarding 场景卡 / driver.js tour / 向导第 3 步的文案数据源。
app.include_router(feature_intro_router)

# ── V1.7：用户上传知识库路由（POST/GET/DELETE /api/knowledge/uploads*）────
# 用户自助上传 txt/md → 解析切分入库 → 对话 RAG 可检索；与 feature-intro 隔离。
app.include_router(knowledge_upload_router)


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
    """健康检查（B4：反映真实 MCP 工具数，不再恒为 connected=True）。"""
    return {
        "service": "GridMind",
        "version": APP_VERSION,
        "status": "running",
        "mcp_connected": _mcp_tools_count > 0,
        "mcp_tools_count": _mcp_tools_count,
    }


# ═══════════════════════════════════════════════════════
# 设备实时监控路由
# ═══════════════════════════════════════════════════════

@app.get("/devices", dependencies=[Depends(verify_jwt_if_prod)])
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


@app.get("/devices/{device_id}", dependencies=[Depends(verify_jwt_if_prod)])
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


@app.get("/devices/{device_id}/telemetry", dependencies=[Depends(verify_jwt_if_prod)])
async def device_telemetry(device_id: str, hours: int = 24) -> dict[str, Any]:
    """设备遥测时间序列（默认最近 24 小时，用于趋势图）。"""
    if not 1 <= hours <= 168:
        raise HTTPException(status_code=422, detail="hours 必须在 1–168 之间")
    rows = await get_device_telemetry(device_id, hours)
    return {"device_id": device_id, "telemetry": rows}


@app.get("/health/scores", dependencies=[Depends(verify_jwt_if_prod)])
async def health_scores() -> dict[str, Any]:
    """全部设备健康评分列表。"""
    return {"scores": await get_all_health_scores()}


@app.get("/health/critical", dependencies=[Depends(verify_jwt_if_prod)])
async def health_critical() -> dict[str, Any]:
    """健康异常（warning/critical）设备列表。"""
    return {"critical": await get_critical_devices()}


@app.post("/chat")
async def chat(
    req: ChatRequest,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
    x_display_mode: str | None = Header(default=None, alias="X-Display-Mode"),
) -> ChatResponse:
    """对话接口（阻塞模式）。

    鉴权说明（V1.7.0）：``identity`` 参数即 ``Depends(verify_jwt_if_prod)``
    —— 生产强制 JWT、dev 放行；不再重复声明路由级依赖（FastAPI 在
    ``from __future__ import annotations`` 下会去重导致参数拿不到值）。

    V1.5.1 T04 改造（架构 §2.6.2 + §6 T04）：
    - 外层包 ``session_lock_manager.acquire(thread_id, timeout=5.0)`` —
      与 pause / resume / rewind / abort 写端点一致串行化
    - 当 ``result.interrupt_action == "pending"`` 时（高危工具 HITL 拦截），
      额外 ``emit_hitl_interrupt`` 推送给该 thread 的所有 SSE 订阅者
      （前端 ``chatStore.interruptRequired = true`` + ``auditStore`` 同步）
    - 超时抛 ``SessionLockTimeout`` → 503 + ``SESSION_LOCKED``

    Bug1 修复：新增 ``X-Display-Mode`` header（standard/presentation），
    透传给 ``graph_builder.run`` 决定 mock/真实 LLM 路径。

    V1.7.0 多用户（架构 §1.3 + §4.3）：
    - ``req.thread_id`` 为空 → 服务端生成 thread_id 并登记 owner（新会话）；
    - ``req.thread_id`` 非空 → ``ensure_thread_owned`` 生产 owner 校验 +
      存量懒登记（403/404 语义见 thread_store）；
    - 用 ``resolve_model(thread_id)`` 解析会话生效模型 → ``graph.run(model_id=...)``
      贯通 per-session 模型隔离（M-2）。
    """
    # V1.7.0：身份 + 角色（dev 下 identity=None → user="dev"/role=dispatcher）
    user_id = _identity_user_id(identity)
    role = get_role(identity if isinstance(identity, dict) else None)

    # ⚠️ 安全校验必须优先于 graph-ready / 业务逻辑（防探测：即使图未就绪，
    #    越权也先返回 403/404，不泄漏 thread 是否存在）
    thread_id = req.thread_id or f"thread-{id(req)}"
    if req.thread_id is None:
        # 新会话：登记 owner（dev 下 owner="dev"，架构 §7.3）
        ThreadStore().create_thread(thread_id, user_id)
    else:
        # 存量会话：生产 owner 校验 + 懒登记（严格模式 404）
        ensure_thread_owned(thread_id, user_id, role)
    # M-2：解析会话生效模型（threads.model_id ?? 全局）
    model_id = resolve_model(thread_id)

    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    try:
        with session_lock_manager.acquire(thread_id, timeout=5.0):
            result = await graph_builder.run(
                thread_id, req.message, display_mode=x_display_mode, model_id=model_id,
            )
            messages = result.get("messages", []) if isinstance(result, dict) else []

            # 提取最后一条 assistant 消息
            last_content = ""
            last_agent = None
            is_demo_out_of_scope = False
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    last_content = msg.get("content", "")
                    # Bug2 修复：从消息 metadata 取演示模式剧本外标记
                    meta = msg.get("metadata") or {}
                    is_demo_out_of_scope = bool(
                        meta.get("is_demo_out_of_scope", False)
                    )
                    break

            # 检查是否有中断等待
            interrupt_required = False
            interrupt_node = None
            interrupt_msg = None
            interrupt_args = None
            if isinstance(result, dict):
                interrupt_required = result.get("interrupt_action") == "pending" or False
                interrupt_node = result.get("interrupt_tool")
                interrupt_msg = result.get("interrupt_msg")
                interrupt_args = result.get("interrupt_args")

            # V1.5.1 T04：HITL 触发时 emit `hitl_interrupt` 事件给 SSE 订阅者
            #   此时 lock 仍持有，前端 EventSource 收到事件时 lock 已释放
            if interrupt_required:
                try:
                    await sse_event_emitter.emit_hitl_interrupt(
                        thread_id=thread_id,
                        tool=interrupt_node,
                        args=interrupt_args if isinstance(interrupt_args, dict) else None,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "SSE emit hitl_interrupt failed (non-fatal) for {}: {}",
                        thread_id, e,
                    )

            # M-3（C-4 防御性补齐）：阻塞 /chat 路径回填 knowledge_answer
            # （HITL 后续轮次基本为 diagnosis，属低成本防御，不影响 SSE 主链路）。
            ka_model: KnowledgeAnswer | None = None
            ka_raw = result.get("knowledge_answer") if isinstance(result, dict) else None
            if isinstance(ka_raw, KnowledgeAnswer):
                ka_model = ka_raw
            elif isinstance(ka_raw, dict):
                try:
                    ka_model = KnowledgeAnswer(**ka_raw)
                except Exception as e:  # noqa: BLE001 — 反解失败按无来源处理
                    logger.debug("chat knowledge_answer parse failed: {}", e)

            return ChatResponse(
                thread_id=thread_id,
                response=last_content or "处理完成",
                agent_name=last_agent,
                interrupt_required=interrupt_required,
                interrupt_node=interrupt_node,
                interrupt_msg=interrupt_msg,
                is_demo_out_of_scope=is_demo_out_of_scope,
                knowledge_answer=ka_model,
            )
    except SessionLockTimeout:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Session {thread_id} is locked by another operation, "
                "retry later"
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        # V1.5.1 T06 R-X3：完整 traceback 入日志，响应体仅返回通用 message
        # （原 ``f"处理出错: {e!s}"`` 泄漏文件路径 / 变量值 / token）
        import traceback as _tb
        logger.error(
            "Chat error for {}: {}\n{}", thread_id, e, _tb.format_exc()
        )
        return ChatResponse(
            thread_id=thread_id,
            response="处理出错，请稍后重试",
        )


@app.get("/chat/stream/{thread_id}", dependencies=[Depends(verify_thread_ownership_if_prod)])
async def chat_stream(
    thread_id: str,
    message: str,
    x_display_mode: str | None = Header(default=None, alias="X-Display-Mode"),
) -> StreamingResponse:
    """对话接口（SSE 流式模式）。

    Bug1 修复：新增 ``X-Display-Mode`` header（standard/presentation），
    透传给 ``graph_builder.run`` 决定 mock/真实 LLM 路径。

    V1.7.0 多用户：依赖升级为 ``verify_thread_ownership_if_prod``
    （生产 owner 校验 + 懒登记；dev 放行），并用 ``resolve_model``
    解析会话生效模型透传 ``graph.run(model_id=...)``。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    # M-2：会话生效模型（threads.model_id ?? 全局）；SSE 是 GET 无 body，
    # owner 校验由依赖注入完成（生产 403/404 在响应返回前触发）
    model_id = resolve_model(thread_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            result = await graph_builder.run(
                thread_id, message, display_mode=x_display_mode, model_id=model_id,
            )
            messages = result.get("messages", []) if isinstance(result, dict) else []

            # Bug 修复（T2 防御层）：只 yield **最后一条** assistant 消息
            # （最终回答）。修复前 supervisor↔agent 循环会让 messages 数组
            # 累积 4-5 条来自不同 Agent 的 assistant 消息，前端一次性显示
            # 全部累积 → 用户看到多段回答。即使上游修复不到位（历史
            # checkpointer 状态 / 异常路径残留），前端也只收到一段回答。
            last_token_content: str | None = None
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if content:
                        last_token_content = content
            if last_token_content:
                yield f"data: {json.dumps({'type': 'token', 'content': last_token_content}, ensure_ascii=False)}\n\n"

            # 发送完成事件（含 HITL 中断信息，供前端流式路径弹出确认框）
            interrupt_required = (
                result.get("interrupt_action") == "pending"
                if isinstance(result, dict) else False
            )
            # Bug2 修复：从最后一条 assistant 消息 metadata 取演示模式剧本外标记
            is_demo_out_of_scope = False
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    meta = msg.get("metadata") or {}
                    is_demo_out_of_scope = bool(
                        meta.get("is_demo_out_of_scope", False)
                    )
                    break
            final = {
                "type": "done",
                "thread_id": thread_id,
                "interrupt_required": interrupt_required,
                "interrupt_node": result.get("interrupt_tool") if isinstance(result, dict) else None,
                "interrupt_msg": result.get("interrupt_msg") if isinstance(result, dict) else None,
                "is_demo_out_of_scope": is_demo_out_of_scope,
            }
            # M-3（K-6）：仅 knowledge_agent 且 knowledge_answer 非空时增量携带；
            # 其他 Agent / 空值不出现该键，向后兼容旧前端（忽略未知键）。
            ka_raw = result.get("knowledge_answer") if isinstance(result, dict) else None
            if ka_raw is not None:
                if hasattr(ka_raw, "model_dump"):
                    ka_raw = ka_raw.model_dump()
                final["knowledge_answer"] = ka_raw
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


@app.post("/interrupt/{thread_id}/approve", dependencies=[Depends(verify_thread_ownership_if_prod)])
@safe_endpoint
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
        # V1.5.1 T06 R-X3：完整 traceback 入日志，detail 仅返回通用 message
        import traceback as _tb
        logger.error(
            "Approve interrupt error for {}: {}\n{}", thread_id, e, _tb.format_exc()
        )
        raise HTTPException(
            status_code=500, detail="Internal server error, please retry later"
        )


@app.post("/interrupt/{thread_id}/reject", dependencies=[Depends(verify_thread_ownership_if_prod)])
@safe_endpoint
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
        # V1.5.1 T06 R-X3：完整 traceback 入日志，detail 仅返回通用 message
        import traceback as _tb
        logger.error(
            "Reject interrupt error for {}: {}\n{}", thread_id, e, _tb.format_exc()
        )
        raise HTTPException(
            status_code=500, detail="Internal server error, please retry later"
        )


@app.post("/interrupt/{thread_id}/decision")
@safe_endpoint
async def decide_interrupt(
    thread_id: str,
    req: EditInterruptRequest,
    ownership: Annotated[
        dict[str, Any] | None, Depends(verify_thread_ownership_if_prod)
    ] = None,
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

    审计身份（V1.7.0 QA 记档修复）：``ownership`` 即
    ``Depends(verify_thread_ownership_if_prod)`` 的返回值——生产模式为 owner
    校验通过的 ownership dict（``user_id`` = JWT ``user_id``/``sub`` claim），
    dev 模式为 ``None``（放行）。审计 ``user_id`` 取该值，使 HITL 决策可追溯
    操作者身份（电网操作票追责）；dev 无 token 时回退 ``anonymous``。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    # 审计 user_id：生产从 JWT 提取（ownership.user_id），dev 回退 anonymous。
    audit_user_id = "anonymous"
    if isinstance(ownership, dict) and ownership.get("user_id"):
        audit_user_id = str(ownership["user_id"])

    ip, ua = _extract_request_meta()

    try:
        result = await process_edit_decision(
            thread_id,
            req,
            user_id=audit_user_id,
            user_name=None,
            user_role=None,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception as e:
        # V1.5.1 T06 R-X3：完整 traceback 入日志，detail 仅返回通用 message
        import traceback as _tb
        logger.error(
            "Decision processing error for {}: {}\n{}", thread_id, e, _tb.format_exc()
        )
        raise HTTPException(
            status_code=500, detail="Internal server error, please retry later"
        )

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


def _identity_user_id(identity: dict[str, Any] | None) -> str:
    """从鉴权主体提取 user_id（V1.7.0 多用户）。

    - 生产 JWT payload → ``user_id`` / ``sub``；
    - dev（identity=None）→ ``"dev"``（架构 §7.3：dev 下新会话 owner 记 dev）。
    """
    if isinstance(identity, dict):
        uid = identity.get("user_id") or identity.get("sub")
        if uid:
            return str(uid)
    return "dev"


def _audit_visible_thread_ids(
    identity: dict[str, Any] | None,
    request: Request | None = None,
) -> list[str] | None:
    """审计列表角色过滤（PRD §四 矩阵「审计读」）。

    Args:
        identity: 鉴权主体（生产 JWT payload / dev None）。
        request: 可选 FastAPI Request（检测 ``X-Admin-Token`` 等效管理员）。

    Returns:
        - ``None``：不过滤（审计/运维/管理员全量视角；admin token 等效；
          dev 模式全量）；
        - ``list[str]``：仅可见 thread_id 列表（调度员/知识管理员本人会话；
          空列表 = 无可见记录）。
    """
    if not settings.is_production or not isinstance(identity, dict):
        return None
    # admin token 等效管理员 → 全量（二选一通过，矩阵说明 4）
    if request is not None:
        x_admin_token = request.headers.get("X-Admin-Token")
        if x_admin_token and GrayscaleAdminService.verify_admin_token(x_admin_token):
            return None
    role = get_role(identity)
    if role in AUDIT_FULL_ACCESS_ROLES:
        return None
    user_id = _identity_user_id(identity)
    return ThreadStore().list_thread_ids_by_owner(user_id)


@app.get("/audit/hitl/{thread_id}", dependencies=[Depends(verify_audit_thread_access)])
async def get_hitl_audit_log(thread_id: str) -> dict[str, Any]:
    """查询指定 thread_id 的 HITL 审计记录（P0：审计追溯）。

    V1.7.0 多用户：依赖升级为 ``verify_audit_thread_access``——
    审计/运维/管理员（或 admin token）全放行；调度员/知识管理员仅本人
    thread（PRD Q1 决策「本人放行」；owner 不符 → 403，懒登记优先于 404）。
    """
    rows = HitlAuditService.query_by_thread(thread_id)
    return {"thread_id": thread_id, "count": len(rows), "entries": rows}


@app.get("/audit/hitl")
async def list_hitl_audit_log(
    decision: str | None = None,
    limit: int = 50,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入；admin token 检测
) -> dict[str, Any]:
    """列出 HITL 审计记录（V1.7.0 按角色过滤可见范围）。

    鉴权：``identity`` 参数即 ``Depends(verify_jwt_if_prod)``（生产强制 JWT、
    dev 放行；避免重复声明路由级依赖触发 FastAPI 去重）。
    
    PRD §四 矩阵「审计读」：
    - 审计/运维/管理员 → 全部 thread（thread_ids=None 不过滤）；
    - 调度员/知识管理员 → 仅本人 thread（``threads`` 表 owner 过滤）；
    - dev 模式 → 全量（identity=None → 不过滤，与旧行为一致）。

    行为修正（架构 §八 待明确事项 4）：``decision=None`` 由「空列表」改为
    「返回全部（按角色过滤）」，支撑审计页默认全量展示。
    """
    thread_ids = _audit_visible_thread_ids(identity, request)
    rows = HitlAuditService.query_by_decision_with_threads(
        decision, limit=limit, thread_ids=thread_ids,
    )
    return {
        "count": len(rows),
        "entries": rows,
        "retention_years": HitlAuditService.retention_years(),
    }


@app.get("/thread/{thread_id}", dependencies=[Depends(verify_thread_ownership_if_prod)])
async def get_thread(thread_id: str) -> dict[str, Any]:
    """获取指定线程的对话历史（V1.5.1 T02: 改用 aget_state 兼容 AsyncSqliteSaver）。

    V1.7.0 多用户：依赖升级为 ``verify_thread_ownership_if_prod``
    （生产 owner 校验 + 懒登记；dev 放行）。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    snapshot = await graph_builder.aget_state(thread_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    # snapshot.values 可能是 dict（直接存）或 AgentState 序列化形式
    values = snapshot.values or {}
    return {
        "thread_id": thread_id,
        "messages": values.get("messages", []) if isinstance(values, dict) else [],
        "interrupt_action": values.get("interrupt_action") if isinstance(values, dict) else None,
        "next_node": snapshot.next[0] if snapshot.next else None,
        "checkpoint_id": snapshot.config.get("configurable", {}).get("checkpoint_id"),
    }


@app.get("/diagnosis/{thread_id}/reasoning", dependencies=[Depends(verify_thread_ownership_if_prod)])
async def get_diagnosis_reasoning(thread_id: str) -> dict[str, Any]:
    """P0 可解释性 AI：拉取指定 thread_id 的完整推理链 JSON。

    V1.7.0 多用户：依赖升级为 ``verify_thread_ownership_if_prod``
    （生产 owner 校验 + 懒登记；dev 放行）。

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


# ═══════════════════════════════════════════════════════
# v1.4.0 多模型 LLM 端点（DashScope + DeepSeek）
# ═══════════════════════════════════════════════════════
# V1.7.0：ModelSwitchRequest 已上移至 api/schemas（新增可选 thread_id），
# 此处直接复用 schemas 模型（见模块顶部 import）。


@app.get("/models")
async def list_models(
    thread_id: str | None = None,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> dict[str, Any]:
    """列出所有可用模型 + 当前/默认（V1.7.0 支持会话维度）。

    鉴权：``identity`` 参数即 ``Depends(verify_jwt_if_prod)``（生产强制 JWT、
    dev 放行；避免重复声明路由级依赖触发 FastAPI 去重）。

    - ``GET /models`` → ``{"available", "current": 全局, "default"}``（v1.6 兼容）；
    - ``GET /models?thread_id=t-A`` → ``current`` 为该会话生效模型
      （``threads.model_id ?? 全局``），并回显 ``thread_id`` 字段（US-2.1/2.2）。

    生产模式：``thread_id`` 非空时先做 owner 校验（不能窥探他人会话模型）；
    dev 放行（``verify_jwt_if_prod`` 语义）。
    """
    user_id = _identity_user_id(identity)
    role = get_role(identity if isinstance(identity, dict) else None)
    if thread_id:
        ensure_thread_owned(thread_id, user_id, role)
        current = get_model_for_thread(thread_id)
        return {
            "available": AVAILABLE_MODELS,
            "current": current,
            "default": get_default_model(),
            "thread_id": thread_id,
        }
    return {
        "available": AVAILABLE_MODELS,
        "current": get_current_model(),
        "default": get_default_model(),
    }


@app.post("/models/switch")
async def switch_model(
    req: ModelSwitchRequest,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> dict[str, Any]:
    """切换 LLM 模型（V1.7.0：全局 或 会话级）。

    鉴权：``identity`` 参数即 ``Depends(verify_jwt_if_prod)``（生产强制 JWT、
    dev 放行；避免重复声明路由级依赖触发 FastAPI 去重）。

    - ``POST /models/switch {model_id}``（无 thread_id）→ 进程级全局，
      行为与 v1.6 完全一致（US-2.3 向后兼容）；
    - ``POST /models/switch {model_id, thread_id}`` → 仅该会话生效
      （``threads.model_id`` UPSERT；NULL=全局默认；US-2.1/2.2）。

    生产模式：``thread_id`` 非空时 ``ensure_thread_owned``（不能切别人的
    会话模型 → 403；懒登记优先于 404）；无 thread_id 仅 ``verify_jwt_if_prod``。

    响应：无 thread_id 与 v1.6 一致 ``{"ok", "current"}``；有 thread_id 追加
    ``"thread_id"`` 字段（向后兼容）。
    """
    try:
        if req.thread_id:
            user_id = _identity_user_id(identity)
            role = get_role(identity if isinstance(identity, dict) else None)
            ensure_thread_owned(req.thread_id, user_id, role)
            set_model_for_thread(req.thread_id, req.model_id)
            return {
                "ok": True,
                "current": req.model_id,
                "thread_id": req.thread_id,
            }
        # 旧路径：全局（进程级）
        set_current_model(req.model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "current": req.model_id}


@app.get("/grayscale/status", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def grayscale_status() -> dict[str, Any]:
    """灰度状态（V1.7.0 RBAC 收口：运维/管理员；admin token 等效；dev 放行）。"""
    return GrayscaleAdminService.get_status()


@app.post("/grayscale/set", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def grayscale_set(
    req: GrayscaleSetRequest,
) -> dict[str, Any]:
    """灰度切流（运维/管理员；admin token 等效管理员，二选一通过）。"""
    try:
        return GrayscaleAdminService.set_ratio(req.ratio, actor=req.actor or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/grayscale/history", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def grayscale_history(limit: int = 20) -> dict[str, Any]:
    """灰度切换历史（V1.7.0 RBAC 收口：运维/管理员）。"""
    rows = GrayscaleAdminService.get_history(limit=limit)
    return {"count": len(rows), "entries": rows}


@app.get("/grayscale/metrics", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def grayscale_metrics() -> dict[str, Any]:
    """灰度统计指标（V1.7.0 RBAC 收口：运维/管理员，衔接 R-1c）。

    包含：当前状态、累计切换次数、最近一次切换、回滚统计、
    5min 滚动窗口监控指标（samples/error_rate/p95）、ChromaSync 状态分布。
    """
    return GrayscaleAdminService.get_metrics()


@app.post("/grayscale/manual_rollback", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def grayscale_manual_rollback(
    req: GrayscaleRollbackRequest,
) -> dict[str, Any]:
    """手动回滚（运维/管理员；admin token 等效管理员，二选一通过）。"""
    return GrayscaleAdminService.manual_rollback(
        reason=req.reason or "manual", actor=req.actor or "admin",
    )


# ═══════════════════════════════════════════════════════
# M2 调试端点（P0：T-M2-07 调试 + 监控）
# ═══════════════════════════════════════════════════════


@app.get("/debug/sync_lag", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def debug_sync_lag() -> dict[str, Any]:
    """同步状态监控端点（V1.7.0 RBAC 收口：运维/管理员；若外部监控脚本
    依赖匿名读取，需在部署侧改配 token —— 架构 §八 待明确事项 5）。"""
    svc = get_sync_service()
    return {
        "queue_length": svc.get_queue_length(),
        "stats": svc.get_stats(),
        "sync_log_stats": GrayscaleAdminService.get_sync_log_stats(),
        "recent_syncs": GrayscaleAdminService.get_sync_log_recent(limit=10),
    }


@app.post("/debug/sync_force", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
async def debug_sync_force() -> dict[str, Any]:
    """强制触发一次全量同步（运维/管理员；admin token 等效）。"""
    svc = get_sync_service()
    try:
        import asyncio
        asyncio.create_task(svc._full_sync_check())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("debug_sync_force failed: {}", exc)
    return {"ok": True, "stats": svc.get_stats()}


# ═══════════════════════════════════════════════════════
# V1.5.1 Checkpoint 监控端点（admin 鉴权 · 架构 §2.3.3）
# ═══════════════════════════════════════════════════════


@app.get("/admin/checkpoint-stats", dependencies=[Depends(require_role(Role.OPERATOR, Role.ADMIN))])
@limiter.limit(lambda: f"{settings.rate_limit_per_minute}/minute")
async def checkpoint_stats(request: Request) -> dict[str, Any]:
    """Checkpoint 统计（V1.7.0 RBAC：运维/管理员；admin token 等效 + IP 限流）。

    返回字段（架构 §2.3.3 / §4.1 CheckpointStats schema）：
        - ``total_checkpoints``: checkpoints 表行数
        - ``total_threads``: 去重 thread_id 数
        - ``expired_cleaned_24h``: 过去 24h TTL 清理条数
        - ``active_sessions``: 当前持有 SessionLock 的 thread_id 数
        - ``db_size_bytes``: ``data/checkpoints.db`` 文件大小
        - ``ttl_seconds``: TTL 配置（默认 1800s = 30min）

    行为（T05 + T06）：
        - 无 ``X-Admin-Token`` header → **401 Unauthorized**
        - header 值与 ``settings.admin_token`` 不匹配 → **403 Forbidden**
        - IP 维度限流（默认 ``settings.rate_limit_per_minute`` 次/分钟）→
          超限 **429 Too Many Requests**（slowapi 标准响应）
        - 鉴权通过：先 ``async_refresh_counts()`` 刷 cache 再 ``get_stats()`` 取同步值
          （保证读到的 count 是最新 SQL 实际值，架构 §2.3.3 验收）

    生产部署：建议 ``RATE_LIMIT_PER_MINUTE`` 环境变量调至 30（更保守）。
    """
    from api.services.checkpoint_service import get_checkpoint_service

    svc = get_checkpoint_service()
    # 刷 cache（async，访问 SQL）
    try:
        await svc.async_refresh_counts()
    except Exception as e:
        logger.warning("admin/checkpoint-stats: refresh_counts failed: {}", e)
    # 读 cache（sync，无 SQL）
    return svc.get_stats().model_dump()


# ═══════════════════════════════════════════════════════
# V1.5.1 T03：会话控制端点（架构 §2.2 + §6 T03）
#   POST /sessions/{thread_id}/pause   — 注入 pause_signal，下次节点 throw interrupt
#   POST /sessions/{thread_id}/resume  — 清除 pause_signal + ainvoke(None) 继续
#   POST /sessions/{thread_id}/rewind  — get_state_history + aupdate_state(as_node=...)
#   POST /sessions/{thread_id}/abort   — 注入 abort_signal（永久）
# 每个写端点都用 session_lock_manager.acquire(thread_id, timeout=5.0) 串行化
# （架构 §2.6.2 决策 #7 + §2.6.3 超时 5s → 503）
# ═══════════════════════════════════════════════════════


@app.post("/sessions/{thread_id}/pause", response_model=ChatResponse, dependencies=[Depends(verify_thread_ownership_if_prod)])
@safe_endpoint
async def pause_session(thread_id: str, req: PauseRequest) -> ChatResponse:
    """暂停推理：注入 ``pause_signal`` 标志。

    行为（架构 §2.2.1）：
    - 调 ``GraphBuilder.pause(thread_id, reason)`` 注入软信号
    - 下次 wrapped 节点入口检查 ``state.pause_signal.get("pause")==True`` →
      throw ``interrupt({"type": "user_pause", ...})`` 挂起图
    - 客户端收到 200 表示"信号已注入"，实际挂起在下一个节点执行时

    异常（V1.5.1 T06 @safe_endpoint 统一处理）：
    - 503 SESSION_LOCKED：同 thread_id 另一操作 5s 内未释放（自动返回）
    - 500 INTERNAL_ERROR：未捕获异常（traceback 入日志，response 仅通用 message）
    - 404 / status="not_found"：thread 不存在
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")
    with session_lock_manager.acquire(thread_id, timeout=5.0):
        ok = await graph_builder.pause(thread_id, req.reason)
    return ChatResponse(
        thread_id=thread_id,
        response="paused" if ok else "not_found",
        interrupt_required=False,
    )


@app.post("/sessions/{thread_id}/resume", response_model=ChatResponse, dependencies=[Depends(verify_thread_ownership_if_prod)])
@safe_endpoint
async def resume_session(thread_id: str, req: ResumeRequest) -> ChatResponse:
    """恢复推理：4 种 action 分支（架构 §2.2.2 + 兼容 v1.5.0 HITL）。

    ``action`` 取值：
    - ``continue_from_pause``：T03 新增——清除 ``pause_signal`` + ``ainvoke(None)``
      从挂起点重跑（**不**走 Command(resume=...)）
    - ``approved`` / ``rejected`` / ``edit_approved``：v1.5.0 HITL 老路径，
      走 ``Command(resume=approval)`` 注入审批结果

    返回：统一映射为 ``ChatResponse``，body 反映操作结果。

    异常（V1.5.1 T06 @safe_endpoint 统一处理）：
    - 503 SESSION_LOCKED：同 thread_id 另一操作 5s 内未释放
    - 500 INTERNAL_ERROR：未捕获异常
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")
    # M-2：恢复时透传会话生效模型（continue_from_pause 重跑用）
    model_id = resolve_model(thread_id)
    with session_lock_manager.acquire(thread_id, timeout=5.0):
        result = await graph_builder.resume(
            thread_id=thread_id,
            action=req.action,
            reason=req.reason,
            edited_args=req.edited_args,
            edit_reason=req.edit_reason,
            model_id=model_id,
        )

    # continue_from_pause 路径：result = {"status": "resumed"|"not_found", ...}
    if isinstance(result, dict) and "status" in result and "messages_count" in result:
        status = result.get("status", "")
        if status == "not_found":
            raise HTTPException(
                status_code=404,
                detail=f"Thread {thread_id} not found or no pause state",
            )
        return ChatResponse(
            thread_id=thread_id,
            response=(
                f"resumed ({result.get('messages_count', 0)} messages)"
                if status == "resumed"
                else status
            ),
            interrupt_required=False,
        )

    # HITL 老路径：result = graph state dict（含 messages 列表）
    messages = result.get("messages", []) if isinstance(result, dict) else []
    last_content = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            last_content = msg.get("content", "")
            break
    return ChatResponse(
        thread_id=thread_id,
        response=last_content or "已恢复",
        interrupt_required=False,
    )


@app.post("/sessions/{thread_id}/rewind", response_model=ChatResponse, dependencies=[Depends(verify_thread_ownership_if_prod)])
@safe_endpoint
async def rewind_session(thread_id: str, req: RewindRequest) -> ChatResponse:
    """回退到指定 step 并从此重跑（F2 主链路，架构 §2.2.3）。

    行为：
    - ``GraphBuilder.rewind_to_step`` 内部：
      1) ``aget_state_history`` 拿历史 checkpoints
      2) 选 target = ``history[step_index]``
      3) ``aupdate_state(target.config, values, as_node=target.next[0])`` 注入
      4) ``ainvoke(None, target.config)`` 从 target 继续（**不**重走已完成 steps）

    异常（V1.5.1 T06 @safe_endpoint 统一处理）：
    - 404 STEP_NOT_FOUND：``step_index`` 越界
    - 500 INTERNAL_ERROR：未捕获异常（saver / LLM 异常，traceback 入日志）
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")
    with session_lock_manager.acquire(thread_id, timeout=5.0):
        result = await graph_builder.rewind_to_step(
            thread_id=thread_id,
            step_index=req.step_index,
            edited_content=req.edited_content,
        )

    if not isinstance(result, dict):
        return ChatResponse(thread_id=thread_id, response="rewound")
    status = result.get("status", "")
    if status == "invalid_step":
        total = result.get("total_steps", 0)
        idx = result.get("step_index", req.step_index)
        raise HTTPException(
            status_code=404,
            detail=(
                f"Step index {idx} not found (thread has {total} steps)"
            ),
        )
    if status in ("history_error", "update_state_error", "rerun_error"):
        raise HTTPException(
            status_code=500,
            detail=f"Rewind failed ({status}): {result.get('error', '')}",
        )
    return ChatResponse(
        thread_id=thread_id,
        response=(
            f"rewound_to_{result.get('rewound_to_step', '?')} "
            f"(from step {result.get('rewound_from_step', '?')}, "
            f"{result.get('messages_count', 0)} new messages)"
        ),
        interrupt_required=False,
    )


@app.post("/sessions/{thread_id}/abort", response_model=ChatResponse, dependencies=[Depends(verify_thread_ownership_if_prod)])
@safe_endpoint
async def abort_session(thread_id: str, req: AbortRequest) -> ChatResponse:
    """强制中止推理：注入永久 ``abort_signal``（架构 §2.2.4）。

    与 ``pause`` 区别：
    - abort 后**不可** resume（``abort_signal`` 永不清除）
    - 客户端应停止 SSE 消费，UI 展示"已中止"

    异常（V1.5.1 T06 @safe_endpoint 统一处理）：
    - 503 SESSION_LOCKED：同 thread_id 另一操作 5s 内未释放
    - 500 INTERNAL_ERROR：未捕获异常

    Returns:
        200 OK + ``response="aborted"`` 注入成功；``response="failed"`` 注入失败。
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")
    with session_lock_manager.acquire(thread_id, timeout=5.0):
        ok = await graph_builder.abort(thread_id, req.reason)
    return ChatResponse(
        thread_id=thread_id,
        response="aborted" if ok else "failed",
        interrupt_required=False,
    )


# ═══════════════════════════════════════════════════════
# M-5 会话管理端点（会话列表 / 重命名 / 归档 / 恢复 / 软删）
#   GET    /sessions?archived=0|1|2|all   — 本人会话列表（管理员跨用户全量）
#   PATCH  /sessions/{thread_id}          — 重命名（body {title}）
#   POST   /sessions/{thread_id}/archive  — 归档（archived=1）
#   POST   /sessions/{thread_id}/restore  — 恢复（archived=0）
#   DELETE /sessions/{thread_id}          — 软删（archived=2 + deleted_at）
#
# 安全口径（架构 session-mgmt §1.3 + §3.3）：
#   - 全部写端点 handler 内联 ``ensure_thread_owned``（生产 owner 校验 +
#     懒登记 + 软删 404 + 管理员跨用户放行），**不新增无鉴权路径**；
#   - 软删会话（archived=2）在 ensure_thread_owned / auth 双路径均 404；
#   - 与既有 ``POST /sessions/{id}/pause|resume|rewind|abort``、
#     ``GET /sessions/{id}/events`` 段数/方法不同，**无路由冲突**。
# ═══════════════════════════════════════════════════════


def _is_admin_view(
    identity: dict[str, Any] | None,
    request: Request | None = None,
) -> bool:
    """判断是否管理员视角（跨用户全量）：管理员角色 或 admin token 有效。"""
    role = get_role(identity if isinstance(identity, dict) else None)
    if role == Role.ADMIN:
        return True
    if request is not None:
        x_admin_token = request.headers.get("X-Admin-Token")
        if x_admin_token and GrayscaleAdminService.verify_admin_token(x_admin_token):
            return True
    return False


@app.get("/sessions")
async def list_sessions(
    archived: str = "0",
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入；admin token 检测
) -> SessionListResponse:
    """M-5：会话列表（AC1-1 本人 / 管理员跨用户全量；updated_at 倒序）。

    - ``archived=0``（默认）活跃；``1`` 归档；``2`` 删除（软删）；
      ``all`` 全状态（管理员视角方便审计/恢复管理）。
    - 生产：owner = JWT 用户；管理员角色/admin token → ``list_all`` 跨用户全量。
    - dev：owner = "dev"（架构 §7.3）。
    """
    user_id = _identity_user_id(identity)
    is_admin = _is_admin_view(identity, request)

    if archived == "all":
        archived_filter: int | None = None
    else:
        try:
            archived_filter = int(archived)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422, detail="archived 必须是 0|1|2|all"
            ) from None
        if archived_filter not in (0, 1, 2):
            raise HTTPException(
                status_code=422, detail="archived 必须是 0|1|2|all"
            )

    rows = (
        ThreadStore().list_all(archived_filter)
        if is_admin
        else ThreadStore().list_by_owner(user_id, archived_filter)
    )
    sessions = [ThreadSummary(**r) for r in rows]
    return SessionListResponse(sessions=sessions, total=len(sessions))


@app.patch("/sessions/{thread_id}")
async def rename_session(
    thread_id: str,
    req: SessionRenameRequest,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> ThreadSummary:
    """M-5：会话重命名（AC1-2；body ``{title}``，非空 ≤100）。

    生产 owner 校验：他人会话 → 403；软删/未知（严格模式）→ 404。
    """
    user_id = _identity_user_id(identity)
    role = get_role(identity if isinstance(identity, dict) else None)
    ensure_thread_owned(thread_id, user_id, role)

    ok = ThreadStore().rename_thread(thread_id, req.title)
    if not ok:
        # 理论不可达（ensure_thread_owned 已懒登记/404），防御性兜底
        raise HTTPException(status_code=404, detail="会话不存在")
    row = ThreadStore().get_thread(thread_id)
    return ThreadSummary(**row)


@app.post("/sessions/{thread_id}/archive")
async def archive_session(
    thread_id: str,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> SessionActionResponse:
    """M-5：归档会话（archived=1，从默认活跃列表移除，进归档视图）。"""
    user_id = _identity_user_id(identity)
    role = get_role(identity if isinstance(identity, dict) else None)
    ensure_thread_owned(thread_id, user_id, role)

    ok = ThreadStore().set_archived(thread_id, 1)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionActionResponse(ok=True, thread_id=thread_id, archived=1)


@app.post("/sessions/{thread_id}/restore")
async def restore_session(
    thread_id: str,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> SessionActionResponse:
    """M-5：恢复归档会话（archived=0，重新进入活跃列表）。

    主理人决策：归档恢复按钮纳入本批（后端 restore 与前端恢复按钮一起做）。
    """
    user_id = _identity_user_id(identity)
    role = get_role(identity if isinstance(identity, dict) else None)
    ensure_thread_owned(thread_id, user_id, role)

    ok = ThreadStore().set_archived(thread_id, 0)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionActionResponse(ok=True, thread_id=thread_id, archived=0)


@app.delete("/sessions/{thread_id}")
async def delete_session(
    thread_id: str,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,
) -> SessionActionResponse:
    """M-5：软删会话（archived=2 + deleted_at；保留 checkpoint 供审计追溯）。

    软删后所有 thread 入口端点（/thread、/chat、/sessions/{id}/*、
    /audit/hitl/{id} 等）一律 404（防泄漏「会话曾存在」）；物理删除属 P2。
    """
    user_id = _identity_user_id(identity)
    role = get_role(identity if isinstance(identity, dict) else None)
    ensure_thread_owned(thread_id, user_id, role)

    ok = delete_thread(thread_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionActionResponse(ok=True, thread_id=thread_id, archived=2)


# ═══════════════════════════════════════════════════════
# V1.5.1 T04：SSE 事件订阅端点（架构 §2.5 + §6 T04）
#   GET /sessions/{thread_id}/events
#   订阅 thread 的所有 V1.5.1 新事件（reasoning_paused / reasoning_resumed /
#   step_replaced / hitl_interrupt / hitl_resolved / reasoning_error），
#   客户端用 EventSource API 长连接消费。**不**影响现有 /chat/stream/{id} 端点。
#
# 行为：
# 1. subscribe → 返回专属 asyncio.Queue（maxsize=100）
# 2. event_generator 循环 await queue.get()（15s 超时 → 发 heartbeat 保活）
# 3. 客户端断开 → CancelledError → finally 中 unsubscribe 清理
# 4. 事件 payload 序列化为 ``data: {json}\n\n``（SSE 规范）
# ═══════════════════════════════════════════════════════


#: SSE 心跳间隔（秒）—— 防止代理服务器超时断开
SSE_HEARTBEAT_INTERVAL_S: float = 15.0


@app.get("/sessions/{thread_id}/events", dependencies=[Depends(verify_thread_ownership)])
async def subscribe_session_events(thread_id: str) -> StreamingResponse:
    """订阅 thread 的 V1.5.1 新 SSE 事件（架构 §2.5 + 决策 #6）。

    **V1.5.1 T06 R-X2 修复**：必须携带 ``Authorization: Bearer <jwt>`` header，
    且 JWT 中 ``thread_id`` claim（若有）必须与 URL 中的 ``thread_id`` 一致。
    缺失 / 无效 / 不匹配的 token 一律拒绝（401 / 403），防止未授权用户监听
    其他用户的推理 / HITL 事件（业务数据泄漏）。

    客户端用法::

        const es = new EventSource(
            '/sessions/' + threadId + '/events',
            { headers: { 'Authorization': 'Bearer ' + jwt } }
        );
        es.onmessage = (e) => {
            const event = JSON.parse(e.data);
            // event.type ∈ {reasoning_paused, reasoning_resumed, step_replaced,
            //                hitl_interrupt, hitl_resolved, reasoning_error}
            switch (event.type) { ... }
        };

    行为细节：
    - 启动时发 1 个 ``connected`` 事件让客户端确认订阅成功
    - 15s 无事件时发 ``heartbeat`` 保活（防 nginx/代理超时）
    - 客户端断开时 ``asyncio.CancelledError`` → finally 调
      ``sse_event_emitter.unsubscribe`` 释放 queue（防内存泄漏）
    - 队列满时 emitter 静默丢事件（best-effort；前端可重连刷新）

    鉴权失败（架构 §2.5.4 + QA R-X2）：
    - 缺失 Authorization header → **401 Unauthorized**
    - token 签名错 / 过期 / 缺 claim → **401 Unauthorized**
    - token ``thread_id`` claim 不匹配 URL → **403 Forbidden**
    """
    if graph_builder is None:
        raise HTTPException(status_code=503, detail="Graph not ready")

    queue = await sse_event_emitter.subscribe(thread_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # 启动事件：让客户端立即收到订阅成功信号
            connected_payload = {
                "type": "connected",
                "thread_id": thread_id,
                "timestamp": __import__("time").time(),
            }
            yield f"data: {json.dumps(connected_payload, ensure_ascii=False)}\n\n"

            while True:
                try:
                    # wait_for 防 queue.get() 无限阻塞；
                    # 超时后发 heartbeat，保持连接
                    event = await asyncio.wait_for(
                        queue.get(), timeout=SSE_HEARTBEAT_INTERVAL_S,
                    )
                except asyncio.TimeoutError:
                    heartbeat_payload = {
                        "type": "heartbeat",
                        "thread_id": thread_id,
                        "timestamp": __import__("time").time(),
                    }
                    yield (
                        f"data: {json.dumps(heartbeat_payload, ensure_ascii=False)}\n\n"
                    )
                    continue

                # 序列化事件：SSE 规范 ``data: {json}\n\n``
                payload = {
                    "type": event.type,
                    "thread_id": event.thread_id,
                    "timestamp": event.timestamp,
                    **event.payload,
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            # 客户端断开（FastAPI 检测到连接关闭 → 取消 generator 任务）
            logger.debug(
                "SSE subscription cancelled: thread_id={}", thread_id
            )
        finally:
            # 关键：必须 unsubscribe，否则 emitter 字典无限增长
            try:
                await sse_event_emitter.unsubscribe(thread_id, queue)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "SSE unsubscribe failed (non-fatal) for {}: {}",
                    thread_id, e,
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关 nginx 缓冲，让 SSE 实时推送
        },
    )
