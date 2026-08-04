"""FR-6 设备异常检测 / 健康评分引擎。

基于 z-score 滚动窗口异常检测 + 温度/电压/负载规则评分，
输出每台设备健康分（0-100）与异常清单。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from api.schemas import (
    AnomalyItem,
    AnomalySeverity,
    HealthLevel,
    HealthScoreResult,
)
from mcp_tools.db.database import get_connection

# ── 阈值常量 ──────────────────────────────────────────
# |z| >= 3.0 视为异常（3σ 原则）。此前 2.0 对种子数据的正常噪声
# （σ≈base×0.02）过于敏感，60 点×3 指标必然产生大量假阳性，
# 导致全部设备误判 critical。注入的异常尖峰 z≈30~70，远高于阈值。
ZSCORE_THRESHOLD = 3.0        # |z| >= 3.0 视为异常
ROLLING_WINDOW = 12           # 12 点滚动窗口（对应 12 小时）

# 规则权重
WEIGHT_TEMPERATURE = 0.35
WEIGHT_VOLTAGE = 0.25
WEIGHT_LOAD = 0.40


class AnomalyDetectionService:
    """z-score 滚动窗口异常检测 + 健康评分。"""

    def __init__(self, window: int = ROLLING_WINDOW) -> None:
        self.window = window

    # ── 公开入口 ─────────────────────────────────────

    def detect_all(self) -> list[HealthScoreResult]:
        """对所有设备执行异常检测，返回健康评分列表。"""
        conn = get_connection()
        try:
            device_rows = conn.execute(
                "SELECT device_id, device_name, device_type, location, status FROM devices"
            ).fetchall()
        finally:
            conn.close()

        results: list[HealthScoreResult] = []
        for row in device_rows:
            dev_id = row["device_id"]
            try:
                score = self._evaluate_device(dev_id, row["device_name"])
                results.append(score)
            except Exception as e:
                logger.warning("Anomaly detection failed for {}: {}", dev_id, e)
                results.append(HealthScoreResult(
                    device_id=dev_id,
                    device_name=row["device_name"],
                    health_score=50.0,
                    health_level=HealthLevel.warning,
                    anomalies=[],
                    summary=f"检测异常: {e}",
                ))
        return results

    def detect_device(self, device_id: str) -> HealthScoreResult | None:
        """检测单台设备。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT device_id, device_name FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None
        return self._evaluate_device(row["device_id"], row["device_name"])

    # ── 核心逻辑 ─────────────────────────────────────

    def _evaluate_device(self, device_id: str, device_name: str) -> HealthScoreResult:
        """对单台设备执行完整评估。"""
        df = self._load_telemetry(device_id)
        anomalies: list[AnomalyItem] = []

        if df.empty:
            return HealthScoreResult(
                device_id=device_id,
                device_name=device_name,
                health_score=50.0,
                health_level=HealthLevel.warning,
                anomalies=[],
                summary="无遥测数据，无法评估",
            )

        # 各指标 z-score 检测
        for metric, label in [
            ("temperature", "温度"),
            ("voltage", "电压"),
            ("current_load", "负载"),
        ]:
            if metric not in df.columns or df[metric].isna().all():
                continue
            col = df[metric].astype(float)
            if len(col) < 3:
                continue
            z_scores = self._rolling_zscore(col)
            for i, z in enumerate(z_scores):
                if z is None or abs(z) < ZSCORE_THRESHOLD:
                    continue
                severity = (
                    AnomalySeverity.high if abs(z) >= 3.5
                    else AnomalySeverity.medium if abs(z) >= 2.8
                    else AnomalySeverity.low
                )
                anomalies.append(AnomalyItem(
                    device_id=device_id,
                    metric=metric,
                    value=float(col.iloc[i]),
                    z_score=round(float(z), 2),
                    severity=severity,
                    description=(
                        f"{label}异常: {col.iloc[i]:.1f} "
                        f"(z={z:.2f}, 阈值={ZSCORE_THRESHOLD})"
                    ),
                ))

        # 规则评分
        latest = df.iloc[-1]
        health = self._rule_score(latest, device_id, anomalies)

        # 异常扣分
        penalty = sum(
            5 if a.severity == AnomalySeverity.low else
            12 if a.severity == AnomalySeverity.medium else
            25
            for a in anomalies
        )
        health = max(0.0, min(100.0, health - penalty))
        health = round(health, 1)

        level = (
            HealthLevel.normal if health >= 80
            else HealthLevel.warning if health >= 60
            else HealthLevel.critical
        )

        summary_parts = [f"健康分 {health}"]
        if anomalies:
            high_count = sum(1 for a in anomalies if a.severity == AnomalySeverity.high)
            summary_parts.append(f"检测到 {len(anomalies)} 项异常")
            if high_count:
                summary_parts.append(f"其中 {high_count} 项严重")
        else:
            summary_parts.append("未检测到显著异常")
        summary_parts.append(f"评级: {level.value}")

        return HealthScoreResult(
            device_id=device_id,
            device_name=device_name,
            health_score=health,
            health_level=level,
            anomalies=anomalies,
            summary="，".join(summary_parts),
        )

    # ── 加载遥测 ─────────────────────────────────────

    def _load_telemetry(self, device_id: str) -> pd.DataFrame:
        """从 SQLite 加载设备遥测数据。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT timestamp, temperature, voltage, current_load "
                "FROM telemetry WHERE device_id = ? ORDER BY timestamp ASC",
                (device_id,),
            ).fetchall()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(
                [dict(r) for r in rows],
                columns=["timestamp", "temperature", "voltage", "current_load"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df
        finally:
            conn.close()

    # ── z-score ──────────────────────────────────────

    @staticmethod
    def _rolling_zscore(series: pd.Series) -> list[float | None]:
        """对序列计算全局 z-score（均值/标准差基于全量样本）。

        此前使用 12 点滚动窗口的局部 std，小样本下 std 估计不稳定
        （正常窗口 std 可低至 0.3~0.5），把正常噪声放大成假阳性。
        全局 z-score 对平稳噪声序列更稳健，注入尖峰（z≈30~70）仍能检出。
        """
        values = series.values.astype(float)
        mean = float(np.nanmean(values))
        std = float(np.nanstd(values))
        if std < 1e-6:
            return [0.0] * len(values)
        return [float((v - mean) / std) for v in values]

    # ── 规则评分 ─────────────────────────────────────

    @staticmethod
    def _rule_score(
        latest: pd.Series, device_id: str, anomalies: list[AnomalyItem],
    ) -> float:
        """基于最新遥测值计算基础健康分（0-100，未扣分前）。"""
        score = 85.0  # 基准分

        # 温度规则
        if "temperature" in latest and pd.notna(latest["temperature"]):
            t = float(latest["temperature"])
            if t > 90:
                score -= 30
            elif t > 80:
                score -= 15
            elif t > 70:
                score -= 5

        # 电压规则
        if "voltage" in latest and pd.notna(latest["voltage"]):
            v = float(latest["voltage"])
            if v < 9.0 or v > 11.5:
                score -= 15
            elif v < 9.5 or v > 11.0:
                score -= 5

        # 负载规则
        if "current_load" in latest and pd.notna(latest["current_load"]):
            l = float(latest["current_load"])
            if l > 100:
                score -= 25
            elif l > 85:
                score -= 10
            elif l > 70:
                score -= 3

        return max(0.0, min(100.0, score))
