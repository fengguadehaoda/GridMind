# -*- coding: utf-8 -*-
"""QA 独立回归验证（第 2 轮）· software-qa-engineer-2 (Edward)

A. mock 路由 6 用例 —— 直接调用 GraphBuilder._llm_route 取路由目标 + 全图 run 实证
B. HITL 触发链路 —— 高危消息 → interrupt 挂起 → resume(approved) 工具真正执行
只验证，不改产品代码。
"""
import asyncio
import os
import sys

PROJECT_ROOT = "F:/GridMind · 灵枢电网"
sys.path.insert(0, PROJECT_ROOT)

# 在导入 api 之前开启 Mock 模式（无需 LLM Key）——与 tests/test_hitl.py 一致
os.environ.setdefault("MOCK_ENABLED", "true")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from langchain_core.tools import BaseTool  # noqa: E402

from api.schemas import AgentState  # noqa: E402
from api.graph import GraphBuilder  # noqa: E402
from api.agents.agent_factory import _high_risk_mock_reply  # noqa: E402


class FakeDispatchTool(BaseTool):
    """模拟高危工具 dispatch_work_order（实际触发的高危工具）。"""

    name: str = "dispatch_work_order"
    description: str = "【高危】派发检修工单"

    def _run(self, **kwargs) -> str:
        return f"EXECUTED dispatch_work_order {kwargs}"

    async def _arun(self, **kwargs) -> str:
        return f"EXECUTED dispatch_work_order {kwargs}"


AGENT_LABELS = ["监控 Agent", "安规 Agent", "诊断 Agent", "知识库 Agent"]


def _last_agent_from_result(result) -> str:
    msgs = result.get("messages", []) if isinstance(result, dict) else []
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "assistant":
            content = m.get("content", "") or ""
            for label in AGENT_LABELS:
                if label in content:
                    return label
    return "(无 assistant 回复 / 无标签)"


def _first_line(text, n=90):
    return (text or "").replace("\n", " ")[:n]


