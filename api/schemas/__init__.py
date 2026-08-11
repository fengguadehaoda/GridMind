"""Pydantic 数据模型 — 涵盖对话、设备、遥测、异常、图谱检索等所有领域类型。

本包由原 ``api/schemas.py`` 模块升级而来，新增 ``hitl_edit`` 子模块用于
HITL Edit & Continue 模式（P0 改造）。所有原 ``from api.schemas import ...``
用法保持向后兼容。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_serializer


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
    # M-3 新增：知识库 Agent 轮次的结构化回答（含 sources）；仅 knowledge_agent
    # 且非空时携带（K-6），其他 Agent / 空值不出现该键。
    knowledge_answer: KnowledgeAnswer | None = None


class ThreadInfo(BaseModel):
    thread_id: str
    messages: list[Message]


# ═══════════════════════════════════════════════════════
# 模型切换 / 会话模型（V1.7.0 多用户 · M-2 per-session 模型隔离）
# ═══════════════════════════════════════════════════════

class ModelSwitchRequest(BaseModel):
    """模型切换请求体（V1.7.0：新增可选 ``thread_id``）。

    - ``{"model_id": "deepseek-chat"}`` —— 旧路径，进程级全局（US-2.3 兼容）；
    - ``{"model_id": "deepseek-chat", "thread_id": "t-A"}`` —— 会话级
      （``threads.model_id`` UPSERT；NULL = 全局默认）。
    """

    model_id: str
    thread_id: str | None = None


class ThreadSummary(BaseModel):
    """会话摘要（``threads`` 表行投影，M-5 会话列表响应）。

    字段与 PRD §五 DDL 对齐：thread_id / title / model_id / created_at /
    updated_at / archived（owner_id 为内部字段，默认不对外暴露）。
    ``archived`` 为 **int**（0=活跃 1=归档 2=删除软删），与后端 threads 列
    一致（主理人决策 Q1 + 架构 §八 待明确 6；非 boolean）。
    """

    thread_id: str
    title: str = "新会话"
    model_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    archived: int = 0


class SessionListResponse(BaseModel):
    """``GET /sessions`` 会话列表响应。"""

    sessions: list[ThreadSummary]
    total: int


class SessionRenameRequest(BaseModel):
    """``PATCH /sessions/{thread_id}`` 重命名请求体。"""

    title: str = Field(..., min_length=1, max_length=100, description="新标题")


class SessionActionResponse(BaseModel):
    """归档 / 恢复 / 删除写端点响应。"""

    ok: bool
    thread_id: str
    archived: int = 0
    title: str | None = None


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


class SourceRef(BaseModel):
    """M-3 知识库来源引用（结构化来源，字段命名对齐 ``search_feature_intro``）。

    字段全部可选（snake_case，K-1）——真实检索/feature-intro/mock 三种来源
    字段完整度不同，缺失字段由前端降级展示（K-5）。
    """

    chunk_id: int | None = None          # SQLite knowledge_chunks 自增 id；feature-intro 无 → None
    doc_id: str | None = None            # 必填语义；空 → 前端 (未知文档)
    filename: str | None = None          # meta.filename 或 source 反解（user-upload/<原名>）
    title: str | None = None             # 文档标题
    source: str | None = None            # 原始 source 字段（user-upload/主变运行规程.md）
    section: str | None = None           # meta.section / md 章节；无 → None
    score: float | None = None           # 真实检索分数 0-1；None → 前端不显示匹配度
    snippet: str | None = None           # ≤120 字摘要（去《标题》前缀后截断）
    content_excerpt: str | None = None   # ≥200 字原文摘录（chunk 全文去前缀；不足取全文）
    chunk_index: int | None = None       # meta.chunk_index；缺失 → None
    total_chunks: int | None = None      # meta.total_chunks；缺失 → None


class RetrievalResult(BaseModel):
    vector_chunks: list[str] = []
    graph_entities: list[GraphEntity] = []
    graph_paths: list[list[str]] = []
    confidence: float = 0.0
    # M-3 新增：结构化来源（与 vector_chunks 并行构建，K-3）
    sources: list[SourceRef] = []
    # M-4 新增：本轮实体抽取的 seed（图谱问答组装用，可选，向后兼容）
    seed_ids: list[str] = []


class GraphAnswerNode(BaseModel):
    """M-4 图谱问答节点（字段与 PRD §四 / 架构 §3.1 完全一致，snake_case）。

    ``hop`` 为距任一 seed 的最短距离（seed=0）；未知 → None。
    ``doc_ids`` 按名称/类型与 sources 子串匹配（P1-4 协同，可为空）。
    """

    id: str
    name: str
    type: str  # 设备/故障/处置/规程/部件/…
    properties: dict[str, Any] = {}
    hop: int | None = None
    doc_ids: list[str] = []
    confidence: float | None = None  # seed=1.0；其余 max(0, 1 - 0.15*hop)


class GraphAnswerEdge(BaseModel):
    """M-4 图谱问答边（rule_id 本批恒为 None——规则推导边不启用，决策 3）。"""

    source: str
    target: str
    relation_type: str  # 触发/导致/包含/关联/处置/CAUSES/…
    confidence: float | None = None  # min(端点节点置信度)
    rule_id: str | None = None


class GraphPath(BaseModel):
    """M-4 图谱推理路径（nodes 为节点 id 有序序列；relations 长度 = nodes - 1）。"""

    nodes: list[str] = []
    relations: list[str] = []
    hops: int = 0
    confidence: float = 0.0  # max(0, 1 - 0.15*hops)（与 KGPathOptimizer.estimate_cost 一致）


class GraphAnswer(BaseModel):
    """M-4 图谱问答答案（随 KnowledgeAnswer.graph_answer 内联下发）。

    ``degraded`` = (backend=="networkx") or 组装异常——networkx 是**常态降级**，
    仅作为前端弱提示，不表示错误、不阻断问答。
    """

    nodes: list[GraphAnswerNode] = []
    edges: list[GraphAnswerEdge] = []
    paths: list[GraphPath] = []
    seed_ids: list[str] = []
    confidence: float = 0.0  # 路径置信度按 1/(hops+1) 加权平均
    backend: str = "networkx"  # "neo4j" | "networkx"
    degraded: bool = False
    latency_ms: float = 0.0
    # M-3 协同（US-5）：与同轮 KnowledgeAnswer.sources 同源/子集
    sources: list[SourceRef] = []


class KnowledgeAnswer(BaseModel):
    answer: str
    citations: list[str] = []
    graph_paths: list[list[str]] = []
    confidence: float
    refuse: bool = False
    refuse_reason: str | None = None
    # M-3 新增：结构化来源（按 score 降序，已过滤 citation_min_score + 截断 top_n）。
    # 旧消费方继续读 citations（string[]）——二者并行、互不替代（K-3）。
    sources: list[SourceRef] = []
    # M-4 新增：图谱问答答案（可选，向后兼容——旧数据无此键反解不报错）。
    graph_answer: GraphAnswer | None = None

    @model_serializer(mode="wrap")
    def _serialize_omit_none_graph_answer(self, handler: Any) -> Any:
        """M-4 向后兼容：``graph_answer`` 为 None 时省略该键。

        Pydantic v2 的 ``model_dump()`` 默认会把值为 None 的可选字段也序列化
        出来，这会让 M-3 旧消费方看到新增的 ``graph_answer: null``。为使 M-3
        时代 ``KnowledgeAnswer`` 序列化**字节级不变**（SSE done 管道零改动，
        架构 §7 共享知识 #8），仅当 ``graph_answer is None`` 时省略该键；
        非 None 时原样透传（有 graph_answer 的 M-4 轮次完整下发）。
        """
        data = handler(self)
        if isinstance(data, dict) and data.get("graph_answer") is None:
            data.pop("graph_answer", None)
        return data


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
    # V1.7.0 M-2：per-session 模型隔离——API 层解析后的会话生效模型；
    # None → agent 节点 achat_completion 内部回退 get_current_model()（向后兼容）
    model_id: str | None = None


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
    # V1.7.0 多用户
    "ModelSwitchRequest",
    "ThreadSummary",
    # M-5 会话管理
    "SessionListResponse",
    "SessionRenameRequest",
    "SessionActionResponse",
    "DeviceInfo",
    "TelemetryReading",
    "AnomalyItem",
    "HealthScoreResult",
    "GraphEntity",
    "GraphRelation",
    "RetrievalResult",
    "KnowledgeAnswer",
    "SourceRef",
    # M-4 新增：图谱问答
    "GraphAnswerNode",
    "GraphAnswerEdge",
    "GraphPath",
    "GraphAnswer",
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
