"""监控类 MCP 工具——基于 SQLite 真实数据。"""

from __future__ import annotations

from typing import Any

from mcp_tools.db.database import get_connection


async def get_device_list() -> list[dict[str, Any]]:
    """获取所有设备列表。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT device_id, device_name, device_type, location, status FROM devices"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def get_device_telemetry(
    device_id: str,
    hours: int = 24,
) -> list[dict[str, Any]]:
    """查询设备最新遥测数据。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT reading_id, device_id, timestamp, temperature, voltage, "
            "current_load, humidity, pressure "
            "FROM telemetry WHERE device_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (device_id, hours),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def get_latest_telemetry(device_id: str) -> dict[str, Any] | None:
    """查询设备最新一条遥测数据。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT timestamp, temperature, voltage, current_load, humidity, pressure "
            "FROM telemetry WHERE device_id = ? ORDER BY timestamp DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_device_info(device_id: str) -> dict[str, Any] | None:
    """查询设备详细信息。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_inspection_records(
    device_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """查询设备巡检记录。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT inspection_id, inspector, inspect_time, result, notes "
            "FROM inspections WHERE device_id = ? ORDER BY inspect_time DESC LIMIT ?",
            (device_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
