"""测试 6: MCP 服务 — 工具注册与启动"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

errors = []

from mcp_tools.server import mcp

try:
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    print(f"[PASS] MCP Server init: name='{mcp.name}'")
    print(f"      工具列表 ({len(tool_names)}): {tool_names}")
    
    expected = {
        "get_device_list", "get_device_telemetry", "get_latest_telemetry",
        "get_device_info", "get_inspection_records",
        "get_safety_rules", "get_safety_rule_by_code",
        "detect_device_anomalies", "get_device_health_score",
        "get_all_health_scores", "get_critical_devices",
        "query_knowledge_base", "search_knowledge_chunks",
        "search_graph_entities", "get_entity_relations",
        "dispatch_work_order", "suggest_shutdown",
    }
    found = set(tool_names)
    missing = expected - found
    if missing:
        errors.append(f"缺少工具: {missing}")
        print(f"[FAIL] 缺少工具: {missing}")
    else:
        print(f"[PASS] 全部 17 个工具注册正确")
    
    extra = found - expected
    if extra:
        print(f"      额外工具: {extra}")

except Exception as e:
    errors.append(f"MCP 服务器错误: {e}")
    print(f"[FAIL] MCP Server: {e}")

print(f"\n{'='*40}")
if errors:
    print(f"结果: {len(errors)} 个失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("结果: ✅ MCP 服务器通过")
