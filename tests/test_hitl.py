"""HITL 端到端测试：验证 S1~S4 修复。

无需真实 LLM / MCP / 数据库：Mock 模式下由高危关键词触发 TOOL_CALL，
LangGraph interrupt() 挂起 -> run() 捕获 GraphInterrupt 并回传 pending ->
resume(approved) 通过 Command(resume=...) 真正执行高危工具 -> resume(rejected) 不执行。

运行：
    python -m pytest tests/test_hitl.py -s
或：
    python tests/test_hitl.py
"""

import asyncio
import os

# 在导入 api 之前开启 Mock 模式（无需 LLM Key）
os.environ.setdefault("MOCK_ENABLED", "true")

from langchain_core.tools import BaseTool

from api.graph import GraphBuilder


class FakeHighRiskTool(BaseTool):
    """模拟高危工具 dispatch_work_order，验证 HITL 批准后会真正执行。"""

    name: str = "dispatch_work_order"
    description: str = "【高危】派发检修工单"

    def _run(self, **kwargs) -> str:
        return f"EXECUTED dispatch_work_order {kwargs}"

    async def _arun(self, **kwargs) -> str:
        return f"EXECUTED dispatch_work_order {kwargs}"


def _text(resumed) -> str:
    msgs = resumed.get("messages", []) if isinstance(resumed, dict) else []
    return " ".join(m.get("content", "") for m in msgs if isinstance(m, dict))


def test_hitl_approve_and_reject() -> None:
    builder = GraphBuilder([FakeHighRiskTool()])

    async def _run() -> None:
        # 1) 触发高危操作 -> 应挂起并回传 pending
        result = await builder.run("hitl-test-1", "请给 TR-001 派发一张检修工单")
        assert result.get("interrupt_action") == "pending", result
        assert result.get("interrupt_tool") == "dispatch_work_order", result
        print("[PASS] HITL 挂起并回传 pending:", result.get("interrupt_tool"))

        # 2) 批准 -> 高危工具应真正执行（S1: Command(resume) 生效；S4: 批准后执行）
        resumed = await builder.resume("hitl-test-1", "approved", "现场已确认")
        text = _text(resumed)
        assert "已批准执行" in text, text
        assert "EXECUTED dispatch_work_order" in text, text
        print("[PASS] 批准后高危工具真正执行")

        # 3) 拒绝 -> 不应执行（S4: 拒绝后不执行）
        r2 = await builder.run("hitl-test-2", "给 TR-001 派发检修工单")
        assert r2.get("interrupt_action") == "pending", r2
        r2r = await builder.resume("hitl-test-2", "rejected", "风险过高")
        text2 = _text(r2r)
        assert "已拒绝" in text2, text2
        assert "EXECUTED dispatch_work_order" not in text2, text2
        print("[PASS] 拒绝后高危工具未执行")

    asyncio.run(_run())
    print("\nALL HITL TESTS PASSED ✅")


if __name__ == "__main__":
    test_hitl_approve_and_reject()
