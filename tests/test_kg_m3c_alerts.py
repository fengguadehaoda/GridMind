"""GridMind M3c · DingTalkAlerter 单元测试（≥5 用例）。

覆盖：
- 正常发送（webhook URL 已配置 → POST 调用 mock）
- 冷却期去重
- feature flag 关闭（send → False）
- sandbox 无 webhook URL → mock log + 返回 False
- 异常 param 校验（severity 不合法等）
- list_recent / stats / reset
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from core.dingtalk_alerter import (
    Alert,
    AlertRecord,
    DingTalkAlerter,
    get_default_alerter,
    is_dingtalk_enabled,
    reset_default_alerter,
)


@pytest.fixture(autouse=True)
def _reset_alerter_singleton():
    """每个测试前重置默认单例。"""
    reset_default_alerter()
    yield
    reset_default_alerter()


# ── 1. 校验 Alert 参数 ──────────────────────────────────────────


def test_alert_requires_valid_severity():
    """Alert.severity 必须是 info/warning/critical 之一。"""
    with pytest.raises(ValueError):
        Alert(title="t", message="m", severity="fatal")


def test_alert_requires_title_and_message():
    """Alert.title / message 非空。"""
    with pytest.raises(ValueError):
        Alert(title="", message="m")
    with pytest.raises(ValueError):
        Alert(title="t", message="")


# ── 2. sandbox 无 URL → 直接 log + 返回 False ──────────────────


def test_send_without_webhook_url_returns_false():
    """webhook_url="" 时发送应走 sandbox mock 分支，返回 False 且 record 含 skipped。"""
    alerter = DingTalkAlerter(webhook_url="", enabled=True, cooldown_s=1)
    alert = Alert(title="KG高错误率", message="P95 超 200ms", severity="critical", labels={"k": "v"})
    # Patch logger 以避免污染日志（但不影响逻辑）
    result = alerter.send(alert)
    assert result is False
    rec = alerter.list_recent(limit=1)
    assert len(rec) == 1
    assert rec[0]["title"] == "KG高错误率"
    assert rec[0]["sent"] is False
    assert rec[0]["error"] == "no_webhook_url"


# ── 3. feature flag 关闭 → no-op + 返回 False ──────────────────


def test_send_disabled_returns_false_without_logging():
    """``enabled=False`` → send 直接返回 False，不进入发送链。"""
    alerter = DingTalkAlerter(webhook_url="https://example.com/webhook", enabled=False, cooldown_s=1)
    ok = alerter.send(Alert(title="t", message="m", severity="info"))
    assert ok is False
    rec = alerter.list_recent(limit=1)
    assert len(rec) == 1
    assert rec[0]["error"] == "alerter_disabled"


# ── 4. 冷却期去重（同 key 在 cooldown 内只发一次） ───────────────


def test_cooldown_dedup_same_alert_in_window():
    """同一告警在 cooldown_s 内重复 send → 第二次被去重返回 False。"""
    alerter = DingTalkAlerter(
        webhook_url="https://example.com/webhook",
        enabled=True,
        cooldown_s=10,
    )
    a = Alert(title="回滚触发", message="auto_error_rate", severity="warning", labels={"rid": "r1"})
    # 第一次发送：mock 掉 _post_webhook 让它"成功"
    with patch.object(alerter, "_post_webhook", return_value=(True, 200, None)):
        first = alerter.send(a)
    assert first is True
    # 第二次：冷却期内 → 必须 False
    second = alerter.send(a)
    assert second is False
    # stats.dedup_hits 应 ≥ 1
    assert alerter.stats()["dedup_hits"] >= 1


def test_cooldown_key_independent_on_label_set():
    """相同 title 但 labels 不同 → 应作为不同告警（不被去重）。"""
    alerter = DingTalkAlerter(
        webhook_url="https://example.com/webhook",
        enabled=True,
        cooldown_s=10,
    )
    a1 = Alert(title="KG告警", message="m", severity="info", labels={"k": "1"})
    a2 = Alert(title="KG告警", message="m", severity="info", labels={"k": "2"})
    with patch.object(alerter, "_post_webhook", return_value=(True, 200, None)):
        r1 = alerter.send(a1)
        r2 = alerter.send(a2)
    # 两个不同 label key → 都不应被去重
    assert r1 is True
    assert r2 is True


# ── 5. cooldown_remaining 状态正确 ──────────────────────────────


def test_cooldown_remaining_after_send():
    """发送后 cooldown_remaining 应接近 cooldown_s。"""
    alerter = DingTalkAlerter(
        webhook_url="https://example.com/webhook",
        enabled=True,
        cooldown_s=60,
    )
    a = Alert(title="t", message="m", severity="info")
    with patch.object(alerter, "_post_webhook", return_value=(True, 200, None)):
        alerter.send(a)
    remain = alerter.cooldown_remaining(a)
    # 60 - ~0 = 接近 60
    assert 55.0 <= remain <= 60.0


# ── 6. POST webhook 失败 → 返回 False + record error ────────────


def test_post_webhook_http_error_recorded():
    """_post_webhook 返回 (False, 500, error_str) → send 返回 False 且记录。"""
    alerter = DingTalkAlerter(
        webhook_url="https://example.com/webhook",
        enabled=True,
        cooldown_s=1,  # 缩短冷却以避免被前面的测试影响
    )
    with patch.object(alerter, "_post_webhook", return_value=(False, 500, "http_500")):
        ok = alerter.send(Alert(title="t", message="m", severity="info"))
    assert ok is False
    rec = alerter.list_recent(limit=1)
    assert rec[0]["response_code"] == 500
    assert rec[0]["error"] == "http_500"


# ── 7. reset 清空去重表 + 历史 ─────────────────────────────────


def test_reset_clears_history_and_cooldown():
    """reset() 后 _recent / _last_sent_at 均清空。"""
    alerter = DingTalkAlerter(webhook_url="", enabled=True, cooldown_s=1)
    alerter.send(Alert(title="t1", message="m"))
    alerter.send(Alert(title="t2", message="m"))
    assert len(alerter.list_recent(limit=100)) >= 2
    alerter.reset()
    assert alerter.list_recent(limit=100) == []
    assert alerter.stats()["distinct_keys"] == 0


# ── 8. feature flag 环境变量查询 ────────────────────────────────


def test_is_dingtalk_enabled_default_false(monkeypatch):
    """DINGTALK_ENABLED 默认 False（Q4=A 已拍板）。"""
    monkeypatch.delenv("DINGTALK_ENABLED", raising=False)
    assert is_dingtalk_enabled() is False


def test_get_default_alerter_uses_env(monkeypatch):
    """get_default_alerter 读 DINGTALK_WEBHOOK_URL + DINGTALK_ENABLED。"""
    monkeypatch.setenv("DINGTALK_ENABLED", "true")
    monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "https://example.com/wh")
    alerter = get_default_alerter()
    assert alerter.enabled is True
    assert alerter.webhook_url == "https://example.com/wh"
