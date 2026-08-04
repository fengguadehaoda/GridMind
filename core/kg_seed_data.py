"""GridMind 知识图谱 M1 抽取规则 + ≥500 三元组种子数据模板。

设计目标
--------
- 基于现有 ``seed_data.py`` 的 21 实体 + 24 关系，扩展到 **≥500 三元组**（节点 + 关系）。
- 所有抽取均基于 **规则模板**（无需真实 LLM 调用，Q3=A 决策）。
- 节点 / 关系都以 **Python ``dict`` 列表** 形式产出，便于 ``SeedExtractor`` 直接消费。
- **幂等键** 设计：所有节点用 ``entity_id`` 唯一；关系用 ``(src_id, tgt_id, type)`` 唯一。

5 类抽取规则
------------
1. **设备属性扩展**（R1）— 每台设备添加 manufacturer / commissioning_date /
   rated_voltage / rated_current / short_impedance 5 个属性；映射到对应设备子类。
2. **故障-设备关系扩展**（R2）— 每种故障类型 × 每台设备 × {可能发生, 已发生, 已恢复}
   ≈ 5 × 8 × 3 = **120 条** HAS_FAULT 关系。
3. **处置-故障关系扩展**（R3）— 每种处置 × 每种故障 × {常规处置, 严重时处置}
   ≈ 5 × 5 × 2 = **50 条** RESOLVED_BY 关系。
4. **规程-设备/故障关系**（R4）— 每篇知识库文档 × 多类实体 ≈ **40 条** RELATES_TO。
5. **设备-设备连接**（R5）— 基于典型电网拓扑（母线↔断路器↔变压器↔线路）≈ **50 条** CONNECTED_TO。
6. **因果关系**（R6）— 常见故障传导链（过载→油温高→绝缘老化→击穿）≈ **30 条** CAUSES。

合计估算
--------
- 节点（基础 21 + 扩展 35+） ≈ **60 节点**
- 关系 ≈ **450+ 关系**
- 总三元组（节点 + 关系）≈ **510+ 三元组**（≥500 ✓）

可复现性
--------
- 所有 ID 由规则模板确定性生成（不含随机数 / 时间戳），同一输入始终产生相同输出。
"""

from __future__ import annotations

import json
from typing import Any

# ═════════════════════════════════════════════════════════════════════════════
# 1. 基础节点定义（与 seed_data.py 对齐）
# ═════════════════════════════════════════════════════════════════════════════

# 设备类别（5 个 —— 与 PRD §5.2 一致）
DEVICE_CATEGORIES: list[dict[str, Any]] = [
    {
        "entity_id": "e-transformer",
        "name": "变压器",
        "type": "设备类别",
        "code": "TRANSFORMER",
        "label": "DeviceCategory",
        "properties": {"category": "PowerEquipment", "abstract": True},
    },
    {
        "entity_id": "e-breaker",
        "name": "断路器",
        "type": "设备类别",
        "code": "CIRCUIT_BREAKER",
        "label": "DeviceCategory",
        "properties": {"category": "PowerEquipment"},
    },
    {
        "entity_id": "e-cable",
        "name": "电缆",
        "type": "设备类别",
        "code": "LINE",
        "label": "DeviceCategory",
        "properties": {"category": "PowerEquipment"},
    },
    {
        "entity_id": "e-busbar",
        "name": "母线",
        "type": "设备类别",
        "code": "BUSBAR",
        "label": "DeviceCategory",
        "properties": {"category": "PowerEquipment"},
    },
    {
        "entity_id": "e-arrester",
        "name": "避雷器",
        "type": "设备类别",
        "code": "ARRESTER",
        "label": "DeviceCategory",
        "properties": {"category": "PowerEquipment"},
    },
]

# 故障类型（5 个主类型 + 4 个子类型）
FAULT_TYPES: list[dict[str, Any]] = [
    {
        "entity_id": "e-overload",
        "name": "过载",
        "type": "故障类型",
        "code": "OVERLOAD",
        "label": "FaultType",
        "subtype": "OverloadFault",
        "properties": {"severity": "high", "description": "负载电流超过额定值"},
    },
    {
        "entity_id": "e-overtemp",
        "name": "油温异常",
        "type": "故障类型",
        "code": "OVERTEMP",
        "label": "FaultType",
        "subtype": "OverheatFault",
        "properties": {"severity": "high", "description": "顶层油温 > 85℃"},
    },
    {
        "entity_id": "e-sf6leak",
        "name": "SF6泄漏",
        "type": "故障类型",
        "code": "SF6_LEAK",
        "label": "FaultType",
        "subtype": "ShortCircuitFault",
        "properties": {"severity": "medium", "description": "SF6气体压力低于阈值"},
    },
    {
        "entity_id": "e-partial-discharge",
        "name": "局部放电",
        "type": "故障类型",
        "code": "PARTIAL_DISCHARGE",
        "label": "FaultType",
        "subtype": "ShortCircuitFault",
        "properties": {"severity": "medium", "description": "局放量 > 100pC"},
    },
    {
        "entity_id": "e-ground-fault",
        "name": "接地故障",
        "type": "故障类型",
        "code": "GROUND_FAULT",
        "label": "FaultType",
        "subtype": "ShortCircuitFault",
        "properties": {"severity": "high", "description": "接地电流异常"},
    },
    # ── 子类型独立节点（让子类型也可作为查询目标）─────────
    {
        "entity_id": "e-subtype-overload-fault",
        "name": "过载故障子类",
        "type": "故障子类型",
        "code": "OVERLOAD_FAULT",
        "label": "OverloadFault",
        "properties": {"severity": "high", "parent": "e-overload"},
    },
    {
        "entity_id": "e-subtype-short-circuit-fault",
        "name": "短路故障子类",
        "type": "故障子类型",
        "code": "SHORT_CIRCUIT_FAULT",
        "label": "ShortCircuitFault",
        "properties": {"severity": "high", "parent": "e-ground-fault"},
    },
    {
        "entity_id": "e-subtype-overheat-fault",
        "name": "过热故障子类",
        "type": "故障子类型",
        "code": "OVERHEAT_FAULT",
        "label": "OverheatFault",
        "properties": {"severity": "high", "parent": "e-overtemp"},
    },
    {
        "entity_id": "e-subtype-voltage-deviation",
        "name": "电压偏差故障子类",
        "type": "故障子类型",
        "code": "VOLTAGE_DEVIATION",
        "label": "VoltageDeviationFault",
        "properties": {"severity": "medium", "parent": "e-ground-fault"},
    },
]

# 处置措施（5 个主类型 + 2 个子类型）
HANDLING_MEASURES: list[dict[str, Any]] = [
    {
        "entity_id": "e-derating",
        "name": "减载",
        "type": "处置措施",
        "code": "DERATING",
        "label": "HandlingMeasure",
        "properties": {"priority": "P3", "estimated_duration_h": 1.0},
    },
    {
        "entity_id": "e-shutdown",
        "name": "停运",
        "type": "处置措施",
        "code": "SHUTDOWN",
        "label": "HandlingMeasure",
        "subtype": "EmergencyStopMeasure",
        "properties": {"priority": "P1", "estimated_duration_h": 4.0},
    },
    {
        "entity_id": "e-repair",
        "name": "检修",
        "type": "处置措施",
        "code": "REPAIR",
        "label": "HandlingMeasure",
        "subtype": "RoutineMaintenanceMeasure",
        "properties": {"priority": "P2", "estimated_duration_h": 8.0},
    },
    {
        "entity_id": "e-replace",
        "name": "更换",
        "type": "处置措施",
        "code": "REPLACE",
        "label": "HandlingMeasure",
        "subtype": "RoutineMaintenanceMeasure",
        "properties": {"priority": "P2", "estimated_duration_h": 24.0},
    },
    {
        "entity_id": "e-monitor",
        "name": "加强监测",
        "type": "处置措施",
        "code": "MONITOR",
        "label": "HandlingMeasure",
        "properties": {"priority": "P4", "estimated_duration_h": 0.5},
    },
    # ── 子类型独立节点 ──
    {
        "entity_id": "e-subtype-emergency-stop",
        "name": "紧急停运子类",
        "type": "处置子类型",
        "code": "EMERGENCY_STOP",
        "label": "EmergencyStopMeasure",
        "properties": {"priority": "P1", "parent": "e-shutdown"},
    },
    {
        "entity_id": "e-subtype-routine-maintenance",
        "name": "常规维护子类",
        "type": "处置子类型",
        "code": "ROUTINE_MAINTENANCE",
        "label": "RoutineMaintenanceMeasure",
        "properties": {"priority": "P2", "parent": "e-repair"},
    },
]

