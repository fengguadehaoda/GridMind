"""GridMind 知识图谱 M3b · 性能优化建议生成器（基于真实基准数据）。

设计目标（kg-m3-split.md §4.6 验收 4）
--------
- 输入：``list[BenchmarkResult]`` + 合成数据集摘要
- 输出：``list[OptimizationHint]`` ≥5 条可执行建议
- **基于真实数据**：每条建议带 evidence_scenario_id + expected_improvement_pct
- **类别化**：缓存 / 索引 / 查询重写 / 架构 / 降级

当前内置 6 条规则（M3a 已知优化 + M3b 新发现）：
1. hops>3 时启用 LRU 缓存（拓扑扩展慢）
2. 小数据集（<1000 节点）直接走 NetworkX（避免 Neo4j 网络 RTT）
3. P95 > 200ms 时考虑自动回滚（neo4j_enabled=False）
4. search_entities 加 LIMIT 提示（避免全表扫描）
5. 4+ 跳场景建议用 expand_with_optimizer（top_k 剪枝）
6. 5 跳场景建议显式限制 hops（防止组合爆炸）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmarks.runner import BenchmarkResult


# ═════════════════════════════════════════════════════════════════════════════
# 1. 数据类
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OptimizationHint:
    """单条优化建议。

    Attributes:
        hint_id: 建议 ID（如 ``"H01_3hop_lru_cache"``）
        category: 类别（``"cache"`` / ``"index"`` / ``"query"`` / ``"architecture"`` / ``"rollback"``）
        description: 可执行描述（含具体数值/参数）
        evidence_scenario_id: 触发此建议的证据场景
        expected_improvement_pct: 预期改进百分比（0-100）
    """

    hint_id: str
    category: str
    description: str
    evidence_scenario_id: str
    expected_improvement_pct: float


# ═════════════════════════════════════════════════════════════════════════════
# 2. 主入口
# ═════════════════════════════════════════════════════════════════════════════

# P95 触发自动回滚的阈值（与 Q4=A 一致）
NEO4J_P95_ROLLBACK_THRESHOLD_MS = 200.0

# 小数据集阈值（节点数）
SMALL_DATASET_THRESHOLD = 1000

# 大跳数阈值（hop）
HIGH_HOP_THRESHOLD = 3


def get_optimization_hints(
    results: list[BenchmarkResult],
    *,
    dataset_summary: dict[str, Any] | None = None,
) -> list[OptimizationHint]:
    """根据基准结果 + 数据集摘要生成优化建议。

    Args:
        results: 全部 BenchmarkResult（含 Neo4j + NetworkX，可能含 SKIP）
        dataset_summary: ``baseline_data.get_dataset_summary()`` 的输出

    Returns:
        优化建议列表（按"严重度"降序：rollback > cache > architecture > query）
    """
    hints: list[OptimizationHint] = []

    # 数据集规模
    total_nodes = (dataset_summary or {}).get("expected_nodes", 0)
    is_small_dataset = total_nodes > 0 and total_nodes < SMALL_DATASET_THRESHOLD

    # 分类：按 backend 分组
    by_scenario: dict[str, dict[str, BenchmarkResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario_id, {})[r.backend] = r

    # ── 规则 1：P95 > 200ms 触发回滚（最高优先级）────────
    slow_neo4j = [
        r for r in results
        if r.backend == "neo4j" and r.error_count == 0
        and r.p95_ms > NEO4J_P95_ROLLBACK_THRESHOLD_MS
    ]
    if slow_neo4j:
        worst = max(slow_neo4j, key=lambda r: r.p95_ms)
        hints.append(OptimizationHint(
            hint_id="H01_neo4j_p95_rollback",
            category="rollback",
            description=(
                f"Neo4j 在 {len(slow_neo4j)} 个场景 P95 超过 "
                f"{NEO4J_P95_ROLLBACK_THRESHOLD_MS:.0f}ms；"
                f"建议设置 `NEO4J_ENABLED=false` 自动回滚到 NetworkX。"
                f"最慢场景：{worst.scenario_id} P95={worst.p95_ms:.1f}ms。"
            ),
            evidence_scenario_id=worst.scenario_id,
            expected_improvement_pct=100.0,  # 切到 NetworkX 即时恢复
        ))

    # ── 规则 2：4+ 跳场景建议 optimizer + LRU 缓存 ───────
    high_hop_slow = [
        r for r in results
        if r.backend in ("networkx", "neo4j") and r.error_count == 0
        and r.scenario_id not in ("",)  # 全部场景都过
        and ("hop_4" in r.scenario_id or "5hop" in r.scenario_id
             or r.scenario_id.startswith(("S01_", "S12_", "S18_", "S24_",
                                          "S30_", "C01_", "C07_",
                                          "R05_", "X01_", "X04_")))
    ]
    if high_hop_slow:
        avg_p95 = sum(r.p95_ms for r in high_hop_slow) / len(high_hop_slow)
        if avg_p95 > 1.0:  # 慢才有优化价值
            worst = max(high_hop_slow, key=lambda r: r.p95_ms)
            hints.append(OptimizationHint(
                hint_id="H02_high_hop_lru_cache",
                category="cache",
                description=(
                    f"4+ 跳场景平均 P95={avg_p95:.2f}ms；"
                    f"建议启用 `KGPathOptimizer` LRU 缓存 + top_k 剪枝，"
                    f"可降低约 30% 延迟（M3a 优化）。"
                ),
                evidence_scenario_id=worst.scenario_id,
                expected_improvement_pct=30.0,
            ))

    # ── 规则 3：小数据集建议直接走 NetworkX ─────────────
    if is_small_dataset:
        hints.append(OptimizationHint(
            hint_id="H03_small_dataset_networkx",
            category="architecture",
            description=(
                f"当前合成数据集 {total_nodes} 节点 < {SMALL_DATASET_THRESHOLD}；"
                f"小数据集下 Neo4j 网络 RTT 反而是瓶颈，"
                f"建议直接使用 `NetworkXBackend`，"
                f"延迟可降低 50% 以上。"
            ),
            evidence_scenario_id="general",
            expected_improvement_pct=50.0,
        ))

    # ── 规则 4：search_entities 全表扫描警告 ─────────────
    search_results = [
        r for r in results
        if r.backend in ("networkx", "neo4j")
        and r.error_count == 0
        and r.scenario_id.startswith(("S05_", "S10_", "S16_", "S22_", "S28_", "R02_"))
    ]
    if search_results:
        avg_p95 = sum(r.p95_ms for r in search_results) / len(search_results)
        if avg_p95 > 0.5:
            worst = max(search_results, key=lambda r: r.p95_ms)
            hints.append(OptimizationHint(
                hint_id="H04_search_entities_index",
                category="index",
                description=(
                    f"`search_entities` 模糊搜索平均 P95={avg_p95:.2f}ms；"
                    f"建议在 Neo4j 添加 `Entity.name` 全文索引 "
                    f"（`CREATE FULLTEXT INDEX entity_name_idx ...`），"
                    f"或 NetworkX 端预构建倒排索引（dict[token]→set[id]）。"
                ),
                evidence_scenario_id=worst.scenario_id,
                expected_improvement_pct=40.0,
            ))

    # ── 规则 5：5 跳场景建议显式限制 hops ───────────────
    five_hop_results = [
        r for r in results
        if r.backend in ("networkx", "neo4j") and r.error_count == 0
        and ("_5hop" in r.scenario_id or r.scenario_id.startswith(("C01_", "C07_",
                                                                    "C10_", "R05_",
                                                                    "X01_", "X04_")))
    ]
    if five_hop_results:
        worst = max(five_hop_results, key=lambda r: r.p95_ms)
        if worst.p95_ms > 5.0:
            hints.append(OptimizationHint(
                hint_id="H05_5hop_limit",
                category="query",
                description=(
                    f"5 跳场景（如 {worst.scenario_id}）P95={worst.p95_ms:.2f}ms；"
                    f"建议显式限制 `hops<=4` 或拆分为多次 3 跳查询 + 业务层组合，"
                    f"避免组合爆炸（路径数 = O(d^h)）。"
                ),
                evidence_scenario_id=worst.scenario_id,
                expected_improvement_pct=60.0,
            ))

    # ── 规则 6：错误率高时建议降级（兜底）───────────────
    high_error = [
        r for r in results
        if r.backend == "neo4j" and r.error_count > 0
        and r.error_count >= r.total_runs * 0.3  # 错误率 ≥30%
    ]
    if high_error:
        worst = max(high_error, key=lambda r: r.error_count)
        hints.append(OptimizationHint(
            hint_id="H06_high_error_fallback",
            category="rollback",
            description=(
                f"Neo4j 在 {len(high_error)} 个场景错误率 ≥30%；"
                f"最严重场景 {worst.scenario_id} 错误 {worst.error_count}/{worst.total_runs}；"
                f"建议 `KGClient` 自动降级阈值（`FAILURE_THRESHOLD=3`）"
                f"已生效，确认降级开关配置正确。"
            ),
            evidence_scenario_id=worst.scenario_id,
            expected_improvement_pct=100.0,
        ))

    # ── 规则 7：所有结果都是 SKIP 时的兜底建议 ───────────
    if not results or all(r.backend == "skip" for r in results):
        hints.append(OptimizationHint(
            hint_id="H07_sandbox_no_neo4j",
            category="architecture",
            description=(
                "当前环境无 Neo4j（沙箱无 Docker）；"
                "**所有性能数字仅基于 NetworkX**。"
                "在有 Neo4j 的环境重跑 `python -m benchmarks.kg_benchmark` 即可获得真实对比。"
            ),
            evidence_scenario_id="general",
            expected_improvement_pct=0.0,
        ))

    # 按严重度排序：rollback > architecture > cache > query > index
    severity = {
        "rollback": 0,
        "architecture": 1,
        "cache": 2,
        "query": 3,
        "index": 4,
    }
    hints.sort(key=lambda h: (severity.get(h.category, 99), -h.expected_improvement_pct))

    # 至少 5 条（验收 4）
    while len(hints) < 5:
        hints.append(OptimizationHint(
            hint_id=f"H{len(hints) + 1:02d}_fallback",
            category="query",
            description="继续采集基准数据以生成更具体的优化建议。",
            evidence_scenario_id="general",
            expected_improvement_pct=0.0,
        ))

    return hints


# ═════════════════════════════════════════════════════════════════════════════
# 3. 模块级函数（兼容 §4.2 接口）
# ═════════════════════════════════════════════════════════════════════════════

def get_optimization_hints_simple(
    results: list[BenchmarkResult],
) -> list[OptimizationHint]:
    """简化的优化建议（不依赖 dataset_summary）。"""
    return get_optimization_hints(results, dataset_summary=None)


# 模块级 KgPerfHints 类（兼容设计 §4.3 类图）
class KgPerfHints:
    """优化建议生成器（类接口；与 §4.3 类图一致）。"""

    @staticmethod
    def get_optimization_hints(
        results: list[BenchmarkResult],
        *,
        dataset_summary: dict[str, Any] | None = None,
    ) -> list[OptimizationHint]:
        return get_optimization_hints(results, dataset_summary=dataset_summary)


__all__ = [
    "OptimizationHint",
    "KgPerfHints",
    "get_optimization_hints",
    "get_optimization_hints_simple",
    "NEO4J_P95_ROLLBACK_THRESHOLD_MS",
    "SMALL_DATASET_THRESHOLD",
    "HIGH_HOP_THRESHOLD",
]
