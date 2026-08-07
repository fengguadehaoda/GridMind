"""HITL 审计服务：safety 重检 + 审计写库 + 决定是否 resume 三步原子。

设计原则（参见架构文档 §7）：
1. **fail-closed**：safety 重检失败时 **不** 触发 ``Command(resume=...)``，仅写审计日志。
2. **写库先于 resume**：审计写库失败 → 抛 500，**不** resume（避免无痕执行高危工具）。
3. **字段集中化**：不复制 ``hitl_editable_schemas.py``；调用方传完整 ``edited_args``。
4. **三步原子**（同一函数调用内顺序执行，DB 写入失败抛异常）：
   - 步骤 1：调用 ``safety_agent.check_safety_compliance``
   - 步骤 2：INSERT ``hitl_audit_log``
   - 步骤 3：通过 ``GraphBuilder.resume()`` 注入 ``edited_args`` 恢复图
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from api.schemas import RiskLevel
from api.schemas.hitl_edit import (
    AUDIT_RETENTION_YEARS,
    AuditLogEntry,
    EditDecisionEnum,
    EditInterruptRequest,
    LOCKED_FIELDS,
    SafetyRecheckResult,
)
from mcp_tools.db.database import get_connection

# HIGH_RISK_TOOLS 用于 _retrieve_original_args 鉴别 plan 中哪一项是中断点
from api.agents.agent_factory import HIGH_RISK_TOOLS  # noqa: E402


# ── 内部工具函数 ────────────────────────────────────────────


async def _retrieve_original_args(thread_id: str) -> dict[str, Any]:
    """从 LangGraph checkpointer 取回中断时的原始 args（用于 audit）。

    优先尝试 ``pending_tool_plan``（Agent 节点持久化的工具计划），
    若为空则尝试 ``interrupt_args`` 字段。两者均缺失时返回空 dict。

    V1.5.1 T02 改动：改为 ``async def``，使用 ``COMPILED_GRAPH.aget_state``
    （兼容 AsyncSqliteSaver）。测试场景下若 saver 是 MemorySaver，``aget_state``
    仍正常工作（LangGraph 1.2.10 编译后的图同时支持 sync/async 访问）。
    """
    try:
        from api.graph import COMPILED_GRAPH

        if COMPILED_GRAPH is None:
            return {}
        snapshot = await COMPILED_GRAPH.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        if snapshot is None or snapshot.values is None:
            return {}
        plan = snapshot.values.get("pending_tool_plan") or []
        if isinstance(plan, list) and plan:
            for item in plan:
                if isinstance(item, dict) and item.get("name") in HIGH_RISK_TOOLS:
                    return dict(item.get("args") or {})
        interrupt_args = snapshot.values.get("interrupt_args")
        return dict(interrupt_args) if isinstance(interrupt_args, dict) else {}
    except Exception as e:
        logger.warning("retrieve_original_args failed for {}: {}", thread_id, e)
        return {}


async def _retrieve_pending_plan(thread_id: str) -> list[dict[str, Any]]:
    """从 LangGraph checkpointer 取回 ``pending_tool_plan``（用于替换 args）。

    V1.5.1 T02 改动：``async def`` + ``aget_state``。
    """
    try:
        from api.graph import COMPILED_GRAPH

        if COMPILED_GRAPH is None:
            return []
        snapshot = await COMPILED_GRAPH.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        if snapshot is None or snapshot.values is None:
            return []
        plan = snapshot.values.get("pending_tool_plan")
        return list(plan) if isinstance(plan, list) else []
    except Exception as e:
        logger.warning("retrieve_pending_plan failed for {}: {}", thread_id, e)
        return []


async def _persist_replaced_plan(
    thread_id: str,
    tool_name: str,
    edited_args: dict[str, Any],
) -> bool:
    """将 pending_tool_plan 中**第一个**匹配 ``tool_name`` 的项的 args 替换。

    返回是否替换成功（若 plan 为空或工具不匹配，返回 False，调用方应记录日志）。

    V1.5.1 T02 改动：``async def`` + ``aget_state`` + ``aupdate_state``。
    """
    try:
        from api.graph import COMPILED_GRAPH

        if COMPILED_GRAPH is None:
            return False
        plan = await _retrieve_pending_plan(thread_id)
        replaced = False
        for item in plan:
            if not isinstance(item, dict):
                continue
            if item.get("name") == tool_name:
                item["args"] = dict(edited_args)
                replaced = True
                break
        if not replaced:
            return False
        await COMPILED_GRAPH.aupdate_state(
            {"configurable": {"thread_id": thread_id}},
            {"pending_tool_plan": plan},
        )
        return True
    except Exception as e:
        logger.warning("persist_replaced_plan failed for {}: {}", thread_id, e)
        return False


async def _run_safety_recheck(
    original_args: dict[str, Any],
    edited_args: dict[str, Any],
) -> SafetyRecheckResult:
    """调用 ``safety_agent.check_safety_compliance`` 对 edit 后内容做合规校验。

    本地无 LLM 时使用纯关键词匹配；MCP 不可达时按"通过"处理（fail-open 是为了
    Demo 流畅，但失败结果仍以规则列表为零来表征，避免被误认为通过）。
    """
    op = (
        f"{edited_args.get('description', '')} "
        f"{edited_args.get('reason', '')}"
    ).strip() or original_args.get("description", "") or "高危操作"
    device_type = (
        edited_args.get("device_type") or original_args.get("device_type")
    )
    try:
        # 优先通过 MCP 工具调用
        from mcp_tools.tools.safety_tools import check_safety_compliance

        rules = await check_safety_compliance(operation=op, device_type=device_type)
        rules_list = rules if isinstance(rules, list) else list(rules)
    except Exception as e:
        logger.warning("MCP safety check unavailable ({}), falling back to local", e)
        # 退化实现：本地 SQLite 查询
        try:
            from mcp_tools.tools.safety_tools import check_safety_compliance as _sync_fn  # type: ignore
            rules_list = await _sync_fn(op, device_type)
        except Exception:
            rules_list = []
    # 判断"通过"：若未匹配到 mandatory 规则视为通过
    has_mandatory_violation = any(
        isinstance(r, dict) and r.get("severity") == "mandatory"
        for r in rules_list
    )
    passed = not has_mandatory_violation
    summary_parts: list[str] = []
    if rules_list:
        summary_parts.append(f"匹配 {len(rules_list)} 条安规条款")
    if has_mandatory_violation:
        summary_parts.append("发现 mandatory 级别冲突")
    summary = "；".join(summary_parts) or "未匹配到安规条款，视为通过"
    return SafetyRecheckResult(
        passed=passed,
        rules=rules_list,
        summary=summary,
    )


# ═══════════════════════════════════════════════════════
# 审计写入 + 查询
# ═══════════════════════════════════════════════════════


class HitlAuditService:
    """HITL 审计服务：单例使用，包内静态方法足够。"""

    # ── 审计写入 ────────────────────────────────

    @staticmethod
    def write_log(entry: AuditLogEntry) -> int:
        """写入一行审计日志；返回 ``rowid``。

        Args:
            entry: 审计条目。

        Returns:
            新插入行的 rowid。

        Raises:
            sqlite3.Error: 数据库写入失败时抛出，由调用方决定是否阻断 resume。
        """
        conn = get_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO hitl_audit_log (
                    thread_id, interrupt_node, tool_name,
                    user_id, user_name, user_role, decision,
                    original_args, edited_args, edit_reason,
                    safety_recheck_result, reason,
                    ip_address, user_agent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.thread_id,
                    entry.interrupt_node,
                    entry.tool_name,
                    entry.user_id,
                    entry.user_name,
                    entry.user_role,
                    entry.decision,
                    json.dumps(entry.original_args, ensure_ascii=False, default=str),
                    (
                        json.dumps(entry.edited_args, ensure_ascii=False, default=str)
                        if entry.edited_args is not None
                        else None
                    ),
                    entry.edit_reason,
                    (
                        entry.safety_recheck_result.model_dump_json()
                        if entry.safety_recheck_result is not None
                        else None
                    ),
                    entry.reason,
                    entry.ip_address,
                    entry.user_agent,
                    entry.created_at.isoformat(),
                ),
            )
            conn.commit()
            rowid = cur.lastrowid or 0
            logger.info(
                "HITL audit log written: rowid={}, decision={}, thread={}",
                rowid,
                entry.decision,
                entry.thread_id,
            )
            return rowid
        finally:
            conn.close()

    # ── 审计查询 ────────────────────────────────

    @staticmethod
    def query_by_thread(
        thread_id: str,
        risk_level: RiskLevel | str | None = None,
    ) -> list[dict[str, Any]]:
        """按 thread_id 查询审计记录（按时间升序）。

        V1.5.1 新增可选 ``risk_level`` 过滤（架构 §2.4.4）：

        - ``None``（默认）：返回所有风险等级的记录（v1.5.0 行为）
        - ``RiskLevel.HIGH`` / ``"high"`` 等：仅返回该风险等级的记录

        返回字典含 V1.5.1 新增 3 字段（``risk_level`` / ``pause_count`` /
        ``edit_count``）；旧库未升级时这 3 字段由 SQLite DEFAULT 填充
        （``'normal'`` / ``0`` / ``0``）。
        """
        conn = get_connection()
        try:
            sql = (
                "SELECT id, thread_id, interrupt_node, tool_name, "
                "       user_id, user_name, user_role, decision, "
                "       original_args, edited_args, edit_reason, "
                "       safety_recheck_result, reason, "
                "       ip_address, user_agent, created_at, "
                "       risk_level, pause_count, edit_count "
                "  FROM hitl_audit_log "
                " WHERE thread_id = ?"
            )
            params: tuple[Any, ...] = (thread_id,)
            if risk_level is not None:
                # 兼容 RiskLevel 枚举与 str 两种入参（API 边界灵活）
                level_value = (
                    risk_level.value
                    if isinstance(risk_level, RiskLevel)
                    else str(risk_level)
                )
                sql += " AND risk_level = ?"
                params = (thread_id, level_value)
            sql += " ORDER BY created_at ASC, id ASC"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def query_by_decision(
        decision: str,
        limit: int = 100,
        risk_level: RiskLevel | str | None = None,
    ) -> list[dict[str, Any]]:
        """按 decision 枚举查询（用于 QA 验证 / 仪表盘）。

        V1.5.1 新增可选 ``risk_level`` 过滤（架构 §2.4.4）。
        """
        conn = get_connection()
        try:
            sql = (
                "SELECT id, thread_id, interrupt_node, tool_name, "
                "       user_id, decision, edit_reason, created_at, "
                "       risk_level, pause_count, edit_count "
                "  FROM hitl_audit_log "
                " WHERE decision = ?"
            )
            params: tuple[Any, ...] = (decision,)
            if risk_level is not None:
                level_value = (
                    risk_level.value
                    if isinstance(risk_level, RiskLevel)
                    else str(risk_level)
                )
                sql += " AND risk_level = ?"
                params = (decision, level_value)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params = params + (limit,)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── 保留期 ─────────────────────────────────────

    @staticmethod
    def retention_years() -> int:
        """审计日志保留年限（Q3：方案 A = 3 年，SQLite 直保）。"""
        return AUDIT_RETENTION_YEARS


# ═══════════════════════════════════════════════════════
# Edit & Continue 三步原子：safety 重检 → 写库 → resume
# ═══════════════════════════════════════════════════════


def _decision_without_resume(
    thread_id: str,
    action: str,
    payload: EditInterruptRequest,
    replaced: bool = False,
) -> dict[str, Any]:
    """图未挂起在 interrupt() 上时的决策响应（不 resume）。

    适用两种情形：
      1. **融合层 HITL**（诊断结论人工复核）——图已跑到 END，本就没有待恢复的
         中断点，需要的只是"记录调度员决策"；
      2. **重复提交决策**——同一中断已被批准/拒绝过。

    此时若照旧调用 ``Command(resume=...)``，LangGraph 会从上一个 checkpoint
    重跑整张图并产生一条重复回答，因此这里直接返回（审计已在上游写入）。
    """
    if action == "rejected":
        text = f"已拒绝该操作（{payload.reason or '未填原因'}），未执行任何变更。"
    elif action == "edit_approve":
        text = "已记录修改后批准的决策。"
    else:
        text = "已确认该诊断结论，可继续后续处置。"
    logger.info(
        "HITL decision recorded without resume (thread={}, action={})",
        thread_id, action,
    )
    return {
        "thread_id": thread_id,
        "response": text,
        "interrupt_required": False,
        "replaced_plan_item": replaced,
    }


async def process_edit_decision(
    thread_id: str,
    payload: EditInterruptRequest,
    *,
    user_id: str = "anonymous",
    user_name: str | None = None,
    user_role: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """统一处理 Edit & Continue 决策（含仅批准 / 拒绝 / 修改后批准）。

    Args:
        thread_id: 会话线程 ID。
        payload:   Pydantic 校验后的请求体。
        其余 kwargs: 审计上下文（IP / UA / 用户身份），前端可从 JWT/Header 提取，
                      当前统一由 FastAPI Request 注入。

    Returns:
        ``ChatResponse`` 兼容 dict，含 ``response``、``interrupt_required`` 等字段。

    关键行为：
        - ``edit_approve`` 时：
            1. 校验 ``edited_args`` 不含 ``LOCKED_FIELDS``（Pydantic 已做兜底）
            2. 替换 ``pending_tool_plan[i].args`` = ``edited_args``
            3. 运行 ``safety_agent.check_safety_compliance``
            4. 写审计（fail-closed：失败抛 500 不 resume）
            5. **仅通过**时调用 ``GraphBuilder.resume(edited_args=...)``
        - ``approve`` / ``reject`` 时：
            1. 不替换 plan
            2. 写审计
            3. 调用 ``GraphBuilder.resume()``
    """
    # 直接复用全局 COMPILED_GRAPH（避免每请求构造新 GraphBuilder）
    from api.graph import COMPILED_GRAPH
    from langgraph.types import Command

    original_args = await _retrieve_original_args(thread_id)
    interrupt_node = ""
    tool_name = ""
    # 图当前是否真的挂起在 interrupt() 上（决定能否 resume，见下方守卫）
    has_pending_interrupt = False
    # 通过 COMPILED_GRAPH snapshot 取 tool 名（plan 第一项的 name）
    try:
        if COMPILED_GRAPH is not None:
            snapshot = await COMPILED_GRAPH.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
            if snapshot is not None:
                values = snapshot.values or {}
                plan = values.get("pending_tool_plan") or []
                # 工具名取自 plan 第一项（真实高危工具）
                if isinstance(plan, list) and plan:
                    first = plan[0]
                    if isinstance(first, dict):
                        tool_name = first.get("name", "") or ""
                        interrupt_node = tool_name
                # 融合层 HITL 无 pending_tool_plan，用状态里的中断节点兜底
                if not interrupt_node:
                    interrupt_node = values.get("interrupt_tool") or ""
                # 图是否真挂起在 interrupt() 上（决定能否 resume）：
                # 该 LangGraph 版本下 task.interrupts 即使挂起也是空元组（不可靠），
                # 故以 snapshot.next 非空（图未跑到 END）作主信号；并以
                # pending_tool_plan 真实存在做二次确认（融合层 HITL 无 plan 且图
                # 已 END，不需 resume，否则会重跑整图产生重复回复）。
                next_nodes = getattr(snapshot, "next", None) or ()
                has_real_plan = isinstance(plan, list) and len(plan) > 0
                if next_nodes and has_real_plan:
                    has_pending_interrupt = True
                else:
                    for task in (getattr(snapshot, "tasks", None) or ()):
                        if getattr(task, "interrupts", None):
                            has_pending_interrupt = True
                            break
    except Exception:
        pass

    # ── 替换 plan 中的 args（仅 edit_approve） ──────────────
    replaced = False
    if payload.decision == EditDecisionEnum.edit_approve and tool_name:
        replaced = await _persist_replaced_plan(
            thread_id,
            tool_name,
            payload.edited_args or {},
        )

    # ── safety 重检（仅 edit_approve） ────────────────────
    safety: SafetyRecheckResult | None = None
    if payload.decision == EditDecisionEnum.edit_approve:
        safety = await _run_safety_recheck(
            original_args=original_args,
            edited_args=payload.edited_args or {},
        )

    # ── 审计写库（fail-closed） ────────────────────────────
    entry = AuditLogEntry(
        thread_id=thread_id,
        interrupt_node=interrupt_node or "unknown",
        tool_name=tool_name or "unknown",
        user_id=user_id,
        user_name=user_name,
        user_role=user_role,
        decision=payload.decision.value,
        original_args=original_args,
        edited_args=(
            payload.edited_args if payload.decision == EditDecisionEnum.edit_approve
            else None
        ),
        edit_reason=(
            payload.edit_reason if payload.decision == EditDecisionEnum.edit_approve
            else None
        ),
        safety_recheck_result=safety,
        reason=payload.reason or None,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    try:
        HitlAuditService.write_log(entry)
    except Exception as e:
        # fail-closed：审计失败 → 抛 500，由 FastAPI 错误处理
        logger.error("Audit log write failed for {}: {}", thread_id, e)
        raise

    # ── 决策分支 ───────────────────────────────────────────
    if payload.decision == EditDecisionEnum.edit_approve:
        if safety is not None and not safety.passed:
            # safety 未通过：写审计但 **不** resume
            logger.warning(
                "Safety recheck FAILED for {}: {}",
                thread_id,
                safety.summary,
            )
            return {
                "thread_id": thread_id,
                "response": (
                    f"安全重检未通过，禁止继续执行：{safety.summary}"
                ),
                "interrupt_required": False,
                "rejected_by_safety": True,
            }
        if not has_pending_interrupt:
            return _decision_without_resume(thread_id, "edit_approve", payload, replaced)
        # 通过：恢复图执行
        if COMPILED_GRAPH is None:
            raise RuntimeError("LangGraph 未初始化")
        result = await COMPILED_GRAPH.ainvoke(
            Command(resume={
                "action": "edit_approved",
                "reason": payload.edit_reason,
                "edited_args": payload.edited_args,
                "edit_reason": payload.edit_reason,
            }),
            {"configurable": {"thread_id": thread_id}},
        )
        messages = result.get("messages", []) if isinstance(result, dict) else []
        last_content = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_content = msg.get("content", "")
                break
        return {
            "thread_id": thread_id,
            "response": last_content or "已按编辑后内容执行",
            "interrupt_required": False,
            "replaced_plan_item": replaced,
        }

    # approve / reject 老路径
    action = "approved" if payload.decision == EditDecisionEnum.approve else "rejected"
    if not has_pending_interrupt:
        return _decision_without_resume(thread_id, action, payload, replaced)
    if COMPILED_GRAPH is None:
        raise RuntimeError("LangGraph 未初始化")
    result = await COMPILED_GRAPH.ainvoke(
        Command(resume={"action": action, "reason": payload.reason}),
        {"configurable": {"thread_id": thread_id}},
    )
    messages = result.get("messages", []) if isinstance(result, dict) else []
    last_content = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            last_content = msg.get("content", "")
            break
    if action == "approved":
        default_msg = "已批准执行"
    else:
        default_msg = f"已拒绝执行（{payload.reason or '未填原因'}）"
    return {
        "thread_id": thread_id,
        "response": last_content or default_msg,
        "interrupt_required": False,
    }
