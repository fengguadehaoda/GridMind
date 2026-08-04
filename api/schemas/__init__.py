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
    """LangGraph Agent 的共享状态。"""
    messages: list[dict[str, Any]] = []
    current_agent: str | None = None
    next_agent: str | None = None
    thread_id: str = "default"
    interrupt_action: InterruptAction | None = None
    interrupt_tool: str | None = None
    interrupt_args: dict[str, Any] | None = None
    health_scores: list[HealthScoreResult] | None = None
    knowledge_answer: KnowledgeAnswer | None = None
    error: str | None = None
    pending_tool_plan: list[dict[str, Any]] | None = None


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
]
