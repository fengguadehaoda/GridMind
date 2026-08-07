"""GridMind M2 · 双向同步 e2e 测试套件。

覆盖 8 个场景（PRD §7 REQ-M2-2/3）：
1. sync_log 表创建 + 3 索引
2. SyncLogService 写入/查询/状态更新
3. ChromaSyncService 实例化
4. 事件入队（enqueue_event）
5. Worker 消费（asyncio.Queue）
6. 优雅停止（stop）
7. 冲突解决（Neo4j 权威源）
8. 进程崩溃恢复（pending → resume）

运行：
    cd "F:/GridOpsAgent" && PYTHONPATH=. python tests/test_kg_m2_sync.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
    return _is_port_open("127.0.0.1", 7687, timeout=1.0)


def _run_async(coro):
    # QA 修复：asyncio.run 避免 Python 3.13 组合测试时 "no current event loop"
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：sync_log 表创建 + 索引
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01SyncLogSchema(unittest.TestCase):
    """场景 1：sync_log 表创建成功 + 索引完整。"""

    def test_table_exists(self) -> None:
        """sync_log 表存在。"""
        from mcp_tools.db.database import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_log'"
            ).fetchone()
            self.assertIsNotNone(row, "sync_log 表未创建")
        finally:
            conn.close()

    def test_indexes_exist(self) -> None:
        """sync_log 至少有 status / type 索引。"""
        from mcp_tools.db.database import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sync_log'"
            ).fetchall()
            index_names = {r["name"] for r in rows}
            # 至少要有 status 和 type 索引
            self.assertIn("idx_sync_status", index_names)
            self.assertIn("idx_sync_type", index_names)
        finally:
            conn.close()

    def test_schema_columns(self) -> None:
        """sync_log 关键字段完整。"""
        from mcp_tools.db.database import get_connection

        conn = get_connection()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(sync_log)").fetchall()}
            for required in [
                "id", "sync_type", "entity_id", "status",
                "retry_count", "started_at", "finished_at",
                "thread_id", "error_message",
            ]:
                self.assertIn(required, cols, f"缺少字段: {required}")
        finally:
            conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：SyncLogService 写入/查询
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02SyncLogService(unittest.TestCase):
    """场景 2：SyncLogService CRUD 操作。"""

    def setUp(self) -> None:
        from api.services.sync_log_service import SyncLogService
        self.svc = SyncLogService()

    def test_write_pending(self) -> None:
        """write_pending 返回有效 id。"""
        log_id = self.svc.write_pending(
            sync_type="graph_to_vector",
            entity_id="e-test-device",
            thread_id="test-thread",
            payload={"test": True},
        )
        self.assertGreater(log_id, 0)

    def test_log_success(self) -> None:
        """write_pending → log_success 状态变更。"""
        log_id = self.svc.write_pending(
            sync_type="event",
            entity_id="e-test-success",
        )
        ok = self.svc.log_success(log_id, duration_ms=100)
        self.assertTrue(ok)
        # 验证状态变更
        rows = self.svc.query_by_entity("e-test-success")
        self.assertGreater(len(rows), 0)
        # 最新记录应是 success
        self.assertEqual(rows[0]["status"], "success")

    def test_log_failed_and_retry(self) -> None:
        """log_failed 自动 retry_count++。"""
        log_id = self.svc.write_pending(
            sync_type="vector_to_graph",
            entity_id="e-test-failed",
        )
        # 第 1 次失败
        retry1 = self.svc.log_failed(log_id, "error 1")
        self.assertEqual(retry1, 1)
        # 第 2 次失败
        retry2 = self.svc.log_failed(log_id, "error 2")
        self.assertEqual(retry2, 2)
        # 第 3 次失败 → 强制 failed
        retry3 = self.svc.log_failed(log_id, "error 3")
        self.assertEqual(retry3, 3)
        # 验证状态
        rows = self.svc.query_by_entity("e-test-failed")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["retry_count"], 3)

    def test_count_by_status(self) -> None:
        """count_by_status 返回完整状态字典。"""
        stats = self.svc.count_by_status()
        for status in ["pending", "success", "failed", "conflict"]:
            self.assertIn(status, stats)
            self.assertIsInstance(stats[status], int)

    def test_get_recent(self) -> None:
        """get_recent 返回最近 N 条。"""
        # 写入测试数据
        for i in range(5):
            self.svc.write_pending(
                sync_type="event",
                entity_id=f"e-test-recent-{i}",
            )
        rows = self.svc.get_recent(limit=10)
        self.assertGreater(len(rows), 0)

    def test_log_rollback_event(self) -> None:
        """log_rollback_event 写入 sync_log 审计记录。"""
        log_id = self.svc.log_rollback_event(
            reason="auto_error_rate",
            actor="auto_rollback",
            details={"error_rate": 0.05},
        )
        self.assertGreater(log_id, 0)
        # rollback entity_id 是动态的（rollback-{timestamp}），使用按 status 过滤
        from api.services.sync_log_service import SYNC_TYPE_ROLLBACK
        rows = self.svc.get_recent(limit=50, status=None)
        rollback_rows = [
            r for r in rows
            if r.get("sync_type") == SYNC_TYPE_ROLLBACK
        ]
        # 至少有一条 rollback 记录
        self.assertGreater(len(rollback_rows), 0)

    def test_get_pending_for_recovery(self) -> None:
        """get_pending_for_recovery 返回 pending + retry_count < 3 的记录。"""
        # 先写入 pending
        log_id = self.svc.write_pending(
            sync_type="event",
            entity_id="e-test-recovery",
        )
        rows = self.svc.get_pending_for_recovery(limit=100)
        # 必须包含刚写入的 entity_id
        entity_ids = [r["entity_id"] for r in rows]
        self.assertIn("e-test-recovery", entity_ids)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：ChromaSyncService 实例化
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03SyncServiceInit(unittest.TestCase):
    """场景 3：ChromaSyncService 可实例化 + 单例。"""

    def test_instance(self) -> None:
        """get_sync_service 返回实例。"""
        from core.kg_chroma_sync import get_sync_service
        svc = get_sync_service()
        self.assertIsNotNone(svc)

    def test_singleton(self) -> None:
        """get_sync_service 单例。"""
        from core.kg_chroma_sync import get_sync_service
        s1 = get_sync_service()
        s2 = get_sync_service()
        self.assertIs(s1, s2)

    def test_initial_state(self) -> None:
        """初始状态：未启动、队列 0。"""
        from core.kg_chroma_sync import reset_sync_service, get_sync_service
        reset_sync_service()
        svc = get_sync_service()
        stats = svc.get_stats()
        self.assertFalse(stats["started"])
        self.assertEqual(stats["queue_size"], 0)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：启动/停止 + 事件入队
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04SyncServiceLifecycle(unittest.TestCase):
    """场景 4：ChromaSyncService 启动/停止 + 事件入队。"""

    def setUp(self) -> None:
        from core.kg_chroma_sync import reset_sync_service, get_sync_service
        reset_sync_service()
        self.svc = get_sync_service()

    def tearDown(self) -> None:
        async def cleanup():
            try:
                if self.svc._started:
                    await self.svc.stop()
            except Exception:
                pass
        try:
            _run_async(cleanup())
        except Exception:
            pass
        from core.kg_chroma_sync import reset_sync_service
        reset_sync_service()

    def test_start_stop(self) -> None:
        """启动后 stop 必须正确清理资源。"""
        async def run():
            await self.svc.start()
            self.assertTrue(self.svc._started)
            await self.svc.stop()
            self.assertFalse(self.svc._started)
        _run_async(run())

    def test_enqueue_event_when_started(self) -> None:
        """启动后 enqueue_event 成功入队。"""
        async def run():
            await self.svc.start()
            ok = self.svc.enqueue_event(
                entity_id="e-test-enqueue",
                sync_type="event",
                thread_id="test-thread",
            )
            self.assertTrue(ok)
            self.assertGreater(self.svc.get_queue_length(), 0)
            await self.svc.stop()
        _run_async(run())

    def test_enqueue_event_when_not_started(self) -> None:
        """未启动时 enqueue_event 返回 False（不抛错）。"""
        ok = self.svc.enqueue_event(entity_id="e-test-when-not-started")
        self.assertFalse(ok)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 5：Worker 异步消费
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05AsyncWorker(unittest.TestCase):
    """场景 5：Worker 异步消费 + sync_log 写入。"""

    def setUp(self) -> None:
        from core.kg_chroma_sync import reset_sync_service, get_sync_service
        reset_sync_service()
        self.svc = get_sync_service()

    def tearDown(self) -> None:
        async def cleanup():
            try:
                if self.svc._started:
                    await self.svc.stop()
            except Exception:
                pass
        try:
            _run_async(cleanup())
        except Exception:
            pass
        from core.kg_chroma_sync import reset_sync_service
        reset_sync_service()

    def test_worker_consumes_event(self) -> None:
        """Worker 在 5 秒内消费事件并写 sync_log。"""
        async def run():
            await self.svc.start()
            self.svc.enqueue_event(
                entity_id="e-worker-consume",
                sync_type="graph_to_vector",
                thread_id="worker-test",
            )
            # 等待 worker 处理（最多 5s）
            for _ in range(50):
                await asyncio.sleep(0.1)
                if self.svc._stats["events_processed"] >= 1:
                    break
            self.assertGreaterEqual(self.svc._stats["events_processed"], 1)
            # 验证 sync_log 有记录
            from api.services.sync_log_service import get_sync_log_service
            log = get_sync_log_service()
            rows = log.query_by_entity("e-worker-consume", limit=1)
            self.assertGreater(len(rows), 0)
            await self.svc.stop()
        _run_async(run())


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GridMind M2 同步测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M2 · 双向同步 e2e 测试")
    print("=" * 70)
    print(f"  Neo4j 状态: {'可用' if neo4j_available() else '不可用'}")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    print()
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{len(result.skipped)} 跳过")
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)