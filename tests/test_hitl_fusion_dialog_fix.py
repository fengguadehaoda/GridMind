"""HITL 审批弹窗修复回归测试。

背景（Bug）：
    诊断融合层（DiagnosisOrchestrator）判定 ``requires_human_review=True`` 时，
    该信号只体现在回复文本尾部的 ``🔍 [可解释性推理链] … hitl=True …``，
    从未写回 LangGraph 状态字段 ``interrupt_action``。而 ``/chat`` 与
    ``/chat/stream`` 仅凭 ``interrupt_action == "pending"`` 生成
    ``interrupt_required``，导致前端 ``chatStore.interruptRequired`` 恒为 false，
    ``HitlEditDialog`` 永不弹出。

本测试锁定修复后的契约：
    1. 规则护栏 forced_hitl → ``_fusion_requires_approval`` 为 True
    2. 仅围栏解析失败（fallback）→ 不弹窗（避免每问必弹的误报）
    3. ``_merge_fusion_hitl`` 命中时写入 ``interrupt_action="pending"``
    4. 未命中 / 陈旧快照 → 显式清零，避免 checkpointer 残留导致误弹窗
    5. ``AgentState`` 具备 ``interrupt_msg`` 字段（说明文案可透传前端）
"""

from __future__ import annotations

import asyncio

from api.agents.agent_factory import (
    FUSION_REVIEW_NODE,
    _fusion_requires_approval,
    _merge_fusion_hitl,
)
from api.schemas import AgentState
from core.diagnosis_orchestrator import FUSION_STORE, DiagnosisOrchestrator


def _fuse(user_msg: str, thread_id: str, llm_text: str | None = None):
    """跑一次真实融合（不 mock，保证与线上同路径）。

    注意：RulesGuard 的 keyword 规则同时扫描 user_msg 与 LLM 输出文本
    （fallback 会把 llm_text 写进 reasoning_text），因此需要弹窗 / 不需要弹窗
    两类用例必须使用**各自干净**的 llm_text，否则会互相污染。
    """
    orch = DiagnosisOrchestrator()
    return asyncio.run(
        orch.fuse(
            llm_text=llm_text if llm_text is not None else user_msg,
            user_msg=user_msg,
            telemetry={},
            device={},
            thread_id=thread_id,
        )
    )


def test_rules_forced_hitl_requires_approval() -> None:
    """「检修」关键词触发 SF 规则 hitl_required → 应弹窗。

    对应用户截图场景：severity=warning | hitl=True | conflict=False | action=none
    """
    result = _fuse("建议对#1主变压器进行停机检修", "t_rule_hitl")
    assert result.rules_guard.forced_hitl is True, result.rules_guard
    assert result.final_severity == "warning", result.final_severity
    assert result.requires_human_review is True
    assert _fusion_requires_approval(result) is True


def test_fence_fallback_alone_does_not_popup() -> None:
    """围栏解析失败（fallback）但无规则/机理触发 → 不应弹窗。

    否则 mock 模式下每个诊断问题都会弹一次审批框（误报）。
    """
    result = _fuse(
        "变压器当前运行状态如何",
        "t_fallback_only",
        llm_text="主变压器各项遥测指标处于正常范围，运行平稳。",
    )
    # fallback 会把 requires_human_review 置 True
    assert result.requires_human_review is True
    assert result.rules_guard.forced_hitl is False
    assert result.conflict_detected is False
    assert result.mechanical_check.critical_failures == 0
    # 但不应据此弹窗
    assert _fusion_requires_approval(result) is False


