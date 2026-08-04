"""安规 Agent 节点。

职责：查询安规条款、安全合规检查。
通过 agent_factory 构建，本文件仅导出节点构建函数。
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import BaseTool

from api.schemas import AgentState


def build_node(mcp_tools: list[BaseTool]) -> Callable[[AgentState], dict[str, Any]]:
    """构建安规 Agent 节点。"""
    from api.agents.agent_factory import build_agent_node
    return build_agent_node("safety_agent", mcp_tools)
