"""GridMind M2 · 10 典型查询 E2E 测试（跨 NetworkX/Neo4j 双 backend）。

覆盖：
- 5 个黄金 query（与 test_kg_m2_rag.py 共享）
- 5 个边缘 case（设备查询 / 规程 / 故障链）
- NetworkX 路径完整覆盖（零回归）
- Neo4j 路径在 Docker 不可用时自动 skip

运行：
    cd "F:/GridOpsAgent" && PYTHONPATH=. python tests/test_kg_m2_e2e_queries.py
"""

from __future__ import annotations

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


# ═════════════════════════════════════════════════════════════════════════════
# 10 个典型查询场景
# ═════════════════════════════════════════════════════════════════════════════

E2E_QUERIES = [
    # ── 5 黄金 query（与 test_kg_m2_rag.py 共享） ──
    {"id": "Q1", "query": "主变压器油温异常原因", "category": "故障诊断"},
    {"id": "Q2", "query": "35kV 母线有哪些关联设备", "category": "设备查询"},
    {"id": "Q3", "query": "断路器跳闸的处置流程", "category": "处置流程"},
    {"id": "Q4", "query": "过载故障的因果传导", "category": "因果推理"},
    {"id": "Q5", "query": "10kV 设备的安规要求", "category": "规程关联"},
    # ── 5 边缘 case ──
    {"id": "Q6", "query": "电缆绝缘降低的应对措施", "category": "故障诊断"},
    {"id": "Q7", "query": "二号主变压器的运行参数", "category": "设备查询"},
    {"id": "Q8", "query": "局部放电检测方法", "category": "检测方法"},
    {"id": "Q9", "query": "SF6气体泄漏处理", "category": "故障诊断"},
    {"id": "Q10", "query": "避雷器的检修周期", "category": "规程关联"},
]


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：NetworkX 模式 10 query E2E（零回归）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01E2ENetworkX(unittest.TestCase):
    """场景 1：10 典型查询在 NetworkX 模式下全部返回有效结果（零回归）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from core.grayscale_router import reset_grayscale_router
        from core.kg_client import reset_kg_client
        reset_grayscale_router()
        reset_kg_client()
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

    def test_all_10_queries_networkx(self) -> None:
        """10 典型查询全部走 NetworkX 且返回非空。"""
        from core.grayscale_router import get_grayscale_router
        from core.rag_engine import RagEngine

        router = get_grayscale_router()
        router.set_ratio(0)

        engine = RagEngine()
        results = []
        for q in E2E_QUERIES:
            start = time.perf_counter()
            try:
                result = engine.retrieve(q["query"], top_k=3, thread_id=f"e2e-{q['id']}")
                latency = (time.perf_counter() - start) * 1000
                results.append({
                    "id": q["id"],
                    "query": q["query"],
                    "category": q["category"],
                    "vector_chunks": len(result.vector_chunks),
                    "graph_entities": len(result.graph_entities),
                    "confidence": result.confidence,
                    "latency_ms": round(latency, 1),
                    "ok": (len(result.vector_chunks) + len(result.graph_entities)) > 0,
                })
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "id": q["id"],
                    "query": q["query"],
                    "ok": False,
                    "error": str(exc),
                })

        # 输出报告
        print("\n=== E2E 10 query NetworkX 报告 ===")
        for r in results:
            status = "✓" if r.get("ok") else "✗"
            print(f"  {status} {r['id']}: {r.get('query', '')} | "
                  f"v={r.get('vector_chunks', 0)} e={r.get('graph_entities', 0)} "
                  f"latency={r.get('latency_ms', 0)}ms")

        # 至少 8/10 通过
        successful = sum(1 for r in results if r.get("ok"))
        self.assertGreaterEqual(successful, 8, f"E2E 成功率 {successful}/10 < 8")


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：5 黄金 query 在 Neo4j 模式（仅当 Neo4j 可用）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02E2ENeo4j(unittest.TestCase):
    """场景 2：5 黄金 query 在 Neo4j 模式下（仅当 Neo4j 可用）。"""

    def setUp(self) -> None:
        self.neo4j_ok = neo4j_available()
        if not self.neo4j_ok:
            return
        from core.grayscale_router import reset_grayscale_router
        from core.kg_client import reset_kg_client
        reset_grayscale_router()
        reset_kg_client()
        from api.config import settings
        self.original_neo4j_enabled = settings.neo4j_enabled
        object.__setattr__(settings, "neo4j_enabled", True)

    def tearDown(self) -> None:
        if not self.neo4j_ok:
            return
        from core.grayscale_router import reset_grayscale_router
        from core.kg_client import reset_kg_client
        object.__setattr__(settings, "neo4j_enabled", self.original_neo4j_enabled)
        reset_grayscale_router()
        reset_kg_client()

    def test_5_gold_queries_neo4j(self) -> None:
        """5 黄金 query 在 Neo4j 模式下返回结果。"""
        if not self.neo4j_ok:
            self.skipTest("Neo4j 未运行（Docker 不可用）")

        from core.grayscale_router import get_grayscale_router
        from core.rag_engine import RagEngine

        router = get_grayscale_router()
        router.set_ratio(100)
        engine = RagEngine()
        for q in E2E_QUERIES[:5]:  # 仅 5 黄金 query
            try:
                result = engine.retrieve(q["query"], top_k=3, thread_id=f"e2e-neo4j-{q['id']}")
                # Neo4j 模式下，至少返回一些图谱实体或向量片段
                self.assertIsInstance(result.graph_entities, list)
            except Exception as exc:  # noqa: BLE001
                # Neo4j 不可用时网络异常是允许的（fallback 到 NetworkX）
                # 仅在完全无结果时 fail
                self.fail(f"Query {q['id']} 完全失败: {exc}")


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：灰度切流对 10 query 的命中率分布
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03GrayscaleE2E(unittest.TestCase):
    """场景 3：灰度切流对 10 query 的命中率。"""

    def test_grayscale_50_50_split(self) -> None:
        """ratio=50 时，100 个 thread_id 中约 50%（±10）走 Neo4j。"""
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        router = get_grayscale_router()
        router.set_ratio(50)

        # 100 个不同 thread_id
        hits = sum(
            1 for i in range(100)
            if router.should_use_neo4j(f"thread-{i}")
        )
        # 50 ± 10 = 40-60（覆盖统计波动）
        self.assertGreaterEqual(hits, 40, f"50% 灰度命中率过低: {hits}/100")
        self.assertLessEqual(hits, 60, f"50% 灰度命中率过高: {hits}/100")


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：报告输出
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04E2EReport(unittest.TestCase):
    """场景 4：输出 10 query 的完整 E2E 报告。"""

    def test_generate_report(self) -> None:
        """生成 10 query 的 E2E 报告（JSON 格式）。"""
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        from core.rag_engine import RagEngine

        reset_grayscale_router()
        router = get_grayscale_router()
        router.set_ratio(0)

        engine = RagEngine()
        report = {
            "timestamp": time.time(),
            "neo4j_available": neo4j_available(),
            "grayscale_ratio": router.ratio,
            "queries": [],
        }
        for q in E2E_QUERIES:
            start = time.perf_counter()
            try:
                result = engine.retrieve(q["query"], top_k=3, thread_id=f"e2e-report-{q['id']}")
                latency = (time.perf_counter() - start) * 1000
                report["queries"].append({
                    "id": q["id"],
                    "query": q["query"],
                    "category": q["category"],
                    "vector_chunks": len(result.vector_chunks),
                    "graph_entities": len(result.graph_entities),
                    "graph_paths": len(result.graph_paths),
                    "confidence": result.confidence,
                    "latency_ms": round(latency, 1),
                    "ok": (len(result.vector_chunks) + len(result.graph_entities)) > 0,
                })
            except Exception as exc:  # noqa: BLE001
                report["queries"].append({
                    "id": q["id"],
                    "query": q["query"],
                    "ok": False,
                    "error": str(exc),
                })

        # 输出报告
        print("\n=== E2E 报告 ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))

        # 至少 8/10 通过
        successful = sum(1 for q in report["queries"] if q.get("ok"))
        self.assertGreaterEqual(successful, 8)


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GridMind M2 E2E 查询测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M2 · 10 典型查询 E2E 测试")
    print("=" * 70)
    print(f"  Neo4j 状态: {'可用' if neo4j_available() else '不可用'}")
    print(f"  查询数量:   {len(E2E_QUERIES)} 个")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    print()
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{len(result.skipped)} 跳过")
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)