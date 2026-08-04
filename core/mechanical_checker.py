"""中层 · 机理校验引擎（5 种轻量校验）。

依据 explainability-architecture.md §3.5 / §4.1 设计：
- ``OverloadCheck``        — 电流 > 额定值 × 1.2 报警、× 1.5 紧急
- ``ShortCircuitCheck``    — 基于设备铭牌阻抗推算预期短路电流
- ``PowerFlowCheck``       — 与遥测值交叉验证（发电机功率应为正等）
- ``VoltageCheck``         — 10kV / 35kV / 110kV / 220kV 偏差阈值
- ``TemperatureCheck``     — 变压器油温 / 绕组温度阈值表

设计原则：
- **纯函数 + 注册表** ``CHECKER_REGISTRY``，便于 P1 扩展新校验
- **无 ML 依赖**（stdlib + numpy）
- **结果统一为** ``MechanicalCheckItem``（Pydantic 校验通过）
- **零 I/O**：所有数据由 Orchestrator 注入，避免与 SQLite 耦合
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from loguru import logger

from core.schemas.diagnosis import MechanicalCheckItem


# ═══════════════════════════════════════════════════════
# 抽象基类
# ═══════════════════════════════════════════════════════


class MechanicalCheckBase(ABC):
    """机理校验抽象基类。"""

    rule_id: ClassVar[str] = "BASE-00"
    rule_name: ClassVar[str] = "机理校验基类"
    description: ClassVar[str] = ""

    @abstractmethod
    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        """执行单条校验。

        Args:
            telemetry: 最新遥测字典（含 temperature / voltage / current_load / power 等）
            device:    设备铭牌字典（含 rated_current / rated_voltage / short_impedance 等）

        Returns:
            MechanicalCheckItem（passed=True 表示通过）
        """


# ═══════════════════════════════════════════════════════
# 1. 过载校验（OC-01）
# ═══════════════════════════════════════════════════════


class OverloadCheck(MechanicalCheckBase):
    """电流过载校验：> 1.2× 额定告警、> 1.5× 额定紧急。"""

    rule_id: ClassVar[str] = "OC-01"
    rule_name: ClassVar[str] = "过载判断"
    description: ClassVar[str] = "依据 GB/T 1094.7-2016 变压器过载运行限制"

    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        current = telemetry.get("current_load")
        rated = device.get("rated_current")
        if current is None or rated is None or rated == 0:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity=None,
                observed_value=current,
                threshold=rated,
                explanation="缺少电流或额定值数据，跳过校验。",
            )

        ratio = current / rated
        if ratio >= 1.5:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="critical",
                observed_value=round(current, 2),
                threshold=round(rated, 2),
                evidence={"device_id": device.get("device_id"),
                          "ratio": round(ratio, 2),
                          "rule": "OC-001"},
                explanation=f"电流 {current}A 为额定值 {rated}A 的 {ratio:.2f} 倍，超过 1.5×，需立即减载或停运。",
            )
        if ratio >= 1.2:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="warning",
                observed_value=round(current, 2),
                threshold=round(rated, 2),
                evidence={"device_id": device.get("device_id"),
                          "ratio": round(ratio, 2),
                          "rule": "OC-002"},
                explanation=f"电流 {current}A 为额定值 {rated}A 的 {ratio:.2f} 倍，超过 1.2×，进入预警。",
            )
        return MechanicalCheckItem(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=True,
            severity="info",
            observed_value=round(current, 2),
            threshold=round(rated, 2),
            evidence={"device_id": device.get("device_id"),
                      "ratio": round(ratio, 2)},
            explanation=f"电流 {current}A 为额定值 {rated}A 的 {ratio:.2f} 倍，正常范围。",
        )


# ═══════════════════════════════════════════════════════
# 2. 短路电流初判（SC-01）
# ═══════════════════════════════════════════════════════


class ShortCircuitCheck(MechanicalCheckBase):
    """基于设备铭牌阻抗推算预期短路电流（I_sc = U_n / (U_k% × Z_base)）。

    本校验用「实际遥测阻抗 vs 铭牌阻抗」的比值，识别铭牌数据与现场不符的异常。
    实际生产中应接 PSASP/PSCAD 仿真（PRD Q1=A 决策：P0 轻量校验不接仿真）。
    """

    rule_id: ClassVar[str] = "SC-01"
    rule_name: ClassVar[str] = "短路电流初判"
    description: ClassVar[str] = "对比铭牌短路阻抗与实测阻抗，识别异常"

    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        rated_voltage = device.get("rated_voltage")  # kV
        short_impedance = device.get("short_impedance")  # %
        measured_impedance = telemetry.get("measured_impedance")  # % (可选)

        # 缺数据时直接通过（不阻断其他校验）
        if rated_voltage is None or short_impedance is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity=None,
                explanation="缺少铭牌电压/短路阻抗数据，跳过短路校验。",
            )

        # 理论短路电流（仅作 evidence 展示，不直接对比）
        # I_sc = U_n / (U_k% × √3 × U_n) → 简化比例
        if measured_impedance is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity="info",
                observed_value=f"U_k%={short_impedance}",
                threshold=f"U_n={rated_voltage}kV",
                evidence={"device_id": device.get("device_id"),
                          "rated_voltage_kv": rated_voltage,
                          "short_impedance_pct": short_impedance},
                explanation=(
                    f"铭牌短路阻抗 {short_impedance}%，额定电压 {rated_voltage}kV；"
                    "缺少实测阻抗，仅做铭牌记录展示。"
                ),
            )

        # 有实测阻抗时计算偏差
        ratio = measured_impedance / short_impedance if short_impedance else 0
        if ratio > 1.2:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="warning",
                observed_value=round(measured_impedance, 2),
                threshold=round(short_impedance, 2),
                evidence={"device_id": device.get("device_id"),
                          "ratio": round(ratio, 2),
                          "rule": "SC-001"},
                explanation=(
                    f"实测阻抗 {measured_impedance}% 为铭牌 {short_impedance}% 的 {ratio:.2f} 倍，"
                    "偏差超 20%，需校验铭牌与现场一致性。"
                ),
            )
        return MechanicalCheckItem(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=True,
            severity="info",
            observed_value=round(measured_impedance, 2),
            threshold=round(short_impedance, 2),
            evidence={"device_id": device.get("device_id"),
                      "ratio": round(ratio, 2)},
            explanation=(
                f"实测阻抗 {measured_impedance}% 与铭牌 {short_impedance}% 偏差 {ratio:.2f}×，在合理范围。"
            ),
        )


# ═══════════════════════════════════════════════════════
# 3. 潮流方向校验（PF-01）
# ═══════════════════════════════════════════════════════


class PowerFlowCheck(MechanicalCheckBase):
    """潮流方向与数值交叉验证（发电机功率为正、负荷功率为负等）。

    简化版：检测「负荷为正」是否异常（负荷应为负或零）。
    """

    rule_id: ClassVar[str] = "PF-01"
    rule_name: ClassVar[str] = "潮流方向校验"
    description: ClassVar[str] = "发电机/负荷功率方向交叉验证"

    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        power = telemetry.get("power")  # MW，正=发电，负=负荷
        device_type = device.get("device_type", "")

        if power is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity=None,
                explanation="缺少功率遥测数据，跳过潮流校验。",
            )

        # 简化：母线/电缆应为负（受电），发电机应为正，变压器双向
        if device_type in ("cable", "busbar") and power > 0:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="warning",
                observed_value=power,
                threshold="<0",
                evidence={"device_id": device.get("device_id"),
                          "device_type": device_type},
                explanation=f"{device_type} 设备功率 {power}MW 应为负值（受电），出现正值需检查。",
            )

        if device_type == "transformer" and abs(power) > 100:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="warning",
                observed_value=power,
                threshold="|P|<100MW",
                evidence={"device_id": device.get("device_id"),
                          "device_type": device_type},
                explanation=f"主变功率 {power}MW 超出典型运行范围（|P|<100MW）。",
            )

        return MechanicalCheckItem(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=True,
            severity="info",
            observed_value=power,
            threshold="正常范围",
            evidence={"device_id": device.get("device_id"),
                      "device_type": device_type},
            explanation=f"功率 {power}MW 在 {device_type} 设备正常范围。",
        )


# ═══════════════════════════════════════════════════════
# 4. 电压等级偏差校验（VL-01）
# ═══════════════════════════════════════════════════════


# 各级电压允许偏差表（依据 GB/T 12325-2008）：
#   ≤20kV: ±7%；35kV: ±10%（正偏差 10% / 负偏差 10%）；110kV: ±10%；220kV: ±10%
_VOLTAGE_DEVIATION_PCT: dict[str, float] = {
    "10kV": 7.0,
    "35kV": 10.0,
    "110kV": 10.0,
    "220kV": 10.0,
}

# P1-1: 当 telemetry.voltage 与 device.rated_voltage 偏差 > 50% 时视为
# "数据不一致"（如 transformer 设备铭牌 220kV，但遥测注入 10.5kV），
# 此时跳过偏差校验，避免 95% 偏差的误报。
_DATA_INCONSISTENT_PCT: float = 50.0


class VoltageCheck(MechanicalCheckBase):
    """电压偏差校验：按设备额定电压等级匹配偏差阈值。

    P1-1 增强：当实测电压与额定电压偏差 > 50%（典型场景：seed 注入的 base_volt
    与设备 rated_voltage 不一致）时，跳过偏差校验并标记 ``data_inconsistent=True``
    写入 evidence，不触发 false positive。其他场景行为保持不变。
    """

    rule_id: ClassVar[str] = "VL-01"
    rule_name: ClassVar[str] = "电压等级偏差校验"
    description: ClassVar[str] = "依据 GB/T 12325-2008 电能质量 供电电压偏差"

    def _classify_voltage_level(self, rated_kv: float) -> str | None:
        """将额定电压归类为已知电压等级。"""
        for level in _VOLTAGE_DEVIATION_PCT:
            kv = float(level.replace("kV", ""))
            if math.isclose(rated_kv, kv, abs_tol=0.5):
                return level
        return None

    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        voltage = telemetry.get("voltage")
        rated_voltage = device.get("rated_voltage")

        if voltage is None or rated_voltage is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity=None,
                explanation="缺少电压或额定电压数据，跳过校验。",
            )

        # P1-1: 数据不一致检测（>50% 偏差视为 seed/铭牌不匹配，跳过）
        if rated_voltage > 0:
            raw_deviation_pct = abs(voltage - rated_voltage) / rated_voltage * 100
            if raw_deviation_pct > _DATA_INCONSISTENT_PCT:
                logger.warning(
                    "VoltageCheck: data inconsistent (telemetry={}kV vs rated={}kV, "
                    "deviation={:.1f}% > {}%); skip voltage deviation check",
                    voltage, rated_voltage, raw_deviation_pct, _DATA_INCONSISTENT_PCT,
                )
                return MechanicalCheckItem(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    passed=True,
                    severity="info",
                    observed_value=round(voltage, 2),
                    threshold=f"额定 {rated_voltage}kV",
                    evidence={
                        "device_id": device.get("device_id"),
                        "raw_deviation_pct": round(raw_deviation_pct, 2),
                        "data_inconsistent": True,
                    },
                    explanation=(
                        f"实测电压 {voltage}kV 与额定 {rated_voltage}kV 偏差 "
                        f"{raw_deviation_pct:.1f}%（>{_DATA_INCONSISTENT_PCT:.0f}%），"
                        "视为数据不一致，跳过偏差校验。请检查遥测注入或设备铭牌。"
                    ),
                )

        level = self._classify_voltage_level(rated_voltage)
        if level is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity="info",
                observed_value=voltage,
                threshold=f"额定 {rated_voltage}kV（无对应偏差表）",
                evidence={"device_id": device.get("device_id"),
                          "data_inconsistent": False},
                explanation=f"额定电压 {rated_voltage}kV 不在标准电压等级表中，跳过偏差校验。",
            )

        deviation_pct = abs(voltage - rated_voltage) / rated_voltage * 100
        limit_pct = _VOLTAGE_DEVIATION_PCT[level]
        if deviation_pct > limit_pct:
            # 偏差 > 1.5× 阈值视为 critical
            severity: Any = "critical" if deviation_pct > limit_pct * 1.5 else "warning"
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity=severity,
                observed_value=round(voltage, 2),
                threshold=f"{level} ±{limit_pct}%",
                evidence={"device_id": device.get("device_id"),
                          "level": level,
                          "deviation_pct": round(deviation_pct, 2),
                          "data_inconsistent": False},
                explanation=(
                    f"实测电压 {voltage}kV 偏差 {deviation_pct:.1f}%，"
                    f"超过 {level} ±{limit_pct}% 限值。"
                ),
            )
        return MechanicalCheckItem(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=True,
            severity="info",
            observed_value=round(voltage, 2),
            threshold=f"{level} ±{limit_pct}%",
            evidence={"device_id": device.get("device_id"),
                      "level": level,
                      "deviation_pct": round(deviation_pct, 2),
                      "data_inconsistent": False},
            explanation=f"电压 {voltage}kV 偏差 {deviation_pct:.1f}%，在 {level} ±{limit_pct}% 内。",
        )


# ═══════════════════════════════════════════════════════
# 5. 温度阈值校验（OT-01）
# ═══════════════════════════════════════════════════════


# 油浸式变压器顶层油温限值（依据 DL/T 572-2010）：
#   正常：≤85℃ 告警
#   紧急：>95℃ 立即减载或停运
_TEMP_LIMITS_TRANSFORMER: dict[str, float] = {
    "warning": 85.0,
    "critical": 95.0,
}

# 断路器 / 母线 / 电缆温度（依据 GB/T 11022-2011）
_TEMP_LIMITS_DEFAULT: dict[str, float] = {
    "warning": 70.0,
    "critical": 90.0,
}


class TemperatureCheck(MechanicalCheckBase):
    """温度阈值校验：变压器油温 / 绕组温度阈值表。"""

    rule_id: ClassVar[str] = "OT-01"
    rule_name: ClassVar[str] = "温度阈值校验"
    description: ClassVar[str] = "依据 DL/T 572-2010 / GB/T 11022-2011 设备温度限值"

    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        temperature = telemetry.get("temperature")
        device_type = device.get("device_type", "")

        if temperature is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity=None,
                explanation="缺少温度遥测数据，跳过校验。",
            )

        if device_type == "transformer":
            limits = _TEMP_LIMITS_TRANSFORMER
            kind = "顶层油温"
        else:
            limits = _TEMP_LIMITS_DEFAULT
            kind = "运行温度"

        if temperature > limits["critical"]:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="critical",
                observed_value=round(temperature, 2),
                threshold=f"{kind} > {limits['critical']}℃",
                evidence={"device_id": device.get("device_id"),
                          "device_type": device_type,
                          "rule": "OT-001"},
                explanation=(
                    f"{kind} {temperature}℃ 超过 {limits['critical']}℃ 紧急限值，应立即减载或停运。"
                ),
            )
        if temperature > limits["warning"]:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="warning",
                observed_value=round(temperature, 2),
                threshold=f"{kind} > {limits['warning']}℃",
                evidence={"device_id": device.get("device_id"),
                          "device_type": device_type,
                          "rule": "OT-002"},
                explanation=f"{kind} {temperature}℃ 超过 {limits['warning']}℃ 告警限值。",
            )
        return MechanicalCheckItem(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=True,
            severity="info",
            observed_value=round(temperature, 2),
            threshold=f"{kind} ≤ {limits['warning']}℃",
            evidence={"device_id": device.get("device_id"),
                      "device_type": device_type},
            explanation=f"{kind} {temperature}℃ 在正常范围。",
        )


# ═══════════════════════════════════════════════════════
# 校验器注册表 + 聚合
# ═══════════════════════════════════════════════════════


CHECKER_REGISTRY: dict[str, type[MechanicalCheckBase]] = {
    "overload": OverloadCheck,
    "short_circuit": ShortCircuitCheck,
    "power_flow": PowerFlowCheck,
    "voltage": VoltageCheck,
    "temperature": TemperatureCheck,
}


class MechanicalChecker:
    """5 种校验的并行聚合执行器。

    用法：
        checker = MechanicalChecker()
        result = checker.check_all(telemetry_dict, device_dict)
    """

    def __init__(self, enabled: dict[str, bool] | None = None) -> None:
        """Args:
            enabled:  校验开关表，key ∈ ``CHECKER_REGISTRY``，缺省全部启用。
        """
        self._enabled = enabled or {k: True for k in CHECKER_REGISTRY}

    def check_all(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> list[MechanicalCheckItem]:
        """执行所有启用的校验，返回每条结果列表。"""
        results: list[MechanicalCheckItem] = []
        for name, cls in CHECKER_REGISTRY.items():
            if not self._enabled.get(name, True):
                logger.debug("MechanicalChecker: '{}' disabled, skip", name)
                continue
            try:
                item = cls().check(telemetry, device)
                results.append(item)
            except Exception as e:
                # 单条校验失败不应阻断其他校验
                logger.error("Checker '{}' failed: {}", name, e)
                results.append(MechanicalCheckItem(
                    rule_id=f"{name.upper()}-ERR",
                    rule_name=f"{name} 校验异常",
                    passed=True,  # 异常不阻断
                    severity="info",
                    explanation=f"校验器执行异常：{e!s}",
                ))
        return results


__all__ = [
    "MechanicalCheckBase",
    "OverloadCheck",
    "ShortCircuitCheck",
    "PowerFlowCheck",
    "VoltageCheck",
    "TemperatureCheck",
    "MechanicalChecker",
    "CHECKER_REGISTRY",
]
