"""可解释性 AI 三层架构的核心 Pydantic 模型。

涵盖 7 个核心数据结构（与 explainability-architecture.md §3.1–§3.4 一致）：

1. ``EvidenceRef``           — 证据引用（LLM 输出可追溯）
2. ``DiagnosisOutput``       — 顶层 LLM 的结构化输出（```diagnosis``` 围栏解析结果）
3. ``MechanicalCheckItem``   — 单项机理校验结果
4. ``MechanicalCheckResult`` — 机理校验汇总
5. ``TriggeredRule``         — 单条被触发的规则
6. ``RulesGuardResult``      — 规则护栏汇总
7. ``ReasoningStep``         — 推理链中每一步的可观测快照
8. ``DiagnosisFusionResult`` — 三层融合后的最终输出（含 reasoning_chain）

设计原则：
- **强类型** + **默认值**：避免 ``Optional[None]`` 满天飞，``severity`` 等枚举给出默认值。
- **Pydantic v2** 语法（与现有 ``api/schemas/`` 一致）：``model_config = ConfigDict(extra='forbid')``。
- **跨模块共享** 字段：``severity`` 统一用 ``Literal['info','warning','critical']``。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ═══════════════════════════════════════════════════════
# 1. 证据引用
# ═══════════════════════════════════════════════════════


class EvidenceRef(BaseModel):
    """单条证据引用（LLM 在 reasoning 中援引的依据）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["telemetry", "rule", "history", "anomaly", "knowledge"]
    id: str
    summary: str


# ═══════════════════════════════════════════════════════
# 2. DiagnosisOutput — LLM 结构化输出
# ═══════════════════════════════════════════════════════


class DiagnosisOutput(BaseModel):
    """顶层 LLM 的结构化诊断输出。

    来自 ``DIAGNOSIS_AGENT_PROMPT`` 中 ``\\`\\`\\`diagnosis`` 围栏的 JSON 内容。
    若 LLM 未输出围栏或 JSON 解析失败，由 ``agent_factory`` fallback 构造一个
    ``severity='info', confidence=0.0, requires_human_review=True`` 的安全默认值。
    """

    model_config = ConfigDict(extra="forbid")

    fault_type: str = "unknown"  # overload / overtemp / short_circuit / normal / unknown
    fault_location: str = "unknown"  # 必须是 device_id（如 "TR-001"），不是设备中文名
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reasoning_text: str = ""
    severity: Literal["info", "warning", "critical"] = "info"
    requires_human_review: bool = False
    suggested_action: Literal["dispatch", "shutdown", "monitor", "none"] = "monitor"

    @model_validator(mode="after")
    def _fallback_unknown_marks_review(self) -> "DiagnosisOutput":
        """``fault_location == 'unknown'`` 或 confidence == 0 时强制走人工复核。

        避免 LLM 编造设备 ID 后直接给出高 confidence 的结论（共享知识 #5 + #8）。
        """
        if self.fault_location == "unknown" or self.confidence == 0.0:
            self.requires_human_review = True
        return self


# ═══════════════════════════════════════════════════════
# 3–4. 机理校验结果
# ═══════════════════════════════════════════════════════


class MechanicalCheckItem(BaseModel):
    """单条机理校验结果（如过载/短路/潮流/电压/温度）。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str  # OC-01 / SC-01 / PF-01 / VL-01 / OT-01
    rule_name: str  # 过载判断 / 短路电流初判 / ...
    passed: bool
    severity: Literal["info", "warning", "critical"] | None = None
    observed_value: float | str | None = None
    threshold: float | str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""


class MechanicalCheckResult(BaseModel):
    """机理校验汇总（5 种校验的并行结果）。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    checks: list[MechanicalCheckItem] = Field(default_factory=list)
    overall_pass: bool = True
    critical_failures: int = 0
    contradicted_with_llm: bool = False  # LLM "无故障" + 机理 high 失败 → True


# ═══════════════════════════════════════════════════════
# 5–6. 规则护栏结果
# ═══════════════════════════════════════════════════════


class TriggeredRule(BaseModel):
    """单条被触发的规则（关键词 / 条件 / 安规条款）。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    source: Literal["safety_regulation", "operation_procedure", "emergency_threshold"]
    code: str | None = None  # DL/T-572-2010-1
    title: str
    matched_keywords: list[str] = Field(default_factory=list)
    action: Literal["warn", "hitl_required", "force_shutdown", "escalate_supervisor"]
    severity: Literal["info", "warning", "critical"] = "warning"
    description: str = ""
    trigger_source: Literal["user_input", "llm_output", "mechanical_check", "fusion"] = "user_input"


class RulesGuardResult(BaseModel):
    """规则护栏汇总。"""

    model_config = ConfigDict(extra="forbid")

    triggered: list[TriggeredRule] = Field(default_factory=list)
    forced_hitl: bool = False
    forced_shutdown: bool = False


# ═══════════════════════════════════════════════════════
# 7. 推理链单步
# ═══════════════════════════════════════════════════════


class ReasoningStep(BaseModel):
    """三层推理链中的单步（严格按时间顺序：LLM → MC → RG → Fusion）。"""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["llm", "mechanical", "rules", "fusion"]
    step_name: str
    outcome: str  # 通过 / 失败 / 触发 / 拦截 / 矛盾 ...
    evidence: dict[str, Any] | str = Field(default_factory=dict)
    elapsed_ms: int = 0


# ═══════════════════════════════════════════════════════
# 8. 三层融合结果
# ═══════════════════════════════════════════════════════


class DiagnosisFusionResult(BaseModel):
    """三层融合后的最终输出——前端推理链面板的数据源。

    关键字段：
    - ``conflict_detected``：LLM 与机理矛盾 → 前端展示红色冲突横幅
    - ``requires_human_review``：机理 / 规则触发 → 前端立即弹 HITL 确认
    - ``reasoning_chain``：严格按时间顺序的 4 步（LLM → MC → RG → Fusion）
    """

    model_config = ConfigDict(extra="forbid")

    llm_output: DiagnosisOutput
    mechanical_check: MechanicalCheckResult
    rules_guard: RulesGuardResult
    final_severity: Literal["info", "warning", "critical"] = "info"
    final_diagnosis: str = ""
    requires_human_review: bool = False
    forced_action: Literal["none", "dispatch", "shutdown"] = "none"
    reasoning_chain: list[ReasoningStep] = Field(default_factory=list)
    conflict_detected: bool = False
    thread_id: str | None = None


__all__ = [
    "EvidenceRef",
    "DiagnosisOutput",
    "MechanicalCheckItem",
    "MechanicalCheckResult",
    "TriggeredRule",
    "RulesGuardResult",
    "ReasoningStep",
    "DiagnosisFusionResult",
]
