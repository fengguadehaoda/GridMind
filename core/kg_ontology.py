"""GridMind 知识图谱本体 Schema —— Neo4j 5.x（M1 完整版）。

设计原则
--------
- M1 在 M0（4 核心节点类 + 5 约束 + 4 索引）之上扩展为：
    * **5 个设备子类**：Transformer / CircuitBreaker / Busbar / Line / DeviceInstance
    * **4 个故障子类**：OverloadFault / ShortCircuitFault / OverheatFault / VoltageDeviationFault
    * **2 个处置子类**：EmergencyStopMeasure / RoutineMaintenanceMeasure
    * **1 个规程节点**：Regulation（带 category / section）
    * **总约束 ≥15**：唯一性 + 端点去重
    * **总索引 ≥10**：按字段类型/分类/严重程度加速查询
- **幂等性**：所有 CREATE 语句带 ``IF NOT EXISTS``；重复调用安全。
- **Cypher 注入防护**：本模块仅产出 Cypher 文本常量；运行时动态查询走
  ``Neo4jBackend.cypher_query(query, params)`` 参数化通道。

向后兼容
--------
- ``ONTOLOGY_CONSTRAINTS`` / ``ONTOLOGY_INDEXES`` 列表扩展，调用方无需修改。
- ``apply_ontology(driver, database=None)`` 签名不变。
- M0 阶段的 5 个约束 + 4 个索引保留在列表中（仅扩展，不替换）。

版本演进
--------
- M0: 5 约束 + 4 索引（基础唯一性）
- M1: 14+ 约束 + 10 索引（设备子类 + 故障子类 + 处置子类 + 规程分类）
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# ═════════════════════════════════════════════════════════════════════════════
# 0. 公共基础 —— Entity 通用标签
# ═════════════════════════════════════════════════════════════════════════════

# Entity 通用 entity_id 唯一约束 —— 迁移幂等性的基石
ENTITY_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (n:Entity) REQUIRE n.entity_id IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 1. 设备类别（DeviceCategory —— 抽象基类 PowerEquipment 占位）
# ═════════════════════════════════════════════════════════════════════════════

DEVICE_CATEGORY_NAME_UNIQUE_CYPHER = """
CREATE CONSTRAINT device_category_name_unique IF NOT EXISTS
FOR (n:DeviceCategory) REQUIRE n.name IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 2. 故障类型（FaultType + 4 个子标签）
# ═════════════════════════════════════════════════════════════════════════════

FAULT_TYPE_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT fault_type_code_unique IF NOT EXISTS
FOR (n:FaultType) REQUIRE n.code IS UNIQUE
""".strip()

# 子标签：过载故障
OVERLOAD_FAULT_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT overload_fault_code_unique IF NOT EXISTS
FOR (n:OverloadFault) REQUIRE n.code IS UNIQUE
""".strip()

# 子标签：短路故障
SHORT_CIRCUIT_FAULT_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT short_circuit_fault_code_unique IF NOT EXISTS
FOR (n:ShortCircuitFault) REQUIRE n.code IS UNIQUE
""".strip()

# 子标签：过热故障
OVERHEAT_FAULT_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT overheat_fault_code_unique IF NOT EXISTS
FOR (n:OverheatFault) REQUIRE n.code IS UNIQUE
""".strip()

# 子标签：电压偏差故障
VOLTAGE_DEVIATION_FAULT_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT voltage_deviation_fault_code_unique IF NOT EXISTS
FOR (n:VoltageDeviationFault) REQUIRE n.code IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 3. 处置措施（HandlingMeasure + 2 个子标签）
# ═════════════════════════════════════════════════════════════════════════════

HANDLING_MEASURE_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT handling_measure_code_unique IF NOT EXISTS
FOR (n:HandlingMeasure) REQUIRE n.code IS UNIQUE
""".strip()

# 子标签：紧急停运
EMERGENCY_STOP_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT emergency_stop_code_unique IF NOT EXISTS
FOR (n:EmergencyStopMeasure) REQUIRE n.code IS UNIQUE
""".strip()

# 子标签：常规维护
ROUTINE_MAINTENANCE_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT routine_maintenance_code_unique IF NOT EXISTS
FOR (n:RoutineMaintenanceMeasure) REQUIRE n.code IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 4. 规程（Regulation）
# ═════════════════════════════════════════════════════════════════════════════