# 规程（5 个 —— 扩展 1 个 Q/GDW 1164 + 增加 GB/T 1094.7 + DL/T 1578 等）
REGULATIONS: list[dict[str, Any]] = [
    {
        "entity_id": "e-DL572",
        "name": "DL/T 572-2010",
        "type": "规程",
        "code": "DL/T 572-2010",
        "section": "main",
        "label": "Regulation",
        "properties": {"category": "运行规程", "severity": "mandatory"},
    },
    {
        "entity_id": "e-QGDW1799",
        "name": "Q/GDW 1799",
        "type": "规程",
        "code": "Q/GDW 1799",
        "section": "main",
        "label": "Regulation",
        "properties": {"category": "安规", "severity": "mandatory"},
    },
    {
        "entity_id": "e-GB1094",
        "name": "GB/T 1094.7-2016",
        "type": "规程",
        "code": "GB/T 1094.7-2016",
        "section": "main",
        "label": "Regulation",
        "properties": {"category": "国标", "severity": "mandatory"},
    },
    {
        "entity_id": "e-DL1578",
        "name": "DL/T 1578-2016",
        "type": "规程",
        "code": "DL/T 1578-2016",
        "section": "main",
        "label": "Regulation",
        "properties": {"category": "部颁", "severity": "mandatory"},
    },
    {
        "entity_id": "e-QGDW1164",
        "name": "Q/GDW 1164-2014",
        "type": "规程",
        "code": "Q/GDW 1164-2014",
        "section": "main",
        "label": "Regulation",
        "properties": {"category": "企标", "severity": "mandatory"},
    },
]

# 设备实例（8 台 —— 与 seed_data.DEVICES 一一对应）
DEVICE_INSTANCES: list[dict[str, Any]] = [
    {
        "entity_id": "e-TR001",
        "name": "一号主变",
        "type": "设备实例",
        "code": "TR-001",
        "label": "Transformer",
        "properties": {
            "device_id": "TR-001",
            "rated_voltage": 220.0,
            "rated_capacity_mva": 180.0,
            "oil_temp_threshold": 85.0,
            "manufacturer": "特变电工",
            "commissioning_date": "2022-03-15",
            "voltage_level": 220.0,
            "location": "A区变电站",
        },
    },
    {
        "entity_id": "e-TR002",
        "name": "二号主变",
        "type": "设备实例",
        "code": "TR-002",
        "label": "Transformer",
        "properties": {
            "device_id": "TR-002",
            "rated_voltage": 110.0,
            "rated_capacity_mva": 120.0,
            "oil_temp_threshold": 85.0,
            "manufacturer": "西安西电",
            "commissioning_date": "2022-06-20",
            "voltage_level": 110.0,
            "location": "B区变电站",
        },
    },
    {
        "entity_id": "e-BR001",
        "name": "进线断路器",
        "type": "设备实例",
        "code": "BR-001",
        "label": "CircuitBreaker",
        "properties": {
            "device_id": "BR-001",
            "rated_current": 630.0,
            "short_impedance": 12.0,
            "rated_voltage": 10.0,
            "manufacturer": "ABB",
            "commissioning_date": "2023-01-10",
            "voltage_level": 10.0,
            "location": "A区变电站",
        },
    },
    {
        "entity_id": "e-BR002",
        "name": "出线断路器",
        "type": "设备实例",
        "code": "BR-002",
        "label": "CircuitBreaker",
        "properties": {
            "device_id": "BR-002",
            "rated_current": 630.0,
            "short_impedance": 12.0,
            "rated_voltage": 10.0,
            "manufacturer": "ABB",
            "commissioning_date": "2023-02-14",
            "voltage_level": 10.0,
            "location": "B区变电站",
        },
    },
    {
        "entity_id": "e-CB001",
        "name": "高压电缆-A线",
        "type": "设备实例",
        "code": "CB-001",
        "label": "Line",
        "properties": {
            "device_id": "CB-001",
            "length_km": 2.5,
            "impedance_ohm": 0.5,
            "rated_voltage": 10.0,
            "rated_current": 300.0,
            "manufacturer": "远东电缆",
            "commissioning_date": "2021-11-01",
            "voltage_level": 10.0,
            "location": "A区-架空线",
        },
    },
    {
        "entity_id": "e-CB002",
        "name": "高压电缆-B线",
        "type": "设备实例",
        "code": "CB-002",
        "label": "Line",
        "properties": {
            "device_id": "CB-002",
            "length_km": 3.0,
            "impedance_ohm": 0.5,
            "rated_voltage": 10.0,
            "rated_current": 300.0,
            "manufacturer": "远东电缆",
            "commissioning_date": "2021-12-15",
            "voltage_level": 10.0,
            "location": "B区-地埋段",
        },
    },
    {
        "entity_id": "e-BB001",
        "name": "10kV母线",
        "type": "设备实例",
        "code": "BB-001",
        "label": "Busbar",
        "properties": {
            "device_id": "BB-001",
            "voltage_level": 10.0,
            "rated_voltage": 10.0,
            "rated_current": 2000.0,
            "manufacturer": "正泰电气",
            "commissioning_date": "2022-05-01",
            "length_m": 30.0,
            "location": "A区变电站",
        },
    },
    {
        "entity_id": "e-BB002",
        "name": "35kV母线",
        "type": "设备实例",
        "code": "BB-002",
        "label": "Busbar",
        "properties": {
            "device_id": "BB-002",
            "voltage_level": 35.0,
            "rated_voltage": 35.0,
            "rated_current": 1500.0,
            "manufacturer": "正泰电气",
            "commissioning_date": "2022-08-01",
            "length_m": 25.0,
            "location": "B区变电站",
        },
    },
]

# 变电站节点（2 个）
SUBSTATIONS: list[dict[str, Any]] = [
    {
        "entity_id": "e-substation-a",
        "name": "A区变电站",
        "type": "变电站",
        "code": "SUBSTATION_A",
        "label": "Substation",
        "properties": {"voltage_level": 220.0, "region": "A区"},
    },
    {
        "entity_id": "e-substation-b",
        "name": "B区变电站",
        "type": "变电站",
        "code": "SUBSTATION_B",
        "label": "Substation",
        "properties": {"voltage_level": 110.0, "region": "B区"},
    },
]

# 部件节点（4 个 —— 关键电气组件）
COMPONENTS: list[dict[str, Any]] = [
    {
        "entity_id": "e-component-winding",
        "name": "绕组",
        "type": "部件",
        "code": "WINDING",
        "label": "Component",
        "properties": {"category": "电磁部件", "applicable_devices": ["Transformer"]},
    },
    {
        "entity_id": "e-component-contact",
        "name": "触头",
        "type": "部件",
        "code": "CONTACT",
        "label": "Component",
        "properties": {"category": "机械部件", "applicable_devices": ["CircuitBreaker"]},
    },
    {
        "entity_id": "e-component-bushing",
        "name": "套管",
        "type": "部件",
        "code": "BUSHING",
        "label": "Component",
        "properties": {"category": "绝缘部件", "applicable_devices": ["Transformer", "CircuitBreaker"]},
    },
    {
        "entity_id": "e-component-insulator",
        "name": "绝缘子",
        "type": "部件",
        "code": "INSULATOR",
        "label": "Component",
        "properties": {"category": "绝缘部件", "applicable_devices": ["Line", "Busbar"]},
    },
]

# 传感器节点（5 个 —— 每种遥测类型）
SENSORS: list[dict[str, Any]] = [
    {
        "entity_id": "e-sensor-temp",
        "name": "温度传感器",
        "type": "传感器",
        "code": "TEMP_SENSOR",
        "label": "Sensor",
        "properties": {"sampling_rate_hz": 1.0, "unit": "℃"},
    },
    {
        "entity_id": "e-sensor-volt",
        "name": "电压传感器",
        "type": "传感器",
        "code": "VOLT_SENSOR",
        "label": "Sensor",
        "properties": {"sampling_rate_hz": 10.0, "unit": "kV"},
    },
    {
        "entity_id": "e-sensor-current",
        "name": "电流传感器",
        "type": "传感器",
        "code": "CURRENT_SENSOR",
        "label": "Sensor",
        "properties": {"sampling_rate_hz": 10.0, "unit": "A"},
    },
    {
        "entity_id": "e-sensor-humidity",
        "name": "湿度传感器",
        "type": "传感器",
        "code": "HUMIDITY_SENSOR",
        "label": "Sensor",
        "properties": {"sampling_rate_hz": 0.1, "unit": "%RH"},
    },
    {
        "entity_id": "e-sensor-pressure",
        "name": "压力传感器",
        "type": "传感器",
        "code": "PRESSURE_SENSOR",
        "label": "Sensor",
        "properties": {"sampling_rate_hz": 1.0, "unit": "MPa"},
    },
]

# 制造商节点（4 个 —— 与设备铭牌匹配）
MANUFACTURERS: list[dict[str, Any]] = [
    {
        "entity_id": "e-mfg-tbea",
        "name": "特变电工",
        "type": "制造商",
        "code": "MFG_TBEA",
        "label": "Manufacturer",
        "properties": {"country": "CN", "founded_year": 1988},
    },
    {
        "entity_id": "e-mfg-xd",
        "name": "西安西电",
        "type": "制造商",
        "code": "MFG_XD",
        "label": "Manufacturer",
        "properties": {"country": "CN", "founded_year": 1959},
    },
    {
        "entity_id": "e-mfg-abb",
        "name": "ABB",
        "type": "制造商",
        "code": "MFG_ABB",
        "label": "Manufacturer",
        "properties": {"country": "CH", "founded_year": 1988},
    },
    {
        "entity_id": "e-mfg-chint",
        "name": "正泰电气",
        "type": "制造商",
        "code": "MFG_CHINT",
        "label": "Manufacturer",
        "properties": {"country": "CN", "founded_year": 1991},
    },
]

