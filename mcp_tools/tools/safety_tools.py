"""安规类 MCP 工具——基于真实 SQLite 安规条款。"""

from __future__ import annotations

from typing import Any

from mcp_tools.db.database import get_connection


async def get_safety_rules(
    category: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """查询安规条款。"""
    conn = get_connection()
    try:
        query = "SELECT rule_id, rule_code, category, content, severity FROM safety_rules WHERE 1=1"
        params: list[Any] = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if keyword:
            query += " AND content LIKE ?"
            params.append(f"%{keyword}%")
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def get_safety_rule_by_code(rule_code: str) -> dict[str, Any] | None:
    """按编号查询安规条款。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT rule_id, rule_code, category, content, severity FROM safety_rules WHERE rule_code = ?",
            (rule_code,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def check_safety_compliance(
    operation: str,
    device_type: str | None = None,
) -> list[dict[str, str]]:
    """检查某项操作是否符合安规（关键词匹配）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT rule_code, category, content, severity FROM safety_rules"
        ).fetchall()
    finally:
        conn.close()

    results: list[dict[str, str]] = []
    op_lower = operation.lower()
    op_keywords = set(op_lower.split())
    for r in rows:
        content = r["content"]
        content_lower = content.lower()
        # 操作关键词出现在规则内容中 → 匹配
        if any(kw in content_lower for kw in op_keywords):
            results.append({
                "rule_code": r["rule_code"],
                "category": r["category"],
                "content": content,
                "severity": r["severity"],
                "relevance": "high",
            })
        elif len(op_keywords & set(content.split())) >= 2:
            results.append({
                "rule_code": r["rule_code"],
                "category": r["category"],
                "content": content,
                "severity": r["severity"],
                "relevance": "medium",
            })
    return results
