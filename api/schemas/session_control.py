"""Pydantic v2 schemas — 会话控制（pause / resume / rewind）+ Checkpoint 元信息（V1.5.1 前置）。

本模块为 GridMind v1.5.1 LangGraph 后端改造（T01-T05）暴露 5 个核心 schema：

1. :class:`RiskLevel` — HITL 风险分级枚举（low / normal / high / critical）
2. :class:`PauseRequest` — 暂停推理请求体（仅 path 参数，本 body 留扩展位）
3. :class:`ResumeRequest` — 恢复推理请求（含 4 种 action）
4. :class:`RewindRequest` — 回退到指定 step 重跑请求
5. :class:`CheckpointStats` — Checkpoint 统计信息（admin 端点返回）

Pydantic v2 风格（``model_config = ConfigDict(...)``），
与 ``api/schemas/__init__.py`` 既有风格保持一致。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ═══════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════


class RiskLevel(str, Enum):
    """HITL 风险分级（V1.5.1 新增，PRD §7.4 主理人决策）。

    分级策略（架构 §2.4.3，待 §8.3 业务侧最终拍板）：
    - ``low``：tool 类别 = monitor / knowledge —— 灰底小徽标
    - ``normal``：默认（80% 场景）—— 蓝底
    - ``high``：tool 类别 = safety / diagnosis 且 AI 置信度 < 0.7 —— 黄底
    - ``critical``：tool 在 HIGH_RISK_TOOLS 列表 —— 红底 + 强制 HITL 弹窗

    V1.5.1 全部 ``risk_level='normal'``，V1.5.2 再优化自动分级（架构 §8.3 选项 A）。
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# ═══════════════════════════════════════════════════════
# Pause / Resume / Rewind / Abort 请求体
# ═══════════════════════════════════════════════════════


class PauseRequest(BaseModel):
    """暂停推理请求（无 body，仅 path 参数 ``thread_id``；保留此 schema 留扩展位）。

    实际 API 形如::

        POST /sessions/{thread_id}/pause

    当前 body 无字段，未来可加 ``reason: str`` 等元信息。
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    # 当前为空 schema，留扩展位（V1.5.1 暂不强制带字段）
    reason: str = Field(
        default="",
        description="暂停原因（可选，前端可填，由 audit log 记录）",
    )


class ResumeRequest(BaseModel):
    """恢复推理请求。

    4 种 ``action``：
    - ``continue_from_pause``：从 ``__pause__`` 状态继续（清除 pause 标志）
    - ``approved`` / ``rejected``：HITL 老路径（v1.5.0 兼容）
    - ``edit_approved``：Edit & Continue 路径（v1.5.0 兼容）
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    action: Literal[
        "continue_from_pause", "approved", "rejected", "edit_approved"
    ] = Field(
        default="continue_from_pause",
        description="恢复动作类型（continue_from_pause 为 V1.5.1 新增分支）",
    )
    reason: str = Field(
        default="",
        description="恢复 / 拒绝原因（仅 approved / rejected 必填）",
    )
    edited_args: dict[str, Any] | None = Field(
        default=None,
        description="edit_approved 时使用的替换参数（仅 edit_approved 使用）",
    )
    edit_reason: str = Field(
        default="",
        description="edit_approved 时的人工修改原因",
    )


class RewindRequest(BaseModel):
    """回退到指定 step 重跑请求（F2 主链路）。

    通过 ``graph.aget_state_history()`` + ``graph.aupdate_state(as_node=...)``
    注入历史 checkpoint 状态，从目标 step 重新执行 LLM 调用。
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    step_index: int = Field(
        ...,
        ge=0,
        description="目标 step 索引（0-based，0 = 重新跑全图）",
    )
    edited_content: dict[str, Any] | None = Field(
        default=None,
        description="编辑后内容（可选，仅改 prompt 片段 / tool args）",
    )


class AbortRequest(BaseModel):
    """强制中止推理请求（与 pause 区别：abort 后不可 resume）。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    reason: str = Field(
        default="",
        description="中止原因（audit log 记录）",
    )


# ═══════════════════════════════════════════════════════
# Checkpoint 统计（admin 端点 + 监控）
# ═══════════════════════════════════════════════════════


class CheckpointStats(BaseModel):
    """Checkpoint 统计信息（``GET /admin/checkpoint-stats`` 返回体）。

    字段映射（架构 §2.3.3）：
    - ``total_checkpoints``：当前 ``checkpoints`` 表行数
    - ``total_threads``：当前去重 thread_id 数
    - ``expired_cleaned_24h``：过去 24h TTL 清理条数（来自 cleanup_log）
    - ``active_sessions``：当前持有 SessionLock 的 thread_id 数
    - ``db_size_bytes``：``data/checkpoints.db`` 文件大小
    - ``ttl_seconds``：当前 TTL 配置（默认 1800s = 30min）
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    total_checkpoints: int = Field(
        default=0,
        description="checkpoint 表总行数",
    )
    total_threads: int = Field(
        default=0,
        description="去重 thread_id 数",
    )
    expired_cleaned_24h: int = Field(
        default=0,
        description="过去 24h TTL 清理条数",
    )
    active_sessions: int = Field(
        default=0,
        description="当前持有锁的会话数",
    )
    db_size_bytes: int = Field(
        default=0,
        description="SQLite 文件字节数",
    )
    ttl_seconds: int = Field(
        default=1800,
        description="TTL 配置（秒），默认 30 分钟",
    )


# ═══════════════════════════════════════════════════════
# 公开导出
# ═══════════════════════════════════════════════════════

__all__ = [
    "RiskLevel",
    "PauseRequest",
    "ResumeRequest",
    "RewindRequest",
    "AbortRequest",
    "CheckpointStats",
]
