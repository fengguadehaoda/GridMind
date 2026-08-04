"""GridMind 知识图谱 M3b · 性能基准单元测试（≥11 用例）。

设计（kg-m3-split.md §4.5）
--------
- **runner 统计** ≥5：percentile 公式 + _summarize + run 输出结构
- **reporter** ≥3：to_markdown 含必需章节 + to_json 结构 + write_to 文件
- **kg_perf_hints** ≥3：≥5 条建议 + 类别覆盖 + 严重度排序
- **baseline_data** 覆盖：节点 / 关系数 / 可重放
- **scenarios** 覆盖：≥30 场景 / ≥10 因果链 / 5 类设备齐全
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# 让 `python -m pytest` 与 `python tests/test_*.py` 都能找到项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.scenarios import (  # noqa: E402
    ALL_SCENARIOS,
    get_scenarios,
    get_causal_chain_scenarios,
    Scenario,
)
from benchmarks.baseline_data import (  # noqa: E402
    build_baseline_graph,
    EXPECTED_NODES,
    EXPECTED_EDGES,
    get_dataset_summary,
    inject_into_networkx_backend,
)
from benchmarks.runner import (  # noqa: E402
    BenchmarkResult,
    BenchmarkRunner,
    compare_results,
    _percentile,
    _summarize,
)
from benchmarks.reporter import ReportGenerator  # noqa: E402
from core.kg_perf_hints import (  # noqa: E402
    KgPerfHints,
    OptimizationHint,
    get_optimization_hints,
    NEO4J_P95_ROLLBACK_THRESHOLD_MS,
    SMALL_DATASET_THRESHOLD,
)


# ═════════════════════════════════════════════════════════════════════════════
# 1. runner 统计（5 用例）
# ═════════════════════════════════════════════════════════════════════════════

class TestRunnerStatistics(unittest.TestCase):
    """BenchmarkRunner 统计逻辑测试。"""

    def test_01_percentile_basic(self) -> None:
        """P50/P95/P99 基础计算正确性。"""
        # [1..100] 100 个值
        values = list(range(1, 101))
        self.assertAlmostEqual(_percentile(values, 50), 50.5, places=1)
        self.assertAlmostEqual(_percentile(values, 95), 95.05, places=2)
        self.assertAlmostEqual(_percentile(values, 99), 99.01, places=1)

    def test_02_percentile_edge_cases(self) -> None:
        """空列表 + 单值。"""
        self.assertEqual(_percentile([], 50), 0.0)
        self.assertEqual(_percentile([42.0], 50), 42.0)
        self.assertEqual(_percentile([1.0, 2.0, 3.0], 50), 2.0)

    def test_03_summarize_correctness(self) -> None:
        """_summarize 计算 P50/P95/P99/mean/min/max。"""
        s = _summarize([10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(s["min"], 10.0)
        self.assertEqual(s["max"], 50.0)
        self.assertEqual(s["mean"], 30.0)
        self.assertEqual(s["p50"], 30.0)
        # P95 = 10..50 排序后，索引 k=(5-1)*0.95=3.8 → 线性插值 40 + 50*0.8 = 48
        self.assertAlmostEqual(s["p95"], 48.0, places=1)
        # P99 = 索引 k=3.96 → 40 + 50*0.96 = 49.6
        self.assertAlmostEqual(s["p99"], 49.6, places=1)

    def test_04_run_output_structure(self) -> None:
        """BenchmarkResult 包含所有必需字段。"""
        sc = Scenario(
            scenario_id="test_01",
            category="device_query",
            query="test",
            params={"method_params": {"entity_id": "x"}},
            method="get_entity",
            expected_hops=1,
        )
        # 使用 Mock backend
        class MockBackend:
            name = "mock"
            def get_entity(self, eid: str):
                return {"id": eid, "name": "X", "type": "T", "properties": {}}
        runner = BenchmarkRunner(backend=MockBackend(), scenario=sc)
        runner.warmup(n=2)
        result = runner.run(n=20)
        self.assertIsInstance(result, BenchmarkResult)
        self.assertEqual(result.scenario_id, "test_01")
        self.assertEqual(result.backend, "mock")
        self.assertEqual(result.total_runs, 20)
        self.assertEqual(result.successful_runs, 20)
        self.assertEqual(result.error_count, 0)
        self.assertGreater(result.p50_ms, 0.0)
        self.assertGreater(result.p95_ms, result.p50_ms)
        self.assertGreater(result.throughput_qps, 0.0)
        # to_dict 必须可序列化
        d = result.to_dict()
        self.assertIn("p50_ms", d)
        self.assertIn("p95_ms", d)

    def test_05_run_handles_exception(self) -> None:
        """单次异常不熔断；记入 error_count。"""
        sc = Scenario(
            scenario_id="test_05",
            category="device_query",
            query="test",
            params={"method_params": {"entity_id": "x"}},
            method="get_entity",
            expected_hops=1,
        )
        class FlakyBackend:
            name = "flaky"
            call_count = 0
            def get_entity(self, eid: str):
                self.call_count += 1
                if self.call_count % 3 == 0:
                    raise RuntimeError("flaky")
                return {"id": eid}
        backend = FlakyBackend()
        runner = BenchmarkRunner(backend=backend, scenario=sc)
        result = runner.run(n=9)  # 9 次中会失败 3 次
        self.assertGreater(result.error_count, 0)
        self.assertEqual(result.successful_runs, 9 - result.error_count)
        self.assertEqual(result.total_runs, 9)

    def test_06_run_skip_unsupported_method(self) -> None:
        """backend 不支持方法时返回 backend='skip'，error_count=total_runs。"""
        sc = Scenario(
            scenario_id="test_06",
            category="causal_chain",
            query="test",
            params={"method_params": {"name": "x", "params": {}}},
            method="execute_template",  # NetworkX 不支持
            expected_hops=1,
        )
        from core.kg_client import NetworkXBackend
        runner = BenchmarkRunner(backend=NetworkXBackend(), scenario=sc)
        result = runner.run(n=5)
        self.assertEqual(result.backend, "skip")
        self.assertEqual(result.error_count, 5)
        self.assertEqual(result.successful_runs, 0)


# ═════════════════════════════════════════════════════════════════════════════
# 2. reporter 单元测试（3 用例）
# ═════════════════════════════════════════════════════════════════════════════

class TestReporter(unittest.TestCase):
    """ReportGenerator 输出格式测试。"""

    def setUp(self) -> None:
        self.gen = ReportGenerator(neo4j_available=False)
        # 构造一组合成结果
        self.results = [
            BenchmarkResult(
                scenario_id=f"S{i:02d}_test",
                backend="networkx",
                p50_ms=1.0, p95_ms=2.0, p99_ms=3.0,
                mean_ms=1.5, min_ms=0.5, max_ms=4.0,
                peak_mem_mb=0.5, throughput_qps=666.0,
                error_count=0, total_runs=10, successful_runs=10,
            )
            for i in range(1, 6)
        ]
        # 加一条 SKIP
        self.results.append(BenchmarkResult(
            scenario_id="S01_test",
            backend="skip",
            p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, mean_ms=0.0,
            min_ms=0.0, max_ms=0.0, peak_mem_mb=0.0,
            throughput_qps=0.0, error_count=10, total_runs=10,
            successful_runs=0, notes="Neo4j: SKIP",
        ))

    def test_07_to_markdown_contains_required_sections(self) -> None:
        """Markdown 报告含必需章节。"""
        self.gen.add_results(self.results)
        md = self.gen.to_markdown()
        for section in [
            "# GridMind 知识图谱 M3b 性能基准报告",
            "## 测试环境",
            "## 场景概览",
            "## Neo4j vs NetworkX 性能对比",
            "## 类别聚合统计",
            "## 吞吐 & 内存",
            "## 优化建议",
            "## 验收标准对照",
        ]:
            self.assertIn(section, md, f"缺失章节: {section}")
        # 标注 SKIP
        self.assertIn("SKIP", md)

    def test_08_to_json_structure(self) -> None:
        """JSON 输出结构化。"""
        self.gen.add_results(self.results)
        data = self.gen.to_json()
        self.assertIn("timestamp", data)
        self.assertIn("neo4j_available", data)
        self.assertFalse(data["neo4j_available"])
        self.assertIn("summary", data)
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 6)
        # 必须可 JSON 序列化
        s = json.dumps(data, ensure_ascii=False)
        self.assertIsInstance(s, str)
        self.assertGreater(len(s), 100)

    def test_09_write_to_creates_file(self) -> None:
        """write_to 正确创建文件。"""
        self.gen.add_results(self.results)
        self.gen.set_hints([])
        out_dir = _PROJECT_ROOT / "benchmarks" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "test_report.md"
        # 兼容 WorkBuddy safe-delete 沙箱（recycle bin 不可用时 fail-closed）
        for p in [target, target.with_suffix(".json")]:
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass  # safe-delete 拦截，忽略即可
        primary = self.gen.write_to(str(target), fmt="both")
        self.assertTrue(primary.exists())
        # JSON 副本也存在
        json_path = target.with_suffix(".json")
        self.assertTrue(json_path.exists())
        # 内容非空
        self.assertGreater(target.stat().st_size, 0)
        self.assertGreater(json_path.stat().st_size, 0)
        # 清理
        for p in [target, json_path]:
            try:
                p.unlink()
            except OSError:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# 3. kg_perf_hints 单元测试（3 用例）
# ═════════════════════════════════════════════════════════════════════════════

class TestKgPerfHints(unittest.TestCase):
    """KgPerfHints 优化建议生成测试。"""

    def test_10_at_least_5_hints(self) -> None:
        """至少 5 条建议（验收 4）。"""
        # 输入空：仍应输出兜底建议
        hints = get_optimization_hints([])
        self.assertGreaterEqual(len(hints), 5)
        for h in hints:
            self.assertIsInstance(h, OptimizationHint)
            self.assertTrue(h.hint_id)
            self.assertTrue(h.category)
            self.assertTrue(h.description)
            self.assertTrue(h.evidence_scenario_id)
            self.assertGreaterEqual(h.expected_improvement_pct, 0.0)

    def test_11_categories_covered(self) -> None:
        """建议覆盖多个类别（缓存/索引/查询/架构/降级）。"""
        # 构造触发各种规则的结果
        results = [
            BenchmarkResult(
                scenario_id="S01_transformer_4hop",
                backend="neo4j",
                p50_ms=150.0, p95_ms=250.0, p99_ms=300.0,  # 触发 rollback
                mean_ms=160.0, min_ms=100.0, max_ms=350.0,
                peak_mem_mb=1.0, throughput_qps=6.25,
                error_count=0, total_runs=10, successful_runs=10,
            ),
            BenchmarkResult(
                scenario_id="S01_transformer_4hop",
                backend="networkx",
                p50_ms=10.0, p95_ms=15.0, p99_ms=20.0,
                mean_ms=11.0, min_ms=8.0, max_ms=22.0,
                peak_mem_mb=0.5, throughput_qps=90.9,
                error_count=0, total_runs=10, successful_runs=10,
            ),
            BenchmarkResult(
                scenario_id="C01_short_circuit_5hop",
                backend="neo4j",
                p50_ms=180.0, p95_ms=220.0, p99_ms=280.0,
                mean_ms=190.0, min_ms=120.0, max_ms=300.0,
                peak_mem_mb=2.0, throughput_qps=5.26,
                error_count=0, total_runs=10, successful_runs=10,
            ),
        ]
        hints = get_optimization_hints(results, dataset_summary=get_dataset_summary())
        categories = {h.category for h in hints}
        # 至少 3 个不同类别
        self.assertGreaterEqual(len(categories), 3, f"建议类别不足: {categories}")

    def test_12_severity_ordering(self) -> None:
        """严重度排序：rollback > architecture > cache > query > index。"""
        # 构造触发 rollback 的结果
        results = [
            BenchmarkResult(
                scenario_id="S01_transformer_4hop",
                backend="neo4j",
                p50_ms=300.0, p95_ms=500.0, p99_ms=600.0,
                mean_ms=350.0, min_ms=200.0, max_ms=700.0,
                peak_mem_mb=1.0, throughput_qps=2.85,
                error_count=0, total_runs=10, successful_runs=10,
            ),
        ]
        hints = get_optimization_hints(results, dataset_summary=get_dataset_summary())
        # 第一条建议应该是 rollback
        if hints:
            self.assertEqual(hints[0].category, "rollback",
                             f"最高优先级应为 rollback，实际: {hints[0].category}")


# ═════════════════════════════════════════════════════════════════════════════
# 4. baseline_data + scenarios 覆盖（3 用例）
# ═════════════════════════════════════════════════════════════════════════════

class TestBaselineAndScenarios(unittest.TestCase):
    """baseline_data + scenarios 基础覆盖测试。"""

    def test_13_scenario_count_at_least_30(self) -> None:
        """至少 30 个场景（验收 2）。"""
        self.assertGreaterEqual(len(ALL_SCENARIOS), 30)
        # 5 类设备
        device_types = {s.device_type for s in ALL_SCENARIOS}
        self.assertIn("transformer", device_types)
        self.assertIn("line", device_types)
        self.assertIn("busbar", device_types)
        self.assertIn("circuit_breaker", device_types)
        self.assertIn("protection_device", device_types)

    def test_14_causal_chain_at_least_10(self) -> None:
        """至少 10 个因果链（验收 2）。"""
        chains = get_causal_chain_scenarios()
        self.assertGreaterEqual(len(chains), 10)

    def test_15_baseline_reproducible(self) -> None:
        """合成数据集可重放（验收 5）。"""
        ds1 = build_baseline_graph(seed=42)
        ds2 = build_baseline_graph(seed=42)
        self.assertEqual(ds1.nodes, ds2.nodes)
        self.assertEqual(ds1.edges, ds2.edges)
        # 节点数 = 500
        self.assertEqual(ds1.nodes, EXPECTED_NODES)
        # 边数 ~5000（允许 10% 误差：4500-5500）
        self.assertGreaterEqual(ds1.edges, int(EXPECTED_EDGES * 0.9))
        self.assertLessEqual(ds1.edges, int(EXPECTED_EDGES * 1.1))

    def test_16_inject_into_networkx(self) -> None:
        """inject_into_networkx_backend 成功注入合成数据。"""
        from core.kg_client import KGClient, reset_kg_client
        reset_kg_client()
        client = KGClient()
        original = client.backend._kg.graph.number_of_nodes() if hasattr(client.backend, "_kg") else 0
        injected = inject_into_networkx_backend(client, seed=42)
        self.assertEqual(injected, EXPECTED_NODES)
        # backend._kg.graph 应已被替换为 500 节点
        self.assertEqual(client.backend._kg.graph.number_of_nodes(), EXPECTED_NODES)
        # 复原
        reset_kg_client()


# ═════════════════════════════════════════════════════════════════════════════
# 5. compare_results 单元测试（额外）
# ═════════════════════════════════════════════════════════════════════════════

class TestCompareResults(unittest.TestCase):
    """compare_results 对比逻辑测试。"""

    def test_17_neo4j_faster(self) -> None:
        """Neo4j P95 < NetworkX P95 → winner='neo4j'。"""
        n4j = BenchmarkResult(
            scenario_id="X", backend="neo4j",
            p50_ms=1.0, p95_ms=2.0, p99_ms=3.0,
            mean_ms=1.5, min_ms=0.5, max_ms=4.0,
            peak_mem_mb=1.0, throughput_qps=666.0,
            error_count=0, total_runs=10, successful_runs=10,
        )
        nx = BenchmarkResult(
            scenario_id="X", backend="networkx",
            p50_ms=10.0, p95_ms=20.0, p99_ms=30.0,
            mean_ms=15.0, min_ms=5.0, max_ms=40.0,
            peak_mem_mb=0.5, throughput_qps=66.0,
            error_count=0, total_runs=10, successful_runs=10,
        )
        comp = compare_results("X", n4j, nx)
        self.assertEqual(comp.winner, "neo4j")
        self.assertLess(comp.p95_speedup, 0.95)

    def test_18_neo4j_skip(self) -> None:
        """Neo4j=None → winner='skip'。"""
        nx = BenchmarkResult(
            scenario_id="X", backend="networkx",
            p50_ms=10.0, p95_ms=20.0, p99_ms=30.0,
            mean_ms=15.0, min_ms=5.0, max_ms=40.0,
            peak_mem_mb=0.5, throughput_qps=66.0,
            error_count=0, total_runs=10, successful_runs=10,
        )
        comp = compare_results("X", None, nx)
        self.assertEqual(comp.winner, "skip")
        self.assertIsNone(comp.neo4j)


if __name__ == "__main__":
    unittest.main()