def test_merge_writes_pending_into_state() -> None:
    """命中审批条件时，节点返回值应带 interrupt_action=pending。"""
    tid = "t_merge_pending"
    prev = FUSION_STORE.get(tid)
    result = _fuse("建议对#1主变压器进行停机检修", tid)
    FUSION_STORE.put(tid, result)

    update = _merge_fusion_hitl({"messages": [], "error": None}, tid, prev)

    assert update["interrupt_action"] == "pending", update
    assert update["interrupt_tool"] == FUSION_REVIEW_NODE
    assert update["interrupt_msg"], "说明文案不应为空"
    assert isinstance(update["interrupt_args"], dict)
    # 状态字段必须能被 AgentState 接受（否则 extra=ignore 会静默丢弃）
    state = AgentState(**update)
    assert state.interrupt_action is not None
    assert state.interrupt_action == "pending"
    assert state.interrupt_msg == update["interrupt_msg"]


def test_stale_snapshot_does_not_retrigger() -> None:
    """同一 thread 的陈旧融合快照不得重复触发弹窗。"""
    tid = "t_stale"
    result = _fuse("建议对#1主变压器进行停机检修", tid)
    FUSION_STORE.put(tid, result)

    # prev 与 current 是同一对象 → 本轮没有新融合 → 必须清零
    update = _merge_fusion_hitl({"messages": []}, tid, result)
    assert update["interrupt_action"] is None, update
    assert update["interrupt_tool"] is None
    assert update["interrupt_msg"] is None


def test_non_diagnosis_turn_clears_stale_pending() -> None:
    """非诊断轮（无新融合结果）应清掉上一轮残留的 pending。"""
    tid = "t_clear"
    result = _fuse("建议对#1主变压器进行停机检修", tid)
    FUSION_STORE.put(tid, result)

    # 模拟下一轮：prev == current（未产生新融合），且上一轮状态残留 pending
    update = _merge_fusion_hitl(
        {"messages": [], "interrupt_action": None}, tid, result,
    )
    assert update["interrupt_action"] is None


def test_agent_state_has_interrupt_msg_field() -> None:
    """AgentState 必须声明 interrupt_msg，否则说明文案无法透传前端。"""
    state = AgentState(interrupt_msg="需人工复核")
    assert state.interrupt_msg == "需人工复核"


# ── 端到端：真实 LangGraph 运行 ─────────────────────────


def test_e2e_graph_emits_pending_for_rule_triggered_diagnosis() -> None:
    """走真实图：诊断问题触发规则护栏 → 状态带 interrupt_action=pending。

    这是用户报告的场景（推理链显示 hitl=True 却不弹窗）的直接回归。
    ``/chat/stream`` 的 done 事件正是用 ``interrupt_action == "pending"``
    生成 ``interrupt_required``。
    """
    import os

    os.environ.setdefault("MOCK_ENABLED", "true")
    from api.graph import GraphBuilder

    async def _run() -> None:
        builder = GraphBuilder([])
        result = await builder.run(
            "t_e2e_fusion_hitl", "请诊断 TR-001 的故障并说明是否需要维护",
        )
        assert result.get("interrupt_action") == "pending", result
        assert result.get("interrupt_tool") == FUSION_REVIEW_NODE
        assert result.get("interrupt_msg")
        # 复刻 api/main.py chat_stream 的判定
        assert (result.get("interrupt_action") == "pending") is True

    asyncio.run(_run())


def test_e2e_pending_does_not_persist_into_next_turn() -> None:
    """走真实图：同一 thread 后续无风险提问不得残留 pending（防误弹窗）。"""
    import os

    os.environ.setdefault("MOCK_ENABLED", "true")
    from api.graph import GraphBuilder

    async def _run() -> None:
        builder = GraphBuilder([])
        tid = "t_e2e_multiturn"
        first = await builder.run(tid, "请诊断 TR-001 的故障并说明是否需要维护")
        assert first.get("interrupt_action") == "pending", first

        second = await builder.run(tid, "请诊断 TR-001 当前是否存在电压异常")
        assert second.get("interrupt_action") is None, second

        third = await builder.run(tid, "变压器过载如何处置")
        assert third.get("interrupt_action") is None, third

    asyncio.run(_run())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[PASS] {name}")
    print("ALL PASS")
