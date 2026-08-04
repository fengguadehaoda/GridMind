"""测试 7: FastAPI 应用 — 路由解析、Schema 验证"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

errors = []

from api.main import app
from api.schemas import (
    ChatRequest, HealthScoreResult, AnomalyItem, AnomalySeverity,
    GraphEntity, GraphRelation, HealthLevel, Message, ThreadInfo,
    ChatResponse, KnowledgeAnswer, RetrievalResult, DeviceInfo,
)

# 1. 路由汇总
try:
    routes = [(r.path, list(r.methods) if hasattr(r, 'methods') else ['ANY']) 
              for r in app.routes if hasattr(r, 'path')]
    print(f"[PASS] FastAPI app init: {len(routes)} 个路由")
    
    route_paths = {r[0] for r in routes}
    expected_routes = {"/", "/chat", "/chat/stream/{thread_id}", 
                       "/interrupt/{thread_id}/approve", "/interrupt/{thread_id}/reject",
                       "/thread/{thread_id}", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    missing_routes = expected_routes - route_paths
    if missing_routes:
        errors.append(f"缺少路由: {missing_routes}")
        print(f"[FAIL] 缺少路由: {missing_routes}")
    else:
        print(f"[PASS] 所有 10 个路由注册正确")
    
except Exception as e:
    errors.append(f"FastAPI 初始化失败: {e}")
    print(f"[FAIL] FastAPI: {e}")

# 2. Schema 验证
print("\n--- Schema 验证 ---")
try:
    req = ChatRequest(message="测试", thread_id="thread-test-001")
    assert req.message == "测试"
    assert req.thread_id == "thread-test-001"
    print(f"[PASS] ChatRequest: message='{req.message}', thread_id='{req.thread_id}'")
except Exception as e:
    errors.append(f"ChatRequest 失败: {e}")
    print(f"[FAIL] ChatRequest: {e}")

try:
    score = HealthScoreResult(
        device_id="TR-001",
        device_name="一号主变",
        health_score=85.0,
        health_level=HealthLevel.warning,
        anomalies=[],
        summary="设备运行正常，建议定期巡检",
    )
    assert score.health_score == 85.0
    assert score.health_level == HealthLevel.warning
    print(f"[PASS] HealthScoreResult: device={score.device_id}, score={score.health_score}, level={score.health_level}")
except Exception as e:
    errors.append(f"HealthScoreResult 失败: {e}")
    print(f"[FAIL] HealthScoreResult: {e}")

try:
    item = AnomalyItem(
        device_id="TR-001",
        metric="current_load",
        value=85.0,
        z_score=3.5,
        severity=AnomalySeverity.high,
        description="负载过高",
    )
    assert item.severity == AnomalySeverity.high
    print(f"[PASS] AnomalyItem: metric={item.metric}, severity={item.severity}, z={item.z_score}")
except Exception as e:
    errors.append(f"AnomalyItem 失败: {e}")
    print(f"[FAIL] AnomalyItem: {e}")

try:
    entity = GraphEntity(id="e-TR001", name="一号主变", type="设备", properties={"device_id": "TR-001"})
    rel = GraphRelation(source_id="e-TR001", target_id="e-overload", relation_type="发生缺陷")
    assert entity.id == "e-TR001"
    assert entity.type == "设备"
    assert rel.relation_type == "发生缺陷"
    print(f"[PASS] GraphEntity + GraphRelation: id={entity.id}, type={entity.type}")
except Exception as e:
    errors.append(f"Graph 结构失败: {e}")
    print(f"[FAIL] Graph: {e}")

# 3. 其他 Schema
try:
    msg = Message(role="user", content="测试消息")
    assert msg.content == "测试消息"
    print(f"[PASS] Message: role={msg.role}")
except Exception as e:
    errors.append(f"Message 失败: {e}")
    print(f"[FAIL] Message: {e}")

try:
    chat_resp = ChatResponse(thread_id="test", response="hello")
    assert chat_resp.thread_id == "test"
    print(f"[PASS] ChatResponse: fields 正确")
except Exception as e:
    errors.append(f"ChatResponse 失败: {e}")
    print(f"[FAIL] ChatResponse: {e}")

try:
    ka = KnowledgeAnswer(answer="测试答案", confidence=0.85)
    assert ka.confidence == 0.85
    assert ka.refuse is False
    print(f"[PASS] KnowledgeAnswer: answer + confidence + refuse defaults")
except Exception as e:
    errors.append(f"KnowledgeAnswer 失败: {e}")
    print(f"[FAIL] KnowledgeAnswer: {e}")

try:
    dev = DeviceInfo(device_id="TR-001", device_name="一号主变", device_type="transformer", location="A区", status="normal")
    assert dev.device_type.value == "transformer"
    print(f"[PASS] DeviceInfo: type enum 正确")
except Exception as e:
    errors.append(f"DeviceInfo 失败: {e}")
    print(f"[FAIL] DeviceInfo: {e}")

# 4. OpenAPI schema
print("\n--- OpenAPI Schema ---")
try:
    openapi = app.openapi()
    assert openapi["info"]["title"] is not None
    paths = openapi["paths"]
    print(f"[PASS] OpenAPI schema: {len(paths)} 个路径")
    for path, spec in sorted(paths.items()):
        methods = list(spec.keys())
        summary = spec.get(methods[0], {}).get("summary", "N/A")
        print(f"       {methods[0].upper()} {path} — {summary}")
except Exception as e:
    errors.append(f"OpenAPI schema 失败: {e}")
    print(f"[FAIL] OpenAPI schema: {e}")

print(f"\n{'='*40}")
if errors:
    print(f"结果: {len(errors)} 个失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("结果: ✅ FastAPI 应用通过")
