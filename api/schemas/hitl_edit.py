"""HITL Edit & Continue 模式 Pydantic 数据模型。

本文件集中放置「修改后批准」流程所用的请求/响应/审计模型，与根级
``api/schemas.py`` 的通用模型并存；通过 ``api/schemas/__init__.py`` re-export。

关键约束（CRITICAL）：
- ``InterruptAction``（根级）已扩展为枚举值 ``edit_approved``，详见 ``api/schemas.py``。
- HTTP 请求体使用 ``edit_approve``（祈使式），落库使用 ``edit_approve``（与 audit 一致）。
- ``LOCKED_FIELDS`` 黑名单阻止设备 ID 等关键字段被人工篡改。
- 审计日志保留 3 年（Q3 决策方案 A），无冷归档。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ── 审计保留期（Q3 决策：方案 A，3 年，直接保留 SQLite，无归档）──

AUDIT_RETENTION_YEARS: int = 3


# ── 黑名单字段（不可编辑）──

LOCKED_FIELDS: frozenset[str] = frozenset({
    "device_id",
    "work_order_id",
    "shutdown_id",
    "created_at",
    "thread_id",
    "audit_id",
    "tool_name",
})


# ═══════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════

class EditDecisionEnum(str, Enum):
    """Edit & Continue 决策枚举（HTTP 请求体专用）。

    - ``approve``      — 仅批准，沿用 Agent 原 args
    - ``reject``       — 拒绝（终止高危工具执行）
    - ``edit_approve`` — 修改后批准（edited_args 必填，走安全重检）

    老的 ``/approve`` ``/reject`` 端点使用 ``approve``/``reject``；
    新的统一 ``/decision`` 端点支持全部三种。
    """

    approve = "approve"
    reject = "reject"
    edit_approve = "edit_approve"


# ═══════════════════════════════════════════════════════
# 可编辑字段定义（前后端共用，前端镜像：web/src/api/hitlSchemas.ts）
# ═══════════════════════════════════════════════════════

EditableFieldType = Literal["text", "textarea", "select", "number"]


class EditableField(BaseModel):
    """单个可编辑字段定义。"""

    key: str
    type: EditableFieldType
    label: str
    required: bool = True
    max_length: int | None = None
    options: list[str] | None = None
    placeholder: str = ""
    help_text: str = ""


# ═══════════════════════════════════════════════════════
# 请求/响应模型
# ═══════════════════════════════════════════════════════

class EditInterruptRequest(BaseModel):
    """Edit & Continue 决策请求体。

    字段语义：
    - ``decision``        — 决策类型（approve / reject / edit_approve）
    - ``reason``          — 拒绝/批准原因（≤200 字）
    - ``edited_args``     — 编辑后的工具参数（仅 edit_approve 必填）
    - ``edit_reason``     — 修改原因（仅 edit_approve 必填）
    """

    decision: EditDecisionEnum
    reason: str = Field(default="", max_length=200)
    edited_args: dict[str, Any] | None = None
    edit_reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def check_edited_args_locked(self) -> "EditInterruptRequest":
        """校验 edit_approve 必须提供参数 + 修改原因，且不得含黑名单字段。"""
        if self.decision == EditDecisionEnum.edit_approve:
            if not self.edited_args:
                raise ValueError("edit_approve 必须提供 edited_args")
            if not self.edit_reason:
                raise ValueError("edit_approve 必须填写 edit_reason")
            locked_hit = [k for k in self.edited_args if k in LOCKED_FIELDS]
            if locked_hit:
                raise ValueError(
                    "字段不可编辑: " + ", ".join(sorted(locked_hit))
                )
        return self


class SafetyRecheckResult(BaseModel):
    """安全重检结果（写入审计日志 + 返回前端展示）。"""

    passed: bool
    rules: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class AuditLogEntry(BaseModel):
    """hitl_audit_log 表行映射（写入前后端共用，便于校验）。"""

    thread_id: str
    interrupt_node: str
    tool_name: str
    user_id: str = "anonymous"
    user_name: str | None = None
    user_role: str | None = None
    decision: Literal["approve", "reject", "edit_approve"]
    original_args: dict[str, Any]
    edited_args: dict[str, Any] | None = None
    edit_reason: str | None = None
    safety_recheck_result: SafetyRecheckResult | None = None
    reason: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