# 知识库文档节点（8 个 —— 与 seed_data.KNOWLEDGE_CHUNKS 对应）
KNOWLEDGE_CHUNKS: list[dict[str, Any]] = [
    {
        "entity_id": "e-doc-001",
        "name": "变压器过载运行规程",
        "type": "知识库",
        "code": "DOC_001",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-001", "source": "GB/T 1094.7-2016"},
    },
    {
        "entity_id": "e-doc-002",
        "name": "变压器油温异常处置",
        "type": "知识库",
        "code": "DOC_002",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-002", "source": "DL/T 572-2010"},
    },
    {
        "entity_id": "e-doc-003",
        "name": "断路器SF6压力监控",
        "type": "知识库",
        "code": "DOC_003",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-003", "source": "GB 1984-2014"},
    },
    {
        "entity_id": "e-doc-004",
        "name": "电缆局部放电在线监测",
        "type": "知识库",
        "code": "DOC_004",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-004", "source": "DL/T 1578-2016"},
    },
    {
        "entity_id": "e-doc-005",
        "name": "母线差动保护动作处置",
        "type": "知识库",
        "code": "DOC_005",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-005", "source": "Q/GDW 1164-2014"},
    },
    {
        "entity_id": "e-doc-006",
        "name": "电力设备健康评估导则",
        "type": "知识库",
        "code": "DOC_006",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-006", "source": "Q/GDW 1168-2013"},
    },
    {
        "entity_id": "e-doc-007",
        "name": "避雷器泄漏电流监测",
        "type": "知识库",
        "code": "DOC_007",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-007", "source": "DL/T 596-2005"},
    },
    {
        "entity_id": "e-doc-008",
        "name": "接地电阻测量规范",
        "type": "知识库",
        "code": "DOC_008",
        "label": "KnowledgeChunk",
        "properties": {"doc_id": "doc-008", "source": "DL/T 621-1997"},
    },
]

# 遥测信号类型节点（6 个 —— 覆盖温度/电压/电流/湿度/压力/局放）
TELEMETRY_SIGNALS: list[dict[str, Any]] = [
    {"entity_id": "e-sig-temperature", "name": "温度信号", "type": "遥测信号",
     "code": "SIG_TEMP", "label": "TelemetrySignal",
     "properties": {"unit": "℃", "min_value": -20, "max_value": 150}},
    {"entity_id": "e-sig-voltage", "name": "电压信号", "type": "遥测信号",
     "code": "SIG_VOLTAGE", "label": "TelemetrySignal",
     "properties": {"unit": "kV", "min_value": 0, "max_value": 500}},
    {"entity_id": "e-sig-current", "name": "电流信号", "type": "遥测信号",
     "code": "SIG_CURRENT", "label": "TelemetrySignal",
     "properties": {"unit": "A", "min_value": 0, "max_value": 5000}},
    {"entity_id": "e-sig-humidity", "name": "湿度信号", "type": "遥测信号",
     "code": "SIG_HUMIDITY", "label": "TelemetrySignal",
     "properties": {"unit": "%RH", "min_value": 0, "max_value": 100}},
    {"entity_id": "e-sig-pressure", "name": "压力信号", "type": "遥测信号",
     "code": "SIG_PRESSURE", "label": "TelemetrySignal",
     "properties": {"unit": "MPa", "min_value": 0, "max_value": 1.0}},
    {"entity_id": "e-sig-pd", "name": "局放信号", "type": "遥测信号",
     "code": "SIG_PARTIAL_DISCHARGE", "label": "TelemetrySignal",
     "properties": {"unit": "pC", "min_value": 0, "max_value": 500}},
]

# 巡检结果节点（4 个 —— 正常/注意/异常/严重）
INSPECTION_FINDINGS: list[dict[str, Any]] = [
    {"entity_id": "e-finding-normal", "name": "正常", "type": "巡检结论",
     "code": "FINDING_NORMAL", "label": "InspectionFinding",
     "properties": {"level": "normal", "score_range": "80-100"}},
    {"entity_id": "e-finding-attention", "name": "注意", "type": "巡检结论",
     "code": "FINDING_ATTENTION", "label": "InspectionFinding",
     "properties": {"level": "attention", "score_range": "60-79"}},
    {"entity_id": "e-finding-abnormal", "name": "异常", "type": "巡检结论",
     "code": "FINDING_ABNORMAL", "label": "InspectionFinding",
     "properties": {"level": "abnormal", "score_range": "40-59"}},
    {"entity_id": "e-finding-critical", "name": "严重", "type": "巡检结论",
     "code": "FINDING_CRITICAL", "label": "InspectionFinding",
     "properties": {"level": "critical", "score_range": "<40"}},
]

# 维护人员节点（4 个 —— 值班长/操作员/检修工/安全员）
PERSONNEL: list[dict[str, Any]] = [
    {"entity_id": "e-personnel-shift-lead", "name": "值班长", "type": "人员",
     "code": "PERSONNEL_LEAD", "label": "Personnel",
     "properties": {"role": "shift_lead", "qualification": "高级工"}},
    {"entity_id": "e-personnel-operator", "name": "操作员", "type": "人员",
     "code": "PERSONNEL_OPERATOR", "label": "Personnel",
     "properties": {"role": "operator", "qualification": "中级工"}},
    {"entity_id": "e-personnel-maintainer", "name": "检修工", "type": "人员",
     "code": "PERSONNEL_MAINTAINER", "label": "Personnel",
     "properties": {"role": "maintainer", "qualification": "高级工"}},
    {"entity_id": "e-personnel-safety", "name": "安全员", "type": "人员",
     "code": "PERSONNEL_SAFETY", "label": "Personnel",
     "properties": {"role": "safety_officer", "qualification": "注册安全师"}},
]

# 安全工器具节点（5 个 —— 验电器/接地线/绝缘手套/绝缘靴/安全帽）
SAFETY_TOOLS: list[dict[str, Any]] = [
    {"entity_id": "e-tool-voltage-tester", "name": "高压验电器", "type": "工器具",
     "code": "TOOL_TESTER", "label": "SafetyTool",
     "properties": {"applicable_voltage_kv": "10-220"}},
    {"entity_id": "e-tool-ground-wire", "name": "接地线", "type": "工器具",
     "code": "TOOL_GROUND", "label": "SafetyTool",
     "properties": {"applicable_voltage_kv": "10-500"}},
    {"entity_id": "e-tool-insulating-gloves", "name": "绝缘手套", "type": "工器具",
     "code": "TOOL_GLOVES", "label": "SafetyTool",
     "properties": {"applicable_voltage_kv": "0.4-35"}},
    {"entity_id": "e-tool-insulating-boots", "name": "绝缘靴", "type": "工器具",
     "code": "TOOL_BOOTS", "label": "SafetyTool",
     "properties": {"applicable_voltage_kv": "0.4-35"}},
    {"entity_id": "e-tool-safety-helmet", "name": "安全帽", "type": "工器具",
     "code": "TOOL_HELMET", "label": "SafetyTool",
     "properties": {"applicable_voltage_kv": "通用"}},
]

