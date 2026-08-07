"""Pydantic 数据模型 — 涵盖对话、设备、遥测、异常、图谱检索等所有领域类型。

本包由原 ``api/schemas.py`` 模块升级而来，新增 ``hitl_edit`` 子模块用于
HITL Edit & Continue 模式（P0 改造）。所有原 ``from api.schemas import ...``
用法保持向后兼容。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════

class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class HealthLevel(str, Enum):
    normal = "normal"        # 健康分 ≥ 80
    warning = "warning"      # 健康分 60–79
    critical = "critical"    # 健康分 < 60


class DeviceType(str, Enum):
    transformer = "transformer"
    breaker = "breaker"
    cable = "cable"
    busbar = "busbar"


class AnomalySeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ToolCategory(str, Enum):
    monitor = "monitor"
    safety = "safety"
    diagnosis = "diagnosis"
    knowledge = "knowledge"


class InterruptAction(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    edit_approved = "edit_approved"  # 新增：Edit & Continue 修改后通过


# ═══════════════════════════════════════════════════════
# 对话
# ═══════════════════════════════════════════════════════

class Message(BaseModel):
    role: MessageRole
    content: str
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    stream: bool = False


class ChatResponse(BaseModel):
    thread_id: str
    response: str
    agent_name: str | None = None
    interrupt_required: bool = False
    interrupt_node: str | None = None
    interrupt_msg: str | None = None
    # Bug2 修复：演示模式剧本外响应标记（前端据此清审批态 + 展示提示）
    is_demo_out_of_scope: bool = False


class ThreadInfo(BaseModel):
    thread_id: str
    messages: list[Message]


# ═══════════════════════════════════════════════════════
# 设备 / 遥测
# ═══════════════════════════════════════════════════════

class DeviceInfo(BaseModel):
    device_id: str
    device_name: str
    device_type: DeviceType
    location: str
    install_date: str | None = None
    status: str = "normal"


class TelemetryReading(BaseModel):
    reading_id: int
    device_id: str
    timestamp: datetime
    temperature: float | None = None
    voltage: float | None = None
    current_load: float | None = None
    humidity: float | None = None
    pressure: float | None = None


# ═══════════════════════════════════════════════════════
# 异常检测 / 健康评分
# ═══════════════════════════════════════════════════════

class AnomalyItem(BaseModel):
    device_id: str
    metric: str
    value: float
    z_score: float
    severity: AnomalySeverity
    description: str
    detected_at: datetime = Field(default_factory=datetime.now)


class HealthScoreResult(BaseModel):
    device_id: str
    device_name: str
    health_score: float
    health_level: HealthLevel
    anomalies: list[AnomalyItem] = []
    summary: str


# ═══════════════════════════════════════════════════════
# 图谱 / RAG
# ═══════════════════════════════════════════════════════

class GraphEntity(BaseModel):
    id: str
    name: str
    type: str  # 设备/规程/故障/处置/部件
    properties: dict[str, Any] = {}


class GraphRelation(BaseModel):
    source_id: str
    target_id: str
    relation_type: str  # 包含/关联/处置/触发/…


class RetrievalResult(BaseModel):
    vector_chunks: list[str] = []
    graph_entities: list[GraphEntity] = []
    graph_paths: list[list[str]] = []
    confidence: float = 0.0


class KnowledgeAnswer(BaseModel):
    answer: str
    citations: list[str] = []
    graph_paths: list[list[str]] = []
    confidence: float
    refuse: bool = False
    refuse_reason: str | None = None


# ═══════════════════════════════════════════════════════
# LangGraph 状态
# ═══════════════════════════════════════════════════════

class AgentState(BaseModel):
    """LangGraph Agent 的共享状态。

    V1.5.1 T03 新增 2 字段（``pause_signal`` / ``abort_signal``）——
    用于 LangGraph checkpoint 持久化层正确序列化暂停/中止软信号。
    Pydantic v2 默认 ``extra='ignore'`` 会静默丢弃未声明字段（含 ``__`` 开头），
    因此必须**显式声明**这 2 字段才能让 ``aupdate_state`` 注入的软信号
    在 ``ainvoke`` 节点入口可被读取（架构 §2.2.1 决策 #2 + §7.1.3）。
    """

    model_config = {"extra": "ignore"}

    messages: list[dict[str, Any]] = []
    current_agent: str | None = None
    next_agent: str | None = None
    thread_id: str = "default"
    interrupt_action: InterruptAction | None = None
    interrupt_tool: str | None = None
    interrupt_args: dict[str, Any] | None = None
    # HITL 弹窗修复：中断说明文案。此前仅存在于 graph.run() 的合成返回 dict 中，
    # 未声明为状态字段 → 融合层（diagnosis fusion）触发的 HITL 无法把说明透传给
    # ``/chat/stream`` 的 done 事件（``result.get("interrupt_msg")`` 恒为 None）。
    interrupt_msg: str | None = None
    health_scores: list[HealthScoreResult] | None = None
    knowledge_answer: KnowledgeAnswer | None = None
    error: str | None = None
    pending_tool_plan: list[dict[str, Any]] | None = None
    # Bug1 修复：前端 X-Display-Mode header 传入的显示模式
    # （'standard' | 'presentation' | None=header 缺失，回退环境变量）
    display_mode: str | None = None
    # V1.5.1 T03：pause() 注入；节点入口检查，命中则 throw interrupt({type: user_pause})
    pause_signal: dict[str, Any] | None = None
    # V1.5.1 T03：abort() 注入（永久）；节点入口检查，命中则 throw interrupt({type: user_abort})
    abort_signal: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════
# Re-export：HITL Edit & Continue 子模块
# ═══════════════════════════════════════════════════════

from api.schemas.hitl_edit import (  # noqa: E402, F401
    LOCKED_FIELDS,
    AUDIT_RETENTION_YEARS,
    EditDecisionEnum,
    EditableField,
    EditInterruptRequest,
    AuditLogEntry,
    SafetyRecheckResult,
)

# V1.5.1：会话控制（pause / resume / rewind / abort）+ Checkpoint 统计
# re-export 自 ``api.schemas.session_control``（T01 新增子模块）
from api.schemas.session_control import (  # noqa: E402, F401
    RiskLevel,
    PauseRequest,
    ResumeRequest,
    RewindRequest,
    AbortRequest,
    CheckpointStats,
)


__all__ = [
    # 原 schemas.py 公共导出
    "MessageRole",
    "HealthLevel",
    "DeviceType",
    "AnomalySeverity",
    "ToolCategory",
    "InterruptAction",
    "Message",
    "ChatRequest",
    "ChatResponse",
    "ThreadInfo",
    "DeviceInfo",
    "TelemetryReading",
    "AnomalyItem",
    "HealthScoreResult",
    "GraphEntity",
    "GraphRelation",
    "RetrievalResult",
    "KnowledgeAnswer",
    "AgentState",
    # 新增 hitl_edit 导出
    "LOCKED_FIELDS",
    "AUDIT_RETENTION_YEARS",
    "EditDecisionEnum",
    "EditableField",
    "EditInterruptRequest",
    "AuditLogEntry",
    "SafetyRecheckResult",
    # V1.5.1 新增：会话控制 + Checkpoint 统计
    "RiskLevel",
    "PauseRequest",
    "ResumeRequest",
    "RewindRequest",
    "AbortRequest",
    "CheckpointStats",
]
