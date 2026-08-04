"""GridMind M3c · Prometheus ``/metrics`` 端点。

设计目标
--------
- **薄端点**：仅返回 ``MetricsCollector.export_text()`` 结果（text/plain），
  与 Prometheus exposition format 完全兼容。
- **Feature flag 隔离**：``METRICS_ENABLED=false`` 时返回 404（端点隐藏），
  防止沙箱关闭时仍暴露指标。
- **冻结端点**：不需要鉴权（沙箱无 Prometheus server 时也能正常 GET），
  ``promtool check metrics`` 可直接验证。

调用方：``api/main.py`` 调用 ``register_metrics_endpoint(app)`` 注册。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Response

from core.metrics_collector import get_metrics_collector


# Prometheus 官方 exposition content-type（text/plain; version=0.0.4）
PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def is_metrics_endpoint_enabled() -> bool:
    """``METRICS_ENABLED`` 环境变量查询（默认 True）。"""
    return os.getenv("METRICS_ENABLED", "true").lower() != "false"


async def metrics_endpoint() -> Response:
    """GET ``/metrics`` — Prometheus exposition format。

    Returns:
        ``Response`` with body = Prometheus exposition format text,
        content-type = ``text/plain; version=0.0.4``。

    即使所有指标为 0，仍返回合法 exposition format（含 HELP/TYPE 行），
    保证 ``promtool check metrics`` 通过。
    """
    if not is_metrics_endpoint_enabled():
        return Response(
            content="metrics endpoint disabled\n",
            status_code=404,
            media_type="text/plain",
        )
    collector = get_metrics_collector()
    body = collector.export_text()
    return Response(content=body, media_type=PROM_CONTENT_TYPE)


async def metrics_summary_endpoint() -> dict[str, Any]:
    """GET ``/metrics/summary`` — JSON 摘要（调试 + 前端面板用，非 Prometheus 抓取）。"""
    if not is_metrics_endpoint_enabled():
        return {"enabled": False}
    collector = get_metrics_collector()
    return {
        "enabled": True,
        "metrics": collector.get_summary(),
    }


def register_metrics_endpoint(app: FastAPI) -> None:
    """注册 ``/metrics`` + ``/metrics/summary`` 端点到 FastAPI ``app``。

    Usage::

        from api.main import app
        from api.metrics_endpoint import register_metrics_endpoint
        register_metrics_endpoint(app)
    """
    app.add_api_route(
        "/metrics",
        metrics_endpoint,
        methods=["GET"],
        summary="Prometheus exposition format metrics",
        description=(
            "Returns metrics in Prometheus text exposition format (text/plain; version=0.0.4). "
            "Compatible with promtool / Grafana Agent / Prometheus server scrape."
        ),
    )
    app.add_api_route(
        "/metrics/summary",
        metrics_summary_endpoint,
        methods=["GET"],
        summary="Metrics summary (JSON, for debug + frontend)",
        description=(
            "Returns a compact JSON snapshot of key metrics — useful for the "
            "GrayscalePanel.vue live update and ad-hoc debugging. Not scraped by Prometheus."
        ),
    )


__all__ = [
    "metrics_endpoint",
    "metrics_summary_endpoint",
    "register_metrics_endpoint",
    "is_metrics_endpoint_enabled",
    "PROM_CONTENT_TYPE",
]
