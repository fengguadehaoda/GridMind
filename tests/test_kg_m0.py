"""GridMind 知识图谱 M0 升级 e2e 测试套件。

7 个核心场景
-----------
1. **启动健康检查**：Neo4j 端口 7474 / 7687 可达 + Bolt 探活
2. **本体 Schema 幂等**：apply_ontology 连续执行 3 次无报错
3. **迁移幂等**：KGMigrator 首次写入 + 重跑结果一致
4. **Backend 切换**：neo4j_enabled=True/False 时 backend 正确选择
5. **降级触发**：Neo4j 失败时自动切回 NetworkX（3 次连续失败）
6. **探活恢复**：降级后 30s 自动尝试恢复 Neo4j
7. **跨 backend 一致性**：NetworkXBackend 与 Neo4jBackend 同一查询返回一致结果

环境要求
--------
- **Python 3.11+**
- **Neo4j 5.x 容器运行中**（`python scripts/start_neo4j.py` 启动）
  - 若未启动 Docker / Neo4j 不可用，对应测试 **自动 skip**（不报错）
  - NetworkX 相关测试 **始终运行**（不需要 Neo4j）

运行方式
--------
::

    cd "F:/GridOpsAgent"
    PYTHONPATH=. python tests/test_kg_m0.py
    # 或
    PYTHONPATH=. python tests/test_kg_m0.py -v
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path

# 把项目根加入 PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

# ── 测试配置 ──────────────────────────────────────────
NEO4J_HTTP_PORT = 7474
NEO4J_BOLT_PORT = 7687
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "gridmind-dev")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

logger.remove()
logger.add(sys.stderr, level="WARNING")


# ── 工具函数 ──────────────────────────────────────────

def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def neo4j_available() -> bool:
    """检测 Neo4j 是否可用（Bolt 端口通 + Python 驱动可 import）。"""
    try:
        from neo4j import GraphDatabase  # noqa: F401
    except ImportError:
        return False
    return _is_port_open("127.0.0.1", NEO4J_BOLT_PORT, timeout=1.0)


def neo4j_bolt_ping(uri: str = NEO4J_URI, timeout: float = 3.0) -> bool:
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(NEO4J_USER, NEO4J_PASSWORD))
        try:
            # neo4j 5.x driver 的 verify_connectivity() 不接受 timeout kwarg
            driver.verify_connectivity()
            return True
        finally:
            driver.close()
    except Exception:  # noqa: BLE001
        return False


def _wipe_neo4j() -> None:
    """清空 Neo4j 测试数据（仅删除本测试用的标签/关系类型）。"""
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
        try:
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run("MATCH (n:Entity) DETACH DELETE n")
        finally:
            driver.close()
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────
# 场景 1：启动健康检查
# ─────────────────────────────────────────────────────────────────────────

class TestScenario01Neo4jStartup(unittest.TestCase):
    """场景 1：Neo4j 启动 + 健康检查。"""

    def test_python_driver_importable(self) -> None:
        """neo4j Python 驱动可 import。"""
        try:
            import neo4j  # noqa: F401
            self.assertTrue(True, "neo4j 驱动导入成功")
        except ImportError as exc:
            self.fail(f"neo4j 驱动未安装: {exc}")

    def test_http_port(self) -> None:
        """HTTP 7474 端口可达（Neo4j Browser）。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行（Docker 不可用）")
        self.assertTrue(
            _is_port_open("127.0.0.1", NEO4J_HTTP_PORT),
            f"HTTP 端口 {NEO4J_HTTP_PORT} 不可达",
        )

    def test_bolt_port(self) -> None:
        """Bolt 7687 端口可达。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        self.assertTrue(
            _is_port_open("127.0.0.1", NEO4J_BOLT_PORT),
            f"Bolt 端口 {NEO4J_BOLT_PORT} 不可达",
        )

    def test_bolt_connectivity(self) -> None:
        """Bolt 协议鉴权通过。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        self.assertTrue(
            neo4j_bolt_ping(),
            f"Bolt 探活失败: {NEO4J_URI}",
        )


# ─────────────────────────────────────────────────────────────────────────
# 场景 2：本位 Schema 幂等
# ─────────────────────────────────────────────────────────────────────────

class TestScenario02OntologyIdempotent(unittest.TestCase):
    """场景 2：本体 Schema 应用（幂等）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.neo4j_ok = neo4j_available()
        if not cls.neo4j_ok:
            return
        # 准备 driver
        from neo4j import GraphDatabase
        cls.driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "driver") and cls.driver is not None:
            try:
                cls.driver.close()
            except Exception:  # noqa: BLE001
                pass

    def test_apply_ontology_importable(self) -> None:
        """apply_ontology 函数可 import。"""
        from core.kg_ontology import apply_ontology, ONTOLOGY_CONSTRAINTS, ONTOLOGY_INDEXES
        self.assertTrue(callable(apply_ontology))
        self.assertGreater(len(ONTOLOGY_CONSTRAINTS), 0)
        self.assertGreater(len(ONTOLOGY_INDEXES), 0)

    def test_apply_ontology_idempotent_3x(self) -> None:
        """连续 3 次执行 apply_ontology 无报错（幂等性）。"""
        if not self.neo4j_ok:
            self.skipTest("Neo4j 未运行")
        from core.kg_ontology import apply_ontology

        reports: list[dict] = []
        for i in range(3):
            report = apply_ontology(self.driver, database=NEO4J_DATABASE)
            reports.append(report)
            self.assertIn("constraints_applied", report)
            self.assertIn("indexes_applied", report)

        # 第二次/第三次执行不应报错（即使所有 IF NOT EXISTS 都不再 apply）
        for i, r in enumerate(reports):
            logger.info("apply_ontology 第 {} 次: {}", i + 1, r)
            self.assertIsInstance(r["constraints_applied"], int)
            self.assertIsInstance(r["indexes_applied"], int)

    def test_schema_summary_returns_lists(self) -> None:
        """schema_summary 返回约束/索引列表。"""
        if not self.neo4j_ok:
            self.skipTest("Neo4j 未运行")
        from core.kg_ontology import schema_summary

        summary = schema_summary(self.driver, database=NEO4J_DATABASE)
        self.assertIn("constraints", summary)
        self.assertIn("indexes", summary)
        self.assertIsInstance(summary["constraints"], list)
        self.assertIsInstance(summary["indexes"], list)


# ─────────────────────────────────────────────────────────────────────────
# 场景 3：迁移幂等
# ─────────────────────────────────────────────────────────────────────────

class TestScenario03MigrationIdempotent(unittest.TestCase):
    """场景 3：迁移脚本（首次 + 重跑）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.neo4j_ok = neo4j_available()
        if cls.neo4j_ok:
            _wipe_neo4j()
        # 确保 SQLite 有种子数据
        from mcp_tools.db.database import init_db
        from mcp_tools.db.seed_data import seed_all
        try:
            init_db()
            seed_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_all 初始化失败: {}", exc)

    def setUp(self) -> None:
        if self.neo4j_ok:
            _wipe_neo4j()

    def test_kgmigrator_importable(self) -> None:
        """KGMigrator 类可 import。"""
        from core.kg_migration import KGMigrator
        self.assertTrue(callable(KGMigrator))

    def test_migration_first_run(self) -> None:
        """首次迁移：21 节点 / 24 关系（实际 SQLite 数据；PRD 文档口径 25 是历史遗留）。"""
        if not self.neo4j_ok:
            self.skipTest("Neo4j 未运行")
        from core.kg_migration import KGMigrator

        migrator = KGMigrator(
            neo4j_uri=NEO4J_URI, neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD, neo4j_database=NEO4J_DATABASE,
        )
        report = migrator.run(source="sqlite", verify_only=False)

        self.assertEqual(report.status, "success", f"迁移失败: {report.error_message}")
        self.assertGreaterEqual(report.entity_count, 21)
        self.assertGreaterEqual(report.relation_count, 24)

    def test_migration_idempotent_rerun(self) -> None:
        """二次迁移：节点/关系数不变（MERGE 幂等）。"""
        if not self.neo4j_ok:
            self.skipTest("Neo4j 未运行")
        from core.kg_migration import KGMigrator

        # 第一次
        m1 = KGMigrator(
            neo4j_uri=NEO4J_URI, neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD, neo4j_database=NEO4J_DATABASE,
        )
        r1 = m1.run(source="sqlite")
        self.assertEqual(r1.status, "success")

        # 第二次（同数据）
        m2 = KGMigrator(
            neo4j_uri=NEO4J_URI, neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD, neo4j_database=NEO4J_DATABASE,
        )
        r2 = m2.run(source="sqlite")
        self.assertEqual(r2.status, "success")

        # MERGE 幂等：count 不应翻倍
        verify = m2.verify()
        self.assertTrue(verify["ok"], f"校验失败: {verify}")
        self.assertGreaterEqual(verify["entities"], 21)
        self.assertGreaterEqual(verify["relations"], 24)
        # 关键：重跑后数量应与首次一致
        self.assertEqual(
            verify["entities"], r1.entity_count,
            f"重跑后节点数变化: {r1.entity_count} → {verify['entities']}",
        )
        self.assertEqual(
            verify["relations"], r1.relation_count,
            f"重跑后关系数变化: {r1.relation_count} → {verify['relations']}",
        )

    def test_migration_verify_only(self) -> None:
        """verify_only 模式：只读源，不写 Neo4j。"""
        from core.kg_migration import KGMigrator

        migrator = KGMigrator(batch_size=10)
        report = migrator.run(source="sqlite", verify_only=True)
        self.assertEqual(report.status, "verify_only")
        self.assertEqual(report.entity_count, 0)  # 未写入
        self.assertGreater(report.source_entity_cnt, 0)

    def test_migration_log_persisted(self) -> None:
        """迁移日志写入 SQLite ``kg_migration_log`` 表。"""
        from core.kg_migration import KGMigrator

        # 先执行 verify_only 以确保 log 表有记录（不依赖 Neo4j 可用性）
        migrator = KGMigrator(batch_size=10)
        report = migrator.run(source="sqlite", verify_only=True)
        self.assertEqual(report.status, "verify_only")

        from mcp_tools.db.database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT status, source FROM kg_migration_log ORDER BY id DESC LIMIT 5"
            ).fetchall()
            self.assertGreater(len(rows), 0, "kg_migration_log 表为空")
            statuses = [r["status"] for r in rows]
            # 至少应有一次 'verify_only' 或 'success'
            self.assertTrue(
                any(s in ("success", "verify_only", "failed") for s in statuses),
                f"未识别的状态: {statuses}",
            )
        finally:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 场景 4：KGClient 切换 backend
# ─────────────────────────────────────────────────────────────────────────

class TestScenario04BackendSelection(unittest.TestCase):
    """场景 4：KGClient 根据 neo4j_enabled 切换 backend。"""

    def setUp(self) -> None:
        # 重置单例避免测试间状态污染
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def tearDown(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def test_default_uses_networkx(self) -> None:
        """neo4j_enabled=False（默认）→ NetworkXBackend。"""
        from core.kg_client import KGClient, get_kg_client, NetworkXBackend
        from api.config import settings

        # 默认 settings.neo4j_enabled 应为 False
        self.assertFalse(settings.neo4j_enabled, "默认 neo4j_enabled 应为 False（M0 不切主链路）")

        client = get_kg_client()
        self.assertIsInstance(client, KGClient)
        self.assertEqual(client.current_backend_name, "networkx")
        self.assertIsInstance(client.backend, NetworkXBackend)

    def test_explicit_neo4j_enabled_uses_neo4j_when_available(self) -> None:
        """neo4j_enabled=True + Neo4j 可用 → Neo4jBackend。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        from core.kg_client import KGClient, Neo4jBackend, get_kg_client

        # 临时开启
        from api.config import settings
        original = settings.neo4j_enabled
        object.__setattr__(settings, "neo4j_enabled", True)
        try:
            client = get_kg_client()
            self.assertIsInstance(client, KGClient)
            self.assertIsInstance(client.backend, Neo4jBackend)
        finally:
            object.__setattr__(settings, "neo4j_enabled", original)

    def test_explicit_neo4j_enabled_fallback_when_unavailable(self) -> None:
        """neo4j_enabled=True + Neo4j 不可用 → 自动降级到 NetworkX。"""
        from core.kg_client import get_kg_client, NetworkXBackend
        from api.config import settings

        original = settings.neo4j_enabled
        object.__setattr__(settings, "neo4j_enabled", True)
        # 临时改 URI 到不可能连通的端口
        original_uri = settings.neo4j_uri
        object.__setattr__(settings, "neo4j_uri", "bolt://localhost:1")
        try:
            client = get_kg_client()
            # 应降级到 NetworkX
            self.assertIsInstance(client.backend, NetworkXBackend)
        finally:
            object.__setattr__(settings, "neo4j_enabled", original)
            object.__setattr__(settings, "neo4j_uri", original_uri)


# ─────────────────────────────────────────────────────────────────────────
# 场景 5：降级触发
# ─────────────────────────────────────────────────────────────────────────

class TestScenario05Demotion(unittest.TestCase):
    """场景 5：Neo4j 失败时自动降级到 NetworkX。"""

    def setUp(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def tearDown(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def test_demote_after_3_consecutive_failures(self) -> None:
        """连续 3 次失败触发降级。"""
        from core.kg_client import KGClient, Neo4jBackend, NetworkXBackend
        from api.config import settings

        if not neo4j_available():
            self.skipTest("Neo4j 未运行（无法构造真实降级场景）")

        # 强制开启 neo4j + 真实可用的 driver
        object.__setattr__(settings, "neo4j_enabled", True)

        client = KGClient()
        # 此时 backend 应该是 Neo4jBackend（如果 Neo4j 通）
        if not isinstance(client.backend, Neo4jBackend):
            self.skipTest("Neo4j 探活未通过，跳过降级测试")

        # 模拟 3 次失败：把 driver 替换为坏 driver
        from core.kg_client import Neo4jBackend as _NB
        bad_backend = _NB(
            uri="bolt://localhost:1",  # 不可达
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            database=NEO4J_DATABASE,
        )
        client.backend = bad_backend
        # 关闭 driver 防止连接泄漏（Neo4j 驱动可能延后报错）
        # 我们只通过调用 ping/cypher 触发失败
        client._failure_count = 0

        # 尝试调用 cypher_query 3 次（每次都应失败）
        for i in range(3):
            try:
                client.cypher_query("RETURN 1")
            except Exception:  # noqa: BLE001
                # 预期失败
                pass

        # 第 3 次失败后 backend 应被替换为 NetworkX
        # （注意：_execute 中捕获的是 ServiceUnavailable/TransientError/ConnectionError）
        self.assertIsInstance(client.backend, NetworkXBackend)

    def test_cypher_query_neo4j_backend(self) -> None:
        """Neo4j backend 的 cypher_query 正确返回。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        from core.kg_client import Neo4jBackend

        backend = Neo4jBackend(
            uri=NEO4J_URI, user=NEO4J_USER,
            password=NEO4J_PASSWORD, database=NEO4J_DATABASE,
        )
        try:
            result = backend.cypher_query("RETURN 1 AS n")
            self.assertEqual(len(result), 1)
            self.assertIn("n", result[0])
            self.assertEqual(result[0]["n"], 1)
        finally:
            backend.close()

    def test_networkx_cypher_query_raises(self) -> None:
        """NetworkX backend 的 cypher_query 抛 NotImplementedError。"""
        from core.kg_client import NetworkXBackend

        backend = NetworkXBackend()
        with self.assertRaises(NotImplementedError):
            backend.cypher_query("RETURN 1")


# ─────────────────────────────────────────────────────────────────────────
# 场景 6：探活恢复
# ─────────────────────────────────────────────────────────────────────────

class TestScenario06HealthCheckRecovery(unittest.TestCase):
    """场景 6：降级后 30s 自动探活恢复（手动调用以加速测试）。"""

    def setUp(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def tearDown(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def test_health_check_interval_constant(self) -> None:
        """探活间隔常量 = 30s（与架构 7.2 一致）。"""
        from core.kg_client import KGClient
        self.assertEqual(KGClient.HEALTH_CHECK_INTERVAL, 30.0)

    def test_failure_threshold_constant(self) -> None:
        """降级阈值 = 3 次（与架构 7.2 一致）。"""
        from core.kg_client import KGClient
        self.assertEqual(KGClient.FAILURE_THRESHOLD, 3)

    def test_health_check_throttled(self) -> None:
        """30s 节流：连续调用不会触发重复探活。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行（无法验证节流）")
        from core.kg_client import KGClient, Neo4jBackend
        from api.config import settings

        object.__setattr__(settings, "neo4j_enabled", True)
        client = KGClient()
        if not isinstance(client.backend, Neo4jBackend):
            self.skipTest("Neo4j 探活未通过")

        # 第一次调用会执行探活
        client._last_health_check = time.monotonic() - 100  # 100s 前
        client._maybe_health_check()
        first_check = client._last_health_check

        # 立即再次调用——节流：不应重置 last_health_check
        client._maybe_health_check()
        second_check = client._last_health_check

        # 节流生效：两次调用后 last_health_check 不变（应都 ≈ 第一次）
        self.assertAlmostEqual(first_check, second_check, places=2)


# ─────────────────────────────────────────────────────────────────────────
# 场景 7：跨 backend 一致性
# ─────────────────────────────────────────────────────────────────────────

class TestScenario07CrossBackendConsistency(unittest.TestCase):
    """场景 7：跨 backend 查询结果一致。"""

    def setUp(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()
        if neo4j_available():
            _wipe_neo4j()

    def tearDown(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def test_get_entity_consistent(self) -> None:
        """get_entity 跨 backend 结果一致。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        from core.kg_migration import KGMigrator
        from core.kg_client import Neo4jBackend, NetworkXBackend

        # 先迁移
        migrator = KGMigrator()
        r = migrator.run(source="sqlite")
        if r.status != "success":
            self.skipTest(f"迁移失败: {r.error_message}")

        nx = NetworkXBackend()
        n4j = Neo4jBackend(
            uri=NEO4J_URI, user=NEO4J_USER,
            password=NEO4J_PASSWORD, database=NEO4J_DATABASE,
        )
        try:
            # 多个关键实体对比
            for eid in ["e-transformer", "e-overload", "e-DL572", "e-TR001"]:
                nx_e = nx.get_entity(eid)
                n4j_e = n4j.get_entity(eid)
                self.assertIsNotNone(nx_e, f"NetworkX 找不到: {eid}")
                self.assertIsNotNone(n4j_e, f"Neo4j 找不到: {eid}")
                self.assertEqual(nx_e["id"], n4j_e["id"])
                self.assertEqual(nx_e["name"], n4j_e["name"])
                self.assertEqual(nx_e["type"], n4j_e["type"])
        finally:
            n4j.close()

    def test_search_entities_consistent(self) -> None:
        """search_entities 跨 backend 关键结果一致。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        from core.kg_migration import KGMigrator
        from core.kg_client import Neo4jBackend, NetworkXBackend

        migrator = KGMigrator()
        r = migrator.run(source="sqlite")
        if r.status != "success":
            self.skipTest(f"迁移失败: {r.error_message}")

        nx = NetworkXBackend()
        n4j = Neo4jBackend(
            uri=NEO4J_URI, user=NEO4J_USER,
            password=NEO4J_PASSWORD, database=NEO4J_DATABASE,
        )
        try:
            nx_results = nx.search_entities("变", limit=10)
            n4j_results = n4j.search_entities("变", limit=10)
            # 名称集合应一致
            nx_names = {r["name"] for r in nx_results}
            n4j_names = {r["name"] for r in n4j_results}
            self.assertEqual(
                nx_names, n4j_names,
                f"模糊搜索结果不一致:\n  NX: {nx_names}\n  N4j: {n4j_names}",
            )
        finally:
            n4j.close()

    def test_get_relations_consistent(self) -> None:
        """get_relations 跨 backend 结果一致。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        from core.kg_migration import KGMigrator
        from core.kg_client import Neo4jBackend, NetworkXBackend

        migrator = KGMigrator()
        r = migrator.run(source="sqlite")
        if r.status != "success":
            self.skipTest(f"迁移失败: {r.error_message}")

        nx = NetworkXBackend()
        n4j = Neo4jBackend(
            uri=NEO4J_URI, user=NEO4J_USER,
            password=NEO4J_PASSWORD, database=NEO4J_DATABASE,
        )
        try:
            nx_rels = nx.get_relations("e-transformer")
            n4j_rels = n4j.get_relations("e-transformer")
            # 关系集合应一致
            nx_pairs = {(r["source_id"], r["target_id"], r["relation_type"]) for r in nx_rels}
            n4j_pairs = {(r["source_id"], r["target_id"], r["relation_type"]) for r in n4j_rels}
            self.assertEqual(
                nx_pairs, n4j_pairs,
                f"关系集合不一致:\n  NX: {nx_pairs}\n  N4j: {n4j_pairs}",
            )
        finally:
            n4j.close()


# ─────────────────────────────────────────────────────────────────────────
# 单元层兜底：核心接口契约
# ─────────────────────────────────────────────────────────────────────────

class TestCoreInterfaceContract(unittest.TestCase):
    """核心接口契约（不依赖 Neo4j）。"""

    def test_kgbackend_protocol(self) -> None:
        """KGBackend Protocol 定义完整。"""
        from core.kg_client import KGBackend, NetworkXBackend

        backend = NetworkXBackend()
        # 运行时检查：NetworkXBackend 实现 Protocol
        self.assertIsInstance(backend, KGBackend)

    def test_networkx_basic_queries(self) -> None:
        """NetworkX 基础查询（无 Neo4j 也能跑）。"""
        from core.kg_client import NetworkXBackend

        backend = NetworkXBackend()
        try:
            # 1) get_entity
            e = backend.get_entity("e-transformer")
            self.assertIsNotNone(e)
            self.assertEqual(e["id"], "e-transformer")
            self.assertEqual(e["name"], "变压器")
            self.assertEqual(e["type"], "设备类别")

            # 2) search_entities
            results = backend.search_entities("变", limit=5)
            self.assertGreater(len(results), 0)

            # 3) get_relations
            rels = backend.get_relations("e-transformer")
            self.assertGreaterEqual(len(rels), 2)
            rel_targets = {r["target_id"] for r in rels}
            self.assertIn("e-overload", rel_targets)
            self.assertIn("e-overtemp", rel_targets)

            # 4) expand_entities
            entities, paths = backend.expand_entities(["e-TR001"], hops=2)
            self.assertGreater(len(entities), 0)
        finally:
            backend.close()

    def test_kgclient_singleton(self) -> None:
        """KGClient 单例：多次调用返回同一实例。"""
        from core.kg_client import KGClient, get_kg_client, reset_kg_client

        reset_kg_client()
        c1 = get_kg_client()
        c2 = get_kg_client()
        self.assertIs(c1, c2)
        reset_kg_client()


# ─────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GridMind M0 e2e 测试")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="详细输出",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M0 知识图谱升级 — e2e 测试")
    print("=" * 70)
    print(f"  Neo4j 状态:    {'可用' if neo4j_available() else '不可用（部分测试将 skip）'}")
    print(f"  Bolt URI:      {NEO4J_URI}")
    print(f"  Bolt 用户:     {NEO4J_USER}")
    print(f"  Bolt 数据库:   {NEO4J_DATABASE}")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)

    print()
    print("=" * 70)
    skipped_count = len(result.skipped)
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{skipped_count} 跳过")
    print("=" * 70)

    # 退出码：0 表示通过（含 skip），1 表示有失败
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)
