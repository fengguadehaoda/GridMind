"""GridMind M2 · 自动回滚 e2e 测试套件。

覆盖 4 个场景：
1. RollbackMonitor 5min 滚动窗口
2. 错误率 >1% 触发回滚
3. P95 >200ms 触发回滚
4. Neo4j 连续失败 ≥3 次触发回滚
5. 触发后调用 GrayscaleRouter.trigger_rollback 切回 off

运行：
    cd "F:/GridOpsAgent" && PYTHONPATH=. python tests/test_kg_m2_rollback.py
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：RollbackMonitor 基础接口
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01RollbackMonitorBasic(unittest.TestCase):
    """场景 1：RollbackMonitor 基础接口 + 样本下限。"""

    def test_construction(self) -> None:
        """可实例化。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor()
        self.assertIsNotNone(m)

    def test_min_samples_required_for_error_rate(self) -> None:
        """错误率路径需 ≥ min_samples 才判断（样本不足不触发）。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=50)
        # 仅写入 10 个全部失败的样本（错误率 100% > 1%，但样本数 < 50）
        # 注意：连续 Neo4j 失败仍会触发（这是独立路径），所以这里用 networkx backend
        for _ in range(10):
            m.record(error=True, latency_ms=100, backend="networkx")
        # 错误率路径不触发（样本不足），且非 Neo4j backend 也不触发连续失败
        self.assertFalse(m.should_rollback())

    def test_get_stats(self) -> None:
        """get_stats 返回完整字段。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor()
        stats = m.get_stats()
        for field in ["samples", "error_rate", "p95_ms", "neo4j_consecutive_failures", "window_s"]:
            self.assertIn(field, stats)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：错误率 > 1% 触发
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02ErrorRateTrigger(unittest.TestCase):
    """场景 2：错误率 >1% 触发回滚。"""

    def test_high_error_rate_triggers(self) -> None:
        """错误率 5%（>1%）触发回滚。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=50)
        # 100 个样本，5 个错误（5% > 1%）
        for i in range(100):
            m.record(error=(i % 20 == 0), latency_ms=100, backend="neo4j")
        self.assertTrue(m.should_rollback())
        self.assertEqual(m.last_reason(), "auto_error_rate")

    def test_low_error_rate_no_trigger(self) -> None:
        """错误率 0.5%（<1%）不触发回滚。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=50)
        # 200 个样本，1 个错误（0.5% < 1%）
        for i in range(200):
            m.record(error=(i == 0), latency_ms=100, backend="neo4j")
        self.assertFalse(m.should_rollback())


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：P95 > 200ms 触发
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03P95Trigger(unittest.TestCase):
    """场景 3：P95 延迟 >200ms 触发回滚。"""

    def test_high_p95_triggers(self) -> None:
        """P95 延迟 250ms（>200ms）触发回滚。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=50)
        # 100 个样本，20 个延迟 250ms（20% 触发 P95 > 200）
        for i in range(100):
            latency = 250 if i < 20 else 100
            m.record(error=False, latency_ms=latency, backend="neo4j")
        self.assertTrue(m.should_rollback())
        self.assertEqual(m.last_reason(), "auto_p95")

    def test_low_p95_no_trigger(self) -> None:
        """P95 延迟 100ms（<200ms）不触发回滚。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=50)
        for _ in range(100):
            m.record(error=False, latency_ms=100, backend="neo4j")
        self.assertFalse(m.should_rollback())


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：Neo4j 连续失败 ≥3 次触发
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04Neo4jFailuresTrigger(unittest.TestCase):
    """场景 4：Neo4j 连续失败 ≥3 次触发回滚。"""

    def test_consecutive_failures_triggers(self) -> None:
        """Neo4j 连续 3 次失败触发回滚。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=10)
        # 3 次连续失败（即使样本不足 50）
        for _ in range(3):
            m.record(error=True, latency_ms=100, backend="neo4j")
        self.assertTrue(m.should_rollback())
        self.assertEqual(m.last_reason(), "auto_neo4j_connect")

    def test_consecutive_failures_reset_on_success(self) -> None:
        """成功请求归零连续失败计数。"""
        from core.auto_rollback import RollbackMonitor
        m = RollbackMonitor(min_samples=10)
        m.record(error=True, latency_ms=100, backend="neo4j")
        m.record(error=True, latency_ms=100, backend="neo4j")
        m.record(error=False, latency_ms=100, backend="neo4j")  # 成功 → 归零
        m.record(error=True, latency_ms=100, backend="neo4j")
        # 此时连续失败 = 1，未达阈值
        self.assertFalse(m.should_rollback())


# ═════════════════════════════════════════════════════════════════════════════
# 场景 5：GrayscaleRouter.record_request 集成
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05RouterIntegration(unittest.TestCase):
    """场景 5：GrayscaleRouter.record_request 集成自动回滚。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        self.router = get_grayscale_router()
        self.router.set_ratio(50)

    def test_record_request_normal(self) -> None:
        """正常请求：record_request 不触发回滚。"""
        for _ in range(20):
            result = self.router.record_request(
                error=False, latency_ms=100, backend="neo4j",
            )
            self.assertIsNone(result)

    def test_record_request_high_error_rate(self) -> None:
        """高错误率：record_request 触发回滚。"""
        # 模拟 60 个样本，5% 错误率（>1%）
        rollback_result = None
        for i in range(60):
            r = self.router.record_request(
                error=(i % 20 == 0),
                latency_ms=100,
                backend="neo4j",
            )
            if r is not None:
                rollback_result = r
                break
        self.assertIsNotNone(rollback_result, "未触发回滚")
        self.assertEqual(rollback_result["ratio"], 0)
        self.assertEqual(self.router.ratio, 0)


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GridMind M2 自动回滚测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M2 · 自动回滚 e2e 测试")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    print()
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{len(result.skipped)} 跳过")
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)