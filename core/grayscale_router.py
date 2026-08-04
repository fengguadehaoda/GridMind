"""GridMind 灰度切流器（GrayscaleRouter）—— M2 阶段核心组件。

设计目标
--------
- **统一入口**：所有 RAG / Agent 工具调用必须 ``get_grayscale_router()`` 取单例，
  **禁止** 在调用方散落 ``if neo4j_enabled:`` 判断（架构 7.1 共享知识 #1）
- **状态机**：6 个状态（off / precheck / gray10 / monitoring_24h / gray50 / full100 /
  stable / rollback），硬编码四态切流比例 0/10/50/100（Q12 默认 A）
- **路由算法**：
    * ratio == 0   → 全部走 NetworkX
    * ratio == 100 → 全部走 Neo4j
    * ratio ∈ {10, 50} → ``md5(thread_id)[:8] % 100 < ratio``
- **自动回滚**：错误率 >1% / P95 >200ms / Neo4j 连续失败 ≥3 次 → set_ratio(0)
- **持久化**：每次切流 / 回滚写 ``sync_log``（rollback 类型）

跨文件约定（架构 7.2 共享知识）
--------------------------------
- 降级链路完整复用 M0（KGClient 已实现连续 3 次失败 + 30s 探活）
- M2 启动时 ``grayscale_ratio=0``（默认 off），M2 完成后才切 10%
- 所有切流 / 回滚动作同时写 sync_log（审计 + 追溯）
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from loguru import logger

from api.config import settings
from api.services.sync_log_service import (
    SYNC_TYPE_EVENT,
    SyncLogService,
    get_sync_log_service,
)
from core.metrics_collector import (
    is_metrics_enabled,
    get_metrics_collector,
)


# 灰度比例硬编码四态（Q12 默认 A）
ALLOWED_RATIOS: tuple[int, ...] = (0, 10, 50, 100)


# 灰度状态 → 数值映射（用于 Prometheus gauge）
_STATE_TO_NUM: dict[str, int] = {
    "off": 0, "precheck": 1, "gray10": 2, "monitoring_24h": 3,
    "gray50": 4, "full100": 5, "stable": 6, "rollback": 7,
}


class GrayscaleRouter:
    """灰度切流器单例（线程安全 + 进程内唯一）。"""

    _instance: "GrayscaleRouter | None" = None

    # ── 状态常量 ────────────────────────────────────────
    STATE_OFF: str = "off"            # 全部走 NetworkX
    STATE_PRECHECK: str = "precheck"   # 灰度前健康检查
    STATE_GRAY10: str = "gray10"       # 10% 走 Neo4j
    STATE_MONITORING_24H: str = "monitoring_24h"  # 24h 观察期
    STATE_GRAY50: str = "gray50"       # 50% 走 Neo4j
    STATE_FULL100: str = "full100"     # 100% 走 Neo4j
    STATE_STABLE: str = "stable"       # 灰度结束稳定运行
    STATE_ROLLBACK: str = "rollback"   # 回滚中（短暂态）

    def __init__(self) -> None:
        # 当前切流比例（0 / 10 / 50 / 100）
        self._ratio: int = int(getattr(settings, "grayscale_ratio", 0) or 0)
        # 当前状态机状态
        self._state: str = self._state_for_ratio(self._ratio)
        # 切流开始时间（秒，monotonic）
        self._started_at: float | None = time.monotonic()
        # 上次回滚原因
        self._rollback_reason: str | None = None
        # 回滚计数（监控埋点）
        self._rollback_count: int = 0
        # 历史切换记录（最近 20 条）
        self._history: list[dict[str, Any]] = []
        # RollbackMonitor 延迟初始化（避免循环 import）
        self._monitor: Any = None
        # 同步日志服务（懒加载）
        self._sync_log: SyncLogService | None = None
        logger.info(
            "GrayscaleRouter initialized: ratio={}%, state={}",
            self._ratio, self._state,
        )

    # ── 单例工厂 ────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "GrayscaleRouter":
        """获取进程内单例（延迟初始化）。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅测试用）。"""
        cls._instance = None

    # ── 路由核心 ────────────────────────────────────────

    def should_use_neo4j(self, thread_id: str | None = None) -> bool:
        """根据切流比例决定当前 thread_id 是否走 Neo4j。

        Args:
            thread_id: 会话 ID（同一会话始终命中同一 backend）

        Returns:
            True → 走 Neo4jBackend；False → 走 NetworkXBackend
        """
        # ratio == 0 → 全 NetworkX
        if self._ratio == 0:
            return False
        # ratio == 100 → 全 Neo4j
        if self._ratio >= 100:
            return True
        # ratio ∈ {10, 50} → md5 取模
        tid = thread_id or "default-thread"
        try:
            digest = hashlib.md5(tid.encode("utf-8")).hexdigest()[:8]
            bucket = int(digest, 16) % 100
        except Exception:  # noqa: BLE001
            bucket = 0
        return bucket < self._ratio

    # ── 切流操作 ────────────────────────────────────────

    def set_ratio(self, ratio: int, actor: str = "system") -> dict[str, Any]:
        """手动切流（管理端点调用）。

        Args:
            ratio: 0 / 10 / 50 / 100 之一
            actor: 操作者（admin / system / auto_rollback）

        Returns:
            切换结果 dict（state / ratio / started_at）

        Raises:
            ValueError: ratio 不在合法四态内
        """
        if ratio not in ALLOWED_RATIOS:
            raise ValueError(
                f"ratio 必须是 {ALLOWED_RATIOS} 之一，got {ratio}"
            )
        old_ratio = self._ratio
        old_state = self._state
        self._ratio = ratio
        self._state = self._state_for_ratio(ratio)
        self._started_at = time.monotonic()
        self._rollback_reason = None  # 清除回滚标记
        record = {
            "ts": time.time(),
            "actor": actor,
            "from_ratio": old_ratio,
            "to_ratio": ratio,
            "from_state": old_state,
            "to_state": self._state,
            "reason": "manual_set",
        }
        self._history.append(record)
        # 截断 history（保留最近 20 条）
        if len(self._history) > 20:
            self._history = self._history[-20:]
        # 监控埋点：JSON 日志
        logger.info(
            json.dumps(
                {
                    "event": "grayscale_switch",
                    "actor": actor,
                    "from_ratio": old_ratio,
                    "to_ratio": ratio,
                    "from_state": old_state,
                    "to_state": self._state,
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            )
        )
        # M3c：Prometheus 指标埋点（feature flag 关闭时 no-op）
        if is_metrics_enabled():
            try:
                metrics = get_metrics_collector()
                metrics.record_switch(
                    actor=actor,
                    from_state=old_state,
                    to_state=self._state,
                )
                metrics.update_grayscale(
                    ratio=self._ratio,
                    state=self._state,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("GrayscaleRouter.set_ratio metrics hook failed: {}", exc)
        return {
            "state": self._state,
            "ratio": self._ratio,
            "started_at": self._started_at,
        }

    # ── 回滚 ────────────────────────────────────────────

    def trigger_rollback(self, reason: str) -> dict[str, Any]:
        """触发回滚（自动或手动）。

        Args:
            reason: 回滚原因（auto_error_rate / auto_p95 / auto_connect / manual）

        Returns:
            切换结果 dict
        """
        self._rollback_reason = reason
        self._rollback_count += 1
        record = {
            "ts": time.time(),
            "actor": "rollback",
            "from_ratio": self._ratio,
            "to_ratio": 0,
            "from_state": self._state,
            "to_state": self.STATE_ROLLBACK,
            "reason": reason,
        }
        self._history.append(record)
        if len(self._history) > 20:
            self._history = self._history[-20:]
        # 监控埋点
        logger.warning(
            json.dumps(
                {
                    "event": "grayscale_auto_rollback",
                    "reason": reason,
                    "stage_before": self._state,
                    "stage_after": "off",
                    "rollback_count": self._rollback_count,
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            )
        )
        # 写 sync_log 审计
        try:
            if self._sync_log is None:
                self._sync_log = get_sync_log_service()
            self._sync_log.log_rollback_event(
                reason=reason,
                details={"rollback_count": self._rollback_count},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync_log rollback event write failed: {}", exc)
        # 切回 off
        result = self.set_ratio(0, actor=f"rollback:{reason}")
        result["rollback_reason"] = reason
        result["rollback_count"] = self._rollback_count
        # M3c：Prometheus 指标埋点（feature flag 关闭时 no-op）
        if is_metrics_enabled():
            try:
                metrics = get_metrics_collector()
                metrics.record_rollback(reason=reason)
            except Exception as exc:  # noqa: BLE001
                logger.debug("GrayscaleRouter.trigger_rollback metrics hook failed: {}", exc)
        return result

    # ── 请求埋点（rAG 引擎调用入口）────────────────────────

    def record_request(
        self,
        *,
        error: bool,
        latency_ms: float,
        backend: str,
    ) -> dict[str, Any] | None:
        """记录一次请求（用于自动回滚监控）。

        Args:
            error:      请求是否失败
            latency_ms: 请求耗时（毫秒）
            backend:    'neo4j' 或 'networkx'

        Returns:
            若触发自动回滚，返回 rollback result dict；否则 None
        """
        monitor = self._get_monitor()
        try:
            monitor.record(error=error, latency_ms=latency_ms, backend=backend)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RollbackMonitor.record failed: {}", exc)
            return None
        if monitor.should_rollback():
            reason = monitor.last_reason() or "auto_rollback"
            return self.trigger_rollback(reason)
        return None

    # ── 状态查询 ────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """返回完整状态机快照（管理端点用）。"""
        monitor = self._get_monitor()
        # M3c：刷新 Prometheus gauge（读取最新值）
        if is_metrics_enabled():
            try:
                metrics = get_metrics_collector()
                metrics.update_grayscale(ratio=self._ratio, state=self._state)
                stats = monitor.get_stats()
                metrics.update_rollback_window(
                    samples=int(stats.get("samples", 0)),
                    error_rate=float(stats.get("error_rate", 0.0)),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("GrayscaleRouter.get_status metrics hook failed: {}", exc)
        return {
            "state": self._state,
            "ratio": self._ratio,
            "started_at": self._started_at,
            "rollback_reason": self._rollback_reason,
            "rollback_count": self._rollback_count,
            "neo4j_enabled": bool(getattr(settings, "neo4j_enabled", False)),
            "monitor": monitor.get_stats(),
            "history": list(self._history),
        }

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """返回切换历史。"""
        return self._history[-limit:]

    @property
    def ratio(self) -> int:
        """当前切流比例（属性访问）。"""
        return self._ratio

    @property
    def state(self) -> str:
        """当前状态机状态（属性访问）。"""
        return self._state

    # ── 内部工具 ────────────────────────────────────────

    def _get_monitor(self) -> Any:
        """延迟初始化 RollbackMonitor（避免循环 import）。"""
        if self._monitor is None:
            from core.auto_rollback import RollbackMonitor
            self._monitor = RollbackMonitor(
                window_s=int(getattr(settings, "auto_rollback_window_s", 300)),
                error_rate_threshold=float(getattr(settings, "auto_rollback_error_rate", 0.01)),
                p95_threshold_ms=float(getattr(settings, "auto_rollback_p95_ms", 200)),
                neo4j_failure_threshold=int(getattr(settings, "auto_rollback_neo4j_fails", 3)),
                min_samples=int(getattr(settings, "auto_rollback_min_samples", 50)),
            )
        return self._monitor

    @staticmethod
    def _state_for_ratio(ratio: int) -> str:
        """ratio → state 映射。"""
        return {
            0: GrayscaleRouter.STATE_OFF,
            10: GrayscaleRouter.STATE_GRAY10,
            50: GrayscaleRouter.STATE_GRAY50,
            100: GrayscaleRouter.STATE_FULL100,
        }.get(ratio, GrayscaleRouter.STATE_OFF)


def get_grayscale_router() -> GrayscaleRouter:
    """获取 GrayscaleRouter 单例（推荐入口）。"""
    return GrayscaleRouter.get_instance()


def reset_grayscale_router() -> None:
    """重置单例（仅测试用）。"""
    GrayscaleRouter.reset_instance()


__all__ = [
    "GrayscaleRouter",
    "get_grayscale_router",
    "reset_grayscale_router",
    "ALLOWED_RATIOS",
]