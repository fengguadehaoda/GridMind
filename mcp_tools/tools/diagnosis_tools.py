"""诊断类 MCP 工具——基于真实遥测数据 + 异常检测引擎。"""

from __future__ import annotations

from typing import Any

from core.anomaly_detection import AnomalyDetectionService
from mcp_tools.db.database import get_connection

_detector = AnomalyDetectionService()


async def detect_device_anomalies(device_id: str) -> dict[str, Any]:
    """检测设备异常（z-score + 规则评分）。"""
    result = _detector.detect_device(device_id)
    if result is None:
        return {"error": f"设备 {device_id} 不存在", "device_id": device_id}
    return result.model_dump()


async def get_device_health_score(device_id: str) -> dict[str, Any]:
    """获取设备健康评分。"""
    result = _detector.detect_device(device_id)
    if result is None:
        return {"error": f"设备 {device_id} 不存在", "device_id": device_id}
    return {
        "device_id": result.device_id,
        "device_name": result.device_name,
        "health_score": result.health_score,
        "health_level": result.health_level.value,
        "anomaly_count": len(result.anomalies),
        "summary": result.summary,
    }


async def get_all_health_scores() -> list[dict[str, Any]]:
    """获取全部设备健康评分。"""
    results = _detector.detect_all()
    return [
        {
            "device_id": r.device_id,
            "device_name": r.device_name,
            "health_score": r.health_score,
            "health_level": r.health_level.value,
            "anomaly_count": len(r.anomalies),
            "summary": r.summary,
        }
        for r in results
    ]


async def get_critical_devices() -> list[dict[str, Any]]:
    """获取所有严重（critical/warning）设备列表。"""
    results = _detector.detect_all()
    critical = [
        {
            "device_id": r.device_id,
            "device_name": r.device_name,
            "health_score": r.health_score,
            "health_level": r.health_level.value,
            "anomaly_count": len(r.anomalies),
            "summary": r.summary,
        }
        for r in results
        if r.health_level.value in ("critical", "warning")
    ]
    return critical
