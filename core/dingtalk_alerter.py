"""GridMind M3c · 钉钉告警客户端（DingTalkAlerter）。

设计目标
--------
- **零新增三方依赖**：使用 ``urllib.request``（stdlib）POST webhook。
  可选 ``requests`` —— 但 sandbox 无 requests 时降级 stdlib。
- **去重 + 冷却**：相同 ``(title, frozenset(labels))`` 在冷却期内只发送一次，
  避免告警风暴。
- **发送记录**：所有发送尝试保留最近 N 条 ``AlertRecord``，便于前端面板回放。
- **Feature flag** + **沙箱 mock**：
    * ``webhook_url=""`` 时直接 log mock，不真发
    * ``dingtalk_enabled=False`` 时 ``send()`` 直接返回 False，no-op

时间戳说明：
    * ``time.time()`` 用于冷却去重（wall clock，需要可比对的秒数）
    * ``datetime.utcnow()`` 用于 ``AlertRecord.timestamp``（仅展示）
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# 1. 数据结构
# ─────────────────────────────────────────────────────────────────────────────

VALID_SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")


@dataclass
class Alert:
    """告警数据结构（业务方构造，传入 ``DingTalkAlerter.send()``）。"""
    title: str
    message: str
    severity: str = "warning"
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("Alert.title must be non-empty")
        if not self.message:
            raise ValueError("Alert.message must be non-empty")
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {VALID_SEVERITIES}, got {self.severity!r}"
            )
        # 强制 labels 是 dict[str,str]
        self.labels = {str(k): str(v) for k, v in (self.labels or {}).items()}


@dataclass
class AlertRecord:
    """告警发送尝试记录（成功 / 失败 / 去重 / no-op）。"""
    title: str
    message: str
    severity: str
    labels: dict[str, str]
    timestamp: float          # wall clock（用于冷却去重比较）
    sent: bool                # 是否真的发送（或 mock）
    skipped: bool             # 是否因冷却/去重/关闭而跳过
    error: str | None = None  # 发送失败的异常信息
    response_code: int | None = None  # HTTP status

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 加上 ISO timestamp 字段便于人类阅读
        try:
            d["iso_time"] = datetime.utcfromtimestamp(self.timestamp).isoformat() + "Z"
        except Exception:
            d["iso_time"] = ""
        return d


# ─────────────────────────────────────────────────────────────────────────────
# 2. DingTalkAlerter 主类
# ─────────────────────────────────────────────────────────────────────────────

class DingTalkAlerter:
    """钉钉机器人告警客户端。

    Args:
        webhook_url: 钉钉 webhook URL；空字符串 = sandbox mock（仅 log）
        secret: 可选签名密钥（M3c 暂未启用，留接口）
        cooldown_s: 相同告警的冷却秒数（默认 300 = 5 分钟）
        enabled: 是否启用（False → send() 永远返回 False，no-op）
        timeout_s: HTTP POST 超时（沙箱无 webhook 时无所谓）
    """

    def __init__(
        self,
        webhook_url: str = "",
        secret: str | None = None,
        cooldown_s: int = 300,
        enabled: bool = True,
        timeout_s: float = 5.0,
    ) -> None:
        self.webhook_url = str(webhook_url or "")
        self.secret = secret
        self.cooldown_s = int(cooldown_s) if cooldown_s > 0 else 300
        self.enabled = bool(enabled)
        self.timeout_s = float(timeout_s)
        # 去重表：{alert_key: last_sent_timestamp}
        self._last_sent_at: dict[str, float] = {}
        # 发送历史环形缓冲：最多保留 200 条
        self._recent: list[AlertRecord] = []
        self._recent_max = 200
        self._dedup_hits = 0   # 调试统计：被去重跳过的次数
        self._lock_lock = __import__("threading").RLock()  # RLock 支持 _record 重入
        logger.info(
            "DingTalkAlerter initialized: enabled={}, url_set={}, cooldown_s={}",
            self.enabled, bool(self.webhook_url), self.cooldown_s,
        )

    # ── 公开 API ─────────────────────────────────────────────

    def send(self, alert: Alert) -> bool:
        """发送一次告警（带冷却去重）。

        Returns:
            True  → 真的发了（或 sandbox mock log）
            False → 被去重跳过 / 功能关闭 / 沙箱无 url mock / 发送失败
        """
        # 1) feature flag 关闭 → 直接 no-op
        if not self.enabled:
            self._record(
                alert,
                sent=False,
                skipped=True,
                error="alerter_disabled",
            )
            return False

        key = self._key(alert)
        now = time.time()

        # 2) 冷却去重
        import threading
        with self._lock_lock:
            last = self._last_sent_at.get(key, 0.0)
            if now - last < self.cooldown_s:
                self._dedup_hits += 1
                self._record(
                    alert,
                    sent=False,
                    skipped=True,
                    error=f"cooldown (next in {self.cooldown_s - (now - last):.0f}s)",
                )
                return False
            # 原子更新：立即占用冷却窗口，避免并发重复发送
            self._last_sent_at[key] = now

        # 3) 真正发送（或 sandbox mock）
        if not self.webhook_url:
            # 沙箱：webhook 未配置 → 仅 log，不真发
            logger.warning(
                "[DingTalkAlerter mock] title={} severity={} labels={}",
                alert.title, alert.severity, alert.labels,
            )
            logger.warning(
                "[DingTalkAlerter mock] message={}", alert.message,
            )
            self._record(alert, sent=False, skipped=True, error="no_webhook_url")
            return False

        payload = self._build_payload(alert)
        ok, code, err = self._post_webhook(payload)
        self._record(
            alert,
            sent=ok,
            skipped=False,
            error=err,
            response_code=code,
        )
        if ok:
            logger.info(
                "[DingTalkAlerter] sent title={} severity={} code={}",
                alert.title, alert.severity, code,
            )
        else:
            logger.warning(
                "[DingTalkAlerter] send failed title={} code={} error={}",
                alert.title, code, err,
            )
        return ok

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """返回最近 N 条发送记录（按时间倒序）。"""
        n = max(0, int(limit))
        # 倒序（最近的在前）
        return [r.to_dict() for r in reversed(self._recent[-n:])]

    def cooldown_remaining(self, alert: Alert) -> float:
        """返回该告警距离下次可发的剩余秒数；0 = 已可发送。"""
        key = self._key(alert)
        last = self._last_sent_at.get(key, 0.0)
        elapsed = time.time() - last
        return max(0.0, self.cooldown_s - elapsed)

    def reset(self) -> None:
        """清空去重表 + 历史（仅测试 / 运维手动用）。"""
        with self._lock_lock:
            self._last_sent_at.clear()
            self._recent.clear()
            self._dedup_hits = 0
        logger.info("DingTalkAlerter reset")

    def stats(self) -> dict[str, Any]:
        """调试统计（dedup_hits / total / 最后一个时间戳等）。"""
        return {
            "enabled": self.enabled,
            "webhook_url_set": bool(self.webhook_url),
            "cooldown_s": self.cooldown_s,
            "dedup_hits": self._dedup_hits,
            "total_records": len(self._recent),
            "distinct_keys": len(self._last_sent_at),
        }

    # ── 内部工具 ─────────────────────────────────────────────

    @staticmethod
    def _key(alert: Alert) -> str:
        """生成告警去重键：``title:frozenset(labels items)``。

        注意：labels 顺序不影响 key —— 用 frozenset 保证稳定。
        message 故意不参与 key（长文本会让 key 不稳定）。
        """
        return f"{alert.title}:{hash(frozenset(alert.labels.items()))}"

    @staticmethod
    def _build_payload(alert: Alert) -> dict[str, Any]:
        """构造钉钉 webhook 请求体（含 markdown 富文本支持）。"""
        # M3c 简单实现：仅 info/warning/critical 三档 → markdown 颜色
        severity_emoji = {
            "info": "ℹ️",
            "warning": "⚠️",
            "critical": "🚨",
        }
        emoji = severity_emoji.get(alert.severity, "⚠️")
        labels_md = "\n".join(
            f"- **{k}**: {v}" for k, v in alert.labels.items()
        ) or "_无_"
        text = (
            f"{emoji} **{alert.title}**\n\n"
            f"{alert.message}\n\n"
            f"**严重程度**：{alert.severity}\n"
            f"**标签**：\n{labels_md}"
        )
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": alert.title,
                "text": text,
            },
        }

    def _post_webhook(self, payload: dict[str, Any]) -> tuple[bool, int | None, str | None]:
        """POST 钉钉 webhook，返回 (ok, http_code, error_str)。

        沙箱无 webhook 时此方法不调用（``send()`` 已短路）。
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                code = resp.getcode()
                body = resp.read(512).decode("utf-8", errors="ignore")
                if 200 <= code < 300:
                    # 钉钉成功一般返回 {"errcode":0,"errmsg":"ok"}
                    try:
                        parsed = json.loads(body)
                        if isinstance(parsed, dict) and parsed.get("errcode", 0) != 0:
                            return False, code, parsed.get("errmsg", "errcode!=0")
                    except json.JSONDecodeError:
                        pass
                    return True, code, None
                return False, code, f"http_{code}"
        except urllib.error.HTTPError as exc:
            return False, exc.code, str(exc)
        except urllib.error.URLError as exc:
            return False, None, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, None, f"{type(exc).__name__}:{exc}"

    def _record(
        self,
        alert: Alert,
        *,
        sent: bool,
        skipped: bool,
        error: str | None,
        response_code: int | None = None,
    ) -> None:
        import threading
        with self._lock_lock:
            self._recent.append(AlertRecord(
                title=alert.title,
                message=alert.message,
                severity=alert.severity,
                labels=dict(alert.labels),
                timestamp=time.time(),
                sent=sent,
                skipped=skipped,
                error=error,
                response_code=response_code,
            ))
            if len(self._recent) > self._recent_max:
                # 环形截断：保留最新 N 条
                self._recent = self._recent[-self._recent_max:]


