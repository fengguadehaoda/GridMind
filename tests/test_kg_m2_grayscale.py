"""GridMind M2 · 灰度切流 e2e 测试套件。

覆盖 6 个场景：
1. GrayscaleRouter 单例
2. thread_id hash 路由分布（10/50/100）
3. 状态机转移（off → gray10 → gray50 → full100）
4. set_ratio 非法值校验
5. 切流历史记录
6. get_status 完整性

运行：
    cd "F:/GridOpsAgent" && PYTHONPATH=. python tests/test_kg_m2_grayscale.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：GrayscaleRouter 单例
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01Singleton(unittest.TestCase):
    """场景 1：GrayscaleRouter 单例。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router
        reset_grayscale_router()

    def test_singleton(self) -> None:
        """get_grayscale_router 返回同一实例。"""
        from core.grayscale_router import get_grayscale_router
        r1 = get_grayscale_router()
        r2 = get_grayscale_router()
        self.assertIs(r1, r2)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：thread_id hash 路由分布
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02RoutingDistribution(unittest.TestCase):
    """场景 2：thread_id hash 取模路由分布验证。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        self.router = get_grayscale_router()

    def test_ratio_0_all_networkx(self) -> None:
        """ratio=0：1000 个 thread_id 全部走 NetworkX。"""
        self.router.set_ratio(0)
        for i in range(1000):
            self.assertFalse(self.router.should_use_neo4j(f"thread-{i}"))

    def test_ratio_100_all_neo4j(self) -> None:
        """ratio=100：1000 个 thread_id 全部走 Neo4j。"""
        self.router.set_ratio(100)
        for i in range(1000):
            self.assertTrue(self.router.should_use_neo4j(f"thread-{i}"))

    def test_ratio_10_distribution(self) -> None:
        """ratio=10：1000 个 thread_id 中约 10%（±5）走 Neo4j。"""
        self.router.set_ratio(10)
        hits = sum(
            1 for i in range(1000)
            if self.router.should_use_neo4j(f"thread-{i}")
        )
        # 100 ± 25 = 75-125（10% ± 2.5%，覆盖统计波动）
        self.assertGreaterEqual(hits, 75, f"10% 灰度命中率过低: {hits}/1000")
        self.assertLessEqual(hits, 125, f"10% 灰度命中率过高: {hits}/1000")

    def test_ratio_50_distribution(self) -> None:
        """ratio=50：1000 个 thread_id 中约 50%（±5）走 Neo4j。"""
        self.router.set_ratio(50)
        hits = sum(
            1 for i in range(1000)
            if self.router.should_use_neo4j(f"thread-{i}")
        )
        # 500 ± 25 = 475-525（50% ± 2.5%，覆盖统计波动）
        self.assertGreaterEqual(hits, 475, f"50% 灰度命中率过低: {hits}/1000")
        self.assertLessEqual(hits, 525, f"50% 灰度命中率过高: {hits}/1000")


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：状态机转移
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03StateMachine(unittest.TestCase):
    """场景 3：状态机转移（off → gray10 → gray50 → full100）。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        self.router = get_grayscale_router()

    def test_off_to_gray10(self) -> None:
        """off → gray10。"""
        self.router.set_ratio(0)
        self.assertEqual(self.router.state, "off")
        self.router.set_ratio(10)
        self.assertEqual(self.router.state, "gray10")

    def test_gray10_to_gray50(self) -> None:
        """gray10 → gray50。"""
        self.router.set_ratio(10)
        self.router.set_ratio(50)
        self.assertEqual(self.router.state, "gray50")

    def test_gray50_to_full100(self) -> None:
        """gray50 → full100。"""
        self.router.set_ratio(50)
        self.router.set_ratio(100)
        self.assertEqual(self.router.state, "full100")

    def test_full100_to_off(self) -> None:
        """full100 → off（手动回滚）。"""
        self.router.set_ratio(100)
        self.router.set_ratio(0)
        self.assertEqual(self.router.state, "off")


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：set_ratio 非法值校验
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04SetRatioValidation(unittest.TestCase):
    """场景 4：set_ratio 非法值必须抛 ValueError。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        self.router = get_grayscale_router()

    def test_reject_5(self) -> None:
        with self.assertRaises(ValueError):
            self.router.set_ratio(5)

    def test_reject_25(self) -> None:
        with self.assertRaises(ValueError):
            self.router.set_ratio(25)

    def test_reject_75(self) -> None:
        with self.assertRaises(ValueError):
            self.router.set_ratio(75)

    def test_reject_negative(self) -> None:
        with self.assertRaises(ValueError):
            self.router.set_ratio(-1)

    def test_accept_0_10_50_100(self) -> None:
        """合法值 0/10/50/100 必须接受。"""
        for ratio in [0, 10, 50, 100]:
            self.router.set_ratio(ratio)  # 不抛错


# ═════════════════════════════════════════════════════════════════════════════
# 场景 5：切流历史记录
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05History(unittest.TestCase):
    """场景 5：切流历史记录。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        self.router = get_grayscale_router()

    def test_history_records(self) -> None:
        """每次 set_ratio 必须记录到 history。"""
        self.router.set_ratio(10)
        self.router.set_ratio(50)
        self.router.set_ratio(100)
        self.router.set_ratio(0)
        history = self.router.get_history()
        self.assertGreaterEqual(len(history), 4)

    def test_history_limit(self) -> None:
        """history limit 参数生效。"""
        for i in range(5):
            self.router.set_ratio(0 if i % 2 == 0 else 10)
        history = self.router.get_history(limit=3)
        self.assertLessEqual(len(history), 3)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 6：get_status 完整性
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario06GetStatus(unittest.TestCase):
    """场景 6：get_status 返回完整字段。"""

    def setUp(self) -> None:
        from core.grayscale_router import reset_grayscale_router, get_grayscale_router
        reset_grayscale_router()
        self.router = get_grayscale_router()

    def test_status_fields(self) -> None:
        """get_status 包含必要字段。"""
        self.router.set_ratio(50)
        status = self.router.get_status()
        for field in ["state", "ratio", "started_at", "rollback_reason", "rollback_count",
                       "neo4j_enabled", "monitor", "history"]:
            self.assertIn(field, status)
        self.assertEqual(status["ratio"], 50)
        self.assertEqual(status["state"], "gray50")

    def test_status_includes_monitor_stats(self) -> None:
        """get_status 包含 RollbackMonitor 统计。"""
        status = self.router.get_status()
        monitor = status["monitor"]
        for field in ["samples", "error_rate", "p95_ms", "neo4j_consecutive_failures"]:
            self.assertIn(field, monitor)


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GridMind M2 灰度切流测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M2 · 灰度切流 e2e 测试")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    print()
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{len(result.skipped)} 跳过")
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)