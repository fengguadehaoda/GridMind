"""FR-4 LangGraph Supervisor 状态图（V1.5.1 后端改造 · T02 拆分 __init__）。

编排 Supervisor 与 4 个专业 Agent 节点：
1. Supervisor → 路由决策
2. 监控/安规/诊断/知识库 Agent 执行
3. Human-in-the-Loop 高危操作拦截

**T02 改动**（架构 §6 T02 · §2.1）：
- ✅ 拆分 ``__init__`` 为**同步 build**（仅构建 StateGraph 框架，不 compile）
  + ``async_init`` 异步 setup（拿 saver + compile）
- ✅ ``MemorySaver`` → ``AsyncSqliteSaver``（via ``CheckpointService`` 单例）
- ✅ ``_ensure_compiled()`` 兜底：未调 ``async_init`` 时**降级到 MemorySaver``
  （保持 ``test_hitl_edit.py`` / T01 测试 100% 向后兼容）
- ✅ 新增 ``aget_state()``（async）—— 配套 ``AsyncSqliteSaver`` 异步访问；
  ``get_state()`` 保留 sync（T01 兼容，依赖 MemorySaver 的 sync 行为）
- ✅ 公开方法签名 100% 保持不变：``run`` / ``resume`` / ``get_state``
- ⏸ ``pause`` / ``rewind_to_step`` / ``abort`` 在 T03 实现（本文档不实现）

流程：
  User Input → Supervisor → Agent Node → Supervisor → ... → End
                                ↓ (高危工具)
                          HumanInterrupt → 等待审批 → 继续/终止
"""

from __future__ import annotations

import asyncio
import json
import warnings
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from langchain_core.tools import BaseTool
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
from api.services.sse_event_emitter import sse_event_emitter
from prompts.system_prompts import SUPERVISOR_PROMPT

