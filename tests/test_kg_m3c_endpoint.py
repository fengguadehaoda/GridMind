"""GridMind M3c · ``/metrics`` 端点测试（≥3 用例）。

覆盖：
- content-type 是 Prometheus exposition format（text/plain; version=0.0.4）
- 输出包含 HELP / TYPE 行 + 至少一个 counter / gauge 样本
- METRICS_ENABLED=false 时返回 404
- /metrics/summary 返回 JSON 格式
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.metrics_endpoint import (
    PROM_CONTENT_TYPE,
    register_metrics_endpoint,
)
from core.metrics_collector import reset_metrics_collector


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_metrics_collector()
    yield
    reset_metrics_collector()


@pytest.fixture
def client():
    """构造仅含 /metrics 端点的最小 FastAPI 客户端。"""
    app = FastAPI()
    register_metrics_endpoint(app)
    return TestClient(app)


# ── 1. content-type + 包含 HELP/TYPE 行 ─────────────────────────


def test_metrics_endpoint_content_type_and_format(client, monkeypatch):
    """``GET /metrics`` 返回 text/plain; version=0.0.4 + 合法 exposition。"""
    monkeypatch.delenv("METRICS_ENABLED", raising=False)  # 默认 open
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # content-type 头至少包含 'text/plain' 与 'version=0.0.4'
    ct = resp.headers["content-type"]
    assert "text/plain" in ct
    assert "version=0.0.4" in ct
    body = resp.text
    assert "# HELP kg_cypher_query_total" in body
    assert "# TYPE kg_cypher_query_total counter" in body
    assert "# TYPE kg_grayscale_ratio gauge" in body
    assert "# TYPE kg_cypher_latency_ms histogram" in body


# ── 2. /metrics 端点数据流（HTTP → MetricsCollector → text）─────


def test_metrics_endpoint_reflects_recorded_metrics(client, monkeypatch):
    """调用 record_cypher 后 /metrics 输出包含对应样本。"""
    from core.metrics_collector import get_metrics_collector

    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    # 先触发一次 metrics 单例初始化
    metrics = get_metrics_collector()
    metrics.record_cypher(backend="neo4j", status="ok", latency_ms=99.0)
    metrics.update_grayscale(ratio=50, state="gray50")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert 'kg_cypher_query_total{backend="neo4j",status="ok"} 1' in body
    # gauge 灰度比例
    assert "kg_grayscale_ratio 50" in body
    # 包含 prometheus exposition format 标识
    assert "kg_cypher_latency_ms_bucket" in body


# ── 3. METRICS_ENABLED=false → 404 ─────────────────────────────


def test_metrics_endpoint_disabled_returns_404(client, monkeypatch):
    """``METRICS_ENABLED=false`` 时 /metrics 返回 404（端点隐藏）。"""
    monkeypatch.setenv("METRICS_ENABLED", "false")
    resp = client.get("/metrics")
    assert resp.status_code == 404
    assert b"disabled" in resp.content.lower()


# ── 4. /metrics/summary 返回 JSON ──────────────────────────────


def test_metrics_summary_returns_json(client, monkeypatch):
    """``/metrics/summary`` 返回 JSON，含 enabled / metrics 字段。"""
    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    resp = client.get("/metrics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "metrics" in data
    assert "grayscale_ratio" in data["metrics"]
    assert "cypher_total" in data["metrics"]


def test_metrics_summary_disabled(client, monkeypatch):
    """关闭时 /metrics/summary 返回 ``{"enabled": False}``。"""
    monkeypatch.setenv("METRICS_ENABLED", "false")
    resp = client.get("/metrics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False


# ── 5. 输出可被 promtool 校验（粗略语法层面）───────────────────


def test_prom_content_type_constant():
    """PROM_CONTENT_TYPE 文字常量保持标准。"""
    assert PROM_CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"
