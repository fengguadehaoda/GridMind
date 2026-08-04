"""GridMind M1 · 三元组抽取 e2e 测试套件。

覆盖 T-KG-07 验收：
1. **5 类抽取规则完整性**：基础/实例/故障-设备/处置-故障/规程-知识/拓扑/因果/部件/扩展
2. **三元组数量 ≥500**：节点 + 关系总和满足阈值
3. **节点标签覆盖**：5 设备子类 + 4 故障子类 + 2 处置子类 + 1 规程 + 1 设备实例
4. **约束数量 ≥15**：M1 完整本体 Schema
5. **抽取报告**：含 is_meeting_threshold=True

运行：
    cd "F:/GridOpsAgent" && PYTHONPATH=. python tests/test_kg_m1_extraction.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═════════════════════════════════════════════════════════════════════════════
# 场景 1：本体 Schema（M0 + M1）
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01OntologySchema(unittest.TestCase):
    """场景 1：本体 Schema 完整性。"""

    def test_ontology_constraints_min_count(self) -> None:
        """本体约束 ≥15（M1 完整化）。"""
        from core.kg_ontology import ONTOLOGY_CONSTRAINTS
        self.assertGreaterEqual(
            len(ONTOLOGY_CONSTRAINTS), 15,
            f"约束数 {len(ONTOLOGY_CONSTRAINTS)} < 15（M1 要求）",
        )

    def test_ontology_indexes_min_count(self) -> None:
        """本体索引 ≥10。"""
        from core.kg_ontology import ONTOLOGY_INDEXES
        self.assertGreaterEqual(
            len(ONTOLOGY_INDEXES), 10,
            f"索引数 {len(ONTOLOGY_INDEXES)} < 10（M1 要求）",
        )

    def test_ontology_includes_all_device_subtypes(self) -> None:
        """包含 5 个设备子类标签。"""
        from core.kg_ontology import DEVICE_SUBTYPE_LABELS
        expected = {"Transformer", "CircuitBreaker", "Busbar", "Line", "DeviceInstance"}
        self.assertEqual(set(DEVICE_SUBTYPE_LABELS), expected)

    def test_ontology_includes_fault_subtypes(self) -> None:
        """包含 4 个故障子类标签。"""
        from core.kg_ontology import FAULT_SUBTYPE_LABELS
        expected = {"OverloadFault", "ShortCircuitFault", "OverheatFault", "VoltageDeviationFault"}
        self.assertEqual(set(FAULT_SUBTYPE_LABELS), expected)

    def test_ontology_includes_measure_subtypes(self) -> None:
        """包含 2 个处置子类标签。"""
        from core.kg_ontology import MEASURE_SUBTYPE_LABELS
        expected = {"EmergencyStopMeasure", "RoutineMaintenanceMeasure"}
        self.assertEqual(set(MEASURE_SUBTYPE_LABELS), expected)

    def test_ontology_includes_relation_types(self) -> None:
        """包含 9 类关系类型常量。"""
        from core.kg_ontology import RELATION_TYPES
        self.assertEqual(len(RELATION_TYPES), 9)
        # 关键关系类型
        for rtype in ["CONNECTED_TO", "BELONGS_TO", "CAUSES", "HANDLED_BY",
                      "APPLIES_TO", "MANDATES", "INSTANCE_OF", "OCCURRED"]:
            self.assertIn(rtype, RELATION_TYPES)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 2：种子数据完整性
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02SeedDataCompleteness(unittest.TestCase):
    """场景 2：种子数据完整性检查。"""

    def test_seed_data_node_collections_loaded(self) -> None:
        """所有节点集合均可 import。"""
        from core.kg_seed_data import (
            DEVICE_CATEGORIES, FAULT_TYPES, HANDLING_MEASURES,
            REGULATIONS, DEVICE_INSTANCES, SUBSTATIONS, COMPONENTS,
            SENSORS, MANUFACTURERS, KNOWLEDGE_CHUNKS, TELEMETRY_SIGNALS,
            INSPECTION_FINDINGS, PERSONNEL, SAFETY_TOOLS,
            MAINTENANCE_RECORDS, DEVICE_TYPE_TEMPLATES,
        )
        # 每个集合至少 2 个节点
        for name, coll in [
            ("DEVICE_CATEGORIES", DEVICE_CATEGORIES),
            ("FAULT_TYPES", FAULT_TYPES),
            ("HANDLING_MEASURES", HANDLING_MEASURES),
            ("REGULATIONS", REGULATIONS),
            ("DEVICE_INSTANCES", DEVICE_INSTANCES),
            ("SUBSTATIONS", SUBSTATIONS),
            ("COMPONENTS", COMPONENTS),
            ("SENSORS", SENSORS),
            ("MANUFACTURERS", MANUFACTURERS),
            ("KNOWLEDGE_CHUNKS", KNOWLEDGE_CHUNKS),
            ("TELEMETRY_SIGNALS", TELEMETRY_SIGNALS),
            ("INSPECTION_FINDINGS", INSPECTION_FINDINGS),
            ("PERSONNEL", PERSONNEL),
            ("SAFETY_TOOLS", SAFETY_TOOLS),
            ("MAINTENANCE_RECORDS", MAINTENANCE_RECORDS),
            ("DEVICE_TYPE_TEMPLATES", DEVICE_TYPE_TEMPLATES),
        ]:
            self.assertGreater(len(coll), 0, f"{name} 为空")

    def test_seed_data_8_device_instances(self) -> None:
        """8 台设备实例与 seed_data.DEVICES 一一对应。"""
        from core.kg_seed_data import DEVICE_INSTANCES
        # 期望的 device_id 集合
        expected_ids = {"e-TR001", "e-TR002", "e-BR001", "e-BR002",
                        "e-CB001", "e-CB002", "e-BB001", "e-BB002"}
        actual_ids = {d["entity_id"] for d in DEVICE_INSTANCES}
        self.assertEqual(actual_ids, expected_ids)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 3：三元组数量 ≥500
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03TripleCountThreshold(unittest.TestCase):
    """场景 3：三元组数量满足 ≥500 阈值。"""

    def test_seed_graph_total_triples(self) -> None:
        """节点 + 关系 ≥500。"""
        from core.kg_seed_data import build_seed_graph
        g = build_seed_graph()
        total = len(g["nodes"]) + len(g["relations"])
        self.assertGreaterEqual(
            total, 500,
            f"三元组数 {total} < 500（M1 阶段要求）",
        )

    def test_extractor_build_returns_correct_counts(self) -> None:
        """SeedExtractor.build() 报告的节点/关系数与种子数据一致。"""
        from core.kg_seed_extractor import SeedExtractor
        from core.kg_seed_data import build_seed_graph

        extractor = SeedExtractor()
        g = extractor.build()
        expected = build_seed_graph()
        self.assertEqual(len(g["nodes"]), len(expected["nodes"]))
        self.assertEqual(len(g["relations"]), len(expected["relations"]))

    def test_extractor_report_meeting_threshold(self) -> None:
        """抽取报告含 is_meeting_threshold=True。"""
        from core.kg_seed_extractor import SeedExtractor
        extractor = SeedExtractor()
        report = extractor.report()
        self.assertTrue(report["is_meeting_threshold"])
        self.assertGreaterEqual(report["total_triples"], 500)
        self.assertIn("nodes_by_type", report)
        self.assertIn("relations_by_type", report)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 4：5 类抽取规则覆盖
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04ExtractionRuleCoverage(unittest.TestCase):
    """场景 4：5+ 类抽取规则模板覆盖。"""

    def test_r1_device_attribute_extension(self) -> None:
        """R1：设备属性扩展（DEVICE_INSTANCES 包含 manufacturer/commissioning_date 等）。"""
        from core.kg_seed_data import DEVICE_INSTANCES
        for dev in DEVICE_INSTANCES:
            props = dev.get("properties", {})
            self.assertIn("manufacturer", props, f"{dev['entity_id']} 缺 manufacturer")
            self.assertIn("commissioning_date", props, f"{dev['entity_id']} 缺 commissioning_date")
            self.assertIn("rated_voltage", props, f"{dev['entity_id']} 缺 rated_voltage")

    def test_r2_fault_device_relations_min_count(self) -> None:
        """R2：故障-设备关系 ≥60 条。"""
        from core.kg_seed_data import FAULT_DEVICE_PAIRS
        self.assertGreaterEqual(
            len(FAULT_DEVICE_PAIRS), 60,
            f"故障-设备关系 {len(FAULT_DEVICE_PAIRS)} < 60",
        )

    def test_r3_handling_fault_relations_min_count(self) -> None:
        """R3：处置-故障关系 ≥20 条。"""
        from core.kg_seed_data import HANDLING_FAULT_PAIRS
        self.assertGreaterEqual(
            len(HANDLING_FAULT_PAIRS), 20,
            f"处置-故障关系 {len(HANDLING_FAULT_PAIRS)} < 20",
        )

    def test_r4_regulation_doc_relations_min_count(self) -> None:
        """R4：规程/知识库关联 ≥40 条。"""
        from core.kg_seed_data import (
            REGULATION_DEVICE_PAIRS, REGULATION_MEASURE_PAIRS,
            REGULATION_FAULT_PAIRS, DOC_ENTITY_PAIRS,
        )
        total = (
            len(REGULATION_DEVICE_PAIRS)
            + len(REGULATION_MEASURE_PAIRS)
            + len(REGULATION_FAULT_PAIRS)
            + len(DOC_ENTITY_PAIRS)
        )
        self.assertGreaterEqual(
            total, 40,
            f"规程/知识库关联 {total} < 40",
        )

    def test_r5_topology_relations_min_count(self) -> None:
        """R5：拓扑连接 ≥30 条。"""
        from core.kg_seed_data import CONNECTED_PAIRS
        self.assertGreaterEqual(
            len(CONNECTED_PAIRS), 30,
            f"拓扑连接 {len(CONNECTED_PAIRS)} < 30",
        )

    def test_r6_causal_chains_min_count(self) -> None:
        """R6：因果关系 ≥30 条。"""
        from core.kg_seed_data import CAUSAL_CHAINS
        self.assertGreaterEqual(
            len(CAUSAL_CHAINS), 30,
            f"因果关系 {len(CAUSAL_CHAINS)} < 30",
        )


# ═════════════════════════════════════════════════════════════════════════════
# 场景 5：SeedExtractor 接口
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05SeedExtractorInterface(unittest.TestCase):
    """场景 5：SeedExtractor 公共接口。"""

    def test_seed_extractor_importable(self) -> None:
        """SeedExtractor 类可 import。"""
        from core.kg_seed_extractor import SeedExtractor
        self.assertTrue(callable(SeedExtractor))

    def test_build_returns_dict(self) -> None:
        """build() 返回 dict 含 nodes + relations。"""
        from core.kg_seed_extractor import SeedExtractor
        g = SeedExtractor().build()
        self.assertIn("nodes", g)
        self.assertIn("relations", g)
        self.assertIsInstance(g["nodes"], list)
        self.assertIsInstance(g["relations"], list)

    def test_report_returns_dict_with_threshold_flag(self) -> None:
        """report() 返回含 is_meeting_threshold 的 dict。"""
        from core.kg_seed_extractor import SeedExtractor
        report = SeedExtractor().report()
        for key in ("total_nodes", "total_relations", "total_triples",
                    "nodes_by_type", "relations_by_type", "is_meeting_threshold"):
            self.assertIn(key, report)
        self.assertTrue(report["is_meeting_threshold"])

    def test_to_cypher_returns_parameterized_statements(self) -> None:
        """to_cypher() 生成参数化 Cypher（每个语句含 $param 占位符）。"""
        from core.kg_seed_extractor import SeedExtractor
        extractor = SeedExtractor()
        extractor.build()
        stmts = extractor.to_cypher()
        self.assertGreater(len(stmts), 0)
        # 抽样检查：每条 statement 必须是 (str, dict)
        for cypher, params in stmts[:5]:
            self.assertIsInstance(cypher, str)
            self.assertIsInstance(params, dict)
            # 必须包含 MERGE 关键字（幂等）
            self.assertIn("MERGE", cypher.upper())

    def test_save_report_writes_json_file(self) -> None:
        """save_report() 写入 extract_report.json。"""
        import tempfile
        from core.kg_seed_extractor import SeedExtractor
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "extract_report.json"
            SeedExtractor().save_report(out)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(data["is_meeting_threshold"])
            self.assertGreaterEqual(data["total_triples"], 500)

    def test_write_to_networkx(self) -> None:
        """write_to_networkx() 写入内存图（无需 Neo4j）。"""
        import networkx as nx
        from core.kg_seed_extractor import SeedExtractor

        g = nx.MultiDiGraph()  # 用 MultiDiGraph 保留重复边
        n = SeedExtractor().write_to_networkx(g)
        self.assertGreater(n, 0)
        # 节点数应等于种子节点数
        expected_nodes = len(SeedExtractor().build()["nodes"])
        self.assertEqual(g.number_of_nodes(), expected_nodes)
        # 关系数应等于种子关系数（MultiDiGraph 保留所有平行边）
        expected_rels = len(SeedExtractor().build()["relations"])
        self.assertEqual(g.number_of_edges(), expected_rels)


# ═════════════════════════════════════════════════════════════════════════════
# 场景 6：与 SQLite 兼容
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario06SqliteShadow(unittest.TestCase):
    """场景 6：写入 SQLite 影子副本（用于审计）。"""

    def setUp(self) -> None:
        from mcp_tools.db.database import init_db
        try:
            init_db()
        except Exception:  # noqa: BLE001
            pass

    def test_save_to_sqlite(self) -> None:
        """save_to_sqlite() 写入 graph_entities/graph_relations。"""
        from core.kg_seed_extractor import SeedExtractor
        from mcp_tools.db.database import get_connection

        # 清空旧数据
        conn = get_connection()
        try:
            conn.execute("DELETE FROM graph_relations")
            conn.execute("DELETE FROM graph_entities")
            conn.commit()
        finally:
            conn.close()

        extractor = SeedExtractor()
        extractor.build()
        # 重新获取连接写入
        conn = get_connection()
        try:
            result = extractor.save_to_sqlite(conn)
            self.assertGreater(result["nodes"], 0)
            self.assertGreater(result["relations"], 0)
            # 验证 SQLite 中节点数
            row = conn.execute("SELECT COUNT(*) AS cnt FROM graph_entities").fetchone()
            self.assertEqual(row["cnt"], result["nodes"])
        finally:
            conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GridMind M1 三元组抽取测试")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("GridMind M1 知识图谱 — 三元组抽取测试")
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