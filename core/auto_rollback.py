"""GridMind 自动回滚监控器（RollbackMonitor）—— M2 阶段自动回滚核心。

设计目标
--------
- **5 分钟滚动窗口**：基于 ``time.monotonic()`` 的环形缓冲区（deque maxlen=10000）
- **3 个硬阈值**：
    1. 错误率 > 1%（rolling window）
    2. P95 延迟 > 200ms（rolling window）
    3. Neo4j 连续失败 ≥ 3 次
- **样本下限**：窗口内样本数 < min_samples 不触发（避免冷启动误判）
- **零新增三方依赖**：仅使用 stdlib（``collections.deque`` + ``time``）

跨文件约定
----------
- 由 ``GrayscaleRouter`` 持有（避免循环 import）
- 任何错误（Neo4j 失败 / P95 超标）触发 → ``GrayscaleRouter.trigger_rollback(reason)``
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from loguru import logger

from core.metrics_collector import (
    get_metrics_collector,
    is_metrics_enabled,
)


class RollbackMonitor:
    """5 分钟滚动窗口监控：硬阈值触发自动回滚。"""

    def __init__(
        self,
        window_s: int = 300,
        error_rate_threshold: float = 0.01,
        p95_threshold_ms: float = 200.0,
        neo4j_failure_threshold: int = 3,
        min_samples: int = 50,
    ) -> None:
        self._window_s = int(window_s)
        self._error_rate_threshold = float(error_rate_threshold)
        self._p95_threshold_ms = float(p95_threshold_ms)
        self._neo4j_fail_threshold = int(neo4j_failure_threshold)
        self._min_samples = int(min_samples)
        # 滚动窗口：元素 = (timestamp_monotonic, error_bool, latency_ms, backend_str)
        self._samples: deque = deque(maxlen=10000)
        # Neo4j 连续失败计数（最近一次成功后归零）
        self._neo4j_consecutive_failures: int = 0
        # 最近一次触发回滚的原因
        self._last_reason: str | None = None

    # ── 写入 ────────────────────────────────────────────

    def record(
        self,
        *,
        error: bool,
        latency_ms: float,
        backend: str,
    ) -> None:
        """记录一次请求指标。

        Args:
            error:      是否失败
            latency_ms: 请求耗时（毫秒）
            backend:    'neo4j' / 'networkx'
        """
        now = time.monotonic()
        self._samples.append((now, bool(error), float(latency_ms), str(backend)))
        # Neo4j 连续失败计数
        if backend == "neo4j" and error:
            self._neo4j_consecutive_failures += 1
        elif backend == "neo4j" and not error:
            self._neo4j_consecutive_failures = 0
        # 节流式清理过期样本
        self._evict_old(now)
        # M3c：刷新窗口统计 gauge（feature flag 关闭时 no-op）
        if is_metrics_enabled():
            try:
                metrics = get_metrics_collector()
                # 重新计算 error_rate（用删除过期样本后的 _samples）
                samples_now = len(self._samples)
                if samples_now > 0:
                    err_count = sum(1 for s in self._samples if s[1])
                    err_rate = err_count / samples_now
                else:
                    err_rate = 0.0
                metrics.update_rollback_window(
                    samples=samples_now,
                    error_rate=err_rate,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("RollbackMonitor.record metrics hook failed: {}", exc)

    def _evict_old(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    # ── 阈值判定 ────────────────────────────────────────

    def should_rollback(self) -> bool:
        """根据当前窗口指标判定是否触发回滚。"""
        if not self._samples:
            # 样本为空但 Neo4j 连续失败已超阈值（边界场景：刚启动就有连续失败）
            if self._neo4j_consecutive_failures >= self._neo4j_fail_threshold:
                self._last_reason = "auto_neo4j_connect"
                return True
            return False

        # 1) 错误率阈值（需 ≥ min_samples 才判断；样本不足跳过避免冷启动误判）
        if len(self._samples) >= self._min_samples:
            error_count = sum(1 for s in self._samples if s[1])
            error_rate = error_count / len(self._samples)
            if error_rate > self._error_rate_threshold:
                self._last_reason = "auto_error_rate"
                logger.warning(
                    "RollbackMonitor: error_rate={:.3f} > threshold={:.3f} (samples={})",
                    error_rate, self._error_rate_threshold, len(self._samples),
                )
                return True

            # 2) P95 延迟阈值（同上，需样本充足）
            latencies = sorted(s[2] for s in self._samples)
            if latencies:
                p95_index = int(len(latencies) * 0.95)
                if p95_index >= len(latencies):
                    p95_index = len(latencies) - 1
                p95 = latencies[p95_index]
                if p95 > self._p95_threshold_ms:
                    self._last_reason = "auto_p95"
                    logger.warning(
                        "RollbackMonitor: p95={:.0f}ms > threshold={:.0f}ms (samples={})",
                        p95, self._p95_threshold_ms, len(self._samples),
                    )
                    return True

        # 3) Neo4j 连续失败（不依赖 min_samples，单调递增即可触发）
        if self._neo4j_consecutive_failures >= self._neo4j_fail_threshold:
            self._last_reason = "auto_neo4j_connect"
            logger.warning(
                "RollbackMonitor: neo4j_consecutive_failures={} >= threshold={}",
                self._neo4j_consecutive_failures, self._neo4j_fail_threshold,
            )
            return True

        self._last_reason = None
        return False

    # ── 查询 ────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """返回当前窗口统计快照。"""
        if not self._samples:
            return {
                "samples": 0,
                "error_rate": 0.0,
                "p95_ms": 0.0,
                "neo4j_consecutive_failures": 0,
                "window_s": self._window_s,
                "thresholds": {
                    "error_rate": self._error_rate_threshold,
                    "p95_ms": self._p95_threshold_ms,
                    "neo4j_failures": self._neo4j_fail_threshold,
                },
            }
        errors = sum(1 for s in self._samples if s[1])
        latencies = sorted(s[2] for s in self._samples)
        p95_index = int(len(latencies) * 0.95)
        if p95_index >= len(latencies):
            p95_index = len(latencies) - 1
        p95 = latencies[p95_index] if latencies else 0.0
        return {
            "samples": len(self._samples),
            "error_rate": round(errors / len(self._samples), 4),
            "p95_ms": round(p95, 1),
            "neo4j_consecutive_failures": self._neo4j_consecutive_failures,
            "window_s": self._window_s,
            "thresholds": {
                "error_rate": self._error_rate_threshold,
                "p95_ms": self._p95_threshold_ms,
                "neo4j_failures": self._neo4j_fail_threshold,
            },
        }

    def last_reason(self) -> str | None:
        """最近一次触发回滚的原因。"""
        return self._last_reason

    def reset_window(self) -> None:
        """清空窗口（仅测试 / 运维手动使用）。"""
        self._samples.clear()
        self._neo4j_consecutive_failures = 0
        self._last_reason = None
        logger.info("RollbackMonitor: window reset")


__all__ = ["RollbackMonitor"]