async def main() -> None:
    builder = GraphBuilder([FakeDispatchTool()])  # 编译 fallback 到 MemorySaver

    # ── A. mock 路由 6 用例 ──────────────────────────────────────────
    cases = [
        ("建议对#1主变压器进行停机检修", "diagnosis_agent"),
        ("变压器过载如何处置", "knowledge_agent"),
        ("查询主变压器当前运行状态和遥测数据", "monitor_agent"),
        ("对所有设备进行异常检测分析", "diagnosis_agent"),
        ("请诊断 #T1 主变压器的温度异常", "diagnosis_agent"),
        ("介绍一下 GridMind 的 5 个核心视图", None),  # 记录实际
    ]
    print("=" * 100)
    print("[A] mock 路由 6 用例（直接 _llm_route 路由决策 + 全图 run 实证）")
    print("=" * 100)
    route_table = []
    for i, (msg, expect) in enumerate(cases, 1):
        # (1) 直接路由决策：构造 AgentState 调 _llm_route（无 visited 干扰）
        state = AgentState(
            messages=[{"role": "user", "content": msg}],
            thread_id=f"qa-r2-route-{i}",
            display_mode=None,
        )
        cmd = await builder._llm_route(state, state.messages[-1])
        selected = cmd.goto

        # (2) 全图 run 实证
        tid = f"qa-r2-run-{i}"
        result = await builder.run(tid, msg)
        agent = _last_agent_from_result(result)
        ia = result.get("interrupt_action")
        it = result.get("interrupt_tool")

        ok = "✅" if (expect is None or selected == expect) else "❌"
        route_table.append((msg, selected, expect, ok))
        print(f"\n{ok} 用例{i}: {msg}")
        print(f"    _llm_route 路由目标 : {selected}  (期望: {expect or '记录实际'})")
        print(f"    全图 run Agent      : {agent}  | interrupt_action={ia} | tool={it}")
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                print(f"    回复预览            : {_first_line(m.get('content', ''))}")
                break

    print("\n" + "-" * 100)
    print("路由结果表：")
    for msg, selected, expect, ok in route_table:
        exp = expect or "记录实际"
        print(f"  {ok}  {msg!r:45s} -> {selected:20s} (期望: {exp})")

    # ── B1. 高危消息 → interrupt（工具级 HITL 触发）──
    print("\n" + "=" * 100)
    print("[B1] HITL 触发：standard/mock 下高危消息 → interrupt_action=pending")
    print("=" * 100)
    hr = _high_risk_mock_reply("建议对#1主变压器进行停机检修")
    print(f"    _high_risk_mock_reply('建议对#1主变压器进行停机检修') = {hr!r}")
    assert hr and "TOOL_CALL" in hr, "高危关键词应触发 TOOL_CALL 演示"

    tid_hr = "qa-r2-hitl-1"
    result = await builder.run(tid_hr, "建议对#1主变压器进行停机检修")
    ia = result.get("interrupt_action")
    it = result.get("interrupt_tool")
    im = result.get("interrupt_msg")
    print(f"    interrupt_action = {ia}")
    print(f"    interrupt_tool   = {it}")
    print(f"    interrupt_msg    = {im}")
    assert ia == "pending", f"期望 interrupt_action=pending, 实际 {ia}"
    assert it in {"dispatch_work_order", "suggest_shutdown"}, f"期望高危工具, 实际 {it}"
    print("    [PASS] 高危消息在 standard/mock 模式下正确触发 HITL 挂起")

    # ── B2. resume(approved) → 高危工具真正执行（链路闭合）──
    resumed = await builder.resume(tid_hr, "approved", "现场已确认")
    text = " ".join(
        m.get("content", "") for m in resumed.get("messages", []) if isinstance(m, dict)
    )
    print(f"    resume(approved) 后文本含「已批准执行」: {'已批准执行' in text}")
    print(f"    resume(approved) 后文本含「EXECUTED dispatch_work_order」: {'EXECUTED dispatch_work_order' in text}")
    assert "已批准执行" in text
    assert "EXECUTED dispatch_work_order" in text
    print("    [PASS] 批准后高危工具真正执行，HITL 链路闭合")

    # ── B3. resume(rejected) → 不执行 ──
    r2 = await builder.run("qa-r2-hitl-2", "给 TR-001 派发检修工单")
    assert r2.get("interrupt_action") == "pending", r2
    r2r = await builder.resume("qa-r2-hitl-2", "rejected", "风险过高")
    text2 = " ".join(
        m.get("content", "") for m in r2r.get("messages", []) if isinstance(m, dict)
    )
    print(f"    resume(rejected) 后文本含「已拒绝」: {'已拒绝' in text2}")
    print(f"    resume(rejected) 后文本含「EXECUTED」: {'EXECUTED' in text2}")
    assert "已拒绝" in text2
    assert "EXECUTED" not in text2
    print("    [PASS] 拒绝后高危工具未执行")

    # ── B4. 边界记录：presentation（演示）模式下高危卡片行为 ──
    print("\n" + "=" * 100)
    print("[B4] 边界记录：presentation（演示）模式下高危卡片行为")
    print("=" * 100)
    r_pres = await builder.run("qa-r2-pres-1", "建议对#1主变压器进行停机检修", display_mode="presentation")
    p_ia = r_pres.get("interrupt_action")
    p_agent = _last_agent_from_result(r_pres)
    p_out = any(
        isinstance(m, dict) and m.get("metadata", {}).get("is_demo_out_of_scope")
        for m in r_pres.get("messages", []) if isinstance(m, dict)
    )
    print(f"    路由 Agent           : {p_agent}")
    print(f"    interrupt_action     : {p_ia}")
    print(f"    is_demo_out_of_scope : {p_out}")
    for m in reversed(r_pres.get("messages", []) if isinstance(r_pres, dict) else []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            print(f"    回复预览 : {_first_line(m.get('content', ''))}")
            break

    print("\n" + "=" * 100)
    print("QA 独立回归验证脚本（第 2 轮）执行完成")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