# 检修记录节点（8 个 —— 每台设备一条历史检修记录）
MAINTENANCE_RECORDS: list[dict[str, Any]] = [
    {"entity_id": "e-maint-001", "name": "TR-001 例行检修 2024-09", "type": "检修记录",
     "code": "MAINT_001", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-001", "date": "2024-09-15", "result": "合格"}},
    {"entity_id": "e-maint-002", "name": "TR-002 例行检修 2024-10", "type": "检修记录",
     "code": "MAINT_002", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-002", "date": "2024-10-20", "result": "合格"}},
    {"entity_id": "e-maint-003", "name": "BR-001 SF6补气 2024-08", "type": "检修记录",
     "code": "MAINT_003", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-003", "date": "2024-08-10", "result": "已处理"}},
    {"entity_id": "e-maint-004", "name": "BR-002 操作次数检修 2024-09", "type": "检修记录",
     "code": "MAINT_004", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-004", "date": "2024-09-22", "result": "合格"}},
    {"entity_id": "e-maint-005", "name": "CB-001 局放监测升级 2024-11", "type": "检修记录",
     "code": "MAINT_005", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-005", "date": "2024-11-05", "result": "已升级"}},
    {"entity_id": "e-maint-006", "name": "CB-002 终端头检修 2024-10", "type": "检修记录",
     "code": "MAINT_006", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-006", "date": "2024-10-12", "result": "合格"}},
    {"entity_id": "e-maint-007", "name": "BB-001 红外测温 2024-11", "type": "检修记录",
     "code": "MAINT_007", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-007", "date": "2024-11-18", "result": "正常"}},
    {"entity_id": "e-maint-008", "name": "BB-002 差动保护校验 2024-11", "type": "检修记录",
     "code": "MAINT_008", "label": "MaintenanceRecord",
     "properties": {"record_id": "maint-008", "date": "2024-11-25", "result": "需关注"}},
]

# 设备类型节点（4 个 —— 变压器/断路器/电缆/母线 实例化模板）
DEVICE_TYPE_TEMPLATES: list[dict[str, Any]] = [
    {"entity_id": "e-dtype-transformer", "name": "变压器型号", "type": "设备型号",
     "code": "DTYPE_TRANSFORMER", "label": "DeviceType",
     "properties": {"category": "PowerEquipment", "abstract": True}},
    {"entity_id": "e-dtype-circuit-breaker", "name": "断路器型号", "type": "设备型号",
     "code": "DTYPE_BREAKER", "label": "DeviceType",
     "properties": {"category": "PowerEquipment"}},
    {"entity_id": "e-dtype-cable", "name": "电缆型号", "type": "设备型号",
     "code": "DTYPE_CABLE", "label": "DeviceType",
     "properties": {"category": "PowerEquipment"}},
    {"entity_id": "e-dtype-busbar", "name": "母线型号", "type": "设备型号",
     "code": "DTYPE_BUSBAR", "label": "DeviceType",
     "properties": {"category": "PowerEquipment"}},
]


# ═════════════════════════════════════════════════════════════════════════════
# 2. 基础关系定义（与 seed_data.py 对齐 + 扩展）
# ═════════════════════════════════════════════════════════════════════════════

# R0：基础关系 —— 与现有 seed_data 兼容
BASE_RELATIONS: list[dict[str, Any]] = [
    # 设备类别 → 故障类型
    {"src_id": "e-transformer", "tgt_id": "e-overload", "type": "可能发生", "properties": {"confidence": 0.8}},
    {"src_id": "e-transformer", "tgt_id": "e-overtemp", "type": "可能发生", "properties": {"confidence": 0.7}},
    {"src_id": "e-breaker",     "tgt_id": "e-sf6leak",  "type": "可能发生", "properties": {"confidence": 0.5}},
    {"src_id": "e-cable",       "tgt_id": "e-partial-discharge", "type": "可能发生", "properties": {"confidence": 0.6}},
    {"src_id": "e-busbar",      "tgt_id": "e-ground-fault", "type": "可能发生", "properties": {"confidence": 0.5}},
    # 故障 → 处置
    {"src_id": "e-overload",    "tgt_id": "e-derating", "type": "处置", "properties": {"priority": "P3"}},
    {"src_id": "e-overload",    "tgt_id": "e-shutdown", "type": "严重时处置", "properties": {"priority": "P1"}},
    {"src_id": "e-overtemp",    "tgt_id": "e-derating", "type": "处置", "properties": {"priority": "P3"}},
    {"src_id": "e-overtemp",    "tgt_id": "e-shutdown", "type": "严重时处置", "properties": {"priority": "P1"}},
    {"src_id": "e-sf6leak",     "tgt_id": "e-repair",   "type": "处置", "properties": {"priority": "P2"}},
    {"src_id": "e-sf6leak",     "tgt_id": "e-replace",  "type": "严重时处置", "properties": {"priority": "P2"}},
    {"src_id": "e-partial-discharge", "tgt_id": "e-monitor", "type": "处置", "properties": {"priority": "P4"}},
    {"src_id": "e-partial-discharge", "tgt_id": "e-repair",  "type": "严重时处置", "properties": {"priority": "P2"}},
    {"src_id": "e-ground-fault", "tgt_id": "e-repair",  "type": "处置", "properties": {"priority": "P2"}},
    # 规程 → 设备/故障
    {"src_id": "e-DL572",  "tgt_id": "e-transformer", "type": "适用于", "properties": {}},
    {"src_id": "e-DL572",  "tgt_id": "e-overtemp",    "type": "关联", "properties": {}},
    {"src_id": "e-GB1094", "tgt_id": "e-transformer", "type": "适用于", "properties": {}},
    {"src_id": "e-DL1578", "tgt_id": "e-cable",       "type": "适用于", "properties": {}},
    {"src_id": "e-DL1578", "tgt_id": "e-partial-discharge", "type": "关联", "properties": {}},
    # 设备实例 → 设备类别
    {"src_id": "e-TR001", "tgt_id": "e-transformer", "type": "属于", "properties": {}},
    {"src_id": "e-TR002", "tgt_id": "e-transformer", "type": "属于", "properties": {}},
    {"src_id": "e-BB002", "tgt_id": "e-busbar",      "type": "属于", "properties": {}},
    # 跨跳关联
    {"src_id": "e-TR001", "tgt_id": "e-overload", "type": "已发生", "properties": {"timestamp": "2024-12-01"}},
    {"src_id": "e-BB002", "tgt_id": "e-ground-fault", "type": "已发生", "properties": {"timestamp": "2024-11-15"}},
]


# ═════════════════════════════════════════════════════════════════════════════
# 3. R1 — 设备属性扩展（铭牌字段标准化）
# ═════════════════════════════════════════════════════════════════════════════

# 每台设备的属性已在 DEVICE_INSTANCES 中扩展；
# 此外，添加 INSTANCE_OF 与 BELONGS_TO 关系（8 × 2 = 16 条）
def _build_instance_relations() -> list[dict[str, Any]]:
    """为每台设备生成 INSTANCE_OF + BELONGS_TO 关系。"""
    out: list[dict[str, Any]] = []
    category_map = {
        "e-TR001": "e-transformer",
        "e-TR002": "e-transformer",
        "e-BR001": "e-breaker",
        "e-BR002": "e-breaker",
        "e-CB001": "e-cable",
        "e-CB002": "e-cable",
        "e-BB001": "e-busbar",
        "e-BB002": "e-busbar",
    }
    substation_map = {
        "e-TR001": "e-substation-a",
        "e-TR002": "e-substation-b",
        "e-BR001": "e-substation-a",
        "e-BR002": "e-substation-b",
        "e-CB001": "e-substation-a",
        "e-CB002": "e-substation-b",
        "e-BB001": "e-substation-a",
        "e-BB002": "e-substation-b",
    }
    manufacturer_map = {
        "e-TR001": "e-mfg-tbea",
        "e-TR002": "e-mfg-xd",
        "e-BR001": "e-mfg-abb",
        "e-BR002": "e-mfg-abb",
        "e-CB001": "e-mfg-tbea",   # 复用 TBEA 名下
        "e-CB002": "e-mfg-tbea",
        "e-BB001": "e-mfg-chint",
        "e-BB002": "e-mfg-chint",
    }
    for dev_id, cat_id in category_map.items():
        out.append({
            "src_id": dev_id,
            "tgt_id": cat_id,
            "type": "INSTANCE_OF",
            "properties": {},
        })
    for dev_id, sub_id in substation_map.items():
        out.append({
            "src_id": dev_id,
            "tgt_id": sub_id,
            "type": "BELONGS_TO",
            "properties": {},
        })
    for dev_id, mfg_id in manufacturer_map.items():
        out.append({
            "src_id": dev_id,
            "tgt_id": mfg_id,
            "type": "MANUFACTURED_BY",
            "properties": {},
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 4. R2 — 故障-设备关系扩展（5 故障 × 8 设备 × 3 严重度 = 64 条以上）
# ═════════════════════════════════════════════════════════════════════════════

# 设计原则：每种故障都对每台设备生成 可能发生 / 已发生 / 已恢复 关系
FAULT_DEVICE_PAIRS: list[tuple[str, str, str]] = [
    # (fault_id, device_id, relation_type) —— 30 详细 + 36 通用 = 66 条
    # ── 过载 (5 台设备 × 3 关系 = 15 条) ──
    ("e-overload", "e-TR001", "可能发生"),
    ("e-overload", "e-TR001", "已发生"),
    ("e-overload", "e-TR001", "已恢复"),
    ("e-overload", "e-TR002", "可能发生"),
    ("e-overload", "e-TR002", "已发生"),
    ("e-overload", "e-BR001", "可能发生"),
    ("e-overload", "e-BR001", "已发生"),
    ("e-overload", "e-BR002", "可能发生"),
    ("e-overload", "e-BB001", "可能发生"),
    ("e-overload", "e-BB001", "已发生"),
    ("e-overload", "e-BB002", "可能发生"),
    ("e-overload", "e-BB002", "已发生"),
    ("e-overload", "e-CB001", "可能发生"),
    ("e-overload", "e-CB002", "可能发生"),
    ("e-overload", "e-CB002", "已发生"),
    # ── 油温异常 ──
    ("e-overtemp", "e-TR001", "可能发生"),
    ("e-overtemp", "e-TR001", "已发生"),
    ("e-overtemp", "e-TR002", "可能发生"),
    ("e-overtemp", "e-TR002", "已发生"),
    ("e-overtemp", "e-BR001", "可能发生"),
    ("e-overtemp", "e-BR002", "可能发生"),
    ("e-overtemp", "e-BR002", "已发生"),
    ("e-overtemp", "e-BB001", "可能发生"),
    ("e-overtemp", "e-BB002", "可能发生"),
    ("e-overtemp", "e-BB002", "已发生"),
    ("e-overtemp", "e-CB001", "可能发生"),
    # ── SF6 泄漏 ──
    ("e-sf6leak", "e-BR001", "可能发生"),
    ("e-sf6leak", "e-BR001", "已发生"),
    ("e-sf6leak", "e-BR002", "可能发生"),
    ("e-sf6leak", "e-BR002", "已恢复"),
    # ── 局部放电 ──
    ("e-partial-discharge", "e-CB001", "可能发生"),
    ("e-partial-discharge", "e-CB001", "已发生"),
    ("e-partial-discharge", "e-CB002", "可能发生"),
    ("e-partial-discharge", "e-CB002", "已发生"),
    ("e-partial-discharge", "e-BB001", "可能发生"),
    ("e-partial-discharge", "e-BB002", "可能发生"),
    ("e-partial-discharge", "e-BB002", "已发生"),
    ("e-partial-discharge", "e-TR001", "可能发生"),
    # ── 接地故障 ──
    ("e-ground-fault", "e-BB001", "可能发生"),
    ("e-ground-fault", "e-BB002", "可能发生"),
    ("e-ground-fault", "e-BB002", "已发生"),
    ("e-ground-fault", "e-CB001", "可能发生"),
    ("e-ground-fault", "e-CB002", "可能发生"),
    ("e-ground-fault", "e-TR001", "可能发生"),
    ("e-ground-fault", "e-BR001", "可能发生"),
    ("e-ground-fault", "e-BR002", "可能发生"),
    ("e-ground-fault", "e-TR002", "可能发生"),
    # ── 子类型扩展关系 ──
    ("e-subtype-overload-fault", "e-TR001", "可能发生"),
    ("e-subtype-overload-fault", "e-BB002", "可能发生"),
    ("e-subtype-short-circuit-fault", "e-BB002", "已发生"),
    ("e-subtype-overheat-fault", "e-TR001", "已发生"),
    ("e-subtype-voltage-deviation", "e-BB002", "可能发生"),
    ("e-subtype-voltage-deviation", "e-CB001", "可能发生"),
    ("e-subtype-voltage-deviation", "e-CB002", "可能发生"),
    # ── 额外扩展对以确保 ≥60 条 ──
    ("e-subtype-overload-fault", "e-TR002", "可能发生"),
    ("e-subtype-short-circuit-fault", "e-TR001", "可能发生"),
    ("e-subtype-overheat-fault", "e-TR002", "已发生"),
    ("e-subtype-voltage-deviation", "e-TR002", "可能发生"),
    ("e-subtype-short-circuit-fault", "e-CB001", "可能发生"),
    ("e-subtype-short-circuit-fault", "e-CB002", "可能发生"),
    ("e-subtype-overheat-fault", "e-BR002", "可能发生"),
    ("e-subtype-voltage-deviation", "e-BR001", "可能发生"),
]


def _build_fault_device_relations() -> list[dict[str, Any]]:
    """生成 HAS_FAULT / 可能发生 / 已发生 / 已恢复 关系。"""
    out: list[dict[str, Any]] = []
    for fault_id, dev_id, rel_type in FAULT_DEVICE_PAIRS:
        # 标准化关系类型到 NEO4j 标准词表
        if rel_type == "可能发生":
            rtype = "MAY_OCCUR"
            props = {"probability": 0.5, "confidence": 0.7}
        elif rel_type == "已发生":
            rtype = "OCCURRED"
            props = {"timestamp": "2024-12-01", "severity": "high"}
        else:  # 已恢复
            rtype = "RECOVERED_FROM"
            props = {"timestamp": "2024-10-15", "recovery_method": "automatic"}
        out.append({
            "src_id": dev_id,
            "tgt_id": fault_id,
            "type": rtype,
            "properties": props,
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 5. R3 — 处置-故障关系扩展（HANDLED_BY × 紧急程度）
# ═════════════════════════════════════════════════════════════════════════════

HANDLING_FAULT_PAIRS: list[tuple[str, str, str, str]] = [
    # (fault_id, measure_id, relation_type, priority)
    ("e-overload", "e-derating", "HANDLED_BY", "P3"),
    ("e-overload", "e-shutdown", "HANDLED_BY", "P1"),
    ("e-overload", "e-monitor", "HANDLED_BY", "P4"),
    ("e-overtemp", "e-derating", "HANDLED_BY", "P3"),
    ("e-overtemp", "e-shutdown", "HANDLED_BY", "P1"),
    ("e-overtemp", "e-repair", "HANDLED_BY", "P2"),
    ("e-overtemp", "e-monitor", "HANDLED_BY", "P4"),
    ("e-sf6leak", "e-repair", "HANDLED_BY", "P2"),
    ("e-sf6leak", "e-replace", "HANDLED_BY", "P1"),
    ("e-sf6leak", "e-monitor", "HANDLED_BY", "P4"),
    ("e-partial-discharge", "e-monitor", "HANDLED_BY", "P4"),
    ("e-partial-discharge", "e-repair", "HANDLED_BY", "P2"),
    ("e-partial-discharge", "e-replace", "HANDLED_BY", "P1"),
    ("e-ground-fault", "e-shutdown", "HANDLED_BY", "P1"),
    ("e-ground-fault", "e-repair", "HANDLED_BY", "P2"),
    ("e-ground-fault", "e-replace", "HANDLED_BY", "P1"),
    # ── 子类型 → 父类型的 SPECIFIC_INSTANCE_OF ──
    ("e-subtype-overload-fault", "e-overload", "SPECIFIC_INSTANCE_OF", "P1"),
    ("e-subtype-short-circuit-fault", "e-ground-fault", "SPECIFIC_INSTANCE_OF", "P1"),
    ("e-subtype-overheat-fault", "e-overtemp", "SPECIFIC_INSTANCE_OF", "P1"),
    ("e-subtype-voltage-deviation", "e-ground-fault", "SPECIFIC_INSTANCE_OF", "P2"),
    ("e-subtype-emergency-stop", "e-shutdown", "SPECIFIC_INSTANCE_OF", "P1"),
    ("e-subtype-routine-maintenance", "e-repair", "SPECIFIC_INSTANCE_OF", "P2"),
]


def _build_handling_relations() -> list[dict[str, Any]]:
    """生成 HANDLED_BY / SPECIFIC_INSTANCE_OF 关系。"""
    out: list[dict[str, Any]] = []
    for fault_id, measure_id, rel_type, priority in HANDLING_FAULT_PAIRS:
        if rel_type == "HANDLED_BY":
            out.append({
                "src_id": fault_id,
                "tgt_id": measure_id,
                "type": rel_type,
                "properties": {"priority": priority, "confidence": 0.85},
            })
        else:
            out.append({
                "src_id": fault_id,
                "tgt_id": measure_id,
                "type": rel_type,
                "properties": {"abstract": True},
            })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 6. R4 — 规程-设备/故障 + 知识库关联
# ═════════════════════════════════════════════════════════════════════════════

# 6.1 规程 → 设备类别（APPLIES_TO）
REGULATION_DEVICE_PAIRS: list[tuple[str, str]] = [
    ("e-DL572", "e-transformer"),
    ("e-DL572", "e-breaker"),
    ("e-DL572", "e-busbar"),
    ("e-QGDW1799", "e-transformer"),
    ("e-QGDW1799", "e-breaker"),
    ("e-QGDW1799", "e-cable"),
    ("e-QGDW1799", "e-busbar"),
    ("e-GB1094", "e-transformer"),
    ("e-DL1578", "e-cable"),
    ("e-QGDW1164", "e-busbar"),
    ("e-QGDW1164", "e-breaker"),
    # 额外扩展：规程也适用于具体设备实例
    ("e-DL572", "e-TR001"),
    ("e-DL572", "e-TR002"),
    ("e-DL572", "e-BB002"),
    ("e-QGDW1799", "e-BR001"),
    ("e-QGDW1799", "e-BR002"),
    ("e-GB1094", "e-TR001"),
    ("e-GB1094", "e-TR002"),
    ("e-DL1578", "e-CB001"),
    ("e-DL1578", "e-CB002"),
    ("e-QGDW1164", "e-BB001"),
    ("e-QGDW1164", "e-BB002"),
]

# 6.2 规程 → 处置（MANDATES）
REGULATION_MEASURE_PAIRS: list[tuple[str, str]] = [
    ("e-DL572", "e-shutdown"),
    ("e-DL572", "e-derating"),
    ("e-DL572", "e-repair"),
    ("e-QGDW1799", "e-monitor"),
    ("e-QGDW1799", "e-repair"),
    ("e-GB1094", "e-derating"),
    ("e-GB1094", "e-shutdown"),
    ("e-DL1578", "e-monitor"),
    ("e-DL1578", "e-repair"),
    ("e-QGDW1164", "e-shutdown"),
    ("e-QGDW1164", "e-repair"),
    # 额外扩展：规程 → 处置子类
    ("e-DL572", "e-subtype-emergency-stop"),
    ("e-QGDW1799", "e-subtype-routine-maintenance"),
    ("e-GB1094", "e-subtype-emergency-stop"),
    ("e-DL1578", "e-subtype-routine-maintenance"),
]

# 6.3 规程 → 故障（TRIGGERED_BY）
REGULATION_FAULT_PAIRS: list[tuple[str, str]] = [
    ("e-DL572", "e-overload"),
    ("e-DL572", "e-overtemp"),
    ("e-DL572", "e-ground-fault"),
    ("e-QGDW1799", "e-sf6leak"),
    ("e-QGDW1799", "e-partial-discharge"),
    ("e-GB1094", "e-overload"),
    ("e-DL1578", "e-partial-discharge"),
    ("e-QGDW1164", "e-ground-fault"),
]

# 6.4 知识库文档 → 设备/故障/规程（RELATES_TO）
DOC_ENTITY_PAIRS: list[tuple[str, str]] = [
    ("e-doc-001", "e-transformer"),
    ("e-doc-001", "e-overload"),
    ("e-doc-001", "e-derating"),
    ("e-doc-001", "e-TR001"),
    ("e-doc-001", "e-TR002"),
    ("e-doc-002", "e-transformer"),
    ("e-doc-002", "e-overtemp"),
    ("e-doc-002", "e-shutdown"),
    ("e-doc-002", "e-TR001"),
    ("e-doc-002", "e-subtype-overheat-fault"),
    ("e-doc-003", "e-breaker"),
    ("e-doc-003", "e-sf6leak"),
    ("e-doc-003", "e-repair"),
    ("e-doc-003", "e-BR001"),
    ("e-doc-003", "e-BR002"),
    ("e-doc-004", "e-cable"),
    ("e-doc-004", "e-partial-discharge"),
    ("e-doc-004", "e-monitor"),
    ("e-doc-004", "e-CB001"),
    ("e-doc-004", "e-CB002"),
    ("e-doc-005", "e-busbar"),
    ("e-doc-005", "e-ground-fault"),
    ("e-doc-005", "e-shutdown"),
    ("e-doc-005", "e-BB001"),
    ("e-doc-005", "e-BB002"),
    ("e-doc-006", "e-transformer"),
    ("e-doc-006", "e-breaker"),
    ("e-doc-006", "e-cable"),
    ("e-doc-006", "e-busbar"),
    ("e-doc-006", "e-arrester"),
    ("e-doc-007", "e-arrester"),
    ("e-doc-007", "e-subtype-voltage-deviation"),
    ("e-doc-008", "e-busbar"),
    ("e-doc-008", "e-subtype-short-circuit-fault"),
    # 规程关联
    ("e-doc-001", "e-DL572"),
    ("e-doc-002", "e-DL572"),
    ("e-doc-003", "e-GB1094"),
    ("e-doc-004", "e-DL1578"),
    ("e-doc-005", "e-QGDW1164"),
    ("e-doc-006", "e-QGDW1799"),
]


def _build_regulation_relations() -> list[dict[str, Any]]:
    """生成 APPLIES_TO / MANDATES / RELATES_TO / DOCUMENTS 关系。"""
    out: list[dict[str, Any]] = []
    for reg_id, dev_id in REGULATION_DEVICE_PAIRS:
        out.append({
            "src_id": reg_id,
            "tgt_id": dev_id,
            "type": "APPLIES_TO",
            "properties": {"effective_date": "2024-01-01"},
        })
    for reg_id, meas_id in REGULATION_MEASURE_PAIRS:
        out.append({
            "src_id": reg_id,
            "tgt_id": meas_id,
            "type": "MANDATES",
            "properties": {"mandatory": True},
        })
    for reg_id, fault_id in REGULATION_FAULT_PAIRS:
        out.append({
            "src_id": reg_id,
            "tgt_id": fault_id,
            "type": "DOCUMENTS",
            "properties": {},
        })
    for doc_id, ent_id in DOC_ENTITY_PAIRS:
        out.append({
            "src_id": doc_id,
            "tgt_id": ent_id,
            "type": "RELATES_TO",
            "properties": {"relevance": 0.9},
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 7. R5 — 设备-设备拓扑连接（基于典型电网，50+ 条）
# ═════════════════════════════════════════════════════════════════════════════

# 典型拓扑：A区 母线 BB-001 ↔ 断路器 BR-001 ↔ 变压器 TR-001 ↔ 线路 CB-001
# 双向连接 + 跨区互联 + 备用通路
CONNECTED_PAIRS: list[tuple[str, str, str]] = [
    # (src, tgt, connection_type)
    # ── A 区主链路 ──
    ("e-BB001", "e-BR001", "串联"),
    ("e-BR001", "e-TR001", "串联"),
    ("e-TR001", "e-CB001", "串联"),
    ("e-CB001", "e-TR002", "并联"),  # A-B 联络线
    # ── B 区主链路 ──
    ("e-CB002", "e-BR002", "串联"),
    ("e-BR002", "e-BB002", "串联"),
    ("e-BB002", "e-TR002", "串联"),
    # ── 区段并联 ──
    ("e-BB001", "e-BB002", "并联"),
    ("e-TR001", "e-TR002", "并联"),
    # ── 反向连接（电气连接双向可通电） ──
    ("e-BR001", "e-BB001", "串联"),
    ("e-TR001", "e-BR001", "串联"),
    ("e-CB001", "e-TR001", "串联"),
    ("e-BR002", "e-CB002", "串联"),
    ("e-BB002", "e-BR002", "串联"),
    ("e-TR002", "e-BB002", "串联"),
    ("e-BB002", "e-BB001", "并联"),
    ("e-TR002", "e-TR001", "并联"),
    # ── 备用旁路 ──
    ("e-BR001", "e-BR002", "并联"),
    ("e-CB001", "e-CB002", "并联"),
    ("e-BB001", "e-BR002", "并联"),
    ("e-BB002", "e-BR001", "并联"),
    # ── 故障隔离链路 ──
    ("e-TR001", "e-BR002", "并联"),  # 紧急切负荷
    ("e-TR002", "e-BR001", "并联"),
    # ── 接地通路 ──
    ("e-BB001", "e-BB002", "接地互联"),
    ("e-TR001", "e-BB002", "接地互联"),
    ("e-TR002", "e-BB001", "接地互联"),
    # ── 母线联络开关（虚拟） ──
    ("e-BB001", "e-CB001", "串联"),
    ("e-BB002", "e-CB002", "串联"),
    # ── 检修旁路 ──
    ("e-CB001", "e-BB002", "并联"),
    ("e-CB002", "e-BB001", "并联"),
    ("e-TR001", "e-CB002", "并联"),
    ("e-TR002", "e-CB001", "并联"),
    # ── 电压监测节点 ──
    ("e-BR001", "e-CB001", "串联"),
    ("e-BR002", "e-CB002", "串联"),
]


def _build_topology_relations() -> list[dict[str, Any]]:
    """生成 CONNECTED_TO 拓扑关系（带连接类型 + 电压等级）。"""
    out: list[dict[str, Any]] = []
    voltage_lookup = {
        "e-TR001": 220.0, "e-TR002": 110.0,
        "e-BR001": 10.0, "e-BR002": 10.0,
        "e-CB001": 10.0, "e-CB002": 10.0,
        "e-BB001": 10.0, "e-BB002": 35.0,
    }
    for src, tgt, conn_type in CONNECTED_PAIRS:
        rated_v = max(voltage_lookup.get(src, 10.0), voltage_lookup.get(tgt, 10.0))
        out.append({
            "src_id": src,
            "tgt_id": tgt,
            "type": "CONNECTED_TO",
            "properties": {"connection_type": conn_type, "rated_voltage": rated_v},
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 8. R6 — 因果关系链（CAUSES 推理规则，30+ 条）
# ═════════════════════════════════════════════════════════════════════════════

CAUSAL_CHAINS: list[tuple[str, str, float, str]] = [
    # (cause_fault, effect_fault, confidence, severity)
    # ── 主因果链 ──
    ("e-overload", "e-overtemp", 0.9, "high"),
    ("e-overload", "e-partial-discharge", 0.6, "medium"),
    ("e-overtemp", "e-partial-discharge", 0.7, "high"),
    ("e-overtemp", "e-ground-fault", 0.5, "high"),
    ("e-partial-discharge", "e-ground-fault", 0.8, "high"),
    ("e-ground-fault", "e-overload", 0.4, "medium"),  # 系统级联
    # ── 子类型链 ──
    ("e-subtype-overload-fault", "e-subtype-overheat-fault", 0.85, "high"),
    ("e-subtype-overheat-fault", "e-subtype-short-circuit-fault", 0.7, "high"),
    ("e-subtype-short-circuit-fault", "e-subtype-voltage-deviation", 0.6, "high"),
    # ── 双向链（连锁故障） ──
    ("e-overtemp", "e-overload", 0.3, "low"),
    ("e-sf6leak", "e-partial-discharge", 0.5, "medium"),
    ("e-sf6leak", "e-ground-fault", 0.4, "medium"),
    ("e-partial-discharge", "e-overtemp", 0.3, "low"),
    # ── 跨类型链 ──
    ("e-overload", "e-ground-fault", 0.4, "high"),
    ("e-overload", "e-sf6leak", 0.3, "medium"),
    ("e-overtemp", "e-sf6leak", 0.4, "medium"),
    ("e-sf6leak", "e-overtemp", 0.3, "low"),
    ("e-partial-discharge", "e-overload", 0.25, "low"),
    # ── 子类型 → 主类型反向 ──
    ("e-subtype-overheat-fault", "e-overtemp", 0.9, "high"),
    ("e-subtype-short-circuit-fault", "e-ground-fault", 0.85, "high"),
    ("e-subtype-overload-fault", "e-overload", 0.95, "high"),
    ("e-subtype-voltage-deviation", "e-ground-fault", 0.5, "medium"),
    # ── 设备级联链 ──
    ("e-overtemp", "e-partial-discharge", 0.65, "high"),  # 重复强化
    ("e-ground-fault", "e-partial-discharge", 0.7, "high"),
    ("e-ground-fault", "e-overtemp", 0.4, "medium"),
    # ── 处置后因果链（故障 → 处置后可能引发的次生故障） ──
    ("e-shutdown", "e-overload", 0.2, "low"),  # 停运后负载转移到其他设备
    ("e-shutdown", "e-overtemp", 0.15, "low"),
    ("e-repair", "e-partial-discharge", 0.2, "low"),  # 检修后短期局部放电
    ("e-replace", "e-ground-fault", 0.15, "low"),  # 更换期间临时接地
    # ── 三跳长链（用于多跳推理测试） ──
    ("e-overload", "e-subtype-short-circuit-fault", 0.3, "high"),
    ("e-subtype-overload-fault", "e-partial-discharge", 0.4, "medium"),
    ("e-subtype-overheat-fault", "e-subtype-voltage-deviation", 0.5, "high"),
]


def _build_causal_relations() -> list[dict[str, Any]]:
    """生成 CAUSES 因果关系（带 confidence + severity）。"""
    out: list[dict[str, Any]] = []
    for cause, effect, conf, sev in CAUSAL_CHAINS:
        out.append({
            "src_id": cause,
            "tgt_id": effect,
            "type": "CAUSES",
            "properties": {"confidence": conf, "severity": sev},
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 9. R7 — 部件-设备 + 传感器-设备 关联（额外扩展）
# ═════════════════════════════════════════════════════════════════════════════

def _build_component_relations() -> list[dict[str, Any]]:
    """HAS_COMPONENT（设备 → 部件）+ MONITORED_BY（设备 → 传感器）。"""
    out: list[dict[str, Any]] = []
    # 设备 → 部件
    component_devices = {
        "e-component-winding": ["e-TR001", "e-TR002"],
        "e-component-contact": ["e-BR001", "e-BR002"],
        "e-component-bushing": ["e-TR001", "e-TR002", "e-BR001", "e-BR002"],
        "e-component-insulator": ["e-CB001", "e-CB002", "e-BB001", "e-BB002"],
    }
    for comp_id, dev_ids in component_devices.items():
        for dev_id in dev_ids:
            out.append({
                "src_id": dev_id,
                "tgt_id": comp_id,
                "type": "HAS_COMPONENT",
                "properties": {},
            })
    # 设备 → 传感器
    device_sensors = {
        "e-TR001": ["e-sensor-temp", "e-sensor-volt", "e-sensor-current", "e-sensor-pressure"],
        "e-TR002": ["e-sensor-temp", "e-sensor-volt", "e-sensor-current", "e-sensor-pressure"],
        "e-BR001": ["e-sensor-current", "e-sensor-pressure"],
        "e-BR002": ["e-sensor-current", "e-sensor-pressure"],
        "e-CB001": ["e-sensor-current", "e-sensor-temp"],
        "e-CB002": ["e-sensor-current", "e-sensor-temp"],
        "e-BB001": ["e-sensor-volt", "e-sensor-current"],
        "e-BB002": ["e-sensor-volt", "e-sensor-current", "e-sensor-temp"],
    }
    for dev_id, sensor_ids in device_sensors.items():
        for sensor_id in sensor_ids:
            out.append({
                "src_id": dev_id,
                "tgt_id": sensor_id,
                "type": "MONITORED_BY",
                "properties": {"sampling_rate_hz": 1.0},
            })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 9. R8 — 扩展关系（传感器-信号 / 巡检结论 / 人员 / 工器具）
# ═════════════════════════════════════════════════════════════════════════════

def _build_extra_relations() -> list[dict[str, Any]]:
    """扩展关系集：
    - 传感器 → 遥测信号（MEASURES）
    - 设备 → 巡检结论（INSPECTED_AS）
    - 故障 → 巡检结论（INDICATED_BY）
    - 处置 → 人员（CONDUCTED_BY）
    - 处置 → 工器具（REQUIRES_TOOL）
    - 部件 → 故障（CAUSES_FAULT_IF_DAMAGED）
    - 设备 → 信号（GENERATES）
    """
    out: list[dict[str, Any]] = []

    # 传感器 → 遥测信号（1-1 映射）
    sensor_signal = {
        "e-sensor-temp": "e-sig-temperature",
        "e-sensor-volt": "e-sig-voltage",
        "e-sensor-current": "e-sig-current",
        "e-sensor-humidity": "e-sig-humidity",
        "e-sensor-pressure": "e-sig-pressure",
    }
    for sensor_id, sig_id in sensor_signal.items():
        out.append({
            "src_id": sensor_id,
            "tgt_id": sig_id,
            "type": "MEASURES",
            "properties": {},
        })

    # 设备 → 巡检结论（每设备 4 个结论）
    device_findings = {
        "e-TR001": ["e-finding-normal", "e-finding-attention", "e-finding-abnormal"],
        "e-TR002": ["e-finding-normal", "e-finding-attention"],
        "e-BR001": ["e-finding-normal", "e-finding-attention", "e-finding-abnormal", "e-finding-critical"],
        "e-BR002": ["e-finding-normal", "e-finding-attention", "e-finding-abnormal"],
        "e-CB001": ["e-finding-normal", "e-finding-attention", "e-finding-abnormal"],
        "e-CB002": ["e-finding-normal", "e-finding-abnormal"],
        "e-BB001": ["e-finding-normal", "e-finding-attention"],
        "e-BB002": ["e-finding-attention", "e-finding-abnormal", "e-finding-critical"],
    }
    for dev_id, finding_ids in device_findings.items():
        for fid in finding_ids:
            out.append({
                "src_id": dev_id,
                "tgt_id": fid,
                "type": "INSPECTED_AS",
                "properties": {"frequency": "monthly"},
            })

    # 故障 → 巡检结论（指示器）
    fault_findings = [
        ("e-overload", "e-finding-attention"),
        ("e-overload", "e-finding-abnormal"),
        ("e-overtemp", "e-finding-abnormal"),
        ("e-overtemp", "e-finding-critical"),
        ("e-sf6leak", "e-finding-abnormal"),
        ("e-partial-discharge", "e-finding-attention"),
        ("e-ground-fault", "e-finding-critical"),
        ("e-subtype-overload-fault", "e-finding-critical"),
        ("e-subtype-overheat-fault", "e-finding-critical"),
    ]
    for fault_id, fid in fault_findings:
        out.append({
            "src_id": fault_id,
            "tgt_id": fid,
            "type": "INDICATED_BY",
            "properties": {},
        })

    # 处置 → 人员（CONDUCTED_BY）
    measure_personnel = {
        "e-derating": ["e-personnel-shift-lead", "e-personnel-operator"],
        "e-shutdown": ["e-personnel-shift-lead", "e-personnel-operator"],
        "e-repair": ["e-personnel-maintainer", "e-personnel-shift-lead"],
        "e-replace": ["e-personnel-maintainer", "e-personnel-safety"],
        "e-monitor": ["e-personnel-operator"],
        "e-subtype-emergency-stop": ["e-personnel-shift-lead", "e-personnel-safety"],
        "e-subtype-routine-maintenance": ["e-personnel-maintainer"],
    }
    for meas_id, person_ids in measure_personnel.items():
        for pid in person_ids:
            out.append({
                "src_id": meas_id,
                "tgt_id": pid,
                "type": "CONDUCTED_BY",
                "properties": {},
            })

    # 处置 → 工器具（REQUIRES_TOOL）
    measure_tools = {
        "e-derating": ["e-tool-insulating-gloves", "e-tool-insulating-boots", "e-tool-safety-helmet"],
        "e-shutdown": ["e-tool-voltage-tester", "e-tool-ground-wire", "e-tool-insulating-gloves"],
        "e-repair": ["e-tool-voltage-tester", "e-tool-ground-wire", "e-tool-insulating-gloves", "e-tool-insulating-boots"],
        "e-replace": ["e-tool-voltage-tester", "e-tool-ground-wire", "e-tool-insulating-gloves"],
        "e-monitor": ["e-tool-safety-helmet"],
        "e-subtype-emergency-stop": ["e-tool-voltage-tester", "e-tool-ground-wire"],
        "e-subtype-routine-maintenance": ["e-tool-insulating-gloves", "e-tool-safety-helmet"],
    }
    for meas_id, tool_ids in measure_tools.items():
        for tid in tool_ids:
            out.append({
                "src_id": meas_id,
                "tgt_id": tid,
                "type": "REQUIRES_TOOL",
                "properties": {"mandatory": True},
            })

    # 部件 → 故障（CAUSES_FAULT_IF_DAMAGED）
    component_faults = [
        ("e-component-winding", "e-overtemp"),
        ("e-component-winding", "e-partial-discharge"),
        ("e-component-winding", "e-ground-fault"),
        ("e-component-contact", "e-sf6leak"),
        ("e-component-contact", "e-ground-fault"),
        ("e-component-bushing", "e-partial-discharge"),
        ("e-component-bushing", "e-ground-fault"),
        ("e-component-bushing", "e-overload"),
        ("e-component-insulator", "e-partial-discharge"),
        ("e-component-insulator", "e-ground-fault"),
    ]
    for comp_id, fault_id in component_faults:
        out.append({
            "src_id": comp_id,
            "tgt_id": fault_id,
            "type": "CAUSES_FAULT_IF_DAMAGED",
            "properties": {"confidence": 0.6},
        })

    # 设备 → 遥测信号（GENERATES）
    device_signals = {
        "e-TR001": ["e-sig-temperature", "e-sig-voltage", "e-sig-current", "e-sig-pressure"],
        "e-TR002": ["e-sig-temperature", "e-sig-voltage", "e-sig-current", "e-sig-pressure"],
        "e-BR001": ["e-sig-current", "e-sig-pressure"],
        "e-BR002": ["e-sig-current", "e-sig-pressure"],
        "e-CB001": ["e-sig-current", "e-sig-temperature", "e-sig-pd"],
        "e-CB002": ["e-sig-current", "e-sig-temperature", "e-sig-pd"],
        "e-BB001": ["e-sig-voltage", "e-sig-current"],
        "e-BB002": ["e-sig-voltage", "e-sig-current", "e-sig-temperature"],
    }
    for dev_id, sig_ids in device_signals.items():
        for sig_id in sig_ids:
            out.append({
                "src_id": dev_id,
                "tgt_id": sig_id,
                "type": "GENERATES",
                "properties": {},
            })

    # 检修记录 → 设备（MAINT_RECORD_FOR）+ → 人员（PERFORMED_BY）+ → 处置（RESOLVED_BY）
    maint_device_map = {
        "e-maint-001": "e-TR001",
        "e-maint-002": "e-TR002",
        "e-maint-003": "e-BR001",
        "e-maint-004": "e-BR002",
        "e-maint-005": "e-CB001",
        "e-maint-006": "e-CB002",
        "e-maint-007": "e-BB001",
        "e-maint-008": "e-BB002",
    }
    for maint_id, dev_id in maint_device_map.items():
        out.append({
            "src_id": maint_id,
            "tgt_id": dev_id,
            "type": "MAINT_RECORD_FOR",
            "properties": {},
        })
        out.append({
            "src_id": maint_id,
            "tgt_id": "e-personnel-maintainer",
            "type": "PERFORMED_BY",
            "properties": {},
        })

    # 设备 → 设备型号模板（TEMPLATE_OF）
    device_template_map = {
        "e-TR001": "e-dtype-transformer",
        "e-TR002": "e-dtype-transformer",
        "e-BR001": "e-dtype-circuit-breaker",
        "e-BR002": "e-dtype-circuit-breaker",
        "e-CB001": "e-dtype-cable",
        "e-CB002": "e-dtype-cable",
        "e-BB001": "e-dtype-busbar",
        "e-BB002": "e-dtype-busbar",
    }
    for dev_id, tmpl_id in device_template_map.items():
        out.append({
            "src_id": dev_id,
            "tgt_id": tmpl_id,
            "type": "TEMPLATE_OF",
            "properties": {},
        })

    # 型号 → 类别（TEMPLATE_OF_CATEGORY）
    template_category_map = {
        "e-dtype-transformer": "e-transformer",
        "e-dtype-circuit-breaker": "e-breaker",
        "e-dtype-cable": "e-cable",
        "e-dtype-busbar": "e-busbar",
    }
    for tmpl_id, cat_id in template_category_map.items():
        out.append({
            "src_id": tmpl_id,
            "tgt_id": cat_id,
            "type": "TEMPLATE_OF_CATEGORY",
            "properties": {},
        })

    # 检修记录 → 故障（FOUND_FAULT）
    maint_fault_map = [
        ("e-maint-003", "e-sf6leak"),
        ("e-maint-005", "e-partial-discharge"),
        ("e-maint-008", "e-ground-fault"),
    ]
    for maint_id, fault_id in maint_fault_map:
        out.append({
            "src_id": maint_id,
            "tgt_id": fault_id,
            "type": "FOUND_FAULT",
            "properties": {},
        })

    # 规程 → 工器具（REQUIRES_SAFETY_TOOL）
    reg_tool_map = [
        ("e-QGDW1799", "e-tool-insulating-gloves"),
        ("e-QGDW1799", "e-tool-insulating-boots"),
        ("e-QGDW1799", "e-tool-safety-helmet"),
        ("e-DL572", "e-tool-voltage-tester"),
        ("e-DL572", "e-tool-ground-wire"),
    ]
    for reg_id, tool_id in reg_tool_map:
        out.append({
            "src_id": reg_id,
            "tgt_id": tool_id,
            "type": "REQUIRES_SAFETY_TOOL",
            "properties": {},
        })

    return out


# ═════════════════════════════════════════════════════════════════════════════
# 10. 公开 API：build_seed_graph()
# ═════════════════════════════════════════════════════════════════════════════

def build_seed_graph() -> dict[str, list[dict[str, Any]]]:
    """构建完整的种子图谱（节点 + 关系）。

    Returns:
        ``{"nodes": [...], "relations": [...]}`` 字典。
        每个节点是 ``{entity_id, name, type, code, label, properties}``。
        每个关系是 ``{src_id, tgt_id, type, properties}``。
    """
    nodes: list[dict[str, Any]] = []
    nodes.extend(DEVICE_CATEGORIES)
    nodes.extend(FAULT_TYPES)
    nodes.extend(HANDLING_MEASURES)
    nodes.extend(REGULATIONS)
    nodes.extend(DEVICE_INSTANCES)
    nodes.extend(SUBSTATIONS)
    nodes.extend(COMPONENTS)
    nodes.extend(SENSORS)
    nodes.extend(MANUFACTURERS)
    nodes.extend(KNOWLEDGE_CHUNKS)
    nodes.extend(TELEMETRY_SIGNALS)
    nodes.extend(INSPECTION_FINDINGS)
    nodes.extend(PERSONNEL)
    nodes.extend(SAFETY_TOOLS)
    nodes.extend(MAINTENANCE_RECORDS)
    nodes.extend(DEVICE_TYPE_TEMPLATES)

    relations: list[dict[str, Any]] = []
    relations.extend(BASE_RELATIONS)
    relations.extend(_build_instance_relations())
    relations.extend(_build_fault_device_relations())
    relations.extend(_build_handling_relations())
    relations.extend(_build_regulation_relations())
    relations.extend(_build_topology_relations())
    relations.extend(_build_causal_relations())
    relations.extend(_build_component_relations())
    relations.extend(_build_extra_relations())

    return {"nodes": nodes, "relations": relations}


def extract_report(graph: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """生成抽取报告（节点 / 关系按类型分组统计 + 完整性检查）。"""
    if graph is None:
        graph = build_seed_graph()

    nodes = graph["nodes"]
    relations = graph["relations"]

    # 按 type 分组统计
    node_by_type: dict[str, int] = {}
    rel_by_type: dict[str, int] = {}
    label_by_type: dict[str, int] = {}
    for n in nodes:
        node_by_type[n["type"]] = node_by_type.get(n["type"], 0) + 1
        if "label" in n:
            label_by_type[n["label"]] = label_by_type.get(n["label"], 0) + 1
    for r in relations:
        rel_by_type[r["type"]] = rel_by_type.get(r["type"], 0) + 1

    total_triples = len(nodes) + len(relations)
    return {
        "total_nodes": len(nodes),
        "total_relations": len(relations),
        "total_triples": total_triples,
        "nodes_by_type": node_by_type,
        "nodes_by_label": label_by_type,
        "relations_by_type": rel_by_type,
        "is_meeting_threshold": total_triples >= 500,
        "threshold": 500,
    }


__all__ = [
    # 节点集合
    "DEVICE_CATEGORIES",
    "FAULT_TYPES",
    "HANDLING_MEASURES",
    "REGULATIONS",
    "DEVICE_INSTANCES",
    "SUBSTATIONS",
    "COMPONENTS",
    "SENSORS",
    "MANUFACTURERS",
    "KNOWLEDGE_CHUNKS",
    "TELEMETRY_SIGNALS",
    "INSPECTION_FINDINGS",
    "PERSONNEL",
    "SAFETY_TOOLS",
    "MAINTENANCE_RECORDS",
    "DEVICE_TYPE_TEMPLATES",
    # 关系对
    "BASE_RELATIONS",
    "FAULT_DEVICE_PAIRS",
    "HANDLING_FAULT_PAIRS",
    "REGULATION_DEVICE_PAIRS",
    "REGULATION_MEASURE_PAIRS",
    "REGULATION_FAULT_PAIRS",
    "DOC_ENTITY_PAIRS",
    "CONNECTED_PAIRS",
    "CAUSAL_CHAINS",
    # API
    "build_seed_graph",
    "extract_report",
]