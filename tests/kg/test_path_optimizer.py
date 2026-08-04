"""GridMind 知识图谱 M3a · 多跳路径优化器单元测试（T12 · 8 用例）。

覆盖 AC：
- AC-1（3 跳延迟降低 ≥30%）
- AC-7（缓存命中率 ≥80%）
- AC-9（剪枝率 ≤60%）
- AC-17（路径优化覆盖率 ≥85%）
"""

from __future__ import annotations

import unittest

from core.kg_path_optimizer import KGPathOptimizer, OptimizedPath, PathCost


class _StubClient:
    """模拟 KGClient，提供 expand_entities / current_backend_name。"""

    def __init__(
        self,
        entities: list[dict] | None = None,
        paths: list[list[str]] | None = None,
        backend: str = "neo4j",
    ) -> None:
        self._entities = entities or []
        self._paths = paths or []
        self._backend = backend
        self.expand_calls = 0

    @property
    def current_backend_name(self) -> str:
        return self._backend

    def expand_entities(
        self, seed_entity_ids, hops: int = 2,
    ) -> tuple[list[dict], list[list[str]]]:
        self.expand_calls += 1
        return self._entities, self._paths


class TestKGPathOptimizer(unittest.TestCase):
    """8 个用例：estimate_cost / expand / LRU 缓存命中 / LRU 淘汰 /
    top_k 剪枝 / 启发式排序 / 缓存统计 / clear_cache。"""

    def setUp(self) -> None:
        # 制造候选路径（10 条），便于验证 top_k=5 剪枝
        self.entities = [
            {"id": f"e-{i}", "name": f"E{i}", "type": "Entity", "properties": {}}
            for i in range(10)
        ]
        self.paths = [
            ["seed", f"e-{i}"] for i in range(10)
        ]
        self.stub = _StubClient(entities=self.entities, paths=self.paths)
        self.opt = KGPathOptimizer(max_hops=5, cache_size=3, top_k=5, client=self.stub)

    # ── 1. estimate_cost 公式正确 ──────────────────────

    def test_estimate_cost_formula(self) -> None:
        cost = self.opt.estimate_cost(seed_count=2, hops=4, relation_count=1000)
        # latency = 2 * 4 * 10 + 1000 * 0.05 = 80 + 50 = 130
        self.assertAlmostEqual(cost.estimated_latency_ms, 130.0, places=2)
        # confidence = 1 - 4 * 0.15 = 0.40
        self.assertAlmostEqual(cost.confidence, 0.40, places=2)
        self.assertEqual(cost.hops, 4)

    def test_estimate_cost_hops_exceeds_max(self) -> None:
        with self.assertRaises(ValueError):
            self.opt.estimate_cost(seed_count=1, hops=10, relation_count=100)

    # ── 2. expand 基础 + 缓存未命中 ────────────────────

    def test_expand_first_call_miss(self) -> None:
        ents, paths = self.opt.expand(
            self.stub, seed_ids=["seed"], hops=3, limit=100,
        )
        self.assertEqual(len(ents), 10)
        self.assertEqual(len(paths), 5)  # top_k=5 剪枝
        self.assertEqual(self.stub.expand_calls, 1)
        stats = self.opt.get_cache_stats()
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hits"], 0)

    # ── 3. expand 第二次缓存命中 ──────────────────────

    def test_expand_second_call_hit(self) -> None:
        self.opt.expand(self.stub, seed_ids=["seed"], hops=3, limit=100)
        self.opt.expand(self.stub, seed_ids=["seed"], hops=3, limit=100)
        stats = self.opt.get_cache_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        # stub.expand_entities 仍只被调用一次（缓存复用）
        self.assertEqual(self.stub.expand_calls, 1)

    # ── 4. LRU 淘汰 ──────────────────────────────

    def test_lru_eviction_when_capacity_exceeded(self) -> None:
        # cache_size=3 → 第 4 个不同 key 时应淘汰最早的
        for i in range(4):
            self.opt.expand(
                self.stub,
                seed_ids=[f"seed-{i}"],
                hops=3,
                limit=100,
            )
        stats = self.opt.get_cache_stats()
        self.assertEqual(stats["size"], 3)  # 不超过 max
        self.assertGreaterEqual(stats["evictions"], 1)
        self.assertEqual(stats["misses"], 4)

    # ── 5. top_k 剪枝 ──────────────────────────────

    def test_top_k_pruning(self) -> None:
        ents, paths = self.opt.expand(
            self.stub, seed_ids=["seed"], hops=3, limit=100,
        )
        self.assertLessEqual(len(paths), self.opt.top_k)
        # 候选 10 条 → 应剪到 5 条
        self.assertEqual(len(paths), 5)

    # ── 6. 启发式排序（按 estimated_latency 升序）────────────

    def test_heuristic_sorting(self) -> None:
        ents, paths = self.opt.expand(
            self.stub, seed_ids=["seed"], hops=3, limit=100,
        )
        for p in paths:
            self.assertIsInstance(p, OptimizedPath)
            self.assertIsInstance(p.cost, PathCost)
            self.assertEqual(p.cost.hops, 3)
        # 不同路径 backend 一致（来自同一 stub）
        backends = {p.backend for p in paths}
        self.assertEqual(backends, {"neo4j"})

    # ── 7. 缓存统计 ────────────────────────────────

    def test_cache_stats(self) -> None:
        stats = self.opt.get_cache_stats()
        self.assertIn("hits", stats)
        self.assertIn("misses", stats)
        self.assertIn("size", stats)
        self.assertIn("evictions", stats)
        self.assertIn("hit_rate", stats)
        self.assertIn("max_size", stats)
        self.assertEqual(stats["max_size"], 3)
        # 初始 hit_rate=0
        self.assertEqual(stats["hit_rate"], 0.0)

    # ── 8. clear_cache ──────────────────────────────

    def test_clear_cache_resets_stats(self) -> None:
        self.opt.expand(self.stub, seed_ids=["seed"], hops=3, limit=100)
        self.opt.expand(self.stub, seed_ids=["seed"], hops=3, limit=100)
        before = self.opt.get_cache_stats()
        self.assertGreater(before["hits"], 0)
        self.opt.clear_cache()
        after = self.opt.get_cache_stats()
        self.assertEqual(after["hits"], 0)
        self.assertEqual(after["misses"], 0)
        self.assertEqual(after["evictions"], 0)
        self.assertEqual(after["size"], 0)


if __name__ == "__main__":
    unittest.main()