"""Agent 工厂——为每个 Agent 绑定 LLM + MCP 工具集 + 系统提示词。

所有 Agent 共享同一套构建逻辑：
1. 从 MCP Server（localhost:9901）获取对应工具
2. 绑定到 DashScope LLM（直接使用 dashscope SDK）
3. 返回 LangGraph 可调用的 agent 节点函数

HITL（Human-in-the-Loop）：当 Agent 解析到高危工具调用（dispatch_work_order /
suggest_shutdown）时，节点内调用 LangGraph 原生的 interrupt() 挂起整个图，
等待人工审批；审批通过（Command(resume=...)）后从 checkpointer 恢复并真正执行该工具。

P0 可解释性 AI：diagnosis_agent 节点的"自然语言回复"完成后，调用
``DiagnosisOrchestrator.fuse()`` 走"LLM + 机理 + 规则"三层融合，并把
``DiagnosisFusionResult`` 注入 message metadata + 持久化到 FUSION_STORE。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from langchain_core.tools import BaseTool
from langgraph.types import interrupt
from loguru import logger

from api.config import settings
from api.schemas import (
    AgentState,
    GraphAnswer,
    GraphAnswerEdge,
    GraphAnswerNode,
    GraphPath,
    KnowledgeAnswer,
    SourceRef,
)
from prompts.system_prompts import get_prompt

# ── DashScope 可用性检测（T1 修复）─────────────────────
# 未安装 dashscope 时，standard 模式自动降级 mock，避免 ImportError。
from core.llm_client import is_dashscope_available as _dashscope_available

# ── API Key 无效检测 + mock 降级（T1 修复）───────────────
# 用户配了真实格式但无效的 key（如 sk-placeholder 之外的假 key）时，
# dashscope 返回 401 "Invalid API-key provided." 或 SDK 抛 InvalidApiKey。
# 此时不应把原始错误弹给用户，而是与 dashscope 未装时一致自动降级 mock。
def _is_invalid_api_key_error(error_text: str) -> bool:
    """判断错误文本是否为「API Key 无效」（覆盖 dashscope 多种报错格式）。"""
    low = (error_text or "").lower()
    return (
        "invalid api-key" in low
        or "invalidapikey" in low
        or "invalid api key" in low
        or "api_key" in low
        or "apikey" in low
    )


async def _degrade_to_mock(agent_name: str, state: AgentState, reason: str) -> dict[str, Any]:
    """API Key 无效时自动降级：返回 mock 剧本回复，不把原始错误暴露给用户。"""
    logger.warning("[{}] {}，自动降级到 mock 模式", agent_name, reason)
    mock_reply = await _get_mock_response(agent_name, state)
    final = await _maybe_run_explainability(
        agent_name, state, mock_reply,
        user_msg=_last_user_message(state),
    )
    update = {
        "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
        "current_agent": agent_name,
        "error": None,
    }
    # M-3（AC-4）：knowledge_agent 真实路径无 Key 自动降级 mock 时同样带结构化来源
    if agent_name == "knowledge_agent":
        update = await _attach_knowledge_answer(
            agent_name, update,
            last_user=_last_user_message(state),
            answer_text=mock_reply,
        )
    return update

# ── 高危工具清单（触发 HITL，需人工确认）─────────────────
# 原本定义在 graph.py，这里统一定义以避免循环依赖，graph.py 直接复用。

HIGH_RISK_TOOLS = {"dispatch_work_order", "suggest_shutdown"}

# ── Agent → MCP 工具名称映射 ──────────────────────────

AGENT_TOOLS_MAP: dict[str, list[str]] = {
    "monitor_agent": [
        "get_device_list",
        "get_device_telemetry",
        "get_latest_telemetry",
        "get_device_info",
        "get_inspection_records",
    ],
    "safety_agent": [
        "get_safety_rules",
        "get_safety_rule_by_code",
        "check_safety_compliance",
    ],
    "diagnosis_agent": [
        "detect_device_anomalies",
        "get_device_health_score",
        "get_all_health_scores",
        "get_critical_devices",
        # 高危操作工具：触发 HITL 人工确认
        "dispatch_work_order",
        "suggest_shutdown",
    ],
    "knowledge_agent": [
        # M0 阶段：4 个原工具（保持不变，向后兼容）
        "query_knowledge_base",
        "search_knowledge_chunks",
        "search_graph_entities",
        "get_entity_relations",
        # M1 阶段：5 个新工具（知识图谱 Neo4j 升级）
        "cypher_query",
        "multi_hop_expand",
        "find_devices_by_substation",
        "get_fault_chain",
        "get_applicable_regulations",
        # V1.6 P0-5：功能介绍优先 grounding 通道（架构增补件 §1.7）
        # 用户问「5 个核心视图/功能介绍/引导/演示」时优先调用，
        # 内部已做意图门控：非功能介绍类问题返回 count=0 由上层兜底。
        "search_feature_intro",
        # M-4 P0-3：图谱问答工具（kg_apply_rules 仅注册；规则推导边默认不启用
        # ——enable_inference_engine=False 时天然返回空，决策 3）
        "kg_multi_hop_reason",
        "kg_apply_rules",
    ],
}


def _filter_tools(
    all_tools: list[BaseTool], agent_name: str,
) -> list[BaseTool]:
    """从全局工具列表中筛选出当前 Agent 可用的工具。"""
    allowed = AGENT_TOOLS_MAP.get(agent_name, [])
    return [t for t in all_tools if t.name in allowed]


def _build_system_prompt(agent_name: str, tools: list[BaseTool]) -> str:
    """为 Agent 构建包含工具描述的系统提示词。"""
    prompt = get_prompt(agent_name)

    if tools:
        tool_descriptions = []
        for t in tools:
            desc = f"  - {t.name}: {t.description or '无描述'}"
            # 添加参数信息
            if hasattr(t, "args") and t.args:
                params = []
                for arg_name, arg_info in t.args.items():
                    required = "必填" if getattr(arg_info, "required", False) else "可选"
                    params.append(f"{arg_name}({required})")
                if params:
                    desc += f" 参数: {', '.join(params)}"
            tool_descriptions.append(desc)

        prompt += "\n\n你可以使用的工具：\n" + "\n".join(tool_descriptions)
        prompt += "\n\n调用工具时，返回格式为: TOOL_CALL: tool_name | arg1=val1 | arg2=val2"

    return prompt


def _convert_to_dashscope_messages(
    state: AgentState, agent_name: str, tools: list[BaseTool],
) -> list[dict[str, Any]]:
    """将 AgentState 转换为 DashScope API 消息格式。"""
    system_prompt = _build_system_prompt(agent_name, tools)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]

    for msg in state.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "tool":
            # 工具结果转为 user 消息
            messages.append({
                "role": "user",
                "content": f"工具返回结果: {content}",
            })
        elif role == "assistant":
            messages.append({"role": "assistant", "content": content})
        elif role == "system":
            messages.append({"role": "system", "content": content})
        else:
            messages.append({"role": "user", "content": content})

    return messages


# ── 工具解析 ──────────────────────────────────────────

_TOOL_CALL_PREFIX = "TOOL_CALL:"

_HIGH_RISK_KEYWORDS_DISPATCH = ["派单", "工单", "检修", "高危操作", "高危"]
_HIGH_RISK_KEYWORDS_SHUTDOWN = ["停机", "停运"]


def _parse_tool_args(tool_args_str: str) -> dict[str, Any]:
    """解析 TOOL_CALL 行中的 `key=val | key=val` 参数。"""
    args: dict[str, Any] = {}
    if not tool_args_str:
        return args
    for part in tool_args_str.split("|"):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            args[key.strip()] = val.strip()
    return args


def _parse_tool_calls(reply: str) -> list[dict[str, Any]]:
    """从 LLM 回复中解析所有 TOOL_CALL 行，返回工具计划列表。"""
    plan: list[dict[str, Any]] = []
    for line in reply.split("\n"):
        line = line.strip()
        if not line.startswith(_TOOL_CALL_PREFIX):
            continue
        rest = line[len(_TOOL_CALL_PREFIX):].strip()
        if "|" in rest:
            name = rest.split("|")[0].strip()
            args_str = "|".join(rest.split("|")[1:])
        else:
            name = rest
            args_str = ""
        plan.append({"name": name, "args": _parse_tool_args(args_str)})
    return plan


async def _invoke_tool(tools: list[BaseTool], tool_name: str, args: dict[str, Any]) -> str:
    """执行单个工具调用（异步），返回结果字符串。"""
    for t in tools:
        if t.name == tool_name:
            try:
                result = await t.ainvoke(args)
                if isinstance(result, str):
                    return result
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                logger.error("Tool '{}' failed: {}", tool_name, e)
                return f"工具调用出错: {e!s}"
    return f"未知工具: {tool_name}"


def _persist_plan(thread_id: str, plan: list[dict[str, Any]]) -> None:
    """将工具计划持久化到 checkpointer，使 HITL 恢复时跳过重复 LLM 调用。"""
    from api.graph import COMPILED_GRAPH

    if COMPILED_GRAPH is None:
        return
    try:
        COMPILED_GRAPH.update_state(
            {"configurable": {"thread_id": thread_id}},
            {"pending_tool_plan": plan},
        )
    except Exception as e:  # pragma: no cover - 持久化失败不应阻断主流程
        logger.warning("persist pending_tool_plan failed: {}", e)


async def _execute_tools(tools: list[BaseTool], plan: list[dict[str, Any]]) -> list[str]:
    """按工具计划执行，高危工具触发 LangGraph interrupt() 等待人工确认。

    在 HITL 恢复（resume）场景下，interrupt() 会直接返回审批结果而非再次挂起。
    支持 Edit & Continue 模式：若审批结果中含 ``edited_args`` 则优先使用。
    """
    results: list[str] = []
    for item in plan:
        name = item.get("name", "")
        args = item.get("args", {})
        if name in HIGH_RISK_TOOLS:
            approval = interrupt({
                "type": "high_risk_tool",
                "tool": name,
                "args": args,
                # original_args 与 args 同值（编辑模式 diff 显示用）
                "original_args": args,
                "message": f"高危工具 '{name}'(参数: {args}) 需要人工确认后才能执行",
            })
            if isinstance(approval, dict) and approval.get("action") == "approved":
                res = await _invoke_tool(tools, name, args)
                results.append(f"【{name}】✅ 已批准执行：{res}")
            elif (
                isinstance(approval, dict)
                and approval.get("action") == "edit_approved"
                and isinstance(approval.get("edited_args"), dict)
            ):
                # Edit & Continue：使用 edited_args 替换原 args 执行
                final_args = approval["edited_args"]
                logger.info(
                    "Executing tool '{}' with edited_args: {}",
                    name,
                    final_args,
                )
                res = await _invoke_tool(tools, name, final_args)
                results.append(
                    f"【{name}】✅ 已按编辑后内容执行：{res}"
                )
            else:
                reason = approval.get("reason", "") if isinstance(approval, dict) else ""
                results.append(
                    f"【{name}】❌ 已拒绝，未执行"
                    + (f"（原因：{reason}）" if reason else "")
                )
        else:
            res = await _invoke_tool(tools, name, args)
            results.append(f"【{name}】结果：{res}")
    return results


def _last_user_message(state: AgentState) -> str:
    for m in reversed(state.messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content", "")
    return ""


def _high_risk_mock_reply(last_user: str) -> str | None:
    """Mock 模式下，高危关键词触发 HITL 演示（无需 LLM Key）。"""
    if any(kw in last_user for kw in _HIGH_RISK_KEYWORDS_DISPATCH):
        return "TOOL_CALL: dispatch_work_order | device_id=TR-001 | description=演示派发检修工单 | priority=high"
    if any(kw in last_user for kw in _HIGH_RISK_KEYWORDS_SHUTDOWN):
        return "TOOL_CALL: suggest_shutdown | device_id=TR-001 | reason=演示高危停运操作"
    return None


# ── Bug2 修复：演示模式剧本范围（白名单）──────────────────
# 演示模式只回答剧本内问题；剧本外直接返回固定提示，不走任何工具调用链。
# 注意：「派发/停运/检修/高危」**不在**白名单内——演示模式不应自动触发
# 高危审批（用户报 bug：剧本外问题误弹高危审批 + 状态卡死）。
_DEMO_SCRIPT_KEYWORDS: tuple[str, ...] = (
    # 监控类
    "变压器", "主变", "TR-001",
    # 设备列表
    "所有设备", "全部", "设备状态", "设备列表",
    # 诊断类
    "故障", "诊断", "电压异常",
    # 知识库
    "知识", "RAG", "规程",
    # 功能介绍（意图门控已拦截）
    "功能介绍", "5 个核心视图", "GridMind", "演示", "场景",
)


# P1-1 修复：高危动作意图词（优先级高于设备/主题白名单）。
# 命中这些词的提问在演示模式下直接判定剧本外——演示模式不应触发高危演示，
# 也不应因含 "TR-001" 等设备 ID 误走知识库 RAG mock。
_DEMO_HIGH_RISK_ACTION_KEYWORDS: tuple[str, ...] = (
    "派发", "派单", "检修", "停运", "停机", "高危", "工单", "跳闸", "隔离", "合闸",
)

# P1-1 修复补充（演示高危话术例外）：演示设备标识提示词。
# 演示模式「高危操作」快捷卡片消息（如“建议对#1主变压器进行停机检修”）是
# **故意的演示话术**——同时命中高危动作词与设备标识时应继续走白名单匹配，
# 从而进入剧本内分支触发 HITL 审批演示；只有**无设备标识的随机高危词**
# （剧本外问题）才维持「不误弹高危审批」的原有防御。
_DEMO_HIGH_RISK_DEVICE_HINTS: tuple[str, ...] = ("tr-001", "主变", "变压器")


def _normalize_ws(text: str) -> str:
    """去除所有空白字符（含中英文空格），用于空白不敏感的匹配。"""
    return "".join(text.split())


def _is_demo_script_match(last_user: str) -> bool:
    """演示模式剧本范围判断。

    判定顺序（P1-1 修复 + 演示高危话术例外）：
    1. 高危动作词否定判定：命中「派发/检修/停运/高危/工单/跳闸/隔离/合闸」等
       **且不含演示设备标识**（TR-001/主变/变压器）时直接返回 False（剧本外），
       优先级高于设备/主题白名单——无设备标识的随机高危词不误弹高危审批；
    2. 演示高危话术例外：同时命中高危动作词**与**演示设备标识
       （如“建议对#1主变压器进行停机检修”）→ 不判剧本外，继续走白名单匹配；
    3. 白名单正向匹配：命中 ``_DEMO_SCRIPT_KEYWORDS`` 才算剧本内。

    空白归一化（P1-2 修复）：匹配前把用户输入与关键词都去掉所有空白字符，
    保证「请介绍5个核心视图」（无空格）与「5 个核心视图」（带空格）均能命中。
    """
    if not last_user:
        return False
    normalized = _normalize_ws(last_user)
    # P1-1：高危动作意图 → 剧本外（优先级最高）；但演示高危话术（高危词+设备标识）
    # 是故意的演示卡片，不判剧本外（继续走下方白名单匹配）。
    # 设备标识做大小写不敏感匹配（TR-001 / tr-001 均命中）。
    normalized_lower = normalized.lower()
    is_high_risk = any(kw in normalized for kw in _DEMO_HIGH_RISK_ACTION_KEYWORDS)
    is_device_hint = any(
        _normalize_ws(d).lower() in normalized_lower
        for d in _DEMO_HIGH_RISK_DEVICE_HINTS
    )
    if is_high_risk and not is_device_hint:
        return False
    # P1-2：白名单正向匹配（关键词同样归一化空白）
    # P2-B（R-1d）：白名单关键字统一 lower() 归一化——与上方高危设备标识例外门
    # （normalized_lower）一致，保证 'TR-001 状态' 与 'tr-001 状态' 均命中剧本。
    return any(
        _normalize_ws(kw).lower() in normalized_lower
        for kw in _DEMO_SCRIPT_KEYWORDS
    )


_DEMO_OUT_OF_SCOPE_TEXT = "当前为演示模式，无法回答您提出的问题。您可以在标准模式下进行提问。"


def _demo_out_of_scope_reply(agent_name: str) -> str:
    """演示模式剧本外固定提示文案。

    带 Agent 中文标签前缀：Supervisor 的 visited 检测依赖消息内容中的
    「监控/诊断/… Agent」标签来判断已访问的 Agent，无标签会导致同轮
    内反复路由同一 Agent（LangGraph 递归上限 → 报错）。
    """
    label = {
        "monitor_agent": "监控",
        "safety_agent": "安规",
        "diagnosis_agent": "诊断",
        "knowledge_agent": "知识库",
    }.get(agent_name, agent_name)
    return f"【{label} Agent】{_DEMO_OUT_OF_SCOPE_TEXT}"


def _format_mock_tool_answer(results: list[str]) -> str:
    return "【诊断 Agent】已处理高危操作请求：\n\n" + "\n".join(results)


# ═══════════════════════════════════════════════════════
# M-3：mock 知识剧本结构化来源（K-4 —— 一处定义，与正文「📄 引用来源」一致）
# ═══════════════════════════════════════════════════════

#: mock 知识剧本 sources（与 ``_get_mock_response`` 正文「📄 引用来源」行完全一致）。
#: doc_id 为演示用途（``user-upload:mock-*``），不要求真实存在于 DB（K-4）。
_MOCK_KNOWLEDGE_SOURCES: dict[str, list[dict[str, Any]]] = {
    "oil_temperature": [
        {
            "doc_id": "user-upload:mock-transformer-rules",
            "filename": "变压器运行规程.md",
            "title": "变压器运行规程",
            "source": "user-upload/变压器运行规程.md",
            "section": "4.2",
            "score": 0.87,
            "snippet": "油温异常分级：变压器顶层油温一般不得超过 85°C，超过 80°C 时应加强监视并及时查明原因……",
            "content_excerpt": (
                "变压器油温异常分级是判断变压器运行状态的重要依据。第 4.2 节规定："
                "顶层油温一般不得超过 85°C，超过 80°C 时应加强监视并及时查明原因。"
                "油温异常按严重程度分为三级：一级为油温超过 85°C 但未达 95°C，"
                "此时应加强巡视并安排停电检查；二级为油温超过 95°C，应立即减载并"
                "申请停电处理；三级为油温超过 105°C 或伴随瓦斯保护动作，应立即停运"
                "并通知检修。变压器运行中应定期检查散热器、风扇和油泵的运行状态，"
                "发现油温异常升高时应结合负载率、环境温度和油色谱分析结果综合判断"
                "故障原因，防止因冷却系统故障或内部故障导致绝缘加速老化。"
            ),
            "chunk_index": 3,
            "total_chunks": 12,
        },
        {
            "doc_id": "user-upload:mock-diagnosis-handbook",
            "filename": "电力设备故障诊断手册.md",
            "title": "电力设备故障诊断手册",
            "source": "user-upload/电力设备故障诊断手册.md",
            "section": None,
            "score": 0.72,
            "snippet": "变压器油温异常原因分析：负载过重、冷却系统故障、绝缘老化、连接不良……",
            "content_excerpt": (
                "变压器油温异常的常见原因包括：负载过重导致发热增加；冷却系统故障"
                "如散热器堵塞、风扇或油泵故障导致散热能力下降；绕组绝缘老化导致"
                "介质损耗增大；分接开关或引线接触不良产生局部过热。诊断时首先核对"
                "当前负载率与环境温度，判断是否属于正常运行条件下的温升；随后检查"
                "冷却系统运行状态，确认油泵、风扇是否正常运转；最后安排油色谱分析"
                "（总烃、乙炔、CO、CO2 等特征气体），判断是否存在内部放电或过热"
                "故障。若油温持续异常且伴随特征气体增长，应尽早安排停电检修，避免"
                "故障扩大导致变压器损坏。"
            ),
            "chunk_index": 5,
            "total_chunks": 20,
        },
    ],
    "overload": [
        {
            "doc_id": "user-upload:mock-transformer-rules",
            "filename": "变压器运行规程.md",
            "title": "变压器运行规程",
            "source": "user-upload/变压器运行规程.md",
            "section": "6.1",
            "score": 0.85,
            "snippet": "过载运行限制：正常周期负载不超过 130% 额定容量、紧急长期负载不超过 140%……",
            "content_excerpt": (
                "变压器过载运行时间限制（依据 IEC 60076 标准）是第 6.1 节的核心内容。"
                "正常周期负载（不超过 130% 额定容量）持续时间不得超过 2 小时；紧急"
                "长期负载（不超过 140%）不得超过 30 分钟；紧急短期负载（不超过 150%）"
                "不得超过 10 分钟。过载运行期间应密切监视顶层油温、绕组热点温度和"
                "冷却系统运行状态，必要时投入备用冷却器。过载会加速绝缘老化，缩短"
                "设备寿命，严重时可能引发热故障甚至损坏变压器。调度人员应根据负载"
                "预测及时调整负荷分配，避免长时间过载运行；当负载超过紧急短期负载"
                "限值时应立即减载或申请转移负荷。"
            ),
            "chunk_index": 8,
            "total_chunks": 12,
        },
        {
            "doc_id": "user-upload:mock-diagnosis-handbook",
            "filename": "电力设备故障诊断手册.md",
            "title": "电力设备故障诊断手册",
            "source": "user-upload/电力设备故障诊断手册.md",
            "section": None,
            "score": 0.66,
            "snippet": "过载章节：长期过载会导致绕组热点温度升高、绝缘老化加速……",
            "content_excerpt": (
                "过载章节分析了变压器过载运行对设备寿命的影响机理。长期过载导致"
                "绕组热点温度升高，绝缘材料在高温下加速老化，根据热老化定律，"
                "绕组热点温度每升高 6°C，绝缘寿命约缩短一半。过载还会引起连接"
                "部位接触电阻增大、局部过热，甚至引发电气连接故障。诊断时应结合"
                "负载曲线、油温记录和油色谱数据综合评估过载危害程度，并给出减载"
                "建议或负荷转移方案。对于频繁过载的变压器，应缩短巡检周期，加强"
                "对绕组温度、油温和冷却系统的监测，必要时安排停电检修和绝缘检测。"
            ),
            "chunk_index": 9,
            "total_chunks": 20,
        },
    ],
    "shutdown": [
        {
            "doc_id": "user-upload:mock-transformer-rules",
            "filename": "变压器运行规程.md",
            "title": "变压器运行规程",
            "source": "user-upload/变压器运行规程.md",
            "section": "6.2",
            "score": 0.82,
            "snippet": "停机检修流程：办理工作票、断开高低压侧断路器、验电接地、挂牌……",
            "content_excerpt": (
                "变压器停机检修必须严格执行第 6.2 节规定的安全流程。检修前应办理"
                "工作票并履行审批手续，明确检修内容、安全措施和监护人；检修时应"
                "先断开高压侧和低压侧断路器，拉开隔离开关并可靠接地，验电确认无电"
                "后悬挂「禁止合闸，有人工作」标示牌。检修内容一般包括：油色谱分析"
                "与油质检测、绕组绝缘电阻与介质损耗测量、分接开关检查、冷却系统"
                "检修、套管及密封件检查等。检修完成后应进行交接试验，确认各项指标"
                "合格后方可恢复送电。全过程应做好检修记录并存档，便于后续追溯。"
            ),
            "chunk_index": 10,
            "total_chunks": 12,
        },
        {
            "doc_id": "user-upload:mock-diagnosis-handbook",
            "filename": "电力设备故障诊断手册.md",
            "title": "电力设备故障诊断手册",
            "source": "user-upload/电力设备故障诊断手册.md",
            "section": None,
            "score": 0.6,
            "snippet": "检修章节：停机检修前应完成故障定位与风险评估……",
            "content_excerpt": (
                "检修章节强调停机检修前的故障定位与风险评估。检修前应通过油色谱"
                "分析、电气试验和运行记录定位故障部位，评估故障严重程度与检修"
                "紧迫性，据此制定检修方案并准备备品备件。检修过程中应重点关注"
                "绕组、分接开关、套管、冷却系统等易发故障部位，对发现的异常进行"
                "详细记录并分析成因。检修完成后应进行交接试验，包括绝缘电阻、"
                "介质损耗、直流电阻等测试，确认设备状态合格后办理工作票终结并"
                "恢复送电。所有检修记录、试验数据和结论应归档保存，为设备状态"
                "评估和后续检修计划提供依据。"
            ),
            "chunk_index": 11,
            "total_chunks": 20,
        },
    ],
    "fallback": [
        {
            "doc_id": "user-upload:mock-equipment-rules",
            "filename": "电力设备运行规程.md",
            "title": "电力设备运行规程",
            "source": "user-upload/电力设备运行规程.md",
            "section": None,
            "score": 0.55,
            "snippet": "通用章节：设备运行维护应遵循相关规程，加强巡视与定期试验……",
            "content_excerpt": (
                "电力设备运行规程通用章节规定了各类电力设备的运行维护基本要求。"
                "设备运行期间应加强巡视检查，按照规定的周期开展定期试验与检修，"
                "及时发现并消除设备隐患。运行维护工作应严格执行工作票、操作票"
                "制度，落实安全组织措施和技术措施，防止误操作和人身伤害。对于"
                "异常运行工况应及时记录并上报，必要时申请停电处理。设备台账、"
                "运行记录、试验报告和检修记录应完整归档，为设备全生命周期管理"
                "提供数据支撑。具体设备的详细运行规程请参阅对应专业手册，如"
                "《变压器运行规程》《断路器运行规程》等专项规定。"
            ),
            "chunk_index": 0,
            "total_chunks": 8,
        },
    ],
}


def _truncate_text(text: str, max_len: int = 120) -> str:
    """截断为最多 ``max_len`` 字符的摘要；超长追加 ``…``。"""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


# ═══════════════════════════════════════════════════════
# M-4（P1-3，决策 7）：mock 图谱答案 —— 仅覆盖 油温/过载/停机检修 三剧本
# ═══════════════════════════════════════════════════════

#: mock 图谱剧本（nodes/edges/paths 与 ``_get_mock_response`` 正文
#: 「🔗 图谱检索路径」语义一致——同一批实体与关系；hop 为距 seed 跳数，
#: 超出 3 跳截断为 3（前端 hops 上限 3，面板标注截断）。
_MOCK_GRAPH_SCRIPTS: dict[str, dict[str, Any]] = {
    "oil_temperature": {
        "seed_ids": ["e-transformer"],
        "nodes": [
            {"id": "e-transformer", "name": "变压器", "type": "设备", "hop": 0},
            {"id": "e-oil-monitor", "name": "油温监控", "type": "部件", "hop": 1},
            {"id": "e-oiltemp-abnormal", "name": "油温异常告警", "type": "告警", "hop": 2},
            {"id": "e-overload", "name": "负载过重", "type": "故障", "hop": 3},
            {"id": "e-cooling-fault", "name": "冷却系统故障", "type": "故障", "hop": 3},
            {"id": "e-insulation-aging", "name": "绝缘老化", "type": "故障", "hop": 3},
        ],
        "edges": [
            {"source": "e-transformer", "target": "e-oil-monitor", "relation_type": "包含"},
            {"source": "e-oil-monitor", "target": "e-oiltemp-abnormal", "relation_type": "触发"},
            {"source": "e-oiltemp-abnormal", "target": "e-overload", "relation_type": "关联"},
            {"source": "e-oiltemp-abnormal", "target": "e-cooling-fault", "relation_type": "关联"},
            {"source": "e-oiltemp-abnormal", "target": "e-insulation-aging", "relation_type": "关联"},
        ],
        "paths": [
            {"nodes": ["e-transformer", "e-oil-monitor", "e-oiltemp-abnormal"], "relations": ["包含", "触发"]},
            {"nodes": ["e-transformer", "e-oil-monitor", "e-oiltemp-abnormal", "e-overload"], "relations": ["包含", "触发", "关联"]},
            {"nodes": ["e-transformer", "e-oil-monitor"], "relations": ["包含"]},
        ],
    },
    "overload": {
        "seed_ids": ["e-overload"],
        "nodes": [
            {"id": "e-overload", "name": "过载", "type": "故障", "hop": 0},
            {"id": "e-overtemp", "name": "温度升高", "type": "故障", "hop": 1},
            {"id": "e-insulation-aging", "name": "绝缘老化", "type": "故障", "hop": 2},
            {"id": "e-life-shortened", "name": "设备寿命缩短", "type": "影响", "hop": 3},
            {"id": "e-thermal-fault", "name": "热故障", "type": "故障", "hop": 3},
            {"id": "e-derating", "name": "减载措施", "type": "处置", "hop": 1},
        ],
        "edges": [
            {"source": "e-overload", "target": "e-overtemp", "relation_type": "触发"},
            {"source": "e-overtemp", "target": "e-insulation-aging", "relation_type": "加速"},
            {"source": "e-insulation-aging", "target": "e-life-shortened", "relation_type": "导致"},
            {"source": "e-life-shortened", "target": "e-thermal-fault", "relation_type": "严重"},
            {"source": "e-overload", "target": "e-derating", "relation_type": "处置"},
        ],
        "paths": [
            {"nodes": ["e-overload", "e-overtemp", "e-insulation-aging"], "relations": ["触发", "加速"]},
            {"nodes": ["e-overload", "e-overtemp", "e-insulation-aging", "e-life-shortened"], "relations": ["触发", "加速", "导致"]},
            {"nodes": ["e-overload", "e-derating"], "relations": ["处置"]},
        ],
    },
    "shutdown": {
        "seed_ids": ["e-shutdown"],
        "nodes": [
            {"id": "e-shutdown", "name": "停机检修", "type": "处置", "hop": 0},
            {"id": "e-work-ticket", "name": "工作票审批", "type": "规程", "hop": 1},
            {"id": "e-grounding", "name": "验电接地", "type": "处置", "hop": 2},
            {"id": "e-testing", "name": "检修试验", "type": "处置", "hop": 3},
            {"id": "e-restore", "name": "恢复送电", "type": "处置", "hop": 3},
        ],
        "edges": [
            {"source": "e-shutdown", "target": "e-work-ticket", "relation_type": "前置"},
            {"source": "e-work-ticket", "target": "e-grounding", "relation_type": "隔离"},
            {"source": "e-grounding", "target": "e-testing", "relation_type": "执行"},
            {"source": "e-testing", "target": "e-restore", "relation_type": "恢复"},
        ],
        "paths": [
            {"nodes": ["e-shutdown", "e-work-ticket", "e-grounding"], "relations": ["前置", "隔离"]},
            {"nodes": ["e-shutdown", "e-work-ticket", "e-grounding", "e-testing"], "relations": ["前置", "隔离", "执行"]},
        ],
    },
}


def _build_mock_graph_answer(
    script: str, sources: list[SourceRef],
) -> GraphAnswer | None:
    """构造 mock 图谱答案（P1-3，决策 7）。

    - 仅覆盖 油温(oil_temperature) / 过载(overload) / 停机检修(shutdown) 三剧本；
      fallback / feature-intro → 返回 None（调用方不 attach）；
    - nodes/edges/paths 与正文「🔗 图谱检索路径」语义一致；
    - 置信度口径与 ``GraphQAEngine`` 完全一致（seed=1.0；路径
      ``max(0, 1-0.15*hops)``；边 = min(端点)；综合 = 1/(hops+1) 加权平均）；
    - ``sources`` 与同轮 :class:`KnowledgeAnswer` **同一份** SourceRef 列表
      （US-5 同源）。
    """
    data = _MOCK_GRAPH_SCRIPTS.get(script)
    if data is None:
        return None
    seed_ids = list(data["seed_ids"])
    all_doc_ids = [s.doc_id for s in sources if s.doc_id]

    nodes: list[GraphAnswerNode] = []
    for n in data["nodes"]:
        hop = int(n.get("hop") or 0)
        nodes.append(GraphAnswerNode(
            id=n["id"],
            name=n["name"],
            type=n.get("type") or "unknown",
            properties=n.get("properties") or {},
            hop=hop,
            doc_ids=list(all_doc_ids),
            confidence=1.0 if hop == 0 else round(max(0.0, 1.0 - 0.15 * hop), 3),
        ))
    nodes_by_id = {n.id: n for n in nodes}

    edges: list[GraphAnswerEdge] = []
    for e in data["edges"]:
        src = nodes_by_id.get(e["source"])
        tgt = nodes_by_id.get(e["target"])
        confs = [
            c for c in ((src.confidence if src else None), (tgt.confidence if tgt else None))
            if c is not None
        ]
        edges.append(GraphAnswerEdge(
            source=e["source"],
            target=e["target"],
            relation_type=e["relation_type"],
            confidence=round(min(confs), 3) if confs else None,
        ))

    paths: list[GraphPath] = []
    for p in data["paths"]:
        hops_n = max(0, len(p["nodes"]) - 1)
        paths.append(GraphPath(
            nodes=list(p["nodes"]),
            relations=list(p["relations"]),
            hops=hops_n,
            confidence=round(max(0.0, 1.0 - 0.15 * hops_n), 3),
        ))

    # 综合置信度：路径按 1/(hops+1) 加权平均（架构决策 2 / §7 #4）
    total_w = sum(1.0 / (p.hops + 1) for p in paths)
    weighted = sum((1.0 / (p.hops + 1)) * p.confidence for p in paths)
    confidence = round(weighted / total_w, 3) if total_w > 0 else 0.0

    return GraphAnswer(
        nodes=nodes,
        edges=edges,
        paths=paths,
        seed_ids=seed_ids,
        confidence=confidence,
        backend="networkx",
        degraded=True,
        latency_ms=0.0,
        sources=sources,
    )


def _extract_knowledge_answer_from_results(results: list[str]) -> KnowledgeAnswer | None:
    """从工具结果字符串中反解 :class:`KnowledgeAnswer`（含 sources）。

    工具结果形如 ``【query_knowledge_base】结果：{json}``，JSON 为
    ``KnowledgeAnswer.model_dump()``（answer/citations/sources/graph_paths/...）。
    仅当 JSON 含 ``answer`` 键且为 dict 时反解；无 sources 也算有效
    （真实检索 0 来源时 ``sources=[]``，前端按 K-3/K-5 不渲染卡片区）。

    Args:
        results: ``_execute_tools`` 返回的工具结果字符串列表。

    Returns:
        反解成功的 :class:`KnowledgeAnswer`；无命中返回 None。
    """
    if not results:
        return None
    for res in results:
        if not isinstance(res, str):
            continue
        # 结果字符串可能带「【tool】结果：」前缀，从第一个 { 开始解析
        start = res.find("{")
        if start < 0:
            continue
        candidate = res[start:]
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict) or "answer" not in parsed:
            continue
        try:
            return KnowledgeAnswer(**parsed)
        except Exception as e:  # noqa: BLE001 — 反解失败跳过，继续下一条
            logger.debug("_extract_knowledge_answer_from_results: 反解失败: {}", e)
            continue
    return None


async def _build_mock_knowledge_answer(
    last_msg: str,
    answer_text: str = "",
) -> KnowledgeAnswer | None:
    """Mock 模式下的 :class:`KnowledgeAnswer`（带结构化 sources，与正文一致）。

    - feature-intro 通道：复用 ``search_feature_intro`` 的 chunk 构建 sources
      （主理人拍板：纳入，成本低）；
    - 油温 / 过载 / 停机检修 / 兜底：使用 :data:`_MOCK_KNOWLEDGE_SOURCES`
      硬编码 sources（K-4 一处定义）。

    Args:
        last_msg: 用户最近一条消息（用于剧本关键词匹配）。
        answer_text: mock 正文（与 ``_get_mock_response`` 同源，避免重复）。

    Returns:
        :class:`KnowledgeAnswer`；``last_msg`` 为空返回 None（不注入）。
    """
    if not last_msg:
        return None

    # ── feature-intro 通道：复用 search_feature_intro 的 chunk 构建 sources ──
    try:
        from core.feature_intro.intent import detect as _fi_detect_ka
        _fi_i = _fi_detect_ka(last_msg)
        if _fi_i.hit:
            from mcp_tools.tools.knowledge_tools import search_feature_intro
            _fi_res = await search_feature_intro(last_msg, top_k=5)
            _fi_chunks = (
                list(_fi_res.get("chunks") or [])
                if isinstance(_fi_res, dict) else []
            )
            if _fi_chunks:
                top_n = int(getattr(settings, "citation_top_n", 5))
                _sources = [
                    SourceRef(
                        doc_id=str(c.get("doc_id") or "") or None,
                        title=str(c.get("title") or "") or None,
                        section=str(c.get("section") or "") or None,
                        score=round(min(1.0, float(c.get("score") or 0.0)), 3),
                        snippet=_truncate_text(c.get("content") or "", 120),
                        content_excerpt=(c.get("content") or "").strip() or None,
                    )
                    for c in _fi_chunks[:top_n]
                ]
                return KnowledgeAnswer(
                    answer=answer_text or "",
                    citations=[],
                    graph_paths=[],
                    confidence=0.95,
                    refuse=False,
                    sources=_sources,
                )
    except Exception as _fi_exc:  # noqa: BLE001 — mock 路径不可阻塞主流程
        logger.debug("mock feature_intro knowledge_answer bypassed: {}", _fi_exc)

    # ── 知识剧本：油温 / 过载 / 停机检修 / 兜底 ──
    script: str | None = None
    if "油温" in last_msg or "油" in last_msg:
        raw_sources = _MOCK_KNOWLEDGE_SOURCES["oil_temperature"]
        confidence = 0.85
        script = "oil_temperature"
    elif "过载" in last_msg or "负荷" in last_msg:
        raw_sources = _MOCK_KNOWLEDGE_SOURCES["overload"]
        confidence = 0.84
        script = "overload"
    elif "停机" in last_msg or "检修" in last_msg:
        raw_sources = _MOCK_KNOWLEDGE_SOURCES["shutdown"]
        confidence = 0.80
        script = "shutdown"
    else:
        raw_sources = _MOCK_KNOWLEDGE_SOURCES["fallback"]
        confidence = 0.75

    ka = KnowledgeAnswer(
        answer=answer_text or "",
        citations=[],
        graph_paths=[],
        confidence=confidence,
        refuse=False,
        sources=[SourceRef(**item) for item in raw_sources],
    )
    # M-4（P1-3，决策 7）：油温/过载/停机检修三剧本携带 mock graph_answer
    # （sources 与 KnowledgeAnswer.sources 同一份，US-5 同源）；fallback 不 attach。
    ga = _build_mock_graph_answer(script, ka.sources)
    if ga is not None:
        ka.graph_answer = ga
    return ka


async def _attach_knowledge_answer(
    agent_name: str,
    update: dict[str, Any],
    *,
    results: list[str] | None = None,
    last_user: str | None = None,
    answer_text: str = "",
) -> dict[str, Any]:
    """统一把本轮 knowledge_agent 的 :class:`KnowledgeAnswer` 注入 AgentState。

    优先级（K-3 / K-4 / K-6）：
    1. ``update`` 已显式设置（调用方手动注入）→ 保留；
    2. 工具结果字符串 JSON 反解（真实路径 ``query_knowledge_base``）→ 注入；
    3. mock 分支 → ``_build_mock_knowledge_answer(last_user)`` 构建注入；
    4. 其余（非 knowledge_agent / 剧本外 / 无来源）→ 不注入（done 事件无该键）。

    Args:
        agent_name: 当前 Agent 名。
        update: 节点返回的 update dict（就地补充 ``knowledge_answer`` 键）。
        results: 工具执行结果字符串（真实路径）。
        last_user: 用户最近消息（mock 路径）。
        answer_text: mock 正文（用于 KnowledgeAnswer.answer 回填）。

    Returns:
        注入后的 update dict（非 knowledge_agent 时原样返回）。
    """
    if agent_name != "knowledge_agent":
        return update
    if update.get("knowledge_answer") is not None:
        return update
    ka: KnowledgeAnswer | None = None
    if results:
        ka = _extract_knowledge_answer_from_results(results)
    if ka is None and last_user:
        ka = await _build_mock_knowledge_answer(last_user, answer_text=answer_text)
    if ka is not None:
        update["knowledge_answer"] = ka
    return update


async def _synthesize_via_llm(
    state: AgentState, agent_name: str, tools: list[BaseTool], results: list[str],
) -> str:
    """用 LLM 基于工具执行结果生成最终回答（多模型抽象）。"""
    from core.llm_client import achat_completion

    messages = _convert_to_dashscope_messages(state, agent_name, tools)
    tool_summary = "\n".join(results)
    messages.append({
        "role": "user",
        "content": f"工具执行结果如下：\n{tool_summary}\n\n请基于以上结果，用清晰的中文给出最终回答。",
    })
    try:
        # B1：chat_completion 是同步 LLM 调用，async 节点内直接调用会阻塞事件循环
        # V1.7.0 M-2：透传 state.model_id（None 时 achat_completion 内部回退全局）
        ok, content = await achat_completion(
            messages=messages, model_id=state.model_id, temperature=0.3,
        )
        if ok:
            return content
    except Exception as e:
        logger.error("[{}] synthesize failed: {}", agent_name, e)
    return "\n".join(results)


async def _get_mock_response(agent_name: str, state: AgentState) -> str:
    """Mock 模式下的 Agent 响应——无需 LLM API Key 即可演示。"""
    last_msg = ""
    for msg in reversed(state.messages):
        if msg.get("role") == "user":
            last_msg = msg.get("content", "")
            break

    # ── V1.6 P0-5 · Mock 模式功能介绍拦截（架构增补件 §1.3）──
    # 双保险第二层：意图命中时强制返回功能介绍（覆盖被 mock Supervisor
    # 错配到 diagnosis/monitor 的答案）。真实 LLM 路径靠 system prompt +
    # 工具注册让 LLM 主动选 search_feature_intro。
    try:
        from core.feature_intro.intent import detect as _fi_detect_mock
        from mcp_tools.tools.knowledge_tools import search_feature_intro

        _fi_i = _fi_detect_mock(last_msg)
        if _fi_i.hit:
            # B1：search_feature_intro 是 async 工具，直接 await（内部无 LLM，
            # 纯 DB/Chroma 快速操作），不再用 asyncio_run_sync 阻塞事件循环
            _fi_res = await search_feature_intro(last_msg, top_k=5)
            _fi_chunks = (
                list(_fi_res.get("chunks") or [])
                if isinstance(_fi_res, dict) else []
            )
            _fi_label = {
                "monitor_agent":"监控","safety_agent":"安规",
                "diagnosis_agent":"诊断","knowledge_agent":"知识库",
            }.get(agent_name, agent_name)
            if _fi_chunks:
                _lines: list[str] = [
                    f"【{_fi_label} Agent】已识别为「{_fi_i.intent}」类功能介绍问题，"
                    f"为您召回 {len(_fi_chunks)} 条文档：", "",
                ]
                for _k, _c in enumerate(_fi_chunks[:5], 1):
                    _t = _c.get("title") or _c.get("doc_id") or f"文档 {_k}"
                    _b = ( _c.get("content") or "").strip().replace("\n", " ")
                    if len(_b) > 280:
                        _b = _b[:280] + "…"
                    _lines.append(f"  {_k}. **{_t}**\n     {_b}\n")
                return "\n".join(_lines).rstrip()
            return (
                f"【{_fi_label} Agent】已识别为功能介绍类问题，"
                "文档暂未就绪，请稍后再试。"
            )
    except Exception as _fi_exc:  # noqa: BLE001 — mock 路径不可阻塞主流程
        logger.debug("mock feature_intro gate bypassed: {}", _fi_exc)

    if agent_name == "monitor_agent":
        if "变压器" in last_msg or "主变" in last_msg:
            return (
                "【监控 Agent】设备 TR-001（#1 主变压器）当前运行状态如下：\n\n"
                "📊 **遥测数据**（最新采样）：\n"
                "  - 温度：68.5°C（正常范围 30–85°C）\n"
                "  - 电压：221.3 kV（额定 220 kV，偏差 +0.6%）\n"
                "  - 负载电流：96.2 A（较正常值 ~60A 偏高）\n"
                "  - 油压：0.32 MPa（正常）\n\n"
                "⚠️ **告警**：负载电流偏高，建议检查是否存在过载情况。"
            )
        elif "所有设备" in last_msg or "全部" in last_msg or "设备状态" in last_msg or "设备列表" in last_msg:
            return (
                "【监控 Agent】系统当前共有 8 台设备，运行状态如下：\n\n"
                "✅ **正常运行**（4 台）：\n"
                "  - TR-001 #1主变压器 / 220kV 变电站\n"
                "  - BR-003 #3断路器 / 开关站A\n"
                "  - CA-005 进线电缆A / 电缆沟\n"
                "  - BB-007 10kV母线 / 配电房\n\n"
                "⚠️ **需关注**（2 台）：\n"
                "  - TR-002 #2主变压器 — 温度偏高\n"
                "  - BR-004 #4断路器 — 操作次数接近维护阈值\n\n"
                "🔴 **异常**（2 台）：\n"
                "  - BB-006 35kV母线 — 电压波动\n"
                "  - CA-008 出线电缆B — 绝缘降低\n\n"
                "建议对异常设备进行详细检测分析。"
            )
        return (
            "【监控 Agent】当前系统运行状态：\n"
            f"  查询内容：{last_msg[:100]}\n\n"
            "📡 设备总数：8 台 | 在线率：100%\n"
            "⚠️ 需关注：2 台 | 🔴 异常：2 台\n"
            "📈 系统综合健康评分：78.5/100（良好）"
        )

    elif agent_name == "safety_agent":
        return (
            "【安规 Agent】根据《国家电网电力安全工作规程》相关条款：\n\n"
            "📋 **适用条款**：\n"
            "  1. **第 4.3.2 条**：操作高压设备应戴绝缘手套、穿绝缘靴\n"
            "  2. **第 5.1.1 条**：检修设备必须办理工作票\n"
            "  3. **第 6.2.5 条**：接地线装设前须验电确认\n\n"
            "✅ **合规检查**：当前操作符合安规要求\n"
            "⚠️ **提醒**：高危操作需经人工确认后方可执行"
        )

    elif agent_name == "diagnosis_agent":
        if "异常" in last_msg or "检测" in last_msg or "健康" in last_msg:
            return (
                "【诊断 Agent】异常检测分析完成。\n\n"
                "📊 **设备健康评分**：\n"
                "  - TR-001 #1主变压器：**62.3 分**（预警）\n"
                "    - 电流异常（z-score: 2.87）→ current_load=96.2A\n"
                "    - 温度偏高（z-score: 1.65）→ temperature=68.5°C\n"
                "  - BB-006 35kV母线：**45.8 分**（严重）\n"
                "    - 电压波动（z-score: 3.12）→ voltage=236.7kV\n"
                "  - 其他 6 台设备：正常（≥80 分）\n\n"
                "🔍 **诊断建议**：\n"
                "  1. #1主变负载电流明显偏高，建议检查负荷分配\n"
                "  2. 35kV母线电压波动超出正常范围，建议检查调压装置\n"
                "  3. 建议在 24 小时内安排现场巡检确认"
            )
        return (
            "【诊断 Agent】系统诊断完成。\n"
            "📊 设备总数：8 台\n"
            "  ✅ 正常：6 台（健康分 ≥ 80）\n"
            "  ⚠️ 预警：1 台（TR-001, 62.3分）\n"
            "  🔴 严重：1 台（BB-006, 45.8分）\n\n"
            "📋 建议对异常设备进行知识库关联分析以获取处置方案。"
        )

    elif agent_name == "knowledge_agent":
        if "油温" in last_msg or "油" in last_msg:
            return (
                "【知识库 Agent】基于混合 RAG 检索（向量 + 图谱）结果：\n\n"
                "📄 **引用来源**：\n"
                "  [1] 《变压器运行规程》第 4.2 节：油温异常分级\n"
                "  [2] 《电力设备故障诊断手册》：变压器油温异常原因分析\n\n"
                "🔗 **图谱检索路径**：\n"
                "  变压器 → 包含 → 油温监控 → 触发 → 油温异常告警\n"
                "  → 关联 → 负载过重 / 冷却系统故障 / 绝缘老化\n\n"
                "📖 **回答**：\n"
                "变压器油温异常的常见原因包括：\n"
                "  1. **负载过重**：长期超额定容量运行导致发热增加\n"
                "  2. **冷却系统故障**：散热器堵塞、风扇或油泵故障\n"
                "  3. **绝缘老化**：绕组绝缘老化导致介质损耗增大\n"
                "  4. **连接不良**：分接开关或引线接触不良产生局部过热\n\n"
                "✅ **建议**：\n"
                "  1. 检查当前负载率，必要时调整负荷分配\n"
                "  2. 检查冷却系统运行状态（油泵、风扇）\n"
                "  3. 安排油色谱分析，判断是否存在内部故障"
            )
        elif "过载" in last_msg or "负荷" in last_msg:
            return (
                "【知识库 Agent】基于混合 RAG 检索结果：\n\n"
                "📄 **引用来源**：\n"
                "  [1] 《变压器运行规程》第 6.1 节：过载运行限制\n"
                "  [2] 《电力设备故障诊断手册》过载章节\n\n"
                "🔗 **图谱检索路径**：\n"
                "  过载运行 → 触发 → 温度升高 → 加速 → 绝缘老化\n"
                "  → 导致 → 设备寿命缩短 → 严重 → 热故障\n\n"
                "📖 **回答**：\n"
                "关于变压器过载运行时间限制（依据 IEC 60076 标准）：\n"
                "  - 正常周期负载（≤130%）：不超过 2 小时\n"
                "  - 紧急长期负载（≤140%）：不超过 30 分钟\n"
                "  - 紧急短期负载（≤150%）：不超过 10 分钟\n\n"
                "⚠️ 当前 #1 主变负载电流偏高，建议尽快调整负荷分配。"
            )
        elif "停机" in last_msg or "检修" in last_msg:
            # P2-A（C-1）：停机检修专属正文 —— 与 _MOCK_KNOWLEDGE_SOURCES["shutdown"]
            # （《变压器运行规程》6.2 + 《电力设备故障诊断手册》检修章节）完全对齐（K-4）。
            # 此前该分支缺失，防御路径下正文回落到兜底《电力设备运行规程》通用章节，
            # 与 sources（6.2 停机检修流程）不一致。
            return (
                "【知识库 Agent】基于混合 RAG 检索（向量 + 图谱）结果：\n\n"
                "📄 **引用来源**：\n"
                "  [1] 《变压器运行规程》第 6.2 节：停机检修流程\n"
                "  [2] 《电力设备故障诊断手册》检修章节\n\n"
                "🔗 **图谱检索路径**：\n"
                "  停机检修 → 前置 → 工作票审批 → 隔离 → 验电接地\n"
                "  → 执行 → 检修试验 → 恢复送电\n\n"
                "📖 **回答**：\n"
                "变压器停机检修必须严格执行第 6.2 节规定的安全流程：\n"
                "  1. 办理工作票并履行审批手续，明确检修内容、安全措施和监护人\n"
                "  2. 断开高压侧和低压侧断路器，拉开隔离开关并可靠接地，验电确认无电后悬挂「禁止合闸，有人工作」标示牌\n"
                "  3. 检修内容一般包括：油色谱分析与油质检测、绕组绝缘电阻与介质损耗测量、分接开关检查、冷却系统检修、套管及密封件检查\n"
                "  4. 检修完成后进行交接试验，确认各项指标合格后方可恢复送电\n\n"
                "✅ **建议**：\n"
                "  1. 检修前完成故障定位与风险评估，制定检修方案并准备备品备件\n"
                "  2. 全过程做好检修记录并存档，便于后续追溯"
            )
        return (
            "【知识库 Agent】基于混合 RAG 检索（向量 + 图谱）结果：\n\n"
            "📄 **引用来源**：\n"
            "  [1] 《电力设备运行规程》通用章节\n\n"
            "🔗 **图谱检索路径**：\n"
            f"  \"{last_msg[:30]}...\" → 检索 → 知识图谱（3 个关联实体）\n\n"
            "📖 **回答**：\n"
            f"关于您查询的「{last_msg[:60]}」问题，\n"
            "建议参考《电力安全工作规程》和《设备运行维护手册》相关规定。\n"
            "如需更精确的知识库答案，请提供更具体的设备型号或规程编号。"
        )

    return (
        f"【{agent_name}】处理完成。\n"
        f"已收到您的问题：{last_msg[:100]}\n"
        "系统正在处理，请继续后续操作。"
    )


def build_agent_node(
    agent_name: str,
    mcp_tools: list[BaseTool],
) -> Callable[[AgentState], dict[str, Any]]:
    """构建一个 LangGraph Agent 节点（基于 dashscope SDK 直接调用）。

    Args:
        agent_name: Agent 名称（用于获取提示词与工具列表）
        mcp_tools: 从 MCP 服务器获取的全部工具

    Returns:
        LangGraph 节点函数
    """
    tools = _filter_tools(mcp_tools, agent_name)
    tool_names = [t.name for t in tools]
    logger.info("Agent '{}' bound with {} tools: {}", agent_name, len(tools), tool_names)

    async def _agent_node_impl(state: AgentState) -> dict[str, Any]:
        """异步 Agent 节点（实现体；HITL 回写由 ``agent_node`` 包装层统一处理）。"""
        # ── Bug1 修复：mock_mode 派生（前端 X-Display-Mode header 优先）──
        # - presentation：强制 mock（演示模式只走 mock 剧本）
        # - standard：有真实 DashScope Key 才走真实 LLM；无 Key（占位符/空）
        #   仍走 mock（保证 dev 模式不被破坏）
        # - header 缺失：保留现有环境变量回退
        display_mode = (getattr(state, "display_mode", None) or "").strip().lower()
        has_real_key = (
            bool(settings.dashscope_api_key)
            and settings.dashscope_api_key not in ("sk-placeholder", "")
            # T2 修复：dashscope 未安装视为无真实能力 → 强制 mock 降级，
            # 覆盖「standard header 未显式传 + 环境仅占位符」等派生漏网路径
            and _dashscope_available()
        )
        if display_mode == "presentation":
            mock_mode = True
        elif display_mode == "standard":
            mock_mode = not has_real_key
        else:
            mock_mode = settings.mock_enabled or not has_real_key

        # ── 恢复路径：HITL 审批后由 checkpointer 恢复，plan 已持久化 ──
        # 此时不应再次调用 LLM，直接执行已计划的工具（高危工具 interrupt 会返回审批结果）
        if state.pending_tool_plan is not None:
            # Bug2 修复：演示模式（presentation）下历史残留的高危工具计划一律
            # **不执行**——演示模式不自动触发高危审批，直接清掉 plan 落到下方
            # mock 剧本分支（防止上一轮审批未决导致后续问题一直走工具执行/卡审批）。
            if display_mode == "presentation":
                logger.info(
                    "[{}] Presentation mode: clearing stale pending_tool_plan={}",
                    agent_name, state.pending_tool_plan,
                )
                state.pending_tool_plan = None
            else:
                plan = state.pending_tool_plan
                results = await _execute_tools(tools, plan)
                if mock_mode:
                    final = _format_mock_tool_answer(results)
                else:
                    final = await _synthesize_via_llm(state, agent_name, tools, results)
                final = await _maybe_run_explainability(
                    agent_name, state, final, user_msg=state.messages[-1].get("content", "") if state.messages else "",
                )
                update = {
                    "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
                    "pending_tool_plan": None,
                    "current_agent": agent_name,
                    "error": None,
                }
                # M-3：HITL 恢复路径同样从工具结果反解结构化来源（如命中 query_knowledge_base）
                if agent_name == "knowledge_agent":
                    update = await _attach_knowledge_answer(agent_name, update, results=results)
                return update

        # ── Mock 模式：无需 API Key ─────
        if mock_mode:
            last_user = _last_user_message(state)

            # ── Bug2 修复：演示模式严格剧本范围 ──
            # 白名单命中才算剧本内；剧本外直接返回固定提示 + 标记字段，
            # **跳过** _high_risk_mock_reply 兜底（不自动触发高危审批）、
            # 不进任何工具执行路径；同时清掉历史残留 pending_tool_plan。
            if display_mode == "presentation":
                if not _is_demo_script_match(last_user):
                    logger.info(
                        "[{}] Demo out-of-scope question: returning fixed prompt",
                        agent_name,
                    )
                    final = _demo_out_of_scope_reply(agent_name)
                    return {
                        "messages": state.messages + [
                            _with_meta(
                                final, agent_name, state.thread_id,
                                is_demo_out_of_scope=True,
                            )
                        ],
                        "pending_tool_plan": None,
                        "current_agent": agent_name,
                        "error": None,
                    }
                # 剧本内：先检查高危话术（如“建议对#1主变压器进行停机检修”）——
                # 命中则走高危工具演示路径（触发 interrupt，HITL 审批弹窗），
                # 逻辑对齐下方非演示 mock 分支（hr_reply → reply → 工具执行/中断）；
                # 未命中才返回普通 mock 回复。
                hr_reply = _high_risk_mock_reply(last_user)
                if hr_reply is not None:
                    logger.info(
                        "[{}] Presentation mode: demo high-risk phrase -> HITL path",
                        agent_name,
                    )
                    reply = hr_reply
                else:
                    mock_reply = await _get_mock_response(agent_name, state)
                    logger.info(
                        "[{}] Presentation mode: returning in-scope mock response",
                        agent_name,
                    )
                    final = await _maybe_run_explainability(
                        agent_name, state, mock_reply, user_msg=last_user,
                    )
                    update = {
                        "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
                        "pending_tool_plan": None,
                        "current_agent": agent_name,
                        "error": None,
                    }
                    # M-3（AC-4）：演示模式剧本内 knowledge 回复携带结构化来源
                    if agent_name == "knowledge_agent":
                        update = await _attach_knowledge_answer(
                            agent_name, update, last_user=last_user, answer_text=mock_reply,
                        )
                    return update

            mock_reply = await _get_mock_response(agent_name, state)
            hr_reply = _high_risk_mock_reply(last_user)
            if hr_reply is None:
                logger.info("[{}] Mock mode: returning simulated response", agent_name)
                final = await _maybe_run_explainability(
                    agent_name, state, mock_reply, user_msg=last_user,
                )
                update = {
                    "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
                    # Bug2 修复：任何 mock 响应结束时清掉残留工具计划（防状态卡死）
                    "pending_tool_plan": None,
                    "current_agent": agent_name,
                    "error": None,
                }
                # M-3：mock 分支 knowledge 回复携带结构化来源（AC-4）
                if agent_name == "knowledge_agent":
                    update = await _attach_knowledge_answer(
                        agent_name, update, last_user=last_user, answer_text=mock_reply,
                    )
                return update
            # 高危工具演示触发：走工具执行路径（无需 LLM）
            reply = hr_reply
        else:
            # ── 真实 LLM 模式 ─────
            if not _dashscope_available():
                # T3 兜底：dashscope 未安装但 mock_mode 派生异常时，主动降级
                # mock 响应而非抛 ImportError（保证用户能正常对话）
                logger.warning(
                    "[{}] dashscope 不可用，降级到 mock 响应", agent_name,
                )
                mock_reply = await _get_mock_response(agent_name, state)
                final = await _maybe_run_explainability(
                    agent_name, state, mock_reply,
                    user_msg=_last_user_message(state),
                )
                update = {
                    "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
                    "pending_tool_plan": None,
                    "current_agent": agent_name,
                    "error": None,
                }
                # M-3：dashscope 不可用降级 mock 同样携带结构化来源（AC-4）
                if agent_name == "knowledge_agent":
                    update = await _attach_knowledge_answer(
                        agent_name, update,
                        last_user=_last_user_message(state),
                        answer_text=mock_reply,
                    )
                return update
            try:
                from dashscope import Generation

                dashscope_messages = _convert_to_dashscope_messages(state, agent_name, tools)
                logger.debug("[{}] Sending {} messages to LLM", agent_name, len(dashscope_messages))

                # B1：Generation.call 是同步 SDK 调用（60s 超时），async 节点内
                # 直接调用会阻塞事件循环 → asyncio.to_thread 移到工作线程
                response = await asyncio.to_thread(
                    Generation.call,
                    model="qwen-plus",
                    messages=dashscope_messages,
                    api_key=settings.dashscope_api_key,
                    temperature=0.3,
                    result_format="message",
                )

                if response.status_code != 200:
                    logger.error("[{}] DashScope API error: {} {}", agent_name, response.status_code, response)
                    # ── T1 修复：API Key 无效 → 自动降级 mock（不弹原始错误给用户）──
                    if _is_invalid_api_key_error(str(response.message)):
                        return await _degrade_to_mock(
                            agent_name, state, f"API key 无效（{response.message}）",
                        )
                    err_content = f"LLM 调用失败: {response.message}"
                    # Bug3 修复：仅在该 agent 首次失败时 append 错误消息（state.error
                    # 复用为去重标记），避免 LangGraph 多节点串联时 messages 数组
                    # 堆积多条相同错误 → /chat/stream 逐条 yield → 前端气泡重复
                    new_messages = state.messages
                    if state.error is None:
                        new_messages = state.messages + [{"role": "assistant", "content": err_content}]
                    return {
                        "messages": new_messages,
                        "current_agent": agent_name,
                        "error": str(response.message),
                    }

                choice = response.output.choices[0] if response.output.choices else None
                if choice is None:
                    return {
                        "messages": state.messages + [
                            {"role": "assistant", "content": "LLM 未返回有效回复"}
                        ],
                        "current_agent": agent_name,
                        "error": None,
                    }

                reply = choice.message.content if hasattr(choice.message, "content") else ""
            except Exception as e:
                logger.error("[{}] Error: {}", agent_name, e)
                # ── T1 修复：API Key 无效（SDK 抛 InvalidApiKey）→ 自动降级 mock ──
                if _is_invalid_api_key_error(str(e)):
                    return await _degrade_to_mock(agent_name, state, "API key 无效")
                err_content = f"处理时出错: {e!s}"
                # Bug3 修复：仅首次失败 append 错误消息（state.error 去重标记）。
                # 当 dashscope 缺失时所有节点都会失败——若每个节点都 append 一条
                # 相同 error assistant 消息，messages 数组会堆积多条相同错误。
                new_messages = state.messages
                if state.error is None:
                    new_messages = state.messages + [{"role": "assistant", "content": err_content}]
                return {
                    "messages": new_messages,
                    "current_agent": agent_name,
                    "error": str(e),
                }

        # ── 工具调用解析与执行 ──
        if reply and _TOOL_CALL_PREFIX in reply:
            plan = _parse_tool_calls(reply)
            _persist_plan(state.thread_id, plan)
            results = await _execute_tools(tools, plan)  # 高危工具在此中断(interrupt)
            if mock_mode:
                final = _format_mock_tool_answer(results)
            else:
                final = await _synthesize_via_llm(state, agent_name, tools, results)
            final = await _maybe_run_explainability(
                agent_name, state, final, user_msg=_last_user_message(state),
            )
            update = {
                "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
                "pending_tool_plan": None,
                "current_agent": agent_name,
                "error": None,
            }
            # M-3：工具执行路径从工具结果反解结构化来源（query_knowledge_base 含 sources）
            if agent_name == "knowledge_agent":
                update = await _attach_knowledge_answer(agent_name, update, results=results)
            return update

        final = await _maybe_run_explainability(
            agent_name, state, reply, user_msg=_last_user_message(state),
        )
        update = {
            "messages": state.messages + [_with_meta(final, agent_name, state.thread_id)],
            "current_agent": agent_name,
            "error": None,
        }
        # M-3：真实回复（未走工具调用）一般无结构化来源；显式挂接为 no-op，
        # 保证所有返回路径行为一致（K-6：非空才携带 knowledge_answer 键）。
        if agent_name == "knowledge_agent":
            update = await _attach_knowledge_answer(agent_name, update)
        return update

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """异步 Agent 节点（对外入口）。

        在实现体之外统一做一件事：把融合层（diagnosis fusion）判定出的 HITL
        需求回写进 LangGraph 状态的 ``interrupt_action``——这是 ``/chat`` 与
        ``/chat/stream`` 唯一据以生成 ``interrupt_required`` 的信号，也是前端
        弹出审批弹窗的开关。

        用包装层而不是逐个 return 点改写，是为了保证**所有**返回路径
        （mock / 演示剧本 / 真实 LLM / 工具执行 / 降级 / 异常）行为一致，
        既不漏触发，也不残留上一轮的 ``pending`` 状态。
        """
        from core.diagnosis_orchestrator import FUSION_STORE

        # 进入节点前的融合快照，用于判定本轮是否**新产生**了融合结果
        prev_fusion = FUSION_STORE.get(state.thread_id)
        update = await _agent_node_impl(state)
        if not isinstance(update, dict):
            return update
        return _merge_fusion_hitl(update, state.thread_id, prev_fusion)

    return agent_node


# ═══════════════════════════════════════════════════════
# 可解释性 AI 集成（P0）
# ═══════════════════════════════════════════════════════

# 进程级单例 Orchestrator（避免每次节点调用都重建）
_ORCHESTRATOR: Any = None


def _get_orchestrator() -> Any:
    """获取进程级单例 DiagnosisOrchestrator。"""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        from core.diagnosis_orchestrator import DiagnosisOrchestrator
        from core.mechanical_checker import MechanicalChecker
        _ORCHESTRATOR = DiagnosisOrchestrator(
            checker=MechanicalChecker(enabled=settings.explainability_checker_enabled),
            enabled=settings.explainability_enabled,
        )
    return _ORCHESTRATOR


async def _maybe_run_explainability(
    agent_name: str,
    state: AgentState,
    final_text: str,
    user_msg: str = "",
) -> str:
    """仅 diagnosis_agent 走 Orchestrator；其他 Agent 直接透传。

    Args:
        agent_name:  Agent 名称
        state:       AgentState（取 thread_id）
        final_text:  LLM/Mock 产生的自然语言回复
        user_msg:    用户原始问题

    Returns:
        融合后的最终文本（普通路径）或附加推理链提示的文本（explainability 路径）
    """
    if agent_name != "diagnosis_agent":
        return final_text
    if not settings.explainability_enabled:
        return final_text

    try:
        from core.diagnosis_orchestrator import FUSION_STORE
        orch = _get_orchestrator()
        # Mock / 真实模式：都注入同一个 telemetry + device 上下文（从 DB 拉取）
        telemetry, device = await _fetch_diagnosis_context(user_msg)
        result = await orch.fuse(
            llm_text=final_text,
            user_msg=user_msg,
            telemetry=telemetry,
            device=device,
            thread_id=state.thread_id,
        )
        FUSION_STORE.put(state.thread_id, result)
        # 在文本末尾追加一行推理链提示（前端可识别）
        return (
            f"{final_text}\n\n"
            f"🔍 [可解释性推理链] severity={result.final_severity} | "
            f"hitl={result.requires_human_review} | "
            f"conflict={result.conflict_detected} | "
            f"action={result.forced_action}"
        )
    except Exception as e:
        # 任何异常都降级到原始文本（不阻断主流程）
        logger.error("Explainability fusion failed for thread {}: {}", state.thread_id, e)
        return final_text


# ═══════════════════════════════════════════════════════
# 融合层 HITL → LangGraph 状态回写（审批弹窗修复）
# ═══════════════════════════════════════════════════════

# 融合层 HITL 的虚拟"节点名"。与 HIGH_RISK_TOOLS 中的真实工具区分：
# 融合层 HITL 不对应任何待执行工具，只要求调度员对诊断结论做人工复核。
# 前端 HitlEditDialog 用它渲染「操作工具」标签；hitlSchemas.getEditableFields
# 对未登记的名字返回 []，弹窗自动退化为「拒绝 / 仅批准」两按钮（无内嵌编辑器），
# 符合"无工具参数可改"的语义。
FUSION_REVIEW_NODE = "diagnosis_review"


def _fusion_requires_approval(result: Any) -> bool:
    """判断融合结果是否应弹出 HITL 审批弹窗。

    与 ``DiagnosisFusionResult.requires_human_review`` **不完全等价**——后者在
    ``fallback_diagnosis``（LLM ```diagnosis 围栏解析失败）时也会置 True，而围栏
    缺失在 mock / 非结构化回复下是常态（api_err.log 中大量
    ``severity=info, hitl=True, action=none`` 即此类）。若直接按该字段弹窗，
    等于每问一次诊断都弹一次，属于误报。

    因此这里只采纳 ``DiagnosisFusionResult`` 文档约定的**实质性**风险来源
    （core/schemas/diagnosis.py:167「机理 / 规则触发 → 前端立即弹 HITL 确认」）：
      - 规则护栏强制 HITL / 强制停运
      - LLM 结论与机理校验矛盾
      - 机理校验存在 critical 级失败
    """
    try:
        rules = result.rules_guard
        if rules.forced_hitl or rules.forced_shutdown:
            return True
        if result.conflict_detected:
            return True
        if result.mechanical_check.critical_failures > 0:
            return True
    except AttributeError:
        # 结构异常不阻断主流程，按"无需审批"处理
        return False
    return False


def _fusion_interrupt_payload(result: Any) -> tuple[str, dict[str, Any]]:
    """构造融合层 HITL 的说明文案与只读上下文参数。"""
    rules = result.rules_guard
    reasons: list[str] = []
    if rules.forced_shutdown:
        reasons.append("规则护栏要求强制停运")
    if rules.forced_hitl:
        reasons.append("规则护栏要求人工复核")
    if result.conflict_detected:
        reasons.append("LLM 结论与机理校验存在矛盾")
    critical = result.mechanical_check.critical_failures
    if critical > 0:
        reasons.append(f"机理校验 {critical} 项 critical 未通过")
    triggered = [t.title for t in rules.triggered]
    if triggered:
        reasons.append("触发规则：" + "、".join(triggered))

    msg = (
        f"诊断结论风险等级为 {result.final_severity}；"
        + ("；".join(reasons) if reasons else "需人工复核")
        + "。请值班负责人 / 调度员确认后再执行后续处置。"
    )
    args: dict[str, Any] = {
        "final_severity": result.final_severity,
        "forced_action": result.forced_action,
        "conflict_detected": result.conflict_detected,
        "triggered_rules": triggered,
    }
    device_id = getattr(result.mechanical_check, "device_id", None)
    if device_id:
        args["device_id"] = device_id
    return msg, args


def _merge_fusion_hitl(
    update: dict[str, Any],
    thread_id: str,
    prev_fusion: Any,
) -> dict[str, Any]:
    """把「本轮新产生的」融合结果的 HITL 判定回写进 LangGraph 状态。

    Bug 修复（高危操作审批弹窗不弹出）：
        融合层的 ``requires_human_review`` 此前只体现在回复文本尾部的
        ``🔍 [可解释性推理链] … hitl=True …`` 与 ``diagnosis_fusion_log`` 表中，
        **从未写回状态字段** ``interrupt_action``；而 ``/chat`` 与
        ``/chat/stream`` 仅凭 ``interrupt_action == "pending"`` 生成
        ``interrupt_required``，于是前端 ``chatStore.interruptRequired`` 恒为
        false，``HitlEditDialog`` 永远不会打开。

    另：``interrupt_action`` 会随 checkpointer 持久化，若只在触发时写入、
    不在未触发时清零，下一轮对话会读到上一轮的残留值而误弹窗；因此这里对
    **每一条**返回路径都显式赋值（触发 → pending，未触发 → None）。
    """
    from core.diagnosis_orchestrator import FUSION_STORE

    # 工具级 HITL（_execute_tools 内的 interrupt()）由 graph.py 合成返回值，
    # 且挂起时本函数根本不会执行；此处仅作防御，已置位则不覆盖。
    if update.get("interrupt_action") is not None:
        return update

    current = FUSION_STORE.get(thread_id)
    # 只认「本轮新产生」的融合结果：FUSION_STORE 按 thread_id 长期保留快照，
    # 不做新鲜度判定的话，同一会话后续任意提问都会读到上一轮结果而反复弹窗。
    is_fresh = current is not None and current is not prev_fusion

    if is_fresh and _fusion_requires_approval(current):
        msg, args = _fusion_interrupt_payload(current)
        update["interrupt_action"] = "pending"
        update["interrupt_tool"] = FUSION_REVIEW_NODE
        update["interrupt_args"] = args
        update["interrupt_msg"] = msg
        logger.info(
            "[fusion-hitl] thread={} severity={} conflict={} → interrupt_action=pending",
            thread_id, current.final_severity, current.conflict_detected,
        )
    else:
        update["interrupt_action"] = None
        update["interrupt_tool"] = None
        update["interrupt_args"] = None
        update["interrupt_msg"] = None
    return update


def _with_meta(
    content: str,
    agent_name: str,
    thread_id: str,
    is_demo_out_of_scope: bool = False,
) -> dict[str, Any]:
    """构造带元数据的消息 dict。

    metadata 包含 ``agent_name`` / ``thread_id`` / ``has_reasoning_chain`` /
    ``is_demo_out_of_scope`` 四个字段：
    - ``has_reasoning_chain`` 供前端判断是否挂载 ``<ReasoningChainPanel />``。
    - Bug2 修复：``is_demo_out_of_scope`` 供前端识别演示模式剧本外提示
      （清审批态 + MessageBubble 展示）。
    """
    from core.diagnosis_orchestrator import FUSION_STORE
    has_chain = FUSION_STORE.get(thread_id) is not None
    return {
        "role": "assistant",
        "content": content,
        "name": agent_name,
        "metadata": {
            "agent_name": agent_name,
            "thread_id": thread_id,
            "has_reasoning_chain": has_chain and agent_name == "diagnosis_agent",
            "is_demo_out_of_scope": is_demo_out_of_scope,
        },
    }


async def _fetch_diagnosis_context(user_msg: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """从 user_msg 中识别 device_id 并拉取其最新 telemetry + 铭牌。

    简化版：扫描消息中形如 ``TR-001`` / ``BR-002`` 的 ID 串（regex 匹配）。
    失败时返回空 dict（Orchestrator 走 fallback）。
    """
    import re as _re
    import sqlite3
    from pathlib import Path

    if not user_msg:
        return {}, {}

    # 1) 提取 device_id
    m = _re.search(r"\b(TR|BR|CB|BB)-\d{3}\b", user_msg)
    if not m:
        return {}, {}
    device_id = m.group(0)

    # 2) 查 device 铭牌
    db_path = Path(settings.database_path)
    if not db_path.exists():
        return {}, {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        dev_row = conn.execute(
            "SELECT device_id, device_name, device_type, location, rated_current, short_impedance, rated_voltage "
            "FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not dev_row:
            return {}, {}
        device = dict(dev_row)

        # 3) 查最新遥测
        tel_row = conn.execute(
            "SELECT temperature, voltage, current_load, humidity, pressure "
            "FROM telemetry WHERE device_id = ? ORDER BY timestamp DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        telemetry = dict(tel_row) if tel_row else {}
        return telemetry, device
    finally:
        conn.close()
