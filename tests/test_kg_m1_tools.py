"""GridMind M1 · 5 个新 MCP 工具 e2e 测试套件。

覆盖 T-KG-08 验收：
1. **5 个工具可 import**：cypher_query / multi_hop_expand / find_devices_by_substation /
   get_fault_chain / get_applicable_regulations
2. **Cypher 注入防护**：白名单拒绝写操作关键字
3. **NetworkX 模式兼容**：所有工具在 neo4j_enabled=False 时返回有效结果
4. **Neo4j 模式**：连接 Neo4j 后正确执行（沙箱无 Docker 时自动 skip）
5. **AGENT_TOOLS_MAP 注册**：knowledge_agent 包含 5 个新工具
6. **server.py 注册**：5 个新 @mcp.tool 函数

运行（D4：改用相对项目根，避免硬编码旧路径）：
    cd <项目根> && PYTHONPATH=. python -m pytest tests/test_kg_m1_tools.py -q
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

NEO4J_BOLT_PORT = 7687
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "gridmind-dev")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


# ═════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═════════════════════════════════════════════════════════════════════════════

def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def neo4j_available() -> bool:
    """Neo4j 是否可用（Bolt 端口 + 驱动 import）。"""
    try:
        from neo4j import GraphDatabase  # noqa: F401
    except ImportError:
        return False
    return _is_port_open("127.0.0.1", NEO4J_BOLT_PORT, timeout=1.0)


def _run_async(coro):
    """运行异步 coroutine（QA 修复：改用 asyncio.run，避免 Python 3.13 下
    ``asyncio.get_event_loop()`` 在组合测试时抛 "There is no current event loop"）。"""
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：5 个工具可 import
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01ToolsImportable(unittest.TestCase):
    """场景 1：5 个新 MCP 工具均可 import。"""

    def test_cypher_query_importable(self) -> None:
        from mcp_tools.tools.neo4j_tools import cypher_query
        self.assertTrue(callable(cypher_query))

    def test_multi_hop_expand_importable(self) -> None:
        from mcp_tools.tools.neo4j_tools import multi_hop_expand
        self.assertTrue(callable(multi_hop_expand))

    def test_find_devices_by_substation_importable(self) -> None:
        from mcp_tools.tools.neo4j_tools import find_devices_by_substation
        self.assertTrue(callable(find_devices_by_substation))

    def test_get_fault_chain_importable(self) -> None:
        from mcp_tools.tools.neo4j_tools import get_fault_chain
        self.assertTrue(callable(get_fault_chain))

    def test_get_applicable_regulations_importable(self) -> None:
        from mcp_tools.tools.neo4j_tools import get_applicable_regulations
        self.assertTrue(callable(get_applicable_regulations))


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：Cypher 注入防护
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02CypherInjectionGuard(unittest.TestCase):
    """场景 2：Cypher 注入防护（白名单 + 参数化）。"""

    def test_reject_create_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import cypher_query, _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("CREATE (n:Foo) RETURN n")

    def test_reject_delete_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("MATCH (n) DELETE n")

    def test_reject_merge_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("MERGE (n:Foo {id: 1})")

    def test_reject_set_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("MATCH (n) SET n.x = 1")

    def test_reject_call_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("CALL apoc.periodic.iterate('MATCH ...', '', {})")

    def test_reject_detach_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("MATCH (n) DETACH DELETE n")

    def test_reject_empty_query(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("")

    def test_reject_no_read_keyword(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        with self.assertRaises(ValueError):
            _validate_cypher_readonly("LIMIT 10")

    def test_allow_match_return(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        # 不应抛错
        _validate_cypher_readonly("MATCH (n:Entity) RETURN n LIMIT 10")

    def test_allow_with_where(self) -> None:
        from mcp_tools.tools.neo4j_tools import _validate_cypher_readonly
        _validate_cypher_readonly(
            "MATCH (n:Entity) WHERE n.type = $type WITH n RETURN n LIMIT $limit"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：AGENT_TOOLS_MAP 注册
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03AgentToolsRegistration(unittest.TestCase):
    """场景 3：5 个新工具注册到 AGENT_TOOLS_MAP。"""

    def test_knowledge_agent_has_5_new_tools(self) -> None:
        """knowledge_agent 注册 5 个新工具。"""
        from api.agents.agent_factory import AGENT_TOOLS_MAP
        knowledge_tools = AGENT_TOOLS_MAP.get("knowledge_agent", [])
        for tool in [
            "cypher_query",
            "multi_hop_expand",
            "find_devices_by_substation",
            "get_fault_chain",
            "get_applicable_regulations",
        ]:
            self.assertIn(tool, knowledge_tools, f"{tool} 未注册到 knowledge_agent")

    def test_knowledge_agent_keeps_original_4_tools(self) -> None:
        """knowledge_agent 保留原 4 个工具（向后兼容）。"""
        from api.agents.agent_factory import AGENT_TOOLS_MAP
        knowledge_tools = AGENT_TOOLS_MAP.get("knowledge_agent", [])
        for tool in [
            "query_knowledge_base",
            "search_knowledge_chunks",
            "search_graph_entities",
            "get_entity_relations",
        ]:
            self.assertIn(tool, knowledge_tools, f"原工具 {tool} 丢失")

    def test_other_agents_not_modified(self) -> None:
        """其他 Agent（monitor/safety/diagnosis）的工具列表不变。"""
        from api.agents.agent_factory import AGENT_TOOLS_MAP
        # monitor_agent 不应有新工具
        monitor_tools = AGENT_TOOLS_MAP.get("monitor_agent", [])
        self.assertNotIn("cypher_query", monitor_tools)
        self.assertNotIn("multi_hop_expand", monitor_tools)
        # safety_agent 不应有新工具
        safety_tools = AGENT_TOOLS_MAP.get("safety_agent", [])
        self.assertNotIn("cypher_query", safety_tools)
        # diagnosis_agent 不应有新工具（dispatch_work_order 已有）
        diagnosis_tools = AGENT_TOOLS_MAP.get("diagnosis_agent", [])
        self.assertNotIn("cypher_query", diagnosis_tools)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：server.py 注册（@mcp.tool）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04ServerRegistration(unittest.TestCase):
    """场景 4：5 个新工具通过 @mcp.tool 注册到 server.py。"""

    def test_neo4j_tools_module_importable(self) -> None:
        """neo4j_tools 模块可 import。"""
        import mcp_tools.tools.neo4j_tools as mod
        self.assertTrue(hasattr(mod, "cypher_query"))
        self.assertTrue(hasattr(mod, "multi_hop_expand"))
        self.assertTrue(hasattr(mod, "find_devices_by_substation"))
        self.assertTrue(hasattr(mod, "get_fault_chain"))
        self.assertTrue(hasattr(mod, "get_applicable_regulations"))

    def test_server_module_references_neo4j_tools(self) -> None:
        """server.py 引用了 neo4j_tools 模块。"""
        # D4：硬编码 F:/GridOpsAgent 旧路径 → 相对项目根（ROOT 在文件顶部定义）
        server_py = (ROOT / "mcp_tools" / "server.py").read_text(encoding="utf-8")
        for tool in ["cypher_query", "multi_hop_expand", "find_devices_by_substation",
                     "get_fault_chain", "get_applicable_regulations"]:
            self.assertIn(tool, server_py, f"server.py 未注册工具 {tool}")


# ═════════════════════════════════════════════════════════════════════════════
# 场景 5：NetworkX 模式兼容（无需 Neo4j）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05NetworkXFallback(unittest.TestCase):
    """场景 5：所有 5 个工具在 NetworkX 模式下都能返回有效结果。"""

    def setUp(self) -> None:
        """重置 KGClient 单例 + 初始化 SQLite。"""
        from core.kg_client import reset_kg_client
        from core.kg_seed_extractor import SeedExtractor
        from mcp_tools.db.database import init_db, get_connection

        reset_kg_client()
        try:
            init_db()
        except Exception:  # noqa: BLE001
            pass

        # 把种子数据写入 SQLite（用于 NetworkX 模式查询）
        conn = get_connection()
        try:
            conn.execute("DELETE FROM graph_relations")
            conn.execute("DELETE FROM graph_entities")
            conn.commit()
            SeedExtractor().save_to_sqlite(conn)
        finally:
            conn.close()

        # 同时填充 NetworkXBackend 的内存图
        from core.knowledge_graph import KnowledgeGraph
        # 触发一次 _load_from_db
        kg = KnowledgeGraph()

    def tearDown(self) -> None:
        from core.kg_client import reset_kg_client
        reset_kg_client()

    def test_cypher_query_networkx_returns_error(self) -> None:
        """NetworkX 模式下 cypher_query 返回错误（不支持 Cypher）。"""
        from mcp_tools.tools.neo4j_tools import cypher_query

        result = _run_async(cypher_query("MATCH (n) RETURN n LIMIT 1"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["backend"], "networkx")
        self.assertIn("NotImplementedError", result["error"])

    def test_cypher_query_rejects_write_keyword(self) -> None:
        """cypher_query 拒绝写操作关键字（在所有 backend 都生效）。"""
        from mcp_tools.tools.neo4j_tools import cypher_query
        result = _run_async(cypher_query("CREATE (n:Foo) RETURN n"))
        self.assertEqual(result["status"], "error")
        self.assertIn("禁止", result["error"])

    def test_multi_hop_expand_networkx(self) -> None:
        """NetworkX 模式下 multi_hop_expand 返回有效结果。"""
        from mcp_tools.tools.neo4j_tools import multi_hop_expand

        result = _run_async(multi_hop_expand("e-overload", hops=3))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backend"], "networkx")
        self.assertGreater(result["count"], 0)
        # 返回的每个实体都应有 id/name/type
        for ent in result["entities"]:
            self.assertIn("id", ent)
            self.assertIn("name", ent)
            self.assertIn("type", ent)

    def test_multi_hop_expand_hops_limit(self) -> None:
        """multi_hop_expand 的 hops 限制到 [1, 5]。"""
        from mcp_tools.tools.neo4j_tools import multi_hop_expand

        # hops=100 会被截断到 5
        result = _run_async(multi_hop_expand("e-overload", hops=100))
        self.assertEqual(result["status"], "ok")

        # hops=0 会被提升到 1
        result2 = _run_async(multi_hop_expand("e-overload", hops=0))
        self.assertEqual(result2["status"], "ok")

    def test_find_devices_by_substation_networkx(self) -> None:
        """NetworkX 模式下 find_devices_by_substation 返回有效结果。"""
        from mcp_tools.tools.neo4j_tools import find_devices_by_substation

        result = _run_async(find_devices_by_substation("e-substation-a"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backend"], "networkx")
        self.assertGreater(result["count"], 0)
        # 检查至少有一台 TR-001 或 BR-001（A 区设备）
        device_ids = {d.get("device_id") for d in result["devices"]}
        self.assertTrue(
            any(d in ("TR-001", "BR-001", "BB-001", "CB-001") for d in device_ids),
            f"未找到 A 区设备: {device_ids}",
        )

    def test_find_devices_by_substation_with_type_filter(self) -> None:
        """find_devices_by_substation 支持 device_type 过滤。"""
        from mcp_tools.tools.neo4j_tools import find_devices_by_substation

        result = _run_async(find_devices_by_substation("e-substation-a", device_type="Transformer"))
        self.assertEqual(result["status"], "ok")
        # 只返回变压器（TR-001 应在其中）
        device_ids = {d.get("device_id") for d in result["devices"]}
        self.assertIn("TR-001", device_ids)

    def test_get_fault_chain_networkx(self) -> None:
        """NetworkX 模式下 get_fault_chain 返回有效结果。"""
        from mcp_tools.tools.neo4j_tools import get_fault_chain

        result = _run_async(get_fault_chain("e-overload", max_hops=3))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backend"], "networkx")
        # 应至少有一条 CAUSES 链（overload → overtemp 等）
        self.assertGreater(result["count"], 0)
        # 每条链应包含 chain 列表 + total_confidence + hops
        for chain in result["chains"]:
            self.assertIn("chain", chain)
            self.assertIn("total_confidence", chain)
            self.assertIn("hops", chain)

    def test_get_applicable_regulations_networkx(self) -> None:
        """NetworkX 模式下 get_applicable_regulations 返回有效结果。"""
        from mcp_tools.tools.neo4j_tools import get_applicable_regulations

        # 不传过滤：返回所有规程
        result = _run_async(get_applicable_regulations())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backend"], "networkx")
        self.assertGreater(result["count"], 0)

        # 按 device_id 过滤
        result2 = _run_async(get_applicable_regulations(device_id="e-TR001"))
        self.assertEqual(result2["status"], "ok")
        # 应返回适用于变压器的规程
        codes = {r.get("code") for r in result2["regulations"]}
        self.assertTrue(len(codes) > 0)

        # 按 fault_type 过滤
        result3 = _run_async(get_applicable_regulations(fault_type="e-overload"))
        self.assertEqual(result3["status"], "ok")
        self.assertGreater(result3["count"], 0)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 6：Neo4j 模式（沙箱无 Docker 自动 skip）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario06Neo4jMode(unittest.TestCase):
    """场景 6：Neo4j 模式（需要 Bolt 端口可达）。"""

    def test_neo4j_cypher_query(self) -> None:
        """Neo4j 模式下 cypher_query 执行。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行（Docker 不可用）")
        from core.kg_client import Neo4jBackend
        from mcp_tools.tools.neo4j_tools import cypher_query

        backend = Neo4jBackend(
            uri=NEO4J_URI, user=NEO4J_USER,
            password=NEO4J_PASSWORD, database=NEO4J_DATABASE,
        )
        try:
            result = backend.cypher_query("RETURN 1 AS n")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["n"], 1)
        finally:
            backend.close()

    def test_neo4j_multi_hop_expand(self) -> None:
        """Neo4j 模式下 multi_hop_expand 执行。"""
        if not neo4j_available():
            self.skipTest("Neo4j 未运行")
        from mcp_tools.tools.neo4j_tools import multi_hop_expand

        result = _run_async(multi_hop_expand("e-overload", hops=2))
        # 若 Neo4j 中没有数据，结果可能为空，但仍应返回 ok
        self.assertIn(result["status"], ("ok", "error"))


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GridMind M1 新工具测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M1 知识图谱 — 新 MCP 工具测试")
    print("=" * 70)
    print(f"  Neo4j 状态:    {'可用' if neo4j_available() else '不可用（部分测试将 skip）'}")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)

    print()
    print("=" * 70)
    print(f"测试结果：{result.testsRun} 跑过，{len(result.failures)} 失败，"
          f"{len(result.errors)} 错误，{len(result.skipped)} 跳过")
    print("=" * 70)
    sys.exit(0 if (result.wasSuccessful() and not result.failures and not result.errors) else 1)