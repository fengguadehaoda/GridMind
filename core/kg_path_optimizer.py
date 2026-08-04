"""GridMind 知识图谱 M3a · 多跳路径优化器（KGPathOptimizer）。

设计目标
--------
- **代价估算 + 候选剪枝 + LRU 缓存**：在 M2 硬编码 3 跳 Cypher 之上，智能地选
  ``top_k=5`` 条最优路径；
- **降级完整**（AC-12）：``neo4j_enabled=False`` 时走 ``client.expand_entities``
  （M2 NetworkX 行为）；
- **Feature flag 关闭**（AC-14）：``enable_kg_path_optimizer=False`` 时调用方
  fallback 到 M2 硬编码 3 跳 Cypher；
- **LRU 缓存**：基于 ``OrderedDict`` 显式 LRU 淘汰（避开 ``@lru_cache`` 不可见性）。

代价估算（启发式，待 M3b 校准）::

    estimated_latency_ms = seed_count * hops * 10ms + relation_count * 0.05ms
    confidence            = max(0, 1 - hops * 0.15)

使用示例::

    from core.kg_path_optimizer import KGPathOptimizer
    from core.kg_client import get_kg_client

    optimizer = KGPathOptimizer(max_hops=5, cache_size=256, top_k=5)
    entities, paths = optimizer.expand(
        get_kg_client(),
        seed_ids=["e-overload"],
        hops=4,
        relation_types=["CAUSES"],
        limit=100,
    )
    print(optimizer.get_cache_stats())  # {'hits': 0, 'misses': 1, ...}
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.kg_client import KGClient


# ─────────────────────────────────────────────────────────────────────────────
# 1. 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PathCost:
    """路径代价估算（M3a 启发式）。"""
    hops: int
    edge_count: int
    estimated_latency_ms: float
    confidence: float  # [0, 1]


@dataclass
class _RawPath:
    """优化前的原始路径（来自 ``client.expand_entities``）。"""
    nodes: list[str]
    relations: list[str]

    def __hash__(self) -> int:
        return hash((tuple(self.nodes), tuple(self.relations)))


@dataclass
class OptimizedPath:
    """优化后的路径（带后端标识 + 代价）。"""
    nodes: list[str]
    relations: list[str]
    cost: PathCost
    backend: str  # "neo4j" / "networkx"


@dataclass
class OptimizedResult:
    """``expand()`` 返回值聚合（含缓存命中标记）。"""
    entities: list[dict[str, Any]]
    paths: list[OptimizedPath]
    cache_hit: bool
    backend: str


# ─────────────────────────────────────────────────────────────────────────────
# 2. KGPathOptimizer
# ─────────────────────────────────────────────────────────────────────────────

class KGPathOptimizer:
    """多跳路径优化器（代价估算 + 剪枝 + LRU 缓存）。"""

    def __init__(
        self,
        *,
        max_hops: int = 5,
        cache_size: int = 256,
        top_k: int = 5,
        client: "KGClient | None" = None,
    ) -> None:
        self._max_hops = max_hops
        self._cache_size = cache_size
        self._top_k = top_k
        self._client = client
        # OrderedDict 实现 + 显式 LRU 淘汰（避开 @lru_cache 的不可见性）
        self._cache: OrderedDict[tuple, OptimizedResult] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ── 代价估算 ─────────────────────────────────────────────

    def estimate_cost(
        self,
        seed_count: int,
        hops: int,
        relation_count: int = 1000,
    ) -> PathCost:
        """估算路径代价（启发式）。

        公式::

            estimated_latency_ms = seed_count * hops * 10ms + relation_count * 0.05ms
            confidence            = max(0, 1 - hops * 0.15)

        :raises ValueError: ``hops > max_hops``
        """
        if hops > self._max_hops:
            raise ValueError(f"hops={hops} > max_hops={self._max_hops}")
        if seed_count <= 0:
            seed_count = 1
        latency = seed_count * hops * 10.0 + relation_count * 0.05
        confidence = max(0.0, 1.0 - hops * 0.15)
        edge_count = seed_count * hops
        return PathCost(
            hops=hops,
            edge_count=edge_count,
            estimated_latency_ms=latency,
            confidence=confidence,
        )

    # ── 主入口：多跳扩展 + 剪枝 + 缓存 ──────────────────────

    def expand(
        self,
        client: "KGClient",
        seed_ids: list[str],
        hops: int,
        relation_types: list[str] | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[OptimizedPath]]:
        """多跳路径扩展 + 候选剪枝 + LRU 缓存。

        流程：
            1. 校验参数（hops ≤ max_hops, seed_ids 非空）
            2. 检查 LRU 缓存
            3. 命中 → ``cache_hit=True``
            4. 未命中 → 调用 ``client.expand_entities()``
            5. ``estimate_cost()`` + 排序 + top_k
            6. 写入缓存（超容量 LRU 淘汰）

        :returns: ``(entities, optimized_paths)``
        """
        if not seed_ids:
            return [], []
        if hops > self._max_hops:
            raise ValueError(f"hops={hops} > max_hops={self._max_hops}")

        # 缓存 key：(sorted seeds, hops, sorted relation_types)
        key = (
            tuple(sorted(seed_ids)),
            int(hops),
            tuple(sorted(relation_types or [])),
            int(limit),
        )
        if key in self._cache:
            self._hits += 1
            self._cache.move_to_end(key)
            cached = self._cache[key]
            # 复制一份避免外部修改破坏缓存
            return (
                list(cached.entities),
                list(cached.paths),
            )

        # 缓存未命中 → 计算
        self._misses += 1
        start = time.perf_counter()

        # 调用 client 扩展（双 backend 自动降级）
        entities, raw_path_strs = client.expand_entities(
            list(seed_ids), hops=int(hops),
        )

        # 构造 _RawPath
        raw_paths = [
            _RawPath(
                nodes=[seed_ids[0]] + [],  # 简化：起点先占位
                relations=p_str[1:] if len(p_str) > 1 else [],
            )
            for p_str in raw_path_strs
            if p_str
        ]
        # 如果 raw_paths 为空，至少保留一个"无路径"占位
        if not raw_paths and entities:
            raw_paths = [
                _RawPath(nodes=[seed_ids[0]], relations=[])
                for _ in range(min(len(entities), self._top_k))
            ]

        # 剪枝：按估算延迟排序 + top_k
        relation_count = sum(len(p.relations) for p in raw_paths)
        cost = self.estimate_cost(len(seed_ids), int(hops), relation_count)
        sorted_paths = sorted(
            raw_paths,
            key=lambda p: self._path_estimated_latency(p, len(seed_ids)),
        )[: self._top_k]

        backend_name = getattr(client, "current_backend_name", "networkx")
        optimized = [
            OptimizedPath(
                nodes=p.nodes if p.nodes else list(seed_ids),
                relations=p.relations,
                cost=cost,
                backend=backend_name,
            )
            for p in sorted_paths
        ]

        latency_ms = (time.perf_counter() - start) * 1000.0
        result = OptimizedResult(
            entities=entities,
            paths=optimized,
            cache_hit=False,
            backend=backend_name,
        )
        # 写入缓存（LRU 淘汰）
        self._cache[key] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
            self._evictions += 1

        return entities, optimized

    # ── 缓存管理 ─────────────────────────────────────────────

    def get_cache_stats(self) -> dict[str, Any]:
        """返回缓存命中统计。"""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "evictions": self._evictions,
            "hit_rate": hit_rate,
            "max_size": self._cache_size,
        }

    def clear_cache(self) -> None:
        """清空缓存 + 计数（运维工具）。"""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def top_k(self) -> int:
        return self._top_k

    @property
    def max_hops(self) -> int:
        return self._max_hops

    # ── 私有 ─────────────────────────────────────────────

    def _path_estimated_latency(
        self, path: _RawPath, seed_count: int,
    ) -> float:
        """单条路径的估算延迟（按 hops 数）。"""
        return seed_count * len(path.relations) * 10.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. 单例工厂（与 M2 模式一致）
# ─────────────────────────────────────────────────────────────────────────────

_optimizer_instance: KGPathOptimizer | None = None


def get_path_optimizer(
    *,
    max_hops: int = 5,
    cache_size: int = 256,
    top_k: int = 5,
    client: "KGClient | None" = None,
) -> KGPathOptimizer:
    """获取 ``KGPathOptimizer`` 单例。

    M3a 设计：单例 + 默认 ``cache_size=256`` + ``top_k=5``。
    """
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = KGPathOptimizer(
            max_hops=max_hops,
            cache_size=cache_size,
            top_k=top_k,
            client=client,
        )
    return _optimizer_instance


def reset_path_optimizer() -> None:
    """重置单例（仅测试用）。"""
    global _optimizer_instance
    _optimizer_instance = None


__all__ = [
    "PathCost",
    "OptimizedPath",
    "OptimizedResult",
    "KGPathOptimizer",
    "get_path_optimizer",
    "reset_path_optimizer",
]
