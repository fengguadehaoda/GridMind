"""测试 5: MCP 工具 — 所有 4 个工具模块, 真实 SQLite 查询"""
import sys, os, json
# D6：脚本已移至 tests/scripts/，需向上两级到项目根
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# 实际函数名与 server.py 注册的一致
from mcp_tools.tools.monitor_tools import get_device_list, get_device_telemetry, get_latest_telemetry, get_device_info, get_inspection_records
from mcp_tools.tools.safety_tools import get_safety_rules, get_safety_rule_by_code
from mcp_tools.tools.diagnosis_tools import detect_device_anomalies, get_device_health_score, get_all_health_scores
from mcp_tools.tools.knowledge_tools import search_knowledge_chunks, search_graph_entities, get_entity_relations

import asyncio

errors = []

async def run_tests():
    global errors
    # ===================== monitor_tools =====================
    print("--- monitor_tools ---")
    try:
        devices = await get_device_list()
        assert len(devices) >= 8, f"期望 >=8 设备, 实际 {len(devices)}"
        first = devices[0]
        assert "device_id" in first and "device_name" in first
        print(f"[PASS] get_device_list: {len(devices)} 个, 示例: {first['device_id']} = {first['device_name']}")
    except Exception as e:
        errors.append(f"get_device_list 失败: {e}")
        print(f"[FAIL] get_device_list: {e}")

    try:
        info = await get_device_info("TR-001")
        assert info is not None and info["device_id"] == "TR-001"
        print(f"[PASS] get_device_info(TR-001): name={info['device_name']}, status={info['status']}")
    except Exception as e:
        errors.append(f"get_device_info 失败: {e}")
        print(f"[FAIL] get_device_info: {e}")

    try:
        telemetry = await get_latest_telemetry("TR-001")
        assert telemetry is not None and "current_load" in telemetry
        print(f"[PASS] get_latest_telemetry(TR-001): load={telemetry.get('current_load')}, temp={telemetry.get('temperature')}")
    except Exception as e:
        errors.append(f"get_latest_telemetry 失败: {e}")
        print(f"[FAIL] get_latest_telemetry: {e}")

    try:
        history = await get_device_telemetry("TR-001", hours=5)
        assert len(history) <= 5
        assert len(history) > 0
        print(f"[PASS] get_device_telemetry(TR-001, hours=5): {len(history)} 条记录")
    except Exception as e:
        errors.append(f"get_device_telemetry 失败: {e}")
        print(f"[FAIL] get_device_telemetry: {e}")

    try:
        info = await get_device_info("UNKNOWN")
        assert info is None
        print(f"[PASS] get_device_info(UNKNOWN): 返回 None")
    except Exception as e:
        errors.append(f"未知设备查询: {e}")
        print(f"[FAIL] get_device_info(UNKNOWN): {e}")

    try:
        records = await get_inspection_records("BB-002")
        assert len(records) >= 1
        print(f"[PASS] get_inspection_records(BB-002): {len(records)} 条")
    except Exception as e:
        errors.append(f"get_inspection_records 失败: {e}")
        print(f"[FAIL] get_inspection_records: {e}")

    # ===================== safety_tools =====================
    print("\n--- safety_tools ---")
    try:
        rules = await get_safety_rules()
        assert len(rules) >= 10
        print(f"[PASS] get_safety_rules(): {len(rules)} 条")
    except Exception as e:
        errors.append(f"get_safety_rules 失败: {e}")
        print(f"[FAIL] get_safety_rules: {e}")

    try:
        rules = await get_safety_rules(keyword="操作票")
        assert len(rules) >= 1
        print(f"[PASS] get_safety_rules(keyword='操作票'): {len(rules)} 条")
    except Exception as e:
        errors.append(f"关键词查询失败: {e}")
        print(f"[FAIL] get_safety_rules(keyword): {e}")

    try:
        rule = await get_safety_rule_by_code("DL/T-572-2010-1")
        assert rule is not None
        assert "操作票" in rule["content"]
        print(f"[PASS] get_safety_rule_by_code(DL/T-572-2010-1): {rule['content'][:30]}...")
    except Exception as e:
        errors.append(f"get_safety_rule_by_code 失败: {e}")
        print(f"[FAIL] get_safety_rule_by_code: {e}")

    # ===================== diagnosis_tools =====================
    print("\n--- diagnosis_tools ---")
    try:
        diag = await detect_device_anomalies("TR-001")
        assert "device_id" in diag
        print(f"[PASS] detect_device_anomalies(TR-001): health={diag.get('health_score','?')}, anomalies={len(diag.get('anomalies',[]))}")
    except Exception as e:
        errors.append(f"detect_device_anomalies 失败: {e}")
        print(f"[FAIL] detect_device_anomalies: {e}")

    try:
        score = await get_device_health_score("TR-001")
        assert "health_score" in score
        print(f"[PASS] get_device_health_score(TR-001): score={score['health_score']}, level={score['health_level']}")
    except Exception as e:
        errors.append(f"get_device_health_score 失败: {e}")
        print(f"[FAIL] get_device_health_score: {e}")

    try:
        scores = await get_all_health_scores()
        assert len(scores) == 8
        print(f"[PASS] get_all_health_scores(): {len(scores)} 设备")
    except Exception as e:
        errors.append(f"get_all_health_scores 失败: {e}")
        print(f"[FAIL] get_all_health_scores: {e}")

    try:
        score = await get_device_health_score("UNKNOWN")
        assert "error" in score
        print(f"[PASS] get_device_health_score(UNKNOWN): 返回错误信息")
    except Exception as e:
        errors.append(f"未知设备健康分: {e}")
        print(f"[FAIL] get_device_health_score(UNKNOWN): {e}")

    # ===================== knowledge_tools =====================
    print("\n--- knowledge_tools ---")
    try:
        chunks = await search_knowledge_chunks("变压器过载", top_k=3)
        assert len(chunks) >= 1
        print(f"[PASS] search_knowledge_chunks('变压器过载'): {len(chunks)} 条")
        for c in chunks[:2]:
            print(f"       - {c['content'][:50]}...")
    except Exception as e:
        errors.append(f"search_knowledge_chunks 失败: {e}")
        print(f"[FAIL] search_knowledge_chunks: {e}")

    try:
        entities = await search_graph_entities("变压器")
        assert len(entities) >= 1
        print(f"[PASS] search_graph_entities('变压器'): {len(entities)} 条")
    except Exception as e:
        errors.append(f"search_graph_entities 失败: {e}")
        print(f"[FAIL] search_graph_entities: {e}")

    try:
        rels = await get_entity_relations("e-transformer")
        assert len(rels) >= 1
        print(f"[PASS] get_entity_relations(e-transformer): {len(rels)} 条关系")
    except Exception as e:
        errors.append(f"get_entity_relations 失败: {e}")
        print(f"[FAIL] get_entity_relations: {e}")

    print(f"\n{'='*40}")
    if errors:
        print(f"结果: {len(errors)} 个失败")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("结果: ✅ MCP 工具全部通过")


if __name__ == "__main__":
    asyncio.run(run_tests())
