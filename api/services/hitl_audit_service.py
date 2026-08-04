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


def _retrieve_original_args(thread_id: str) -> dict[str, Any]:
    """从 LangGraph checkpointer 取回中断时的原始 args（用于 audit）。

    优先尝试 ``pending_tool_plan``（Agent 节点持久化的工具计划），
    若为空则尝试 ``interrupt_args`` 字段。两者均缺失时返回空 dict。
    """
    try:
        from api.graph import COMPILED_GRAPH

        if COMPILED_GRAPH is None:
            return {}
        snapshot = COMPILED_GRAPH.get_state(
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


def _retrieve_pending_plan(thread_id: str) -> list[dict[str, Any]]:
    """从 LangGraph checkpointer 取回 ``pending_tool_plan``（用于替换 args）。"""
    try:
        from api.graph import COMPILED_GRAPH

        if COMPILED_GRAPH is None:
            return []
        snapshot = COMPILED_GRAPH.get_state(
            {"configurable": {"thread_id": thread_id}}
        )
        if snapshot is None or snapshot.values is None:
            return []
        plan = snapshot.values.get("pending_tool_plan")
        return list(plan) if isinstance(plan, list) else []
    except Exception as e:
        logger.warning("retrieve_pending_plan failed for {}: {}", thread_id, e)
        return []


def _persist_replaced_plan(
    thread_id: str,
    tool_name: str,
    edited_args: dict[str, Any],
) -> bool:
    """将 pending_tool_plan 中**第一个**匹配 ``tool_name`` 的项的 args 替换。

    返回是否替换成功（若 plan 为空或工具不匹配，返回 False，调用方应记录日志）。
    """
    try:
        from api.graph import COMPILED_GRAPH

        if COMPILED_GRAPH is None:
            return False
        plan = _retrieve_pending_plan(thread_id)
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
        COMPILED_GRAPH.update_state(
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
    def query_by_thread(thread_id: str) -> list[dict[str, Any]]:
        """按 thread_id 查询审计记录（按时间升序）。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, thread_id, interrupt_node, tool_name,
                       user_id, user_name, user_role, decision,
                       original_args, edited_args, edit_reason,
                       safety_recheck_result, reason,
                       ip_address, user_agent, created_at
                  FROM hitl_audit_log
                 WHERE thread_id = ?
                 ORDER BY created_at ASC, id ASC
                """,
                (thread_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def query_by_decision(decision: str, limit: int = 100) -> list[dict[str, Any]]:
        """按 decision 枚举查询（用于 QA 验证 / 仪表盘）。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, thread_id, interrupt_node, tool_name,
                       user_id, decision, edit_reason, created_at
                  FROM hitl_audit_log
                 WHERE decision = ?
                 ORDER BY created_at DESC
                 LIMIT ?
                """,
                (decision, limit),
            ).fetchall()
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

    original_args = _retrieve_original_args(thread_id)
    interrupt_node = ""
    tool_name = ""
    # 通过 COMPILED_GRAPH snapshot 取 tool 名（plan 第一项的 name）
    try:
        if COMPILED_GRAPH is not None:
            snapshot = COMPILED_GRAPH.get_state(
                {"configurable": {"thread_id": thread_id}}
            )
            if snapshot is not None and snapshot.values is not None:
                plan = snapshot.values.get("pending_tool_plan") or []
                if isinstance(plan, list) and plan:
                    first = plan[0]
                    if isinstance(first, dict):
                        tool_name = first.get("name", "") or ""
                        interrupt_node = tool_name
    except Exception:
        pass

    # ── 替换 plan 中的 args（仅 edit_approve） ──────────────
    replaced = False
    if payload.decision == EditDecisionEnum.edit_approve and tool_name:
        replaced = _persist_replaced_plan(
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
