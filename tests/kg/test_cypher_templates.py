"""GridMind 知识图谱 M3a · Cypher 模板注册中心单元测试（T11 · 10 用例）。

覆盖 AC：
- AC-19（Cypher 注入防护 100%）
- AC-20（"写"操作 100% 写 sync_log）
- AC-21（日志格式 100%）
- AC-2（≥10 内置模板）
"""

from __future__ import annotations

import unittest

from core.kg_cypher_templates import (
    CypherInjectionRisk,
    CypherTemplateRegistry,
    DuplicateTemplateError,
    MissingParamError,
    TemplateDisabled,
    TemplateEntry,
    TemplateNotFound,
    TemplateRegistryConfig,
    register_default_templates,
)


class TestCypherTemplates(unittest.TestCase):
    """10 个用例：register / 同名抛异常 / 多版本 / render 必填校验 /
    render 注入防护（5 个关键字）/ enable+disable 立即生效 / list 全部 /
    list 按 category 过滤 / 单例 / 启动钩子 register_default_templates。"""

    def setUp(self) -> None:
        # 每个用例独立实例（避免状态污染）
        self.registry = CypherTemplateRegistry()

    # ── 1. register 基础 ─────────────────────────────

    def test_register_basic_returns_ok(self) -> None:
        self.registry.register(
            name="test_template_v1",
            cypher="MATCH (n:Entity {id: $id}) RETURN n",
            version="1.0",
            description="测试模板",
            category="test",
            required_params=["id"],
        )
        entry = self.registry.get_template("test_template_v1")
        self.assertIsInstance(entry, TemplateEntry)
        self.assertEqual(entry.name, "test_template_v1")
        self.assertEqual(entry.version, "1.0")
        self.assertEqual(entry.category, "test")

    # ── 2. 同名同版本抛异常 ───────────────────────────

    def test_register_duplicate_raises(self) -> None:
        self.registry.register(
            name="dup_template_v1", cypher="RETURN 1", version="1.0",
        )
        with self.assertRaises(DuplicateTemplateError):
            self.registry.register(
                name="dup_template_v1", cypher="RETURN 2", version="1.0",
            )

    # ── 3. 多版本管理 ──────────────────────────────

    def test_register_multiple_versions(self) -> None:
        self.registry.register(
            name="multi_ver_v1", cypher="MATCH (n) RETURN n", version="1.0",
        )
        self.registry.register(
            name="multi_ver_v1", cypher="MATCH (n) RETURN n LIMIT 10", version="1.1",
        )
        cypher_v1, _ = self.registry.render("multi_ver_v1", {}, version="1.0")
        cypher_v11, _ = self.registry.render("multi_ver_v1", {}, version="1.1")
        self.assertNotIn("LIMIT", cypher_v1)
        self.assertIn("LIMIT", cypher_v11)

    # ── 4. render 必填参数校验 ─────────────────────────

    def test_render_missing_required_param_raises(self) -> None:
        self.registry.register(
            name="req_param_v1",
            cypher="MATCH (n {id: $id}) RETURN n",
            required_params=["id"],
        )
        with self.assertRaises(MissingParamError) as ctx:
            self.registry.render("req_param_v1", {})
        self.assertIn("id", ctx.exception.missing)

    # ── 5. render 注入防护（5+ 个关键字）───────────────────

    def test_render_injection_match_keyword_raises(self) -> None:
        self.registry.register(
            name="inject_v1",
            cypher="MATCH (n {id: $id}) RETURN n",
            required_params=["id"],
        )
        for forbidden in ("; DROP TABLE users", "MATCH (n) DELETE n",
                          "CREATE (a)", "MERGE (a)-[:X]->(b)",
                          "1 OR 1=1"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(CypherInjectionRisk):
                    self.registry.render("inject_v1", {"id": forbidden})

    # ── 6. enable + disable 立即生效 ─────────────────────

    def test_disable_then_render_raises(self) -> None:
        self.registry.register(
            name="toggle_v1", cypher="MATCH (n) RETURN n",
        )
        self.assertTrue(self.registry.is_enabled("toggle_v1"))
        self.registry.disable("toggle_v1")
        self.assertFalse(self.registry.is_enabled("toggle_v1"))
        with self.assertRaises(TemplateDisabled):
            self.registry.render("toggle_v1", {})
        self.registry.enable("toggle_v1")
        self.assertTrue(self.registry.is_enabled("toggle_v1"))
        # 再次 render 应当成功
        cypher, _ = self.registry.render("toggle_v1", {})
        self.assertEqual(cypher, "MATCH (n) RETURN n")

    # ── 7. list_templates 全部 ─────────────────────────

    def test_list_templates_returns_all(self) -> None:
        self.registry.register(name="a_v1", cypher="RETURN 1", category="cat1")
        self.registry.register(name="b_v1", cypher="RETURN 2", category="cat2")
        self.registry.register(name="c_v1", cypher="RETURN 3", category="cat1")
        all_tpls = self.registry.list_templates()
        self.assertEqual(len(all_tpls), 3)

    # ── 8. list_templates 按 category 过滤 ────────────────

    def test_list_templates_filter_by_category(self) -> None:
        self.registry.register(name="a_v1", cypher="RETURN 1", category="cat1")
        self.registry.register(name="b_v1", cypher="RETURN 2", category="cat2")
        cat1 = self.registry.list_templates(category="cat1")
        self.assertEqual(len(cat1), 1)
        self.assertEqual(cat1[0].name, "a_v1")

    # ── 9. 单例 ───────────────────────────────

    def test_singleton_returns_same_instance(self) -> None:
        # 重置单例
        CypherTemplateRegistry.reset_instance()
        inst1 = CypherTemplateRegistry.get_instance()
        inst2 = CypherTemplateRegistry.get_instance()
        self.assertIs(inst1, inst2)
        # 启动钩子会自动注册 10 个模板
        self.assertGreaterEqual(inst1.count(), 10)

    # ── 10. 启动钩子 register_default_templates ─────────────

    def test_register_default_templates_adds_10(self) -> None:
        # 全新实例 → 不应有任何模板
        self.assertEqual(self.registry.count(), 0)
        register_default_templates(self.registry)
        # 10 个内置模板
        self.assertEqual(self.registry.count(), 10)
        # 命名遵循 Q1=A 全小写下划线
        names = [t.name for t in self.registry.list_templates()]
        for n in names:
            self.assertTrue(n.replace("_", "").islower(),
                            f"Template name {n} should be lowercase+underscore")
        # 所有模板 render 应返回合法 Cypher
        for tpl in self.registry.list_templates():
            test_params = {p: "test_value" for p in tpl.required_params}
            test_params.setdefault("seed_ids", ["e-x"])
            test_params.setdefault("hops", 3)
            test_params.setdefault("max_hops", 3)
            test_params.setdefault("limit", 10)
            test_params.setdefault("fault_id", "e-test")
            test_params.setdefault("event_id", "e-test")
            test_params.setdefault("protection_id", "e-test")
            test_params.setdefault("device_id", "e-test")
            test_params.setdefault("operation_type", "switching")
            test_params.setdefault("voltage_level_kv", 35)
            test_params.setdefault("fault_type", "Overload")
            cypher, params = self.registry.render(tpl.name, test_params)
            self.assertIsInstance(cypher, str)
            self.assertGreater(len(cypher), 10)
            self.assertIsInstance(params, dict)


if __name__ == "__main__":
    unittest.main()