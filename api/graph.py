"""FR-4 LangGraph Supervisor 状态图。

编排 Supervisor 与 4 个专业 Agent 节点：
1. Supervisor → 路由决策
2. 监控/安规/诊断/知识库 Agent 执行
3. Human-in-the-Loop 高危操作拦截

流程：
  User Input → Supervisor → Agent Node → Supervisor → ... → End
                                ↓ (高危工具)
                          HumanInterrupt → 等待审批 → 继续/终止
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

try:  # GraphInterrupt 在 1.x 位于 langgraph.types，个别版本在 langgraph.errors
    from langgraph.types import GraphInterrupt
except ImportError:  # pragma: no cover
    from langgraph.errors import GraphInterrupt
from loguru import logger

from api.agents.agent_factory import HIGH_RISK_TOOLS, _filter_tools
from api.schemas import AgentState, InterruptAction
from prompts.system_prompts import SUPERVISOR_PROMPT

# 编译后的图引用，供 Agent 节点在 HITL 恢复时持久化工具计划
COMPILED_GRAPH = None

# ── Agent 节点名称 ─────────────────────────────────────

AGENT_NAMES = ["monitor_agent", "safety_agent", "diagnosis_agent", "knowledge_agent"]

# Agent 中文标签 → 节点名称映射（Mock 回复以「【监控 Agent】…」开头，
# visited 扫描需同时匹配中文标签与英文名称，否则无法识别已访问的 Agent）
AGENT_NAME_BY_LABEL = {
    "monitor_agent": "监控 Agent",
    "safety_agent": "安规 Agent",
    "diagnosis_agent": "诊断 Agent",
    "knowledge_agent": "知识库 Agent",
}


class GraphBuilder:
    """构建并持有 LangGraph 状态图。"""

    def __init__(self, mcp_tools: list[BaseTool]) -> None:
        self.mcp_tools = mcp_tools
        self.graph = self._build()

    # ── 构建图 ─────────────────────────────────────────

    def _build(self) -> StateGraph:
        """构建 LangGraph 状态图。"""
        builder = StateGraph(AgentState)

        # 节点
        builder.add_node("supervisor", self._supervisor_node)

        from api.agents.monitor_agent import build_node as build_monitor
        from api.agents.safety_agent import build_node as build_safety
        from api.agents.diagnosis_agent import build_node as build_diagnosis
        from api.agents.knowledge_agent import build_node as build_knowledge

        builder.add_node("monitor_agent", build_monitor(self.mcp_tools))
        builder.add_node("safety_agent", build_safety(self.mcp_tools))
        builder.add_node("diagnosis_agent", build_diagnosis(self.mcp_tools))
        builder.add_node("knowledge_agent", build_knowledge(self.mcp_tools))

        # 入口
        builder.set_entry_point("supervisor")

        # Agent → Supervisor（执行完成后回到 Supervisor）
        for name in AGENT_NAMES:
            builder.add_edge(name, "supervisor")

        # Supervisor 条件路由：使用 Command 模式，路由函数检查 Command.goto
        builder.add_conditional_edges(
            "supervisor",
            self._route_from_supervisor,
            {
                "monitor_agent": "monitor_agent",
                "safety_agent": "safety_agent",
                "diagnosis_agent": "diagnosis_agent",
                "knowledge_agent": "knowledge_agent",
                "__end__": END,
            },
        )

        # Checkpointer（MemorySaver 用于 HITL 状态持久化）
        self.checkpointer = MemorySaver()

        self.graph = builder.compile(checkpointer=self.checkpointer)
        global COMPILED_GRAPH
        COMPILED_GRAPH = self.graph
        return self.graph

    # ── Supervisor 节点 ────────────────────────────────

    async def _supervisor_node(self, state: AgentState) -> Command[Literal["monitor_agent", "safety_agent", "diagnosis_agent", "knowledge_agent", "__end__"]]:
        """Supervisor 路由节点：判断下一 Agent 或是否需要 HITL。"""
        # 如果已有错误且不是 llm 错误，直接结束
        if state.error:
            logger.warning("Supervisor detected error, ending: {}", state.error)
            return Command(goto=END, update={"current_agent": None, "next_agent": None})

        # 获取最后一条消息
        last_msg = state.messages[-1] if state.messages else None
        if not last_msg:
            return Command(goto=END, update={"current_agent": None, "next_agent": None})

        # 调用 LLM 做路由决策。
        # 注：高危工具拦截已下沉到 Agent 节点内部（interrupt），
        # Supervisor 仅负责路由；Agent 执行 TOOL_CALL 时触发 HITL。
        return await self._llm_route(state, last_msg)

    async def _llm_route(self, state: AgentState, last_msg: dict) -> Command:
        """使用 LLM（dashscope SDK 直接调用）做路由决策。"""
        from api.config import settings

        # ── Mock 模式：关键词路由，无需 LLM ─────
        if settings.mock_enabled or settings.dashscope_api_key in ("sk-placeholder", ""):
            # 收集已完成的 agent：依赖 state.current_agent，而非解析消息内容
            visited = set()
            if state.current_agent:
                # 从所有消息的 assistant 回复中提取已访问过的 agent
                # （Mock 中文标签「监控 Agent」或真实 LLM 英文名 "monitor_agent" 均匹配）
                for m in state.messages:
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        content = m.get("content", "")
                        for name in AGENT_NAMES:
                            if name in content or AGENT_NAME_BY_LABEL[name] in content:
                                visited.add(name)
                # 同时记录 state.current_agent（即使消息内容中不包含 agent 名）
                visited.add(state.current_agent)

            # 若所有 agent 都已访问过，结束本轮
            if len(visited) >= len(AGENT_NAMES):
                logger.info("[Mock] All {} agents visited, ending".format(len(visited)))
                return Command(goto=END, update={"current_agent": None, "next_agent": None})

            # 仅在用户消息（非 assistant 回复）上做关键词匹配
            content = ""
            for m in reversed(state.messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
                    break
            if any(kw in content for kw in ["设备", "状态", "遥测", "运行", "监控"]):
                selected = "monitor_agent"
            elif any(kw in content for kw in ["异常", "检测", "健康", "诊断", "故障"]):
                selected = "diagnosis_agent"
            elif any(kw in content for kw in ["知识", "规程", "原因", "方法", "油温", "过载", "什么", "如何", "多少"]):
                selected = "knowledge_agent"
            elif any(kw in content for kw in ["安全", "安规", "合规", "操作票"]):
                selected = "safety_agent"
            elif any(kw in content for kw in ["停机", "检修", "派单", "高危"]):
                selected = "diagnosis_agent"
            else:
                selected = "diagnosis_agent"

            # 跳过已访问过的 agent
            if selected in visited:
                for name in AGENT_NAMES:
                    if name not in visited:
                        selected = name
                        break
                else:
                    logger.info("[Mock] All agents visited, ending")
                    return Command(goto=END, update={"current_agent": None, "next_agent": None})

            logger.info("[Mock] Supervisor routed '{}' → {} (visited: {})", content[:50], selected, visited)
            return Command(
                goto=selected,
                update={"current_agent": selected, "next_agent": selected},
            )

        # 构建路由上下文
        conversation_summary = ""
        if len(state.messages) > 1:
            recent = state.messages[-3:]
            conversation_summary = "\n".join(
                f"{m.get('role', 'unknown')}: {m.get('content', '')[:200]}"
                for m in recent if isinstance(m, dict)
            )

        # 收集已完成的 agent（避免循环路由同一个 agent）
        visited = set()
        for m in state.messages:
            if isinstance(m, dict) and m.get("role") == "assistant":
                content = m.get("content", "")
                for name in AGENT_NAMES:
                    if name in content or AGENT_NAME_BY_LABEL[name] in content:
                        visited.add(name)

        route_context = (
            f"当前对话摘要：\n{conversation_summary}\n\n"
            f"已调用过的 Agent：{', '.join(visited) if visited else '无'}\n"
            f"用户最新问题：{last_msg.get('content', '')}"
        )

        try:
            from dashscope import Generation

            response = Generation.call(
                model="qwen-plus",
                messages=[
                    {"role": "system", "content": SUPERVISOR_PROMPT},
                    {"role": "user", "content": route_context},
                ],
                api_key=settings.dashscope_api_key,
                temperature=0.1,
                result_format="message",
            )

            if response.status_code != 200:
                logger.warning("Supervisor LLM call failed ({}), fallback to diagnosis_agent", response.message)
                return Command(
                    goto="diagnosis_agent",
                    update={"current_agent": "diagnosis_agent", "next_agent": "diagnosis_agent"},
                )

            # 提取决策
            decision = ""
            if response.output and response.output.choices:
                choice = response.output.choices[0]
                if choice and hasattr(choice.message, "content"):
                    decision = choice.message.content.strip().lower()

            selected = None
            for name in AGENT_NAMES:
                if name in decision:
                    selected = name
                    break

            if selected:
                logger.info("Supervisor routed to: {} (visited: {})", selected, visited)
                return Command(
                    goto=selected,
                    update={"current_agent": selected, "next_agent": selected},
                )

            logger.info("Supervisor: no agent matched, ending (decision: {})", decision)
            return Command(goto=END, update={"current_agent": None, "next_agent": None})

        except Exception as e:
            logger.warning("Supervisor LLM route failed ({}), fallback to diagnosis_agent", e)
            return Command(
                goto="diagnosis_agent",
                update={"current_agent": "diagnosis_agent", "next_agent": "diagnosis_agent"},
            )

    # ── 路由条件 ───────────────────────────────────────

    @staticmethod
    def _route_from_supervisor(
        state: AgentState,
    ) -> Literal["monitor_agent", "safety_agent", "diagnosis_agent", "knowledge_agent", "__end__"]:
        """从 Supervisor 的 Command goto 中读取下一个目标。"""
        # Command 模式已经处理了路由，这里不会实际被调用
        # 保留以供类型检查
        return state.next_agent or END

    # ── 公开方法 ───────────────────────────────────────

    async def run(
        self,
        thread_id: str,
        message: str,
    ) -> dict[str, Any]:
        """运行一次对话（阻塞，流式由 API 层处理）。"""
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = AgentState(
            messages=[{"role": "user", "content": message}],
            thread_id=thread_id,
        )
        try:
            result = await self.graph.ainvoke(initial_state, config)
            # langgraph ≥1.0 的行为变化：interrupt() 不再抛出 GraphInterrupt，
            # 而是正常返回，结果中携带 __interrupt__ 键（值为 Interrupt 对象列表）。
            interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
            if interrupts:
                interrupt_value = interrupts[0].value if interrupts else None
                tool = interrupt_value.get("tool") if isinstance(interrupt_value, dict) else None
                args = interrupt_value.get("args") if isinstance(interrupt_value, dict) else None
                msg = interrupt_value.get("message") if isinstance(interrupt_value, dict) else None
                return {
                    "messages": result.get("messages", []) or [],
                    "interrupt_action": "pending",
                    "interrupt_tool": tool,
                    "interrupt_args": args,
                    "interrupt_msg": msg,
                }
            return result
        except GraphInterrupt:
            # 图在 HITL 高危工具处挂起：提取中断信息回传给 API / 前端弹出确认框
            snapshot = self.graph.get_state(config)
            interrupt_value = None
            if snapshot and snapshot.tasks:
                for task in snapshot.tasks:
                    for intr in getattr(task, "interrupts", []):
                        interrupt_value = intr.value
            tool = interrupt_value.get("tool") if isinstance(interrupt_value, dict) else None
            args = interrupt_value.get("args") if isinstance(interrupt_value, dict) else None
            msg = interrupt_value.get("message") if isinstance(interrupt_value, dict) else None
            messages = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
            return {
                "messages": messages,
                "interrupt_action": "pending",
                "interrupt_tool": tool,
                "interrupt_args": args,
                "interrupt_msg": msg,
            }

    def get_state(self, thread_id: str) -> AgentState | None:
        """获取指定线程的当前状态（用于中断恢复）。"""
        try:
            state = self.graph.get_state({"configurable": {"thread_id": thread_id}})
            if state and state.values:
                return AgentState(**state.values)
        except Exception as e:
            logger.warning("get_state failed for {}: {}", thread_id, e)
        return None

    async def resume(
        self,
        thread_id: str,
        action: str,
        reason: str = "",
        edited_args: dict[str, Any] | None = None,
        edit_reason: str = "",
    ) -> dict[str, Any]:
        """从中断处恢复执行（用于 HITL 审批后）。

        通过 ``Command(resume=...)`` 将审批结果作为 ``interrupt()`` 的返回值注入，
        图从挂起点继续执行（高危工具在人工批准后真正执行）。

        Args:
            thread_id:   会话线程 ID。
            action:      审批动作（approved / rejected / edit_approved）。
            reason:      拒绝/批准原因（仅 approve/reject 用）。
            edited_args: 编辑后参数（仅 edit_approved 注入到 interrupt() 返回值）。
            edit_reason: 修改原因（仅 edit_approved 用）。
        """
        config = {"configurable": {"thread_id": thread_id}}

        # 注入审批结果（含编辑参数）
        approval: dict[str, Any] = {"action": action, "reason": reason}
        if edited_args is not None:
            approval["edited_args"] = edited_args
            approval["edit_reason"] = edit_reason
        result = await self.graph.ainvoke(Command(resume=approval), config)
        return result
