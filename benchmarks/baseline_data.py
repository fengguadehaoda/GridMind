"""GridMind 知识图谱 M3b · 性能基准合成数据集（500 节点 / 5000 关系）。

设计目标（kg-m3-split.md §4.2 + Q9=A）
--------
- **规模**：500 节点 / 5000 关系（M1 88/451 的 5-10 倍放大）
- **可重放**：使用固定随机种子（``seed=42``），不同次执行结果完全一致
- **零污染**：不写真实数据库 / 不写 Neo4j；纯内存生成
- **结构真实**：模拟电力设备拓扑（变电站 → 设备 → 故障 → 规程）
- **不依赖网络**：所有数据由 ``random`` + Python stdlib 生成

合成模型
--------
节点类型（5 设备 + 3 故障 + 1 规程 + 1 拓扑根 = 10 类）：

    substation(20) → busbar(60) → device(280) → fault(80) → regulation(40) + grid(20)

关系类型（7 类）：

    LOCATED_IN / CONNECTED_TO / PROTECTS / HAS_FAULT /
    APPLIES_TO / CAUSED_BY / HANDLED_BY

使用::

    from benchmarks.baseline_data import build_baseline_graph
    G = build_baseline_graph(seed=42)  # 500 nodes, 5000 edges
    print(G.number_of_nodes(), G.number_of_edges())

或注入到 ``KGClient``::

    from benchmarks.baseline_data import inject_into_networkx_backend
    inject_into_networkx_backend(client)
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import networkx as nx


# ═════════════════════════════════════════════════════════════════════════════
# 1. 合成模型配置（固定参数）
# ═════════════════════════════════════════════════════════════════════════════

# 节点类型分布（合计 500）
NODE_TYPE_DIST: dict[str, int] = {
    "substation": 20,
    "busbar": 60,
    "transformer": 60,
    "line": 50,
    "circuit_breaker": 60,
    "protection_device": 50,
    "overload_fault": 30,
    "short_circuit_fault": 25,
    "overheat_fault": 25,
    "regulation": 40,
    "grid_root": 1,
    "emergency_measure": 39,
    "maintenance_measure": 40,
}

# 关系类型分布（合计 5000，按权重分配）
RELATION_TYPE_DIST: dict[str, int] = {
    "LOCATED_IN": 600,        # 设备 → 变电站
    "CONNECTED_TO": 1500,     # 设备 ↔ 设备
    "PROTECTS": 400,          # 保护装置 → 断路器 / 变压器
    "HAS_FAULT": 350,         # 设备 → 故障
    "CAUSED_BY": 350,         # 故障 → 故障
    "APPLIES_TO": 800,        # 规程 → 设备
    "HANDLED_BY": 600,        # 故障 → 处置
    "PART_OF": 400,           # 拓扑关系
}

# 节点 ID 前缀（按类型）
NODE_ID_PREFIX: dict[str, str] = {
    "substation": "e-sub",
    "busbar": "e-bus",
    "transformer": "e-tx",
    "line": "e-line",
    "circuit_breaker": "e-cb",
    "protection_device": "e-relay",
    "overload_fault": "e-fault-ov",
    "short_circuit_fault": "e-fault-sc",
    "overheat_fault": "e-fault-ot",
    "regulation": "e-reg",
    "grid_root": "e-grid",
    "emergency_measure": "e-emer",
    "maintenance_measure": "e-maint",
}

# 节点名称模板（按类型）
NODE_NAME_TPL: dict[str, str] = {
    "substation": "{key}号变电站",
    "busbar": "{key}号母线",
    "transformer": "{key}号主变",
    "line": "{key}号线路",
    "circuit_breaker": "{key}号断路器",
    "protection_device": "{key}号保护装置",
    "overload_fault": "过载故障{key}",
    "short_circuit_fault": "短路故障{key}",
    "overheat_fault": "过热故障{key}",
    "regulation": "{key}号检修规程",
    "grid_root": "电网根节点",
    "emergency_measure": "{key}号紧急处置",
    "maintenance_measure": "{key}号例行检修",
}

# 关系标签
RELATION_LABEL: dict[str, str] = {
    "LOCATED_IN": "位于",
    "CONNECTED_TO": "连接",
    "PROTECTS": "保护",
    "HAS_FAULT": "存在故障",
    "CAUSED_BY": "由...引起",
    "APPLIES_TO": "适用",
    "HANDLED_BY": "由...处置",
    "PART_OF": "属于",
}

# 期望规模（验收 5：报告数字可重现）
EXPECTED_NODES = 500
EXPECTED_EDGES = 5000


@dataclass(frozen=True)
class BaselineDataset:
    """合成数据集摘要。"""

    nodes: int
    edges: int
    seed: int
    type_dist: dict[str, int]
    relation_dist: dict[str, int]
    graph: nx.DiGraph


# ═════════════════════════════════════════════════════════════════════════════
# 2. 数据生成
# ═════════════════════════════════════════════════════════════════════════════

def _build_node_id(node_type: str, index: int) -> str:
    """生成稳定的节点 ID（``e-tx-001`` / ``e-tx-002`` ...）。"""
    return f"{NODE_ID_PREFIX[node_type]}-{index:03d}"


def _build_node_name(node_type: str, index: int) -> str:
    """生成节点名称。"""
    tpl = NODE_NAME_TPL.get(node_type, "{key}号节点")
    return tpl.format(key=index)


def _generate_nodes(rng: random.Random) -> list[tuple[str, str, str, dict[str, Any]]]:
    """生成所有节点（id, type, name, properties）。"""
    nodes: list[tuple[str, str, str, dict[str, Any]]] = []
    for node_type, count in NODE_TYPE_DIST.items():
        for i in range(1, count + 1):
            nid = _build_node_id(node_type, i)
            name = _build_node_name(node_type, i)
            # 模拟 properties（M0 兼容）
            props: dict[str, Any] = {
                "type": node_type,
                "voltage_level": rng.choice(["10kV", "35kV", "110kV", "220kV", "500kV"]),
                "status": rng.choice(["运行", "检修", "备用"]),
                "commission_date": f"20{rng.randint(10, 24):02d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            }
            nodes.append((nid, node_type, name, props))
    return nodes


def _group_nodes_by_type(
    nodes: list[tuple[str, str, str, dict[str, Any]]],
) -> dict[str, list[str]]:
    """按类型分组节点 ID。"""
    out: dict[str, list[str]] = {}
    for nid, ntype, _, _ in nodes:
        out.setdefault(ntype, []).append(nid)
    return out


def _generate_relations(
    rng: random.Random,
    nodes_by_type: dict[str, list[str]],
) -> list[tuple[str, str, str]]:
    """生成所有关系（source, target, rel_type）。"""
    rels: list[tuple[str, str, str]] = []

    # ── LOCATED_IN：所有设备 → 随机变电站 ──────────────────
    device_types = [
        "busbar", "transformer", "line", "circuit_breaker", "protection_device",
    ]
    for dtype in device_types:
        for did in nodes_by_type.get(dtype, []):
            # 每个设备连接到 2 个变电站（增加密度到 ~600 LOCATED_IN）
            targets = rng.sample(
                nodes_by_type["substation"],
                min(2, len(nodes_by_type["substation"])),
            )
            for target in targets:
                rels.append((did, target, "LOCATED_IN"))

    # ── CONNECTED_TO：设备 ↔ 设备（最密集）──────────────
    for dtype in device_types:
        ids = nodes_by_type.get(dtype, [])
        for nid in ids:
            # 每个设备连接 9-15 个其他设备
            n_conn = rng.randint(9, 15)
            targets = rng.sample(ids, min(n_conn, len(ids)))
            for tgt in targets:
                if tgt != nid:
                    rels.append((nid, tgt, "CONNECTED_TO"))

    # ── PROTECTS：保护装置 → 断路器 / 变压器 ─────────────
    for rid in nodes_by_type.get("protection_device", []):
        n = rng.randint(2, 4)
        targets = rng.sample(
            nodes_by_type.get("circuit_breaker", [])
            + nodes_by_type.get("transformer", []),
            min(n, len(nodes_by_type.get("circuit_breaker", [])
                       + nodes_by_type.get("transformer", []))),
        )
        for tgt in targets:
            rels.append((rid, tgt, "PROTECTS"))

    # ── HAS_FAULT：设备 → 故障 ─────────────────────────
    fault_types = ["overload_fault", "short_circuit_fault", "overheat_fault"]
    for dtype in device_types:
        for did in nodes_by_type.get(dtype, []):
            n = rng.randint(0, 2)
            if n > 0:
                ft = rng.choice(fault_types)
                fids = nodes_by_type.get(ft, [])
                if fids:
                    target = rng.choice(fids)
                    rels.append((did, target, "HAS_FAULT"))

    # ── CAUSED_BY：故障 → 故障（因果）──────────────────
    for ft in fault_types:
        for fid in nodes_by_type.get(ft, []):
            other = rng.choice(fault_types)
            if other != ft:
                targets = rng.sample(
                    nodes_by_type.get(other, []),
                    min(1, len(nodes_by_type.get(other, []))),
                )
                for tgt in targets:
                    rels.append((fid, tgt, "CAUSED_BY"))

    # ── APPLIES_TO：规程 → 设备 ─────────────────────────
    for rid in nodes_by_type.get("regulation", []):
        n = rng.randint(10, 20)
        all_devices = []
        for dt in device_types:
            all_devices.extend(nodes_by_type.get(dt, []))
        targets = rng.sample(all_devices, min(n, len(all_devices)))
        for tgt in targets:
            rels.append((rid, tgt, "APPLIES_TO"))

    # ── HANDLED_BY：故障 → 处置 ─────────────────────────
    measures = nodes_by_type.get("emergency_measure", []) + nodes_by_type.get(
        "maintenance_measure", []
    )
    for ft in fault_types:
        for fid in nodes_by_type.get(ft, []):
            if measures:
                target = rng.choice(measures)
                rels.append((fid, target, "HANDLED_BY"))

    # ── PART_OF：电网根 → 变电站；母线 → 变电站 ─────────
    grid_root = nodes_by_type.get("grid_root", ["e-grid-001"])[0]
    for sid in nodes_by_type.get("substation", []):
        rels.append((grid_root, sid, "PART_OF"))
    for bid in nodes_by_type.get("busbar", []):
        # 母线 → 变电站（每个母线都连）
        for target in nodes_by_type.get("substation", []):
            rels.append((bid, target, "PART_OF"))

    return rels


def _trim_to_target(
    rng: random.Random,
    rels: list[tuple[str, str, str]],
    target: int,
) -> list[tuple[str, str, str]]:
    """将关系裁剪到目标数量（去重 + 随机采样）。"""
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for r in rels:
        if r not in seen:
            seen.add(r)
            out.append(r)
    rng.shuffle(out)
    if len(out) > target:
        out = out[:target]
    elif len(out) < target:
        # 不够 → 补一些随机 self-loop-free 关系（保持规模稳定）
        # 用现有节点扩展
        # 实际场景几乎不会触发；兜底即可
        pass
    return out


def build_baseline_graph(seed: int = 42) -> BaselineDataset:
    """构建合成数据集（500 节点 / 5000 关系，固定种子可重放）。

    Args:
        seed: 随机种子（默认 42，与 Q9=A 一致）

    Returns:
        ``BaselineDataset``，含 nx.DiGraph + 摘要元数据
    """
    rng = random.Random(seed)
    nodes = _generate_nodes(rng)
    nodes_by_type = _group_nodes_by_type(nodes)
    rels = _generate_relations(rng, nodes_by_type)
    rels = _trim_to_target(rng, rels, EXPECTED_EDGES)

    # 构建 NetworkX 图
    g = nx.DiGraph()
    for nid, ntype, name, props in nodes:
        g.add_node(nid, name=name, type=ntype, properties=props)

    for src, tgt, rtype in rels:
        # 跳过自环
        if src == tgt:
            continue
        g.add_edge(src, tgt, label=RELATION_LABEL.get(rtype, rtype), rel_type=rtype)

    return BaselineDataset(
        nodes=g.number_of_nodes(),
        edges=g.number_of_edges(),
        seed=seed,
        type_dist=NODE_TYPE_DIST,
        relation_dist=RELATION_TYPE_DIST,
        graph=g,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3. 注入到 KGClient（用于跑基准）
# ═════════════════════════════════════════════════════════════════════════════

def inject_into_networkx_backend(client: Any, seed: int = 42) -> int:
    """将合成数据集注入到 ``client`` 的 NetworkX backend。

    注意：直接替换 ``client.backend._kg.graph``，仅供基准脚本使用。
    真实业务代码不应调用此函数。

    Args:
        client: ``KGClient`` 实例（``neo4j_enabled=False`` 时为 NetworkXBackend）
        seed: 随机种子

    Returns:
        注入的节点数
    """
    dataset = build_baseline_graph(seed=seed)
    backend = client.backend
    if backend.name != "networkx":
        # Neo4j 模式不注入（避免污染），返回 0
        return 0
    # 直接替换图对象
    backend._kg.graph = dataset.graph
    return dataset.nodes


def get_dataset_summary(seed: int = 42) -> dict[str, Any]:
    """返回合成数据集的摘要（不构造完整图；用于快速报告）。"""
    rng = random.Random(seed)
    return {
        "seed": seed,
        "expected_nodes": EXPECTED_NODES,
        "expected_edges": EXPECTED_EDGES,
        "node_type_dist": dict(NODE_TYPE_DIST),
        "relation_type_dist": dict(RELATION_TYPE_DIST),
    }


__all__ = [
    "BaselineDataset",
    "EXPECTED_NODES",
    "EXPECTED_EDGES",
    "NODE_TYPE_DIST",
    "RELATION_TYPE_DIST",
    "build_baseline_graph",
    "inject_into_networkx_backend",
    "get_dataset_summary",
]