REGULATION_CODE_UNIQUE_CYPHER = """
CREATE CONSTRAINT regulation_code_unique IF NOT EXISTS
FOR (n:Regulation) REQUIRE n.code IS UNIQUE
""".strip()

# 规程按 code + section 联合唯一（同一规程多章节）
REGULATION_SECTION_UNIQUE_CYPHER = """
CREATE CONSTRAINT regulation_section_unique IF NOT EXISTS
FOR (n:Regulation) REQUIRE (n.code, n.section) IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 5. 设备子类（Transformer / CircuitBreaker / Busbar / Line / DeviceInstance）
# ═════════════════════════════════════════════════════════════════════════════

TRANSFORMER_DEVICE_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT transformer_device_id_unique IF NOT EXISTS
FOR (n:Transformer) REQUIRE n.device_id IS UNIQUE
""".strip()

CIRCUIT_BREAKER_DEVICE_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT circuit_breaker_device_id_unique IF NOT EXISTS
FOR (n:CircuitBreaker) REQUIRE n.device_id IS UNIQUE
""".strip()

BUSBAR_DEVICE_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT busbar_device_id_unique IF NOT EXISTS
FOR (n:Busbar) REQUIRE n.device_id IS UNIQUE
""".strip()

LINE_DEVICE_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT line_device_id_unique IF NOT EXISTS
FOR (n:Line) REQUIRE n.device_id IS UNIQUE
""".strip()

DEVICE_INSTANCE_DEVICE_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT device_instance_device_id_unique IF NOT EXISTS
FOR (n:DeviceInstance) REQUIRE n.device_id IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 6. 关系端点 —— RELATION 按 (type, src_id, tgt_id) 复合去重
# ═════════════════════════════════════════════════════════════════════════════

RELATION_ID_UNIQUE_CYPHER = """
CREATE CONSTRAINT relation_id_unique IF NOT EXISTS
FOR ()-[r:RELATION]-() REQUIRE r.relation_id IS UNIQUE
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 7. 索引 —— 按字段加速常用查询
# ═════════════════════════════════════════════════════════════════════════════

# 已有 4 个索引（M0 阶段）
ENTITY_NAME_INDEX_CYPHER = """
CREATE INDEX entity_name_index IF NOT EXISTS
FOR (n:Entity) ON (n.name)
""".strip()

ENTITY_TYPE_INDEX_CYPHER = """
CREATE INDEX entity_type_index IF NOT EXISTS
FOR (n:Entity) ON (n.type)
""".strip()

ENTITY_CODE_INDEX_CYPHER = """
CREATE INDEX entity_code_index IF NOT EXISTS
FOR (n:Entity) ON (n.code)
""".strip()

RELATION_TYPE_INDEX_CYPHER = """
CREATE INDEX relation_type_index IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.type)
""".strip()

# 新增 6 个索引（M1 阶段）
# 设备制造商索引（按厂家过滤设备）
DEVICE_MANUFACTURER_INDEX_CYPHER = """
CREATE INDEX device_manufacturer_index IF NOT EXISTS
FOR (n:DeviceInstance) ON (n.manufacturer)
""".strip()

# 设备投运日期索引（按服役年限过滤）
DEVICE_COMMISSIONING_DATE_INDEX_CYPHER = """
CREATE INDEX device_commissioning_date_index IF NOT EXISTS
FOR (n:DeviceInstance) ON (n.commissioning_date)
""".strip()

# 变压器电压等级索引（按电压分级）
TRANSFORMER_VOLTAGE_INDEX_CYPHER = """
CREATE INDEX transformer_voltage_index IF NOT EXISTS
FOR (n:Transformer) ON (n.voltage_level)
""".strip()

# 规程分类索引（DL/T 572 / Q/GDW 1799 等）
REGULATION_CATEGORY_INDEX_CYPHER = """
CREATE INDEX regulation_category_index IF NOT EXISTS
FOR (n:Regulation) ON (n.category)
""".strip()

# 故障严重程度索引（high / medium / low）
FAULT_SEVERITY_INDEX_CYPHER = """
CREATE INDEX fault_severity_index IF NOT EXISTS
FOR (n:FaultType) ON (n.severity)
""".strip()

# 关系置信度索引（CAUSES 推理的 confidence 字段）
RELATION_CONFIDENCE_INDEX_CYPHER = """
CREATE INDEX relation_confidence_index IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.confidence)
""".strip()