# 编译后的图引用，供 Agent 节点在 HITL 恢复时持久化工具计划
# 注意：V1.5.1 起在 ``GraphBuilder.async_init`` 中赋值；T01 之前在
# ``GraphBuilder.__init__`` 同步赋值（已废）。
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
    """构建并持有 LangGraph 状态图。

    **T02 生命周期**：
        # 生产路径
        builder = GraphBuilder(tools)         # 同步 build（不 compile）
        await builder.async_init()            # 异步 init：拿 saver + compile
        await builder.run(thread_id, msg)     # 正常使用

        # 测试 / T01 兼容路径
        builder = GraphBuilder(tools)         # 首次 run() / get_state() 时
        await builder.run(thread_id, msg)     # 自动 fallback 到 MemorySaver

    Attributes:
        mcp_tools: MCP 工具列表（4 个 Agent 共用）。
        graph: 已 compile 的 StateGraph，``async_init`` 后非 None。
        checkpointer: 当前的 saver 实例（``AsyncSqliteSaver`` 或 ``MemorySaver``）。
    """

    def __init__(self, mcp_tools: list[BaseTool]) -> None:
        """**同步**构建 StateGraph 框架（**不**调 compile、不连 DB）。

        Args:
            mcp_tools: MCP 工具列表，传给 4 个 Agent 节点工厂。
        """
        self.mcp_tools = mcp_tools
        # V1.5.1 T02：graph 延后到 async_init 完成；未 init 时为 None
        self.graph: Any = None
        self.checkpointer: Any = None
        self._builder: StateGraph = self._build_builder()
        self._compiled: bool = False
        self._lock = asyncio.Lock()  # 保护 _compiled 状态切换

    # ── 构建图（仅框架，不 compile） ────────────────────

    def _build_builder(self) -> StateGraph:
        """构建 StateGraph 框架（节点 + 边 + 入口），**不** compile。

        V1.5.1 T03 改造（架构 §2.2.1 决策 #2）：
        - 5 个节点（supervisor / 4 个 agent）每个前都加 ``_pause_check_node`` 包装
        - 包装函数检查 ``state.pause_signal`` 和 ``state.abort_signal``
          → 命中则 throw LangGraph ``interrupt()`` 挂起图
        - 节点名**不变**（保持 supervisor Command.goto 路由 + 边定义不变）
        """
        builder = StateGraph(AgentState)

        # 节点（V1.5.1 T03：每个节点前加 _pause_check_node 包装）
        builder.add_node(
            "supervisor",
            self._wrap_with_pause_check("supervisor", self._supervisor_node),
        )

        from api.agents.monitor_agent import build_node as build_monitor
        from api.agents.safety_agent import build_node as build_safety
        from api.agents.diagnosis_agent import build_node as build_diagnosis
        from api.agents.knowledge_agent import build_node as build_knowledge

        builder.add_node(
            "monitor_agent",
            self._wrap_with_pause_check("monitor_agent", build_monitor(self.mcp_tools)),
        )
        builder.add_node(
            "safety_agent",
            self._wrap_with_pause_check("safety_agent", build_safety(self.mcp_tools)),
        )
        builder.add_node(
            "diagnosis_agent",
            self._wrap_with_pause_check(
                "diagnosis_agent", build_diagnosis(self.mcp_tools)
            ),
        )
        builder.add_node(
            "knowledge_agent",
            self._wrap_with_pause_check(
                "knowledge_agent", build_knowledge(self.mcp_tools)
            ),
        )

        # 入口
        builder.set_entry_point("supervisor")

        # Agent → 条件路由（Bug 修复：一次回答只出一个 Agent 的回复）：
        # 原 ``add_edge(name, "supervisor")`` 会让每个 Agent 执行完都回到
        # Supervisor 再路由到**另一个** Agent（如 monitor 做"质量检查"），
        # 循环 4-5 次直到所有 Agent 都访问过 → 前端一次回答显示 4-5 段。
        # 现改为：普通问题 Agent 回复后**直接 END**；仅 HITL 工具计划未决
        # （pending_tool_plan 非 None）时回 Supervisor 走审核/兜底。
        for name in AGENT_NAMES:
            builder.add_conditional_edges(
                name,
                self._route_after_agent,
                {"supervisor": "supervisor", "__end__": END},
            )

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
        return builder

    # ── V1.5.1 T02 异步 init ──────────────────────────

    async def async_init(self) -> None:
        """**异步**初始化 checkpointer + 编译图（FastAPI lifespan 调用）。

        步骤：
        1. 调 :func:`api.services.checkpoint_service.get_checkpoint_service`
           拿全局单例
        2. 调 ``await service.async_init()``（幂等）
        3. 拿 ``service.get_saver()`` —— 已 ``async with`` 持有 aiosqlite 连接
        4. ``self._builder.compile(checkpointer=self.checkpointer)``
        5. 更新全局 ``COMPILED_GRAPH`` 引用（供 ``hitl_audit_service`` 等
           不持 builder 引用的模块访问）

        幂等：重复调用直接返回（防止 lifespan 多次重启时重复 compile）。
        """
        if self._compiled:
            return
        async with self._lock:
            if self._compiled:  # 双重检查
                return
            from api.services.checkpoint_service import get_checkpoint_service

            service = get_checkpoint_service()
            await service.async_init()
            self.checkpointer = service.get_saver()
            self.graph = self._builder.compile(checkpointer=self.checkpointer)
            self._compiled = True
            global COMPILED_GRAPH
            COMPILED_GRAPH = self.graph
            logger.info(
                "GraphBuilder async_init complete: saver={}, graph_compiled=True",
                type(self.checkpointer).__name__,
            )

    def _ensure_compiled(self) -> None:
        """兜底：未调 ``async_init`` 时，**用 MemorySaver 同步 compile**。

        用途：
        - ``test_hitl_edit.py`` 等老测试直接 ``GraphBuilder(tools)`` 后调
          ``run()`` / ``get_state()``，无 ``async_init`` 入口
        - 开发时 ``python -c "from api.graph import GraphBuilder; ..."`` 也能跑

        行为：
        - 第一次调时：``MemorySaver()`` + ``compile()``，发 ``UserWarning``
          提醒生产环境必须用 ``async_init``
        - 已 compile（async_init 调过）则直接返回
        """
        if self._compiled:
            return
        from langgraph.checkpoint.memory import MemorySaver

        self.checkpointer = MemorySaver()
        self.graph = self._builder.compile(checkpointer=self.checkpointer)
        self._compiled = True
        global COMPILED_GRAPH
        COMPILED_GRAPH = self.graph
        warnings.warn(
            "GraphBuilder used without async_init; falling back to MemorySaver "
            "(NO persistence across restarts). Call `await builder.async_init()` "
            "in production (lifespan hook).",
            UserWarning,
            stacklevel=2,
        )

    # ── V1.5.1 T03：_pause_check_node 包装器（架构 §2.2.1）────

    @staticmethod
    def _wrap_with_pause_check(
        node_name: str, original_node: Callable[[AgentState], Any]
    ) -> Callable[[AgentState], Any]:
        """包装 LangGraph 节点：进入前检查 ``pause_signal`` / ``abort_signal``。

        行为（架构 §2.2.1 + §2.2.4 决策 #2）：
        1. 检查 ``state.abort_signal``（如 ``aborted=True``）→ throw
           ``interrupt({"type": "user_abort", ...})`` 挂起；下次 invoke 不执行
           （abort 持久存在）
        2. 检查 ``state.pause_signal``（如 ``pause=True``）→ throw
           ``interrupt({"type": "user_pause", ...})`` 挂起；interrupt 不改 state，
           下次 ``ainvoke(None, config)`` 仍从此 wrapped 节点重跑
           → :py:meth:`resume` 清除 ``pause_signal`` 后 ``ainvoke(None)`` 继续
        3. 否则 ``await original_node(state)`` 调用原节点逻辑

        Args:
            node_name: 节点名（用于 ``wrapped.__name__`` 调试追踪）。
            original_node: 原始节点函数（async，``(state) -> dict``）。

        Returns:
            包装后的 async 节点函数。**节点名保持原样**（不重命名为
            ``{node_name}_with_pause_check``，否则会破坏 supervisor 的
            ``Command.goto`` 路由 + 边定义）。
        """
        # 延迟 import：避免模块加载时循环（interrupt 在 langgraph.types）

        async def wrapped(state: AgentState) -> dict[str, Any]:
            # 1) abort 检查（永久标志，优先于 pause）
            abort_signal = getattr(state, "abort_signal", None)
            if isinstance(abort_signal, dict) and abort_signal.get("aborted"):
                interrupt({
                    "type": "user_abort",
                    "aborted_at": abort_signal.get("aborted_at"),
                    "reason": abort_signal.get("reason", ""),
                })
                # abort 永久 → 返回空 update，state 不变，下次 invoke 仍 throw
                return {}
            # 2) pause 检查
            pause_signal = getattr(state, "pause_signal", None)
            if isinstance(pause_signal, dict) and pause_signal.get("pause"):
                interrupt({
                    "type": "user_pause",
                    "paused_at": pause_signal.get("paused_at"),
                    "reason": pause_signal.get("reason", ""),
                })
                # pause 挂起：interrupt 不改 state，下次 ainvoke(None) 仍
                # 回到此 wrapped 节点；resume() 清除 pause_signal 后才能继续
            # 3) 调用原节点
            return await original_node(state)

        # 保持节点名一致（不重命名），方便 LangGraph node registry 调试
        wrapped.__name__ = node_name
        wrapped.__qualname__ = node_name
        return wrapped

    # ── Supervisor 节点 ────────────────────────────────

    async def _supervisor_node(self, state: AgentState) -> Command[Literal["monitor_agent", "safety_agent", "diagnosis_agent", "knowledge_agent", "__end__"]]:
        """Supervisor 路由节点：判断下一 Agent 或是否需要 HITL。"""
        # 如果已有错误且不是 llm 错误，直接结束
        if state.error:
            logger.warning("Supervisor detected error, ending: {}", state.error)
            return Command(goto=END, update={"current_agent": None, "next_agent": None})

        # Bug 修复（防御层）：本轮已有 Agent 回复后 Supervisor 被再次进入
        # （历史状态残留 / 异常路径）→ 直接 END，避免"评价类"Agent 空跑
        # 再追加一段消息（正常流程 Agent 出口已直接 END，本分支兜底）。
        for m in reversed(state.messages):
            if isinstance(m, dict) and m.get("role") == "user":
                break  # 已回溯到本轮用户消息之前
            if isinstance(m, dict) and m.get("role") == "assistant":
                logger.info(
                    "Supervisor re-entered after agent reply, ending directly"
                )
                return Command(
                    goto=END,
                    update={"current_agent": None, "next_agent": None},
                )

        # 获取最后一条消息
        last_msg = state.messages[-1] if state.messages else None
        if not last_msg:
            return Command(goto=END, update={"current_agent": None, "next_agent": None})

        # 调用 LLM 做路由决策。
        # 注：高危工具拦截已下沉到 Agent 节点内部（interrupt），
        # Supervisor 仅负责路由；Agent 执行 TOOL_CALL 时触发 HITL。
        return await self._llm_route(state, last_msg)

    async def _llm_route(self, state: AgentState, last_msg: dict) -> Command:
        """使用 LLM（多模型抽象）做路由决策。"""
        from api.config import settings
        from core.llm_client import has_key_for, ModelProvider

        # ── Mock 模式：关键词路由，无需 LLM ─────
        # Bug1 修复：演示模式（X-Display-Mode: presentation）强制走 mock 关键词
        # 路由，避免演示态仍调用真实 LLM 做路由决策。
        display_mode = (getattr(state, "display_mode", None) or "").strip().lower()
        if (
            display_mode == "presentation"
            or settings.mock_enabled
            or (not has_key_for(ModelProvider.DASHSCOPE) and not has_key_for(ModelProvider.DEEPSEEK))
        ):
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
            # 按"问题意图"路由（优先级从高到低）：
            # 1. 高危操作（派工/停机/检修）→ diagnosis_agent（最高优先，
            #    避免被"建议"等泛词误吞——否则 knowledge_agent 不会触发
            #    interrupt，前端 HITL 弹窗不弹出）
            if any(kw in content for kw in ["停机", "停运", "检修", "派单", "派发", "高危", "工单", "跳闸", "隔离", "合闸"]):
                selected = "diagnosis_agent"
            # 2. 求助/问方法型 → knowledge_agent（移除"建议"这一泛词，避免误吞高危场景）
            elif any(kw in content for kw in ["怎么办", "如何", "方法", "规程", "处置", "排查", "解决", "原因", "知识", "油温", "过载", "什么", "多少"]):
                selected = "knowledge_agent"
            # 3. 主动诊断/告警确认型 → diagnosis_agent（明确诊断语义；"异常"仅作助词，不触发）
            elif any(kw in content for kw in ["检测", "健康", "诊断", "故障", "告警"]):
                selected = "diagnosis_agent"
            # 4. 状态查询型 → monitor_agent（"设备"等泛词放诊断之后，避免吞掉"检测设备健康评分"）
            elif any(kw in content for kw in ["设备", "状态", "遥测", "运行", "监控"]):
                selected = "monitor_agent"
            # 5. 安全/操作票 → safety_agent
            elif any(kw in content for kw in ["安全", "安规", "合规", "操作票"]):
                selected = "safety_agent"
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
            from core.llm_client import achat_completion

            # B1：chat_completion 是同步 LLM 调用（DashScope SDK / urllib 60s
            # 超时），在 async 节点内直接调用会阻塞事件循环 → 走 achat_completion
            # （内部 asyncio.to_thread 包装到工作线程）。
            ok, decision_raw = await achat_completion(
                messages=[
                    {"role": "system", "content": SUPERVISOR_PROMPT},
                    {"role": "user", "content": route_context},
                ],
                temperature=0.1,
            )
            if not ok:
                logger.warning("Supervisor LLM call failed: {}, fallback to diagnosis_agent", decision_raw)
                return Command(
                    goto="diagnosis_agent",
                    update={"current_agent": "diagnosis_agent", "next_agent": "diagnosis_agent"},
                )

            # 提取决策
            decision = decision_raw.strip().lower()

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
    def _route_after_agent(
        state: AgentState,
    ) -> Literal["supervisor", "__end__"]:
        """Agent 节点执行完成后的路由决策（Bug 修复核心）。

        原行为：Agent 执行完固定回 Supervisor → Supervisor 再路由到下一个
        未访问 Agent（"质量检查"）→ 每个 Agent 各追加一段 assistant 消息 →
        前端一次回答显示 4-5 段来自不同 Agent 的内容。

        新行为：
        - ``pending_tool_plan`` 非 None（HITL 工具计划未决 / 异常残留）→
          回 ``supervisor`` 走审核/兜底（高危协作场景保留多 Agent 能力）
        - 否则 → Agent 已产出**最终回答** → 直接 ``__end__``
          （普通问题一次问答只回复一段；HITL 恢复完成后同样直接结束）

        Note:
            LangGraph 在节点返回后先应用 update 再调用本函数，因此正常
            回复路径中 ``state.pending_tool_plan`` 已被 Agent 清为 None。
        """
        if state.pending_tool_plan is not None:
            logger.info(
                "[route_after_agent] pending_tool_plan set, back to supervisor"
            )
            return "supervisor"
        return END

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
        display_mode: str | None = None,
    ) -> dict[str, Any]:
        """运行一次对话（阻塞，流式由 API 层处理）。

        V1.5.1 T02 改动：方法首部调 ``_ensure_compiled()``，兼容未调
        ``async_init`` 的老代码路径。

        Bug1 修复：新增 ``display_mode`` 参数——前端 ``X-Display-Mode``
        header 透传进 AgentState，agent 节点据此决定 mock/真实 LLM 路径。
        """
        self._ensure_compiled()
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = AgentState(
            messages=[{"role": "user", "content": message}],
            thread_id=thread_id,
            display_mode=display_mode,
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
            snapshot = await self.aget_state(thread_id)
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
        """**同步**获取指定线程的当前状态（T01 兼容接口，仅适用于 MemorySaver）。

        警告：在使用 ``AsyncSqliteSaver`` 的生产环境，本方法会抛
        ``asyncio.InvalidStateError``（LangGraph 1.2.10 内部保护：AsyncSaver
        的同步接口禁止在主线程 event loop 中调用）。生产代码请用
        :py:meth:`aget_state`。

        T01 行为：保留原签名（sync），保证 ``test_hitl_edit.py`` 等老测试通过。
        """
        self._ensure_compiled()
        try:
            state = self.graph.get_state({"configurable": {"thread_id": thread_id}})
            if state and state.values:
                return AgentState(**state.values)
        except Exception as e:
            logger.warning("get_state failed for {}: {}", thread_id, e)
        return None

    async def aget_state(self, thread_id: str) -> Any:
        """**异步**获取指定线程的当前状态（V1.5.1 推荐接口）。

        与 :py:meth:`get_state` 区别：
        - 本方法用 ``await self.graph.aget_state(...)``，兼容
          ``AsyncSqliteSaver``（生产环境）
        - :py:meth:`get_state` 仍为 sync，**仅**适用于 ``MemorySaver``
          降级路径（测试 / 本地开发）

        Returns:
            LangGraph ``StateSnapshot`` 对象（含 ``values`` / ``next`` /
            ``config`` / ``tasks`` / ``metadata``），**不**是 ``AgentState``。
            调用方按需取 ``snapshot.values``。
        """
        self._ensure_compiled()
        try:
            return await self.graph.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
        except Exception as e:
            logger.warning("aget_state failed for {}: {}", thread_id, e)
            return None

    async def resume(
        self,
        thread_id: str,
        action: str,
        reason: str = "",
        edited_args: dict[str, Any] | None = None,
        edit_reason: str = "",
    ) -> dict[str, Any]:
        """从中断处恢复执行（用于 HITL 审批后 **或** pause 软恢复）。

        V1.5.1 T03 扩展（架构 §2.2.2）：
        - ``action == "continue_from_pause"``：清除 ``state.pause_signal`` 标志
          + ``ainvoke(None, config)`` 从挂起点继续（**不**走 Command(resume=...)）
        - 其他 action（``approved`` / ``rejected`` / ``edit_approved``）：HITL 老路径
          走 ``Command(resume=approval)`` 注入审批结果（图从原 ``interrupt()`` 处恢复）

        Args:
            thread_id:   会话线程 ID。
            action:      恢复动作（continue_from_pause / approved / rejected / edit_approved）。
            reason:      拒绝/批准原因（仅 approve/reject 用）。
            edited_args: 编辑后参数（仅 edit_approved 注入到 interrupt() 返回值）。
            edit_reason: 修改原因（仅 edit_approved 用）。

        Returns:
            - continue_from_pause 路径：``{"status": "resumed", "thread_id", "messages_count"}``
              或 ``{"status": "not_found", "thread_id"}``
            - HITL 老路径：LangGraph 返回的 state dict（与 T02 行为一致）
        """
        self._ensure_compiled()
        config = {"configurable": {"thread_id": thread_id}}

        # V1.5.1 T03 新增分支：continue_from_pause —— 清除 pause_signal + ainvoke(None)
        if action == "continue_from_pause":
            try:
                current = await self.graph.aget_state(config)
            except Exception as e:
                logger.warning(
                    "resume(continue_from_pause) failed: cannot get state for {}: {}",
                    thread_id, e,
                )
                return {"status": "not_found", "thread_id": thread_id}
            if current is None:
                return {"status": "not_found", "thread_id": thread_id}

            # 1) 清除 pause_signal：LangGraph 1.x ``aupdate_state`` 不会自动删除
            #    未在 values 中出现的字段，必须**显式设 None** 才能清空
            new_values = dict(current.values or {})
            new_values.pop("pause_signal", None)
            new_values["pause_signal"] = None  # 显式置 None
            try:
                await self.graph.aupdate_state(config, new_values)
            except Exception as e:
                logger.error(
                    "resume(continue_from_pause) aupdate_state failed for {}: {}",
                    thread_id, e,
                )
                raise

            # 2) 从挂起点继续：ainvoke(None, config) —— LangGraph 1.x 语义：
            #    无新 input，从上一个中断的 wrapped 节点重跑（pause_signal 已清，
            #    _pause_check_node 不会再 throw，正常执行原节点）
            try:
                result = await self.graph.ainvoke(None, config)
            except Exception as e:
                logger.error(
                    "resume(continue_from_pause) ainvoke failed for {}: {}",
                    thread_id, e,
                )
                raise

            messages = result.get("messages", []) if isinstance(result, dict) else []
            msg_count = len(messages) if isinstance(messages, list) else 0
            logger.info(
                "resume(continue_from_pause) OK: thread_id={}, messages_count={}",
                thread_id, msg_count,
            )
            # V1.5.1 T04（架构 §2.5.2）：emit `reasoning_resumed` 事件
            resumed_at = datetime.now(timezone.utc).isoformat()
            await self._safe_emit(
                sse_event_emitter.emit_resumed(
                    thread_id=thread_id,
                    resumed_at=resumed_at,
                ),
                event_name="reasoning_resumed",
                thread_id=thread_id,
            )
            return {
                "status": "resumed",
                "thread_id": thread_id,
                "messages_count": msg_count,
            }

        # ── 原 HITL 路径（T02 行为不变） ──
        # 注入审批结果（含编辑参数）到 interrupt() 返回值
        approval: dict[str, Any] = {"action": action, "reason": reason}
        if edited_args is not None:
            approval["edited_args"] = edited_args
            approval["edit_reason"] = edit_reason
        result = await self.graph.ainvoke(Command(resume=approval), config)
        # V1.5.1 T04（架构 §2.5.2）：HITL 决策后 emit `hitl_resolved`
        #   action='approved'/'rejected'/'edit_approved' → decision 字段
        resolved_at = datetime.now(timezone.utc).isoformat()
        await self._safe_emit(
            sse_event_emitter.emit_hitl_resolved(
                thread_id=thread_id,
                decision=action,
                resolved_at=resolved_at,
            ),
            event_name="hitl_resolved",
            thread_id=thread_id,
        )
        return result

    # ── V1.5.1 T03：pause / rewind_to_step / abort（架构 §2.2）─────

    @staticmethod
    async def _safe_emit(
        awaitable: Any, event_name: str, thread_id: str
    ) -> int:
        """Await 一个 SSE emit 协程，错误 swallow（emit 失败不破坏主操作）。

        V1.5.1 T04（架构 §2.5）：pause / resume / rewind / abort 4 个方法
        在 lock 释放前 emit 事件；emit 协程本身是 best-effort，失败仅记 warning。

        Args:
            awaitable:  ``sse_event_emitter.emit_*(...)`` 返回的 awaitable。
            event_name: 事件显示名（用于日志，如 ``"reasoning_paused"``）。
            thread_id:  会话 ID（用于日志）。

        Returns:
            成功送出的订阅者数（emit 失败时返回 0）。
        """
        try:
            delivered = await awaitable
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "SSE emit {} failed (non-fatal) for {}: {}",
                event_name, thread_id, e,
            )
            return 0
        logger.debug(
            "{} event emitted: thread={} delivered={}",
            event_name, thread_id, delivered,
        )
        return delivered

    async def pause(self, thread_id: str, reason: str = "") -> bool:
        """注入 ``pause_signal`` 标志到指定 thread 的 state（架构 §2.2.1 决策 #2）。

        流程：
        1. ``aget_state`` 拿当前 state（确认 thread 存在）
        2. ``aupdate_state(config, {"pause_signal": {...}})`` 注入软信号
        3. 下一个 wrapped 节点入口检查 ``pause_signal.get("pause")==True``
           → throw ``interrupt({"type": "user_pause", ...})`` 挂起图

        Returns:
            bool: True=成功注入；False=thread 不存在或图未编译。
        """
        self._ensure_compiled()
        config = {"configurable": {"thread_id": thread_id}}
        try:
            current = await self.graph.aget_state(config)
        except Exception as e:
            logger.warning("pause failed: cannot get state for {}: {}", thread_id, e)
            return False
        if current is None:
            logger.warning("pause failed: thread_id={} state is None", thread_id)
            return False

        pause_value: dict[str, Any] = {
            "pause": True,
            "paused_at": datetime.now(timezone.utc).isoformat(),
            "paused_by": "user",
            "reason": reason,
        }
        try:
            await self.graph.aupdate_state(config, {"pause_signal": pause_value})
        except Exception as e:
            logger.error("pause aupdate_state failed for {}: {}", thread_id, e)
            return False
        logger.info("pause injected: thread_id={}, reason={}", thread_id, reason)
        # V1.5.1 T04（架构 §2.5.2）：emit `reasoning_paused` 事件给所有订阅者
        # 此时 main.py 仍持有 session_lock（emit 完毕才释放），保证客户端
        # 在 lock 释放后立刻拿到事件（无竞态窗口）
        current_step = current.next[0] if current.next else None
        await self._safe_emit(
            sse_event_emitter.emit_paused(
                thread_id=thread_id,
                current_step=current_step,
                paused_at=pause_value["paused_at"],
            ),
            event_name="reasoning_paused",
            thread_id=thread_id,
        )
        return True

    async def rewind_to_step(
        self,
        thread_id: str,
        step_index: int,
        edited_content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """回退到指定 step 并从此重跑（架构 §2.2.3 决策 #3，F2 主链路）。

        流程（LangGraph 1.2.10 标准模式）：
        1. ``aget_state_history(config)`` 拿所有历史 checkpoints
        2. ``history[step_index]`` 拿 target state
        3. ``aupdate_state(target.config, values=target.values, as_node=target.next[0])``
           注入历史 state + 编辑内容
        4. ``ainvoke(None, target.config)`` 从 target step 重新跑
           —— LangGraph 默认从该 checkpoint 继续，**不**重走已完成 steps

        Args:
            thread_id:     会话线程 ID。
            step_index:    目标 step 索引（0-based；0 = 从入口重跑）。
            edited_content: 可选编辑内容（覆盖 ``target.values`` 的部分字段）。

        Returns:
            dict 含 ``status``（"rewound" / "invalid_step" / "history_error"
            / "update_state_error" / "rerun_error"）+ 详细字段。
        """
        self._ensure_compiled()
        config = {"configurable": {"thread_id": thread_id}}

        # 1) 拿历史 states
        history: list[Any] = []
        try:
            async for state in self.graph.aget_state_history(config):
                history.append(state)
        except Exception as e:
            logger.warning("rewind: aget_state_history failed for {}: {}", thread_id, e)
            return {
                "status": "history_error",
                "thread_id": thread_id,
                "error": str(e),
            }

        if step_index < 0 or step_index >= len(history):
            logger.warning(
                "rewind: invalid step_index={} (total={})",
                step_index, len(history),
            )
            return {
                "status": "invalid_step",
                "thread_id": thread_id,
                "step_index": step_index,
                "total_steps": len(history),
            }

        target = history[step_index]
        # 2) 注入历史 state（as_node 决定下一步从哪个 node 跑）
        #    target.next 是 (node_name,) 元组；空时表示该 checkpoint 已完成
        #    LangGraph 1.x aupdate_state 不接受 as_node="__end__"/"__start__"
        if not target.next:
            # 边界情况：target 已处于"已完成"状态。rewind 退化为"读取历史"
            # （不调 aupdate_state / ainvoke，state 已不可变更）
            logger.info(
                "rewind: target step_index={} already at end (next=()), no-op",
                step_index,
            )
            return {
                "status": "rewound",
                "thread_id": thread_id,
                "rewound_from_step": step_index,
                "rewound_to_step": "__end__",
                "messages_count": len(target.values.get("messages", [])) if target.values else 0,
                "note": "target already at end, rewind is no-op",
            }
        next_node = target.next[0]
        if next_node in ("__end__", "__start__"):
            # 虚拟节点：映射到入口节点（supervisor）以确保 aupdate_state 合法
            next_node = "supervisor"
        values = dict(target.values) if target.values else {}
        # 应用编辑内容（仅覆盖显式提供的字段）
        if edited_content:
            values.update(edited_content)

        try:
            await self.graph.aupdate_state(
                target.config,
                values=values,
                as_node=next_node,
            )
        except Exception as e:
            logger.error(
                "rewind: aupdate_state failed for {}: {}", thread_id, e,
            )
            return {
                "status": "update_state_error",
                "thread_id": thread_id,
                "error": str(e),
            }

        # 3) 从 target step 重新跑（ainvoke(None, ...) = 继续，不传新 input）
        try:
            result = await self.graph.ainvoke(None, target.config)
        except Exception as e:
            logger.error("rewind: ainvoke failed for {}: {}", thread_id, e)
            return {
                "status": "rerun_error",
                "thread_id": thread_id,
                "error": str(e),
            }

        messages = result.get("messages", []) if isinstance(result, dict) else []
        msg_count = len(messages) if isinstance(messages, list) else 0
        logger.info(
            "rewind OK: thread_id={}, from step={}, next={}, messages_count={}",
            thread_id, step_index, next_node, msg_count,
        )
        # V1.5.1 T04（架构 §2.5.2）：emit `step_replaced` 通知前端清空后续 steps
        #   old/new content hash 当前由 LangGraph values 简单序列化生成（生产
        #   可换 sha256(messages_json) 等强 hash；T04 简化为内容长度 + step）
        try:
            old_content_repr = repr(target.values)[:200] if target.values else ""
            new_content_repr = repr(messages)[:200] if messages else ""
            old_hash = f"sha1:{hash(old_content_repr) & 0xffffffff:08x}"
            new_hash = f"sha1:{hash(new_content_repr) & 0xffffffff:08x}"
        except Exception:  # noqa: BLE001
            old_hash = ""
            new_hash = ""
        await self._safe_emit(
            sse_event_emitter.emit_step_replaced(
                thread_id=thread_id,
                step_index=step_index,
                old_content_hash=old_hash,
                new_content_hash=new_hash,
            ),
            event_name="step_replaced",
            thread_id=thread_id,
        )
        return {
            "status": "rewound",
            "thread_id": thread_id,
            "rewound_from_step": step_index,
            "rewound_to_step": next_node,
            "messages_count": msg_count,
        }

    async def abort(self, thread_id: str, reason: str = "") -> bool:
        """强制中止：注入 ``abort_signal`` 永久标志（架构 §2.2.4）。

        与 ``pause`` 区别：abort 后**不可** resume（``abort_signal`` 永不清除，
        节点入口每次都 throw ``interrupt({"type": "user_abort", ...})``）。

        Returns:
            bool: True=成功注入；False=注入失败。
        """
        self._ensure_compiled()
        config = {"configurable": {"thread_id": thread_id}}
        abort_value: dict[str, Any] = {
            "aborted": True,
            "aborted_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        try:
            await self.graph.aupdate_state(config, {"abort_signal": abort_value})
        except Exception as e:
            logger.error("abort aupdate_state failed for {}: {}", thread_id, e)
            return False
        logger.info("abort injected: thread_id={}, reason={}", thread_id, reason)
        # V1.5.1 T04（架构 §2.5.2）：abort 终态通过 `reasoning_error` 事件
        #   通知订阅者（``recoverable=False`` 前端停止等待；error 前缀
        #   ``aborted_by_user:`` 供前端区分"用户主动中止"与"系统错误"）。
        #   注：主理人决策 #6 仅列出 6 个 type，abort 不在专用 list；选
        #   ``reasoning_error`` 因其语义最接近"推理被强制终止"。
        await self._safe_emit(
            sse_event_emitter.emit_reasoning_error(
                thread_id=thread_id,
                error=f"aborted_by_user: {reason}" if reason else "aborted_by_user",
                recoverable=False,
            ),
            event_name="reasoning_error",
            thread_id=thread_id,
        )
        return True
