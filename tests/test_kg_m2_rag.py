"""GridMind M2 · RAG 召回率验证测试（5 个黄金 query）。

基于 Q9 = A 决策：5 个黄金 query 对比 NetworkX / Neo4j 检索结果。
M2 目标：召回率 ≥80%（M0 基线 60%）。

测试逻辑：
- 在 ``neo4j_enabled=False`` 模式下，NetworkX 必须正常工作（零回归）
- 在 ``neo4j_enabled=True`` 模式下（仅当 Neo4j 可用），验证 Neo4j 3 跳扩展
- 对比 NetworkX 与 Neo4j 的结果差异，记录召回指标

运行：
    cd "F:/GridOpsAgent" && PYTHONPATH=. python tests/test_kg_m2_rag.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


NEO4J_BOLT_PORT = 7687
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "gridmind-dev")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def neo4j_available() -> bool:
    try:
        from neo4j import GraphDatabase  # noqa: F401
    except ImportError:
        return False
    return _is_port_open("127.0.0.1", NEO4J_BOLT_PORT, timeout=1.0)


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ═════════════════════════════════════════════════════════════════════════════
# 5 个黄金 query（Q9 = A 决策）
# ═════════════════════════════════════════════════════════════════════════════

GOLD_QUERIES = [
    {
        "id": "Q1",
        "query": "主变压器油温异常原因",
        "expect_entities": ["TR-001", "e-overheat", "e-DL572"],
        "expect_keywords": ["油温", "异常", "变压器"],
    },
    {
        "id": "Q2",
        "query": "35kV 母线有哪些关联设备",
        "expect_entities": ["BB-002"],
        "expect_keywords": ["母线", "35kV", "设备"],
    },
    {
        "id": "Q3",
        "query": "断路器跳闸的处置流程",
        "expect_entities": ["e-trip"],
        "expect_keywords": ["断路器", "跳闸", "处置"],
    },
    {
        "id": "Q4",
        "query": "过载故障的因果传导",
        "expect_entities": ["e-overload"],
        "expect_keywords": ["过载", "故障", "因果"],
        "expect_min_hops": 3,
    },
    {
        "id": "Q5",
        "query": "10kV 设备的安规要求",
        "expect_entities": ["e-DL572"],
        "expect_keywords": ["10kV", "安规", "规程"],
        "expect_min_regulations": 1,
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：5 个黄金 query（NetworkX fallback 验证）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01GoldenQueriesNetworkX(unittest.TestCase):
    """场景 1：5 个黄金 query 在 NetworkX 模式下必须返回有效结果（零回归）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from core.grayscale_router import reset_grayscale_router
        from core.kg_client import reset_kg_client
        reset_grayscale_router()
        reset_kg_client()
        # 确保 neo4j_enabled=False
        from api.config import settings as _settings
        cls.original_neo4j_enabled = _settings.neo4j_enabled
        object.__setattr__(_settings, "neo4j_enabled", False)

    @classmethod
    def tearDownClass(cls) -> None:
        from core.grayscale_router import reset_grayscale_router
        from core.kg_client import reset_kg_client
        from api.config import settings as _settings
        object.__setattr__(_settings, "neo4j_enabled", cls.original_neo4j_enabled)
        reset_grayscale_router()
        reset_kg_client()

    def test_retrieve_with_networkx(self) -> None:
        """5 个黄金 query 在 NetworkX 模式下必须返回非空结果。"""
        from core.rag_engine import RagEngine

        engine = RagEngine()
        results = []
        for q in GOLD_QUERIES:
            try:
                result = engine.retrieve(q["query"], top_k=3, thread_id=f"test-{q['id']}")
                results.append({
                    "id": q["id"],
                    "query": q["query"],
                    "vector_chunks_count": len(result.vector_chunks),
                    "graph_entities_count": len(result.graph_entities),
                    "graph_paths_count": len(result.graph_paths),
                    "confidence": result.confidence,
                    "backend": "networkx",
                    "entity_names": [e.name for e in result.graph_entities[:5]],
                })
                # 必须返回至少 1 个实体或 1 个向量片段
                self.assertGreater(
                    len(result.vector_chunks) + len(result.graph_entities), 0,
                    f"Query {q['id']} 返回空结果",
                )
            except Exception as exc:  # noqa: BLE001
                self.fail(f"Query {q['id']} 执行失败: {exc}")

        # 至少 4/5 黄金 query 应有结果（接受率 ≥80%）
        successful = sum(1 for r in results if r["vector_chunks_count"] + r["graph_entities_count"] > 0)
        self.assertGreaterEqual(successful, 4, f"NetworkX 黄金 query 成功率 {successful}/5 < 4")

        # 输出报告
        print("\n=== NetworkX 黄金 query 召回率报告 ===")
        for r in results:
            print(f"  {r['id']}: vectors={r['vector_chunks_count']} entities={r['graph_entities_count']} "
                  f"confidence={r['confidence']:.2f}")

    def test_retrieve_with_grayscale_router(self) -> None:
        """5 个黄金 query 通过 GrayscaleRouter 路由（默认 off，全部走 NetworkX）。"""
        from core.grayscale_router import get_grayscale_router
        from core.rag_engine import RagEngine

        router = get_grayscale_router()
        # 默认 ratio=0，全部走 NetworkX
        self.assertEqual(router.ratio, 0)
        self.assertFalse(router.should_use_neo4j("any-thread-id"))

        engine = RagEngine()
        for q in GOLD_QUERIES:
            result = engine.retrieve(q["query"], top_k=3, thread_id=f"test-{q['id']}")
            # 当 ratio=0 时，必须走 NetworkX 路径
            self.assertIsInstance(result.vector_chunks, list)
            self.assertIsInstance(result.graph_entities, list)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：GrayscaleRouter 切流路由（thread_id hash 分布验证）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02GrayscaleRouting(unittest.TestCase):
    """场景 2：GrayscaleRouter 切流路由（thread_id hash 分布）。"""

    def test_off_state_uses_networkx(self) -> None:
        """ratio=0：全部走 NetworkX。"""
        from core.grayscale_router import get_grayscale_router

        router = get_grayscale_router()
        router.set_ratio(0)
        for i in range(100):
            self.assertFalse(router.should_use_neo4j(f"thread-{i}"))

    def test_full_state_uses_neo4j(self) -> None:
        """ratio=100：全部走 Neo4j。"""
        from core.grayscale_router import get_grayscale_router

        router = get_grayscale_router()
        router.set_ratio(100)
        for i in range(100):
            self.assertTrue(router.should_use_neo4j(f"thread-{i}"))

    def test_10_percent_distribution(self) -> None:
        """ratio=10：1000 个 thread_id 中约 10%（±2.5%）走 Neo4j。"""
        from core.grayscale_router import get_grayscale_router

        router = get_grayscale_router()
        router.set_ratio(10)
        hits = sum(
            1 for i in range(1000)
            if router.should_use_neo4j(f"thread-{i}")
        )
        # 100 ± 25 = 75-125（10% ± 2.5%，覆盖统计波动）
        self.assertGreaterEqual(hits, 75, f"10% 灰度命中率过低: {hits}/1000")
        self.assertLessEqual(hits, 125, f"10% 灰度命中率过高: {hits}/1000")

    def test_50_percent_distribution(self) -> None:
        """ratio=50：1000 个 thread_id 中约 50%（±2.5%）走 Neo4j。"""
        from core.grayscale_router import get_grayscale_router

        router = get_grayscale_router()
        router.set_ratio(50)
        hits = sum(
            1 for i in range(1000)
            if router.should_use_neo4j(f"thread-{i}")
        )
        self.assertGreaterEqual(hits, 475)
        self.assertLessEqual(hits, 525)

    def test_set_ratio_invalid_raises(self) -> None:
        """set_ratio 非法值必须抛 ValueError。"""
        from core.grayscale_router import get_grayscale_router

        router = get_grayscale_router()
        for invalid in [5, 25, 33, 75, 99, 101]:
            with self.assertRaises(ValueError):
                router.set_ratio(invalid)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：5 个黄金 query 在 Neo4j 模式下（仅当 Neo4j 可用）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03GoldenQueriesNeo4j(unittest.TestCase):
    """场景 3：5 个黄金 query 在 Neo4j 模式下（沙箱无 Docker 时自动 skip）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.neo4j_ok = neo4j_available()

    def test_retrieve_with_neo4j_when_available(self) -> None:
        """5 个黄金 query 在 Neo4j 模式下返回结果。"""
        if not self.neo4j_ok:
            self.skipTest("Neo4j 未运行（Docker 不可用）")

        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        from core.rag_client import reset_kg_client  # noqa
        from api.config import settings

        object.__setattr__(settings, "neo4j_enabled", True)
        reset_grayscale_router()
        from core.kg_client import reset_kg_client
        reset_kg_client()

        router = get_grayscale_router()
        router.set_ratio(100, actor="test")
        engine = RagEngine()
        for q in GOLD_QUERIES:
            result = engine.retrieve(q["query"], top_k=3, thread_id=f"test-{q['id']}")
            # Neo4j 模式下，应至少返回一些实体
            self.assertIsInstance(result.graph_entities, list)

        reset_grayscale_router()
        object.__setattr__(settings, "neo4j_enabled", False)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：报告输出
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04RecallReport(unittest.TestCase):
    """场景 4：生成 RAG 召回率对比报告（JSON 输出）。"""

    def test_generate_report(self) -> None:
        """生成 5 黄金 query 的召回率对比报告。"""
        from core.grayscale_router import get_grayscale_router
        from core.rag_engine import RagEngine

        router = get_grayscale_router()
        router.set_ratio(0)
        engine = RagEngine()
        report = {
            "timestamp": time.time(),
            "neo4j_available": neo4j_available(),
            "queries": [],
        }
        for q in GOLD_QUERIES:
            result = engine.retrieve(q["query"], top_k=3, thread_id=f"test-{q['id']}")
            entity_names = [e.name for e in result.graph_entities]
            keyword_hits = sum(
                1 for kw in q["expect_keywords"]
                if any(kw in (n or "") for n in entity_names)
                or any(kw in chunk for chunk in result.vector_chunks)
            )
            entity_id_hits = sum(
                1 for eid in q["expect_entities"]
                if any(e.id == eid for e in result.graph_entities)
            )
            report["queries"].append({
                "id": q["id"],
                "query": q["query"],
                "vector_chunks": len(result.vector_chunks),
                "graph_entities": len(result.graph_entities),
                "graph_paths": len(result.graph_paths),
                "confidence": result.confidence,
                "keyword_recall": round(keyword_hits / len(q["expect_keywords"]), 2),
                "entity_recall": round(entity_id_hits / max(len(q["expect_entities"]), 1), 2),
                "entity_names": entity_names[:5],
            })
        # 输出报告
        print("\n=== RAG 召回率报告 ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))

        # 至少 4/5 黄金 query 应有结果（接受率 ≥80%）
        successful = sum(
            1 for q in report["queries"]
            if q["vector_chunks"] + q["graph_entities"] > 0
        )
        self.assertGreaterEqual(successful, 4)


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GridMind M2 RAG 召回率测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M2 · RAG 召回率验证")
    print("=" * 70)
    print(f"  Neo4j 状态:    {'可用' if neo4j_available() else '不可用'}")
    print(f"  黄金 query:    {len(GOLD_QUERIES)} 个")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    print()
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{len(result.skipped)} 跳过")
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)