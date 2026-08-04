"""GridMind 知识图谱 M3b · 性能基准场景库（30+ 场景）。

设计原则（kg-m3-split.md §4.2 / §4.5）
--------
- 5 类设备 × 6+ 场景 = 30+ 场景（含 ≥10 个因果链 + ≥5 个跨域推理）
- 每个 ``Scenario`` 描述一个**可重放**的查询：参数 + 期望跳数 + 后端偏好
- **沙箱兼容**：所有场景在 NetworkX backend 下也可执行（Neo4j 列在不可用时显示 SKIP）
- 场景参数**真实**：从 M1 真实设备命名派生（变压器 / 断路器 / 母线 / 线路 / 保护装置）

使用示例::

    from benchmarks.scenarios import get_scenarios
    for s in get_scenarios():
        print(s.scenario_id, s.category, s.expected_hops)

    # 单个类别
    only_chains = get_scenarios(category="causal_chain")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Scenario:
    """基准测试场景（不可变）。

    Attributes:
        scenario_id: 场景唯一 ID（如 ``"S01_transformer_4hop"``）
        category: 类别（``"device_query"`` / ``"causal_chain"`` /
            ``"regulation_link"`` / ``"cross_domain"``）
        query: 自然语言查询描述（仅报告用）
        params: 传给 ``KGClient`` 的方法参数
        method: 调用的 ``KGClient`` 方法名（``"get_entity"`` /
            ``"search_entities"`` / ``"get_relations"`` /
            ``"expand_entities"`` / ``"expand_with_optimizer"`` /
            ``"execute_template"``）
        expected_hops: 期望跳数（用于报告分组 + 性能期望）
        backend_preference: ``"neo4j"`` / ``"networkx"`` / ``"both"``
        device_type: 设备类别（``"transformer"`` / ``"line"`` / ``"busbar"`` /
            ``"circuit_breaker"`` / ``"protection_device"`` / ``"general"``）
        tags: 额外标签（用于过滤 / 报告分组）
    """

    scenario_id: str
    category: str
    query: str
    params: dict[str, Any]
    method: str
    expected_hops: int
    backend_preference: str = "both"
    device_type: str = "general"
    tags: tuple[str, ...] = field(default_factory=tuple)


# ═════════════════════════════════════════════════════════════════════════════
# 1. 设备查询（5 类 × 6+ 场景 = 30）
# ═════════════════════════════════════════════════════════════════════════════

_DEVICE_SCENARIOS: list[Scenario] = [
    # ── 变压器（6）────────────────────────────────────────
    Scenario(
        scenario_id="S01_transformer_4hop",
        category="device_query",
        query="变压器 → 所属变电站 → 电压等级 → 制造商 → 投运日期",
        params={"method_params": {"seed_entity_ids": ["e-tx-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        device_type="transformer",
        tags=("4hop", "device"),
    ),
    Scenario(
        scenario_id="S02_transformer_3hop",
        category="device_query",
        query="变压器 → 关联断路器 → 操作记录",
        params={"method_params": {"seed_entity_ids": ["e-tx-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
        device_type="transformer",
    ),
    Scenario(
        scenario_id="S03_transformer_2hop",
        category="device_query",
        query="变压器 → 关联母线",
        params={"method_params": {"seed_entity_ids": ["e-tx-001"], "hops": 2}},
        method="expand_entities",
        expected_hops=2,
        device_type="transformer",
    ),
    Scenario(
        scenario_id="S04_transformer_get_entity",
        category="device_query",
        query="按 ID 查变压器",
        params={"method_params": {"entity_id": "e-tx-001"}},
        method="get_entity",
        expected_hops=1,
        device_type="transformer",
    ),
    Scenario(
        scenario_id="S05_transformer_search",
        category="device_query",
        query="模糊搜索 '主变' 关键词",
        params={"method_params": {"query": "主变", "limit": 10}},
        method="search_entities",
        expected_hops=1,
        device_type="transformer",
    ),
    Scenario(
        scenario_id="S06_transformer_relations",
        category="device_query",
        query="变压器所有出边关系",
        params={"method_params": {"entity_id": "e-tx-001"}},
        method="get_relations",
        expected_hops=1,
        device_type="transformer",
    ),
    # ── 线路（6）────────────────────────────────────────
    Scenario(
        scenario_id="S07_line_3hop",
        category="device_query",
        query="线路 → 两侧变电站 → 电压等级",
        params={"method_params": {"seed_entity_ids": ["e-line-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
        device_type="line",
    ),
    Scenario(
        scenario_id="S08_line_2hop",
        category="device_query",
        query="线路 → 关联断路器",
        params={"method_params": {"seed_entity_ids": ["e-line-001"], "hops": 2}},
        method="expand_entities",
        expected_hops=2,
        device_type="line",
    ),
    Scenario(
        scenario_id="S09_line_get_entity",
        category="device_query",
        query="按 ID 查线路",
        params={"method_params": {"entity_id": "e-line-001"}},
        method="get_entity",
        expected_hops=1,
        device_type="line",
    ),
    Scenario(
        scenario_id="S10_line_search",
        category="device_query",
        query="模糊搜索 '线路'",
        params={"method_params": {"query": "线路", "limit": 10}},
        method="search_entities",
        expected_hops=1,
        device_type="line",
    ),
    Scenario(
        scenario_id="S11_line_relations",
        category="device_query",
        query="线路所有出边关系",
        params={"method_params": {"entity_id": "e-line-001"}},
        method="get_relations",
        expected_hops=1,
        device_type="line",
    ),
    Scenario(
        scenario_id="S12_line_4hop_optimizer",
        category="device_query",
        query="线路 → 拓扑扩展（使用 optimizer 缓存）",
        params={
            "method_params": {
                "seeds": ["e-line-001"],
                "hops": 4,
                "limit": 20,
            }
        },
        method="expand_with_optimizer",
        expected_hops=4,
        backend_preference="neo4j",
        device_type="line",
    ),
    # ── 母线（6）────────────────────────────────────────
    Scenario(
        scenario_id="S13_busbar_3hop",
        category="device_query",
        query="母线 → 连接断路器 → 保护装置",
        params={"method_params": {"seed_entity_ids": ["e-busbar-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
        device_type="busbar",
    ),
    Scenario(
        scenario_id="S14_busbar_2hop",
        category="device_query",
        query="母线 → 关联变压器",
        params={"method_params": {"seed_entity_ids": ["e-busbar-001"], "hops": 2}},
        method="expand_entities",
        expected_hops=2,
        device_type="busbar",
    ),
    Scenario(
        scenario_id="S15_busbar_get_entity",
        category="device_query",
        query="按 ID 查母线",
        params={"method_params": {"entity_id": "e-busbar-001"}},
        method="get_entity",
        expected_hops=1,
        device_type="busbar",
    ),
    Scenario(
        scenario_id="S16_busbar_search",
        category="device_query",
        query="模糊搜索 '母线'",
        params={"method_params": {"query": "母线", "limit": 10}},
        method="search_entities",
        expected_hops=1,
        device_type="busbar",
    ),
    Scenario(
        scenario_id="S17_busbar_relations",
        category="device_query",
        query="母线所有出边关系",
        params={"method_params": {"entity_id": "e-busbar-001"}},
        method="get_relations",
        expected_hops=1,
        device_type="busbar",
    ),
    Scenario(
        scenario_id="S18_busbar_4hop",
        category="device_query",
        query="母线 → 拓扑全扩展（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-busbar-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        device_type="busbar",
    ),
    # ── 断路器（6）────────────────────────────────────────
    Scenario(
        scenario_id="S19_breaker_3hop",
        category="device_query",
        query="断路器 → 操作记录 → 检修记录",
        params={"method_params": {"seed_entity_ids": ["e-breaker"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
        device_type="circuit_breaker",
    ),
    Scenario(
        scenario_id="S20_breaker_2hop",
        category="device_query",
        query="断路器 → 关联母线 / 线路",
        params={"method_params": {"seed_entity_ids": ["e-breaker"], "hops": 2}},
        method="expand_entities",
        expected_hops=2,
        device_type="circuit_breaker",
    ),
    Scenario(
        scenario_id="S21_breaker_get_entity",
        category="device_query",
        query="按 ID 查断路器",
        params={"method_params": {"entity_id": "e-breaker"}},
        method="get_entity",
        expected_hops=1,
        device_type="circuit_breaker",
    ),
    Scenario(
        scenario_id="S22_breaker_search",
        category="device_query",
        query="模糊搜索 '断路'",
        params={"method_params": {"query": "断路", "limit": 10}},
        method="search_entities",
        expected_hops=1,
        device_type="circuit_breaker",
    ),
    Scenario(
        scenario_id="S23_breaker_relations",
        category="device_query",
        query="断路器所有出边关系",
        params={"method_params": {"entity_id": "e-breaker"}},
        method="get_relations",
        expected_hops=1,
        device_type="circuit_breaker",
    ),
    Scenario(
        scenario_id="S24_breaker_4hop_optimizer",
        category="device_query",
        query="断路器 → 拓扑全扩展（4 跳 + optimizer）",
        params={
            "method_params": {
                "seeds": ["e-breaker"],
                "hops": 4,
                "limit": 30,
            }
        },
        method="expand_with_optimizer",
        expected_hops=4,
        backend_preference="neo4j",
        device_type="circuit_breaker",
    ),
    # ── 保护装置（6）────────────────────────────────────────
    Scenario(
        scenario_id="S25_protection_3hop",
        category="device_query",
        query="保护装置 → 整定值 → 动作记录",
        params={"method_params": {"seed_entity_ids": ["e-relay-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
        device_type="protection_device",
    ),
    Scenario(
        scenario_id="S26_protection_2hop",
        category="device_query",
        query="保护装置 → 关联断路器",
        params={"method_params": {"seed_entity_ids": ["e-relay-001"], "hops": 2}},
        method="expand_entities",
        expected_hops=2,
        device_type="protection_device",
    ),
    Scenario(
        scenario_id="S27_protection_get_entity",
        category="device_query",
        query="按 ID 查保护装置",
        params={"method_params": {"entity_id": "e-relay-001"}},
        method="get_entity",
        expected_hops=1,
        device_type="protection_device",
    ),
    Scenario(
        scenario_id="S28_protection_search",
        category="device_query",
        query="模糊搜索 '保护'",
        params={"method_params": {"query": "保护", "limit": 10}},
        method="search_entities",
        expected_hops=1,
        device_type="protection_device",
    ),
    Scenario(
        scenario_id="S29_protection_relations",
        category="device_query",
        query="保护装置所有出边关系",
        params={"method_params": {"entity_id": "e-relay-001"}},
        method="get_relations",
        expected_hops=1,
        device_type="protection_device",
    ),
    Scenario(
        scenario_id="S30_protection_4hop",
        category="device_query",
        query="保护装置 → 保护链路（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-relay-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        device_type="protection_device",
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
# 2. 因果链（10+ 场景）
# ═════════════════════════════════════════════════════════════════════════════

_CAUSAL_CHAIN_SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="C01_short_circuit_5hop",
        category="causal_chain",
        query="短路 → 跳闸 → 保护动作 → 隔离 → 检修（5 跳）",
        params={"method_params": {"seed_entity_ids": ["e-shortcircuit-001"], "hops": 5}},
        method="expand_entities",
        expected_hops=5,
        backend_preference="neo4j",
        tags=("5hop", "fault"),
    ),
    Scenario(
        scenario_id="C02_overload_4hop",
        category="causal_chain",
        query="过载 → 油温 → 绝缘降低 → 故障（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-overload"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        tags=("4hop", "fault"),
    ),
    Scenario(
        scenario_id="C03_overheat_3hop",
        category="causal_chain",
        query="过热 → 报警 → 跳闸（3 跳）",
        params={"method_params": {"seed_entity_ids": ["e-overheat-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
    ),
    Scenario(
        scenario_id="C04_voltage_deviation_3hop",
        category="causal_chain",
        query="电压偏差 → 保护动作 → 隔离（3 跳）",
        params={"method_params": {"seed_entity_ids": ["e-volt-dev-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
    ),
    Scenario(
        scenario_id="C05_emergency_stop_4hop",
        category="causal_chain",
        query="紧急停运 → 处置 → 检修 → 复电（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-emergency-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
    ),
    Scenario(
        scenario_id="C06_routine_maint_3hop",
        category="causal_chain",
        query="例行检修 → 试验 → 验收（3 跳）",
        params={"method_params": {"seed_entity_ids": ["e-maint-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
    ),
    Scenario(
        scenario_id="C07_transformer_fault_chain",
        category="causal_chain",
        query="变压器故障链：故障 → 保护 → 跳闸 → 隔离 → 检修",
        params={"method_params": {"seed_entity_ids": ["e-tx-001"], "hops": 5}},
        method="expand_entities",
        expected_hops=5,
        device_type="transformer",
    ),
    Scenario(
        scenario_id="C08_line_fault_chain",
        category="causal_chain",
        query="线路故障链：故障 → 跳闸 → 保护 → 隔离",
        params={"method_params": {"seed_entity_ids": ["e-line-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        device_type="line",
    ),
    Scenario(
        scenario_id="C09_breaker_action_chain",
        category="causal_chain",
        query="断路器动作链：跳闸 → 保护 → 隔离 → 检修",
        params={"method_params": {"seed_entity_ids": ["e-breaker"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        device_type="circuit_breaker",
    ),
    Scenario(
        scenario_id="C10_multi_fault_5hop",
        category="causal_chain",
        query="复合故障链：多重故障 → 处置 → 复电（5 跳）",
        params={"method_params": {"seed_entity_ids": ["e-multi-fault"], "hops": 5}},
        method="expand_entities",
        expected_hops=5,
    ),
    Scenario(
        scenario_id="C11_protection_misoperation_4hop",
        category="causal_chain",
        query="保护误动链：误动 → 跳闸 → 失电 → 复电（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-misop-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
        device_type="protection_device",
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
# 3. 规程关联（5+ 场景）
# ═════════════════════════════════════════════════════════════════════════════

_REGULATION_SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="R01_regulation_device_4hop",
        category="regulation_link",
        query="规程 → 适用设备类别 → 设备实例 → 关联规程（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-reg-001"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
    ),
    Scenario(
        scenario_id="R02_regulation_search",
        category="regulation_link",
        query="搜索'检修规程'",
        params={"method_params": {"query": "检修规程", "limit": 10}},
        method="search_entities",
        expected_hops=1,
    ),
    Scenario(
        scenario_id="R03_regulation_relations",
        category="regulation_link",
        query="规程所有出边关系",
        params={"method_params": {"entity_id": "e-reg-001"}},
        method="get_relations",
        expected_hops=1,
    ),
    Scenario(
        scenario_id="R04_regulation_3hop",
        category="regulation_link",
        query="规程 → 适用设备 → 关联事件（3 跳）",
        params={"method_params": {"seed_entity_ids": ["e-reg-001"], "hops": 3}},
        method="expand_entities",
        expected_hops=3,
    ),
    Scenario(
        scenario_id="R05_regulation_5hop",
        category="regulation_link",
        query="规程全链路（5 跳）",
        params={"method_params": {"seed_entity_ids": ["e-reg-001"], "hops": 5}},
        method="expand_entities",
        expected_hops=5,
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
# 4. 跨域推理（5+ 场景）
# ═════════════════════════════════════════════════════════════════════════════

_CROSS_DOMAIN_SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="X01_fault_to_doc_5hop",
        category="cross_domain",
        query="故障 → 处置 → 强制要求 → 适用规程 → 文档（5 跳）",
        params={"method_params": {"seed_entity_ids": ["e-overload"], "hops": 5}},
        method="expand_entities",
        expected_hops=5,
    ),
    Scenario(
        scenario_id="X02_fault_template_optimizer",
        category="cross_domain",
        query="故障因果链模板（execute_template + optimizer 组合）",
        params={
            "method_params": {
                "name": "fault_chain_v1",
                "params": {"fault_id": "e-overload", "max_hops": 3, "limit": 10},
            }
        },
        method="execute_template",
        expected_hops=3,
        backend_preference="neo4j",
    ),
    Scenario(
        scenario_id="X03_fault_4hop_optimizer",
        category="cross_domain",
        query="故障 → 处置（optimizer 加速，4 跳）",
        params={
            "method_params": {
                "seeds": ["e-overload"],
                "hops": 4,
                "limit": 50,
            }
        },
        method="expand_with_optimizer",
        expected_hops=4,
        backend_preference="neo4j",
    ),
    Scenario(
        scenario_id="X04_substation_5hop",
        category="cross_domain",
        query="变电站 → 设备 → 故障 → 处置 → 规程（5 跳）",
        params={"method_params": {"seed_entity_ids": ["e-station-001"], "hops": 5}},
        method="expand_entities",
        expected_hops=5,
    ),
    Scenario(
        scenario_id="X05_grid_overview_4hop",
        category="cross_domain",
        query="电网拓扑总览（4 跳）",
        params={"method_params": {"seed_entity_ids": ["e-grid-root"], "hops": 4}},
        method="expand_entities",
        expected_hops=4,
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
# 5. 公共 API
# ═════════════════════════════════════════════════════════════════════════════

# 全部场景（冻结顺序，确保报告可重现）
ALL_SCENARIOS: list[Scenario] = (
    _DEVICE_SCENARIOS
    + _CAUSAL_CHAIN_SCENARIOS
    + _REGULATION_SCENARIOS
    + _CROSS_DOMAIN_SCENARIOS
)


def get_scenarios(
    category: str | None = None,
    device_type: str | None = None,
) -> list[Scenario]:
    """获取场景列表（可选过滤）。

    Args:
        category: ``"device_query"`` / ``"causal_chain"`` / ``"regulation_link"`` /
            ``"cross_domain"``
        device_type: ``"transformer"`` / ``"line"`` / ``"busbar"`` /
            ``"circuit_breaker"`` / ``"protection_device"``

    Returns:
        匹配的场景列表（保持原顺序）
    """
    out: list[Scenario] = []
    for s in ALL_SCENARIOS:
        if category is not None and s.category != category:
            continue
        if device_type is not None and s.device_type != device_type:
            continue
        out.append(s)
    return out


def get_scenarios_by_hop(hop: int) -> list[Scenario]:
    """按期望跳数过滤场景。"""
    return [s for s in ALL_SCENARIOS if s.expected_hops == hop]


def get_causal_chain_scenarios() -> list[Scenario]:
    """获取所有因果链场景（≥10）。"""
    return get_scenarios(category="causal_chain")


__all__ = [
    "Scenario",
    "ALL_SCENARIOS",
    "get_scenarios",
    "get_scenarios_by_hop",
    "get_causal_chain_scenarios",
]