# ═════════════════════════════════════════════════════════════════════════════
# 8. Schema 集合（M1 阶段总览）
# ═════════════════════════════════════════════════════════════════════════════

# 顺序：先 Entity 通用 → 子类 → 子标签 → 规程 → 关系端点
# 保持 M0 5 个约束在前以确保向后兼容
ONTOLOGY_CONSTRAINTS: list[str] = [
    # ── M0 阶段（5 个）──────────────────────────────────
    ENTITY_ID_UNIQUE_CYPHER,
    DEVICE_CATEGORY_NAME_UNIQUE_CYPHER,
    FAULT_TYPE_CODE_UNIQUE_CYPHER,
    HANDLING_MEASURE_CODE_UNIQUE_CYPHER,
    REGULATION_CODE_UNIQUE_CYPHER,
    # ── M1 新增：故障子类（4 个）─────────────────────────
    OVERLOAD_FAULT_CODE_UNIQUE_CYPHER,
    SHORT_CIRCUIT_FAULT_CODE_UNIQUE_CYPHER,
    OVERHEAT_FAULT_CODE_UNIQUE_CYPHER,
    VOLTAGE_DEVIATION_FAULT_CODE_UNIQUE_CYPHER,
    # ── M1 新增：处置子类（2 个）─────────────────────────
    EMERGENCY_STOP_CODE_UNIQUE_CYPHER,
    ROUTINE_MAINTENANCE_CODE_UNIQUE_CYPHER,
    # ── M1 新增：规程 section 唯一（1 个）───────────────
    REGULATION_SECTION_UNIQUE_CYPHER,
    # ── M1 新增：设备子类（5 个）─────────────────────────
    TRANSFORMER_DEVICE_ID_UNIQUE_CYPHER,
    CIRCUIT_BREAKER_DEVICE_ID_UNIQUE_CYPHER,
    BUSBAR_DEVICE_ID_UNIQUE_CYPHER,
    LINE_DEVICE_ID_UNIQUE_CYPHER,
    DEVICE_INSTANCE_DEVICE_ID_UNIQUE_CYPHER,
    # ── M1 新增：关系端点（1 个）─────────────────────────
    RELATION_ID_UNIQUE_CYPHER,
]

# 共 18 个约束（M0 5 + M1 13）
ONTOLOGY_INDEXES: list[str] = [
    # ── M0 阶段（4 个）──────────────────────────────────
    ENTITY_NAME_INDEX_CYPHER,
    ENTITY_TYPE_INDEX_CYPHER,
    ENTITY_CODE_INDEX_CYPHER,
    RELATION_TYPE_INDEX_CYPHER,
    # ── M1 新增（6 个）──────────────────────────────────
    DEVICE_MANUFACTURER_INDEX_CYPHER,
    DEVICE_COMMISSIONING_DATE_INDEX_CYPHER,
    TRANSFORMER_VOLTAGE_INDEX_CYPHER,
    REGULATION_CATEGORY_INDEX_CYPHER,
    FAULT_SEVERITY_INDEX_CYPHER,
    RELATION_CONFIDENCE_INDEX_CYPHER,
]

# 共 10 个索引（M0 4 + M1 6）


# ═════════════════════════════════════════════════════════════════════════════
# 9. 节点类清单（供 SeedExtractor / 迁移器引用）
# ═════════════════════════════════════════════════════════════════════════════

# 设备子类（5 个）—— device.py / seed_data.py 中按设备类型映射到对应标签
DEVICE_SUBTYPE_LABELS: list[str] = [
    "Transformer",
    "CircuitBreaker",
    "Busbar",
    "Line",
    "DeviceInstance",
]

# 故障子类（4 个）—— 基于 severity 字段映射
FAULT_SUBTYPE_LABELS: list[str] = [
    "OverloadFault",
    "ShortCircuitFault",
    "OverheatFault",
    "VoltageDeviationFault",
]

# 处置子类（2 个）—— 基于优先级 / 紧急程度映射
MEASURE_SUBTYPE_LABELS: list[str] = [
    "EmergencyStopMeasure",
    "RoutineMaintenanceMeasure",
]

# 关系类型常量（9 类 —— 与 PRD §5.3 对齐）
RELATION_TYPES: dict[str, str] = {
    "CONNECTED_TO": "设备间电气连接（串联/并联）",
    "BELONGS_TO": "设备实例归属变电站/区域",
    "CAUSES": "故障类型因果推理链",
    "HANDLED_BY": "故障处置措施",
    "APPLIES_TO": "规程适用设备类型",
    "MANDATES": "规程强制要求动作",
    "INSTANCE_OF": "设备实例归属类别",
    "OCCURRED": "故障发生记录",
    "RELATES_TO": "规程文档关联（通用）",
}


# ═════════════════════════════════════════════════════════════════════════════
# 10. apply_ontology —— 幂等应用
# ═════════════════════════════════════════════════════════════════════════════

def apply_ontology(driver: Any, database: str | None = None) -> dict[str, int]:
    """应用 M1 本体 Schema 到 Neo4j（幂等）。

    Args:
        driver: ``neo4j.Driver`` 实例（来自 ``GraphDatabase.driver(...)``）。
        database: 数据库名；为 None 时使用 driver 的默认 database。

    Returns:
        报告字典，包含：
            - ``constraints_applied``: 成功执行的约束数
            - ``indexes_applied``: 成功执行的索引数
            - ``total_statements``: 总执行语句数
    """
    if driver is None:
        raise ValueError("driver 不能为空")

    constraints_applied = 0
    indexes_applied = 0
    total_statements = 0

    session_kwargs: dict[str, Any] = {}
    if database is not None:
        session_kwargs["database"] = database

    with driver.session(**session_kwargs) as session:
        # 1) 约束
        for cypher in ONTOLOGY_CONSTRAINTS:
            try:
                session.run(cypher)
                constraints_applied += 1
                total_statements += 1
                logger.debug("约束已执行: {}", _summarize(cypher))
            except Exception as exc:  # noqa: BLE001
                # IF NOT EXISTS 仍可能在某些 Neo4j 版本下抛错（如已存在同名不同定义）
                # 但我们保持幂等设计：捕获并继续
                logger.warning("约束跳过: {} | err={}", _summarize(cypher), exc)

        # 2) 索引
        for cypher in ONTOLOGY_INDEXES:
            try:
                session.run(cypher)
                indexes_applied += 1
                total_statements += 1
                logger.debug("索引已执行: {}", _summarize(cypher))
            except Exception as exc:  # noqa: BLE001
                logger.warning("索引跳过: {} | err={}", _summarize(cypher), exc)

    report = {
        "constraints_applied": constraints_applied,
        "indexes_applied": indexes_applied,
        "total_statements": total_statements,
    }
    logger.info(
        "Ontology applied (M1): {} constraints, {} indexes ({} statements)",
        constraints_applied, indexes_applied, total_statements,
    )
    return report


def _summarize(cypher: str) -> str:
    """从 Cypher 文本中提取首行关键摘要（用于日志）。"""
    return " | ".join(line.strip() for line in cypher.splitlines() if line.strip())[:120]


def schema_summary(driver: Any, database: str | None = None) -> dict[str, list[str]]:
    """查询当前 Neo4j 已注册的约束/索引（用于验证 / 调试）。

    Returns:
        ``{"constraints": [...], "indexes": [...]}``
    """
    out: dict[str, list[str]] = {"constraints": [], "indexes": []}
    if driver is None:
        return out

    session_kwargs: dict[str, Any] = {}
    if database is not None:
        session_kwargs["database"] = database

    with driver.session(**session_kwargs) as session:
        # 约束
        try:
            for record in session.run("SHOW CONSTRAINTS"):
                out["constraints"].append(str(record.get("name") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("SHOW CONSTRAINTS 失败: {}", exc)

        # 索引
        try:
            for record in session.run("SHOW INDEXES"):
                out["indexes"].append(str(record.get("name") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("SHOW INDEXES 失败: {}", exc)

    return out


__all__ = [
    # 约束 / 索引列表（M0 兼容 + M1 扩展）
    "ONTOLOGY_CONSTRAINTS",
    "ONTOLOGY_INDEXES",
    # 节点类清单
    "DEVICE_SUBTYPE_LABELS",
    "FAULT_SUBTYPE_LABELS",
    "MEASURE_SUBTYPE_LABELS",
    "RELATION_TYPES",
    # 函数
    "apply_ontology",
    "schema_summary",
]