"""顶层 · 三层编排器（LLM + 机理 + 规则 → 融合）。

依据 explainability-architecture.md §4.1 时序图实现：
- 接收 LLM 原始输出（含 ```diagnosis 围栏 或 自由文本 fallback）
- 拉取 telemetry / device 上下文
- 并行调用 MechanicalChecker + RulesGuard（asyncio.gather）
- 融合：LLM 结论 vs 机理矛盾 → **机理优先 + 强制人工复核**
- severity 取三层最大值
- 返回 DiagnosisFusionResult（含 reasoning_chain 按时间顺序）

设计原则：
- **零 LLM 依赖**（编排器只消费 LLM 输出，不调用 LLM）
- **纯函数倾向**（除 RulesGuard 热加载外无副作用）
- **可重入**：每次 fuse() 独立，可并发调用
- **可观测**：每个阶段 elapsed_ms 写入 reasoning_chain
- **fallback 容错**：任何一层失败都降级到下一层，不抛出
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from loguru import logger

from core.mechanical_checker import MechanicalChecker
from core.rules_guard import RulesGuard
from core.schemas.diagnosis import (
    DiagnosisFusionResult,
    DiagnosisOutput,
    MechanicalCheckResult,
    ReasoningStep,
    RulesGuardResult,
    TriggeredRule,
)

# P1-4: 延迟导入服务（避免顶层循环依赖：service 依赖 core.schemas）
# 实际持久化在 fuse() 末尾调用，失败不影响主流程（fail-closed）


# ═══════════════════════════════════════════════════════
# 严重度比较（取 max）
# ═══════════════════════════════════════════════════════

_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


def _max_severity(*levels: str | None) -> str:
    """取多个 severity 中的最大值（None 视为 info）。"""
    rank = 0
    name = "info"
    for s in levels:
        if s and _SEVERITY_RANK.get(s, 0) > rank:
            rank = _SEVERITY_RANK[s]
            name = s
    return name  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════
# LLM 围栏解析
# ═══════════════════════════════════════════════════════

_DIAGNOSIS_FENCE = re.compile(r"```diagnosis\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def parse_diagnosis_fence(llm_text: str) -> DiagnosisOutput | None:
    """从 LLM 自由文本回复中解析 ````diagnosis`` 围栏 JSON。

    Args:
        llm_text: LLM 完整回复（含自然语言 + 围栏 JSON）

    Returns:
        解析成功 → ``DiagnosisOutput``；失败 → None（调用方应 fallback）
    """
    if not llm_text:
        return None
    match = _DIAGNOSIS_FENCE.search(llm_text)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("parse_diagnosis_fence: JSON decode error: {}", e)
        return None
    try:
        return DiagnosisOutput(**data)
    except Exception as e:
        logger.warning("parse_diagnosis_fence: validation error: {}", e)
        return None


def fallback_diagnosis(
    user_msg: str,
    llm_text: str | None = None,
    fault_location: str = "unknown",
) -> DiagnosisOutput:
    """围栏解析失败时的安全 fallback（severity=info + requires_human_review=True）。"""
    reasoning = (llm_text or user_msg)[:200] if (llm_text or user_msg) else "（无内容）"
    return DiagnosisOutput(
        fault_type="unknown",
        fault_location=fault_location,
        confidence=0.0,
        evidence_refs=[],
        reasoning_text=reasoning,
        severity="info",
        requires_human_review=True,
        suggested_action="monitor",
    )


# ═══════════════════════════════════════════════════════
# 融合核心
# ═══════════════════════════════════════════════════════


class DiagnosisOrchestrator:
    """三层融合编排器。

    用法：
        orch = DiagnosisOrchestrator()
        result = await orch.fuse(
            llm_text=reply,
            user_msg=state.messages[-1],
            telemetry=telemetry_dict,
            device=device_dict,
            thread_id=tid,
        )
    """

    def __init__(
        self,
        checker: MechanicalChecker | None = None,
        rules: RulesGuard | None = None,
        enabled: bool = True,
    ) -> None:
        """Args:
            checker: 自定义机理校验器（None = 默认全开）
            rules:   自定义规则护栏（None = 默认从 safety_rules.json 加载）
            enabled: 全局开关（false 时退化为 LLM 直返回，见 explainability-architecture.md §7.2 #8）
        """
        self._checker = checker or MechanicalChecker()
        self._rules = rules or RulesGuard()
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = value

    async def fuse(
        self,
        llm_text: str | None,
        user_msg: str = "",
        telemetry: dict[str, Any] | None = None,
        device: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> DiagnosisFusionResult:
        """主入口：融合三层推理 → DiagnosisFusionResult。

        Args:
            llm_text:   LLM 完整回复（含 ```diagnosis 围栏或自由文本）
            user_msg:   用户原始问题（用于关键词规则）
            telemetry:  最新遥测 dict（缺省视为通过）
            device:     设备铭牌 dict（缺省视为通过）
            thread_id:  会话 ID（写入 reasoning_chain snapshot）

        Returns:
            DiagnosisFusionResult（含 reasoning_chain）
        """
        t0 = time.time()
        telemetry = telemetry or {}
        device = device or {}

        # ── Step 1: 解析 LLM 输出 ──
        step_llm_start = time.time()
        llm_output = parse_diagnosis_fence(llm_text or "")
        if llm_output is None:
            logger.warning("Orchestrator: LLM fence parse failed, using fallback")
            llm_output = fallback_diagnosis(user_msg, llm_text)
        step_llm = ReasoningStep(
            layer="llm",
            step_name="LLM 结构化解析",
            outcome="通过" if not llm_output.requires_human_review else "需要复核",
            evidence={
                "fault_type": llm_output.fault_type,
                "fault_location": llm_output.fault_location,
                "confidence": llm_output.confidence,
                "severity": llm_output.severity,
                "fence_parsed": parse_diagnosis_fence(llm_text or "") is not None,
            },
            elapsed_ms=int((time.time() - step_llm_start) * 1000),
        )

        # ── Step 2 & 3: 并行调用 机理 + 规则 ──
        device_id = device.get("device_id") or llm_output.fault_location or "unknown"

        async def _run_mechanical() -> MechanicalCheckResult:
            t = time.time()
            items = self._checker.check_all(telemetry, device)
            overall = all(i.passed for i in items)
            critical_fails = sum(
                1 for i in items
                if not i.passed and i.severity == "critical"
            )
            return MechanicalCheckResult(
                device_id=device_id,
                checks=items,
                overall_pass=overall,
                critical_failures=critical_fails,
            )

        async def _run_rules() -> RulesGuardResult:
            t = time.time()
            return self._rules.scan({
                "user_msg": user_msg,
                "llm_text": llm_text or "",
                "llm_output": llm_output.model_dump(),
                "telemetry": telemetry,
                "device": device,
                "stage": "user_input",
            })

        (mc_result, rg_result) = await asyncio.gather(
            _run_mechanical(), _run_rules(),
        )
        step_mc = ReasoningStep(
            layer="mechanical",
            step_name="机理校验 (5 项)",
            outcome="通过" if mc_result.overall_pass else f"失败 {mc_result.critical_failures} critical",
            evidence={
                "device_id": mc_result.device_id,
                "checks": [i.model_dump() for i in mc_result.checks],
                "critical_failures": mc_result.critical_failures,
            },
            elapsed_ms=int((time.time() - step_llm_start) * 1000),
        )
        step_rg = ReasoningStep(
            layer="rules",
            step_name="规则护栏扫描",
            outcome=f"触发 {len(rg_result.triggered)} 条" if rg_result.triggered else "无触发",
            evidence={
                "triggered": [t.model_dump() for t in rg_result.triggered],
                "forced_hitl": rg_result.forced_hitl,
                "forced_shutdown": rg_result.forced_shutdown,
            },
            elapsed_ms=int((time.time() - step_llm_start) * 1000),
        )

        # ── Step 4: 冲突检测 + 融合决策 ──
        step_fusion_start = time.time()
        conflict = _detect_conflict(llm_output, mc_result, rg_result)
        final_severity = _decide_final_severity(llm_output, mc_result, rg_result)
        requires_human = _decide_requires_human(llm_output, mc_result, rg_result, conflict)
        forced_action = _decide_forced_action(llm_output, mc_result, rg_result)
        final_diagnosis = _build_final_diagnosis(llm_output, mc_result, rg_result)

        step_fusion = ReasoningStep(
            layer="fusion",
            step_name="三层融合决策",
            outcome=(
                f"severity={final_severity}, "
                f"hitl={requires_human}, "
                f"action={forced_action}, "
                f"conflict={conflict}"
            ),
            evidence={
                "final_severity": final_severity,
                "requires_human_review": requires_human,
                "forced_action": forced_action,
                "conflict_detected": conflict,
                "final_diagnosis": final_diagnosis,
            },
            elapsed_ms=int((time.time() - step_fusion_start) * 1000),
        )

        # 如果是 fusion 阶段触发了 MS-001（LLM-机理矛盾），再扫一次 rules
        if conflict and not any(t.rule_id == "MS-001" for t in rg_result.triggered):
            # 追加一条 MS-001 触发记录（不修改 rg_result 本身，保持可观测）
            rg_extra = self._rules.scan({
                "conflict_detected": True,
                "stage": "fusion",
            })
            for extra in rg_extra.triggered:
                rg_result.triggered.append(extra)
            rg_result.forced_hitl = rg_result.forced_hitl or True

        result = DiagnosisFusionResult(
            llm_output=llm_output,
            mechanical_check=mc_result,
            rules_guard=rg_result,
            final_severity=final_severity,
            final_diagnosis=final_diagnosis,
            requires_human_review=requires_human,
            forced_action=forced_action,
            reasoning_chain=[step_llm, step_mc, step_rg, step_fusion],
            conflict_detected=conflict,
            thread_id=thread_id,
        )

        total_ms = int((time.time() - t0) * 1000)
        logger.info(
            "Orchestrator fused in {}ms: severity={}, hitl={}, conflict={}, action={}",
            total_ms, final_severity, requires_human, conflict, forced_action,
        )

        # P1-4: 持久化融合结果到 diagnosis_fusion_log（独立表，不修改 hitl_audit_log）
        # fail-closed：写入失败仅 log warning，不影响主流程返回
        self._persist_fusion_snapshot(result)

        return result

    @staticmethod
    def _persist_fusion_snapshot(result: DiagnosisFusionResult) -> None:
        """P1-4：将 DiagnosisFusionResult 写入 diagnosis_fusion_log 表。

        使用延迟导入避免 core → api.services → core 的循环依赖。
        任何异常都会被 service 内部捕获并仅记 warning，不会向上传播。
        """
        try:
            from api.services.diagnosis_fusion_service import persist_fusion_result
            persist_fusion_result(result)
        except Exception as e:
            logger.warning(
                "Orchestrator: fusion persistence unavailable ({}); "
                "diagnosis result still returned to caller",
                e,
            )


# ═══════════════════════════════════════════════════════
# 融合策略（纯函数）
# ═══════════════════════════════════════════════════════


def _detect_conflict(
    llm: DiagnosisOutput,
    mc: MechanicalCheckResult,
    rg: RulesGuardResult,
) -> bool:
    """LLM 与机理矛盾检测。

    判定规则（explainability-architecture.md §7.1）：
    - LLM 说"无故障"（fault_type=normal, severity=info）但机理 critical 失败 → 矛盾
    - LLM confidence < 0.5 + 机理触发 high → 矛盾
    """
    if mc.overall_pass and not rg.forced_hitl and not rg.forced_shutdown:
        return False  # 机理通过 + 规则未拦截 → 不算矛盾

    if llm.fault_type == "normal" and mc.critical_failures > 0:
        return True

    if llm.fault_type == "normal" and llm.severity == "info" and rg.forced_shutdown:
        return True  # LLM 说没事但规则要立即停运

    if llm.confidence < 0.5 and mc.critical_failures > 0:
        return True

    return False


def _decide_final_severity(
    llm: DiagnosisOutput,
    mc: MechanicalCheckResult,
    rg: RulesGuardResult,
) -> str:
    """取 LLM / 机理 / 规则 三层 severity 最大值（Q9 决策：取 max 安全保守）。"""
    candidates: list[str | None] = [llm.severity]
    for item in mc.checks:
        if not item.passed:
            candidates.append(item.severity)
    for rule in rg.triggered:
        candidates.append(rule.severity)
    return _max_severity(*candidates)


def _decide_requires_human(
    llm: DiagnosisOutput,
    mc: MechanicalCheckResult,
    rg: RulesGuardResult,
    conflict: bool,
) -> bool:
    """是否需要人工复核（HITL）。

    规则（explainability-architecture.md §7.1）：
    - 任何规则 hitl_required / force_shutdown → 必须 HITL
    - 矛盾场景（LLM vs 机理）→ 必须 HITL（机理优先）
    - LLM 自评 requires_human_review → HITL
    - LLM 故障 + 机理通过 + 低 confidence → HITL
    """
    if rg.forced_hitl or rg.forced_shutdown:
        return True
    if conflict:
        return True
    if llm.requires_human_review:
        return True
    if llm.fault_type not in ("normal", "unknown") and llm.confidence < 0.5:
        return True
    return False


def _decide_forced_action(
    llm: DiagnosisOutput,
    mc: MechanicalCheckResult,
    rg: RulesGuardResult,
) -> str:
    """强制动作决策。

    - 规则 force_shutdown → shutdown
    - 否则 None（让 LLM 建议 + HITL 决定）
    """
    if rg.forced_shutdown:
        return "shutdown"
    if rg.forced_hitl and llm.suggested_action in ("dispatch", "shutdown"):
        return llm.suggested_action
    return "none"


def _build_final_diagnosis(
    llm: DiagnosisOutput,
    mc: MechanicalCheckResult,
    rg: RulesGuardResult,
) -> str:
    """融合后的最终中文诊断结论。"""
    parts: list[str] = []
    parts.append(f"【LLM 判定】{llm.fault_type} @ {llm.fault_location}（confidence={llm.confidence:.2f}）")

    failed_checks = [c for c in mc.checks if not c.passed]
    if failed_checks:
        parts.append(
            f"【机理校验】{len(failed_checks)} 项不通过："
            + "；".join(c.explanation for c in failed_checks)
        )
    else:
        parts.append("【机理校验】5 项全部通过")

    if rg.triggered:
        parts.append(
            f"【规则护栏】触发 {len(rg.triggered)} 条规则："
            + "；".join(t.title for t in rg.triggered)
        )
    else:
        parts.append("【规则护栏】无规则触发")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════
# 推理链快照持久化（线程级缓存，供 /reasoning 端点取用）
# ═══════════════════════════════════════════════════════


class _FusionSnapshotStore:
    """线程 ID → 最近一次 DiagnosisFusionResult 的内存缓存。

    简单 dict 实现；P1 可换 Redis。保留最近 100 条，超出后丢弃最早的。
    """

    def __init__(self, max_size: int = 100) -> None:
        self._store: dict[str, DiagnosisFusionResult] = {}
        self._max = max_size

    def put(self, thread_id: str, result: DiagnosisFusionResult) -> None:
        if len(self._store) >= self._max:
            # 丢弃最早插入的（dict 保插入序）
            oldest = next(iter(self._store))
            self._store.pop(oldest, None)
        self._store[thread_id] = result

    def get(self, thread_id: str) -> DiagnosisFusionResult | None:
        return self._store.get(thread_id)

    def clear(self) -> None:
        self._store.clear()


FUSION_STORE = _FusionSnapshotStore()


__all__ = [
    "DiagnosisOrchestrator",
    "parse_diagnosis_fence",
    "fallback_diagnosis",
    "FUSION_STORE",
]
