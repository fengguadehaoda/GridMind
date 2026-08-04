"""GridMind 知识图谱 M3a · Cypher 模板注册中心（CypherTemplateRegistry）。

设计目标
--------
- **集中仓库**：把 M2 散落在 ``mcp_tools/tools/neo4j_tools.py`` 的 inline Cypher 收敛为命名模板；
- **参数化** + **版本化**：所有动态值走 ``$param`` 参数化通道，版本号格式 ``MAJOR.MINOR``；
- **注入防护**：``render()`` 时用正则黑名单校验参数值不含 ``;`` / ``MATCH`` / ``CREATE`` /
  ``DELETE`` / ``MERGE`` / ``DROP`` 等关键字；
- **Feature flag**：``enable/disable`` 立即生效，``disable`` 后 ``render`` 抛 ``TemplateDisabled``；
- **单例模式**：全局唯一 ``CypherTemplateRegistry.get_instance()``，与 M2 ``GrayscaleRouter``
  / ``KGClient`` 一致。

使用示例::

    from core.kg_cypher_templates import CypherTemplateRegistry

    registry = CypherTemplateRegistry.get_instance()
    cypher, params = registry.render(
        "fault_chain_v1",
        {"fault_id": "e-overload", "max_hops": 4, "limit": 10},
    )

跨文件约定（与 M2 一致）
--------
- 零新增三方依赖（仅 stdlib + loguru）
- "写"操作（``register`` / ``enable`` / ``disable``）100% 写 ``sync_log``
- 错误码：``TEMPLATE_NOT_FOUND`` / ``TEMPLATE_DISABLED`` / ``MISSING_PARAM`` /
  ``CYPHER_INJECTION_RISK`` / ``DUPLICATE_TEMPLATE``
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from api.services.sync_log_service import SyncLogService
from core.metrics_collector import (
    get_metrics_collector,
    is_metrics_enabled,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TemplateEntry:
    """单个 Cypher 模板条目（不可变 + 启动后只读）。"""
    name: str
    cypher: str
    version: str
    registered_at: datetime
    enabled: bool
    required_params: list[str]
    description: str
    category: str


@dataclass
class TemplateRegistryConfig:
    """注册中心配置（从 ``api/config.py`` 注入）。"""
    enabled: bool = True
    injection_check_enabled: bool = True
    max_templates: int = 100


# ─────────────────────────────────────────────────────────────────────────────
# 2. 异常类型
# ─────────────────────────────────────────────────────────────────────────────

class TemplateNotFound(KeyError):
    """模板未注册。"""
    def __init__(self, name: str) -> None:
        super().__init__(f"Template '{name}' not found")
        self.name = name


class TemplateDisabled(RuntimeError):
    """模板被 feature flag 禁用。"""
    def __init__(self, name: str) -> None:
        super().__init__(f"Template '{name}' is disabled")
        self.name = name


class MissingParamError(ValueError):
    """``render()`` 缺少必填参数。"""
    def __init__(self, name: str, missing: list[str]) -> None:
        super().__init__(f"Template '{name}' missing params: {missing}")
        self.name = name
        self.missing = missing


class DuplicateTemplateError(ValueError):
    """同名同版本重复注册。"""
    def __init__(self, name: str, version: str) -> None:
        super().__init__(f"Template '{name}@{version}' already registered")
        self.name = name
        self.version = version


class CypherInjectionRisk(ValueError):
    """参数值含注入特征。"""
    def __init__(self, param: str, value: str, keyword: str) -> None:
        super().__init__(
            f"Param '{param}' value '{value[:50]}...' contains forbidden keyword '{keyword}'"
        )
        self.param = param
        self.value = value
        self.keyword = keyword


# ─────────────────────────────────────────────────────────────────────────────
# 3. Cypher 注入检测器（正则黑名单）
# ─────────────────────────────────────────────────────────────────────────────

class CypherInjectionDetector:
    """Cypher 注入检测器（正则黑名单）。"""

    _FORBIDDEN_PATTERNS = (
        r"\bMATCH\b",
        r"\bCREATE\b",
        r"\bDELETE\b",
        r"\bMERGE\b",
        r"\bDROP\b",
        r"\bDETACH\b",
        r"\bSET\b",
        r"\bREMOVE\b",
        r"\bCALL\b",
        r";",
        r"--",
        r"\bOR\b\s+\d+=\d+",
    )
    _COMPILED: list[re.Pattern[str]] = [
        re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_PATTERNS
    ]

    def check(self, params: dict[str, Any]) -> None:
        """遍历所有参数值，命中黑名单则抛 ``CypherInjectionRisk``。"""
        for key, value in params.items():
            if not self.is_safe(str(value)):
                keyword = self._find_keyword(str(value))
                raise CypherInjectionRisk(key, str(value), keyword)

    def is_safe(self, value: str) -> bool:
        for pat in self._COMPILED:
            if pat.search(value):
                return False
        return True

    def _find_keyword(self, value: str) -> str:
        for pat, src in zip(self._COMPILED, self._FORBIDDEN_PATTERNS):
            if pat.search(value):
                return src
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 4. CypherTemplateRegistry 单例
# ─────────────────────────────────────────────────────────────────────────────

class CypherTemplateRegistry:
    """Cypher 模板注册中心（单例 + 版本化 + 注入防护）。"""

    _instance: "CypherTemplateRegistry | None" = None

    def __init__(self, *, config: TemplateRegistryConfig | None = None) -> None:
        self._templates: dict[str, TemplateEntry] = {}
        self._versions: dict[str, dict[str, str]] = {}
        self._config = config or TemplateRegistryConfig()
        self._validator = CypherInjectionDetector()
        self._audit = SyncLogService()

    # ── 单例工厂 ─────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "CypherTemplateRegistry":
        """获取全局唯一实例（首次调用时自动注册 10 个内置模板）。"""
        if cls._instance is None:
            cls._instance = cls()
            register_default_templates(cls._instance)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅测试用）。"""
        cls._instance = None

    # ── 模板注册 ─────────────────────────────────────────────

    def register(
        self,
        name: str,
        cypher: str,
        *,
        version: str = "1.0",
        description: str = "",
        category: str = "general",
        required_params: list[str] | None = None,
    ) -> None:
        """注册模板。

        :raises DuplicateTemplateError: 同名同版本已注册
        :raises ValueError: 模板数超过 ``max_templates``
        """
        if not name or not isinstance(name, str):
            raise ValueError("Template name must be non-empty string")
        if not cypher or not isinstance(cypher, str):
            raise ValueError("Template cypher must be non-empty string")
        if name in self._versions and version in self._versions[name]:
            raise DuplicateTemplateError(name, version)
        if len(self._templates) >= self._config.max_templates:
            raise ValueError(
                f"Template count exceeds {self._config.max_templates}"
            )
        entry = TemplateEntry(
            name=name,
            cypher=cypher,
            version=version,
            registered_at=datetime.utcnow(),
            enabled=True,
            required_params=list(required_params or []),
            description=description,
            category=category,
        )
        self._templates[name] = entry
        self._versions.setdefault(name, {})[version] = cypher
        # 审计（M2 约定："写"操作 100% 写 sync_log）
        try:
            self._audit.write_pending(
                sync_type="event",
                entity_id=f"template:{name}",
                payload={"event": "template_register", "name": name, "version": version, "category": category},
            )
        except Exception:  # noqa: BLE001
            # 审计失败不影响主流程
            pass

    # ── 模板渲染（核心）─────────────────────────────────────────

    def render(
        self,
        name: str,
        params: dict[str, Any],
        *,
        version: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """渲染模板为 ``(cypher, params)`` 元组。

        :raises TemplateNotFound: 模板未注册
        :raises TemplateDisabled: 模板被 feature flag 关闭
        :raises MissingParamError: 必填参数缺失
        :raises CypherInjectionRisk: 参数值含注入特征
        """
        if not self._config.enabled:
            raise TemplateDisabled(name)
        entry = self._templates.get(name)
        if entry is None:
            raise TemplateNotFound(name)
        if not entry.enabled:
            raise TemplateDisabled(name)

        # 校验必填参数
        missing = [p for p in entry.required_params if p not in params]
        if missing:
            raise MissingParamError(name, missing)

        # M3c：模板渲染耗时 + 使用次数 metrics（feature flag 关闭时 no-op）
        start_ts = time.perf_counter()

        # 注入防护：检查所有参数值
        if self._config.injection_check_enabled:
            self._validator.check(params)

        # 选版本（None → 最新版）
        ver = version or self._latest_version(name)
        cypher = self._versions[name][ver]

        # M3c：上报 Prometheus 指标（始终在最后；失败也不抛出影响主调用）
        if is_metrics_enabled():
            try:
                elapsed_ms = (time.perf_counter() - start_ts) * 1000.0
                metrics = get_metrics_collector()
                metrics.record_template(
                    template=name,
                    version=ver,
                    latency_ms=elapsed_ms,
                )
            except Exception as exc:  # noqa: BLE001
                # 静默失败：metrics 不影响模板渲染结果
                pass

        return cypher, dict(params)

    # ── 查询 / 控制 ─────────────────────────────────────────────

    def list_templates(
        self,
        category: str | None = None,
    ) -> list[TemplateEntry]:
        """列出模板（可按 category 过滤）。"""
        return [
            e for e in self._templates.values()
            if category is None or e.category == category
        ]

    def enable(self, name: str) -> None:
        if name not in self._templates:
            raise TemplateNotFound(name)
        self._templates[name].enabled = True
        try:
            self._audit.write_pending(
                sync_type="event",
                entity_id=f"template:{name}",
                payload={"event": "template_enable", "name": name},
            )
        except Exception:  # noqa: BLE001
            pass

    def disable(self, name: str) -> None:
        if name not in self._templates:
            raise TemplateNotFound(name)
        self._templates[name].enabled = False
        try:
            self._audit.write_pending(
                sync_type="event",
                entity_id=f"template:{name}",
                payload={"event": "template_disable", "name": name},
            )
        except Exception:  # noqa: BLE001
            pass

    def is_enabled(self, name: str) -> bool:
        e = self._templates.get(name)
        return e is not None and e.enabled

    def get_template(
        self, name: str, version: str | None = None,
    ) -> TemplateEntry:
        """获取模板条目（仅元数据，不渲染）。"""
        if name not in self._templates:
            raise TemplateNotFound(name)
        return self._templates[name]

    def count(self) -> int:
        return len(self._templates)

    def _latest_version(self, name: str) -> str:
        """返回指定模板的最新版本号。"""
        versions = list(self._versions[name].keys())
        return sorted(versions, key=_version_sort_key)[-1]


def _version_sort_key(v: str) -> tuple[int, int]:
    """``MAJOR.MINOR`` 字符串 → ``(MAJOR, MINOR)`` 元组。"""
    parts = v.split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return (major, minor)


# ─────────────────────────────────────────────────────────────────────────────
# 5. 10 个内置 Cypher 模板（启动钩子）
# ─────────────────────────────────────────────────────────────────────────────

def register_default_templates(registry: CypherTemplateRegistry) -> None:
    """注册 10 个内置 Cypher 模板（Q1=A 全小写下划线）。"""

    # 1. fault_chain_v1：故障因果链（沿 CAUSES 关系多跳扩展）
    registry.register(
        name="fault_chain_v1",
        cypher="""
MATCH path = (start:Event {event_id: $fault_id})-[:CAUSES*1..$max_hops]->(downstream:Event)
WHERE start.event_type IN ['Overload', 'ShortCircuit', 'Overtemp', 'VoltageDeviation']
RETURN
    start.event_id AS src_id,
    [node IN nodes(path) | node.event_id] AS path_nodes,
    [rel IN relationships(path) | type(rel)] AS path_relations,
    downstream.event_id AS tgt_id,
    downstream.severity AS severity
ORDER BY length(path) ASC
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "查询某故障实体的完整因果链（沿 CAUSES 关系多跳扩展）。"
            "用于：故障根因分析、传导链展示。不适用于：设备列表查询（用 find_devices_v1）。"
        ),
        category="fault_chain",
        required_params=["fault_id", "max_hops"],
    )

    # 2. multi_hop_v1：通用多跳扩展
    registry.register(
        name="multi_hop_v1",
        cypher="""
MATCH path = (seed:Entity)-[r*1..$hops]->(target:Entity)
WHERE seed.entity_id IN $seed_ids
  AND ($relation_types IS NULL OR any(rel IN relationships(path) WHERE type(rel) IN $relation_types))
RETURN DISTINCT
    seed.entity_id AS src_id,
    [node IN nodes(path) | node.entity_id] AS path_nodes,
    [rel IN relationships(path) | type(rel)] AS path_relations,
    target.entity_id AS tgt_id,
    target.name AS target_name
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "通用多跳扩展（任意 seed + 任意关系类型）。"
            "用于：探索图谱关联、上下文召回。不适用于：专用故障链（用 fault_chain_v1）。"
        ),
        category="multi_hop",
        required_params=["seed_ids", "hops"],
    )

    # 3. find_devices_v1：按变电站/类别查设备
    registry.register(
        name="find_devices_v1",
        cypher="""
MATCH (d:Device)
WHERE ($substation_id IS NULL OR d.substation_id = $substation_id)
  AND ($device_category IS NULL OR d.category = $device_category)
  AND ($voltage_level_kv IS NULL OR d.voltage_level_kv = $voltage_level_kv)
RETURN d.device_id AS device_id, d.name AS name, d.category AS category,
       d.voltage_level_kv AS voltage_level_kv, d.manufacturer AS manufacturer
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "按变电站 / 设备类别 / 电压等级查询设备列表。"
            "用于：设备清单查询、铭牌筛选。不适用于：单设备详情（用 device_subgraph_v1）。"
        ),
        category="find_devices",
        required_params=[],
    )

    # 4. regulations_v1：适用规程清单
    registry.register(
        name="regulations_v1",
        cypher="""
MATCH (reg:Regulation)-[r:APPLIES_TO]->(target)
WHERE ($device_id IS NULL OR target.device_id = $device_id)
  AND ($device_category IS NULL OR target.category = $device_category)
  AND ($regulation_type IS NULL OR reg.regulation_type = $regulation_type)
RETURN reg.regulation_id AS regulation_id, reg.code AS code,
       reg.title AS title, reg.regulation_type AS regulation_type
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "查询设备 / 类别适用的规程清单（APPLIES_TO 关系）。"
            "用于：合规检查、规程检索。不适用于：操作步骤（用 applicable_procedures_v1）。"
        ),
        category="regulations",
        required_params=[],
    )

    # 5. causal_chain_v1：事件因果链（多关系类型）
    registry.register(
        name="causal_chain_v1",
        cypher="""
MATCH path = (start:Event {event_id: $event_id})-[r*1..$max_hops]->(end:Event)
WHERE any(rel IN relationships(path) WHERE type(rel) IN $relation_types)
RETURN
    start.event_id AS src_id,
    [node IN nodes(path) | node.event_id] AS path_nodes,
    [rel IN relationships(path) | type(rel)] AS path_relations,
    end.event_id AS tgt_id
ORDER BY length(path) ASC
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "查询事件的因果传导链（含所有中间节点，可指定关系类型）。"
            "用于：因果链推理、影响范围分析。比 fault_chain_v1 更灵活（可指定关系类型白名单）。"
        ),
        category="causal_chain",
        required_params=["event_id", "max_hops"],
    )

    # 6. mandates_v1：保护装置强制的应急措施
    registry.register(
        name="mandates_v1",
        cypher="""
MATCH (p:Protection {protection_id: $protection_id})-[r:MANDATES]->(m:EmergencyMeasure)
WHERE ($severity IS NULL OR m.severity = $severity)
RETURN m.measure_id AS measure_id, m.name AS name, m.action AS action,
       m.severity AS severity, m.priority AS priority
ORDER BY m.priority ASC
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "查询保护装置强制要求的应急措施（MANDATES 关系）。"
            "用于：事故应急响应、保护逻辑查阅。不适用于：一般操作规程（用 applicable_procedures_v1）。"
        ),
        category="mandates",
        required_params=["protection_id"],
    )

    # 7. device_subgraph_v1：设备 1 跳子图
    registry.register(
        name="device_subgraph_v1",
        cypher="""
MATCH (d:Device {device_id: $device_id})-[r]-(neighbor)
RETURN
    d.device_id AS src_id,
    type(r) AS rel_type,
    neighbor.device_id AS tgt_id,
    labels(neighbor) AS tgt_labels
LIMIT $max_relations
""".strip(),
        version="1.0",
        description=(
            "提取某设备的所有 1 跳子图（节点 + 关系）。"
            "用于：单设备上下文、设备拓扑。不适用于：多跳扩展（用 multi_hop_v1）。"
        ),
        category="device_subgraph",
        required_params=["device_id"],
    )

    # 8. fault_subgraph_v1：故障完整子图（含处置 / 规程）
    registry.register(
        name="fault_subgraph_v1",
        cypher="""
MATCH (f:Event {event_id: $fault_id})-[r]-(neighbor)
OPTIONAL MATCH (neighbor)-[r2:APPLIES_TO]->(reg:Regulation)
WHERE $include_regulations = true
RETURN
    f.event_id AS src_id,
    type(r) AS rel_type,
    neighbor.event_id AS neighbor_id,
    reg.code AS regulation_code
LIMIT $max_relations
""".strip(),
        version="1.0",
        description=(
            "提取某故障实体的完整子图（含处置 / 规程，可选是否包含规程）。"
            "用于：故障可视化、关联分析。比 device_subgraph_v1 增加了规程关联。"
        ),
        category="fault_subgraph",
        required_params=["fault_id"],
    )

    # 9. applicable_procedures_v1：操作步骤适用的规程
    registry.register(
        name="applicable_procedures_v1",
        cypher="""
MATCH (op:Operation {operation_type: $operation_type, voltage_level_kv: $voltage_level_kv})
      -[r:APPLIES_TO]->(proc:Procedure)
WHERE ($equipment_type IS NULL OR proc.equipment_type = $equipment_type)
RETURN proc.procedure_id AS procedure_id, proc.title AS title,
       proc.mandatory_actions AS mandatory_actions
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "查询某操作步骤适用的操作规程（含强制动作）。"
            "用于：操作票生成、SOP 检索。不适用于：设备适用规程（用 regulations_v1）。"
        ),
        category="regulations",
        required_params=["operation_type", "voltage_level_kv"],
    )

    # 10. impact_analysis_v1：故障影响范围
    registry.register(
        name="impact_analysis_v1",
        cypher="""
MATCH path = (d:Device {device_id: $device_id})-[r*1..$max_hops]->(impacted:Device)
WHERE any(rel IN relationships(path) WHERE type(rel) IN ['CAUSES', 'CONNECTED_TO', 'BELONGS_TO'])
  AND ($fault_type IS NULL OR any(node IN nodes(path) WHERE node.fault_type = $fault_type))
RETURN DISTINCT
    d.device_id AS src_id,
    [node IN nodes(path) | node.device_id] AS impacted_devices,
    [rel IN relationships(path) | type(rel)] AS relation_types
LIMIT $limit
""".strip(),
        version="1.0",
        description=(
            "查询某设备故障的影响范围（关联设备 + 关联规程）。"
            "用于：停电范围评估、连锁故障分析。比 fault_chain_v1 粒度更粗（按设备而非事件）。"
        ),
        category="impact_analysis",
        required_params=["device_id", "fault_type"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. 全局单例工厂
# ─────────────────────────────────────────────────────────────────────────────

def get_template_registry() -> CypherTemplateRegistry:
    """获取 ``CypherTemplateRegistry`` 单例。"""
    return CypherTemplateRegistry.get_instance()


__all__ = [
    "TemplateEntry",
    "TemplateRegistryConfig",
    "TemplateNotFound",
    "TemplateDisabled",
    "MissingParamError",
    "DuplicateTemplateError",
    "CypherInjectionRisk",
    "CypherInjectionDetector",
    "CypherTemplateRegistry",
    "CypherTemplateRegistry.get_instance",
    "get_template_registry",
    "register_default_templates",
]
