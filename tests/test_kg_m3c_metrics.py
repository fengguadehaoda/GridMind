"""GridMind M3c · MetricsCollector 单元测试（≥8 用例）。

覆盖：
- Counter / Gauge / Histogram 基础语义
- record_cypher / record_template / record_switch / record_rollback
- export_text() Prometheus 格式（HELP / TYPE / label 渲染）
- 单例一致性
- label 元组稳定键
- 沙箱空流量场景
"""

from __future__ import annotations

import re
import time

import pytest

from core.metrics_collector import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    get_metrics_collector,
    is_metrics_enabled,
    reset_metrics_collector,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个测试前重置 MetricsCollector 单例（避免相互污染）。"""
    reset_metrics_collector()
    yield
    reset_metrics_collector()


# ── 1. Counter 基础 ─────────────────────────────────────────────


def test_counter_inc_and_value():
    """counter 单调递增 + 默认 0。"""
    c = Counter("kg_test_total", "test", labelnames=("k",))
    assert c.value(k="a") == 0.0
    c.inc(k="a")
    c.inc(amount=2.5, k="a")
    assert c.value(k="a") == 3.5


def test_counter_rejects_negative():
    """counter 不允许负增。"""
    c = Counter("kg_test_neg_total", "test")
    with pytest.raises(ValueError):
        c.inc(amount=-1.0)


# ── 2. Gauge 基础 ────────────────────────────────────────────────


def test_gauge_set_inc_dec():
    """gauge 支持 set / inc / dec / value。"""
    g = Gauge("kg_test_ratio", "test")
    g.set(50)
    assert g.value() == 50
    g.inc(25)
    assert g.value() == 75
    g.dec(75)
    assert g.value() == 0


# ── 3. Histogram 基础 ───────────────────────────────────────────


def test_histogram_buckets_and_count_sum():
    """histogram 分桶 + _count + _sum 正确。"""
    h = Histogram("kg_test_ms", "test", labelnames=("op",), buckets=(1, 5, 10))
    h.observe(0.5, op="x")
    h.observe(2.0, op="x")
    h.observe(8.0, op="x")
    assert h.value_count(op="x") == 3
    assert abs(h.value_sum(op="x") - 10.5) < 1e-9


# ── 4. MetricsCollector.record_cypher ───────────────────────────


def test_record_cypher_increments_counter_and_histogram():
    """record_cypher 同时计数 + 打点 histogram。"""
    mc = get_metrics_collector()
    mc.record_cypher(backend="neo4j", status="ok", latency_ms=85.0)
    mc.record_cypher(backend="neo4j", status="ok", latency_ms=120.0)
    mc.record_cypher(backend="neo4j", status="error", latency_ms=300.0)
    assert mc.cypher_query_total.value(backend="neo4j", status="ok") == 2
    assert mc.cypher_query_total.value(backend="neo4j", status="error") == 1
    # Histogram 收到 3 次
    assert mc.cypher_latency_ms.value_count(backend="neo4j") == 3


# ── 5. MetricsCollector.record_template ─────────────────────────


def test_record_template_with_latency():
    """record_template 同时记 counter + histogram（可选 latency）。"""
    mc = get_metrics_collector()
    mc.record_template(template="fault_chain_v1", version="1.0", latency_ms=0.7)
    mc.record_template(template="fault_chain_v1", version="1.0")  # no latency
    assert mc.template_render_total.value(template="fault_chain_v1", version="1.0") == 2
    assert mc.template_render_latency_ms.value_count(template="fault_chain_v1") == 1


# ── 6. MetricsCollector.record_switch + update_grayscale ────────


def test_record_switch_updates_gauge():
    """record_switch 增 counter + update_grayscale 写 gauge。"""
    mc = get_metrics_collector()
    mc.record_switch(actor="admin", from_state="off", to_state="gray10")
    mc.record_switch(actor="admin", from_state="gray10", to_state="gray50")
    mc.update_grayscale(ratio=50, state="gray50")
    assert mc.grayscale_switch_total.value(actor="admin", from_state="off", to_state="gray10") == 1
    assert mc.grayscale_ratio.value() == 50
    # state gray50 → 4
    assert mc.grayscale_state.value() == 4


# ── 7. MetricsCollector.record_rollback + update_rollback_window ─


def test_record_rollback_and_window_gauge():
    """record_rollback + rollback_window_* gauge 联动。"""
    mc = get_metrics_collector()
    mc.record_rollback(reason="auto_error_rate")
    mc.record_rollback(reason="auto_error_rate")
    mc.record_rollback(reason="manual")
    assert mc.rollback_total.value(reason="auto_error_rate") == 2
    assert mc.rollback_total.value(reason="manual") == 1
    mc.update_rollback_window(samples=100, error_rate=0.02)
    assert mc.rollback_window_samples.value() == 100
    assert abs(mc.rollback_window_error_rate.value() - 0.02) < 1e-9


# ── 8. export_text() Prometheus 格式 + 空场景 ────────────────────


def test_export_text_prometheus_format_with_data():
    """有数据时 export_text 包含 HELP / TYPE + 带 label 的样本。"""
    mc = get_metrics_collector()
    mc.record_cypher(backend="neo4j", status="ok", latency_ms=42.0)
    mc.record_switch(actor="admin", from_state="off", to_state="gray10")
    mc.update_grayscale(ratio=10, state="gray10")
    text = mc.export_text()
    # 必需的 HELP / TYPE 行
    assert "# HELP kg_cypher_query_total" in text
    assert "# TYPE kg_cypher_query_total counter" in text
    assert "# TYPE kg_grayscale_ratio gauge" in text
    assert "# TYPE kg_cypher_latency_ms histogram" in text
    # counter / gauge 样本
    assert 'kg_cypher_query_total{backend="neo4j",status="ok"} 1' in text
    assert 'kg_grayscale_switch_total{actor="admin",from_state="off",to_state="gray10"} 1' in text
    # histogram 包含 bucket 行 + count + sum
    assert 'kg_cypher_latency_ms_bucket' in text
    assert 'kg_cypher_latency_ms_count' in text
    assert 'kg_cypher_latency_ms_sum' in text


def test_export_text_empty_still_legal():
    """空流量仍返回合法 exposition format（含 HELP/TYPE 行）。"""
    mc = get_metrics_collector()
    text = mc.export_text()
    # HELP / TYPE 行应存在（沙箱无流量时仍合法）
    assert "# HELP kg_cypher_query_total" in text
    assert "# TYPE kg_cypher_query_total counter" in text
    assert "# TYPE kg_grayscale_ratio gauge" in text
    # 不含 error
    assert "# error" not in text.lower()


# ── 9. 单例一致性 ────────────────────────────────────────────────


def test_get_metrics_collector_returns_same_instance():
    """get_metrics_collector 返回同一实例（单例模式生效）。"""
    a = get_metrics_collector()
    b = get_metrics_collector()
    assert a is b
    # reset 后再取应是新实例
    reset_metrics_collector()
    c = get_metrics_collector()
    assert c is not a


# ── 10. feature flag 函数 ────────────────────────────────────────


def test_is_metrics_enabled_default(monkeypatch):
    """METRICS_ENABLED 未设置时 = True（默认开）。"""
    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    assert is_metrics_enabled() is True


def test_is_metrics_enabled_false(monkeypatch):
    """METRICS_ENABLED=false 时 = False。"""
    monkeypatch.setenv("METRICS_ENABLED", "false")
    assert is_metrics_enabled() is False


# ── 11. label 稳定性（避免 label 顺序错乱）───────────────────────


def test_label_key_is_order_independent_via_histogram_observe():
    """Histogram 同一 labels 不同顺序应累加到同一桶（key 稳定）。"""
    h = Histogram("kg_test_label", "test", labelnames=("a", "b"))
    # 故意两次调用：first 用 a=1,b=2；second 用 b=2,a=1（顺序不同）
    h.observe(1.0, a="1", b="2")
    h.observe(2.0, b="2", a="1")
    assert h.value_count(a="1", b="2") == 2


# ── 12. 数值格式化（避免 NaN/科学计数法）───────────────────────


def test_export_text_no_scientific_notation():
    """浮点数不应输出为科学计数法。"""
    mc = get_metrics_collector()
    mc.record_template(template="x", version="1.0", latency_ms=0.123456)
    text = mc.export_text()
    # 不会出现 "e+05" / "e-07" 之类
    assert not re.search(r"\d\.\d+e[-+]\d+", text, flags=re.IGNORECASE)