# ─────────────────────────────────────────────────────────────────────────────
# 3. 全局单例工厂 + feature flag
# ─────────────────────────────────────────────────────────────────────────────

_default_instance: DingTalkAlerter | None = None


def get_default_alerter() -> DingTalkAlerter:
    """获取默认 ``DingTalkAlerter`` 单例（懒初始化）。

    配置来源：
        * ``DINGTALK_WEBHOOK_URL``（空字符串 = sandbox mock）
        * ``DINGTALK_ENABLED``（默认 ``false`` —— M3c Q4=A 已拍板）
        * ``DINGTALK_COOLDOWN_S``（默认 300）
    """
    global _default_instance
    if _default_instance is None:
        _default_instance = DingTalkAlerter(
            webhook_url=os.getenv("DINGTALK_WEBHOOK_URL", ""),
            secret=os.getenv("DINGTALK_SECRET") or None,
            cooldown_s=int(os.getenv("DINGTALK_COOLDOWN_S", "300")),
            enabled=os.getenv("DINGTALK_ENABLED", "false").lower() == "true",
        )
    return _default_instance


def reset_default_alerter() -> None:
    """重置默认单例（仅测试用）。"""
    global _default_instance
    _default_instance = None


def is_dingtalk_enabled() -> bool:
    """``DINGTALK_ENABLED`` 环境变量查询（默认 False —— Q4=A 已拍板）。"""
    return os.getenv("DINGTALK_ENABLED", "false").lower() == "true"


__all__ = [
    "Alert",
    "AlertRecord",
    "DingTalkAlerter",
    "VALID_SEVERITIES",
    "get_default_alerter",
    "reset_default_alerter",
    "is_dingtalk_enabled",
]
