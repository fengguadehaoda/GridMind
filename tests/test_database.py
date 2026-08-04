"""测试 1: 数据库层 — init_db, seed_data, CRUD"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp_tools.db.database import init_db, get_connection
from mcp_tools.db.seed_data import seed_all

errors = []

# 1. 初始化
try:
    init_db()
    print("[PASS] init_db() — 表创建成功")
except Exception as e:
    errors.append(f"init_db failed: {e}")
    print(f"[FAIL] init_db() — {e}")

# 2. 种子数据
try:
    seed_all()
    print("[PASS] seed_all() — 种子数据写入成功")
except Exception as e:
    errors.append(f"seed_all failed: {e}")
    print(f"[FAIL] seed_all() — {e}")

# 3. 验证数据
conn = get_connection()
try:
    # 设备
    devices = conn.execute("SELECT COUNT(*) as cnt FROM devices").fetchone()
    assert devices["cnt"] == 8, f"期望 8 个设备, 实际 {devices['cnt']}"
    print(f"[PASS] devices: {devices['cnt']} 条")

    # 遥测
    telemetry = conn.execute("SELECT COUNT(*) as cnt FROM telemetry").fetchone()
    assert telemetry["cnt"] == 480, f"期望 480 条遥测, 实际 {telemetry['cnt']}"
    print(f"[PASS] telemetry: {telemetry['cnt']} 条")

    # 巡检
    insp = conn.execute("SELECT COUNT(*) as cnt FROM inspections").fetchone()
    assert insp["cnt"] == 32, f"期望 32 条巡检, 实际 {insp['cnt']}"
    print(f"[PASS] inspections: {insp['cnt']} 条")

    # 安规
    rules = conn.execute("SELECT COUNT(*) as cnt FROM safety_rules").fetchone()
    assert rules["cnt"] == 10, f"期望 10 条安规, 实际 {rules['cnt']}"
    print(f"[PASS] safety_rules: {rules['cnt']} 条")

    # 知识片段
    chunks = conn.execute("SELECT COUNT(*) as cnt FROM knowledge_chunks").fetchone()
    assert chunks["cnt"] == 8, f"期望 8 个知识片段, 实际 {chunks['cnt']}"
    print(f"[PASS] knowledge_chunks: {chunks['cnt']} 条")

    # 图谱实体
    entities = conn.execute("SELECT COUNT(*) as cnt FROM graph_entities").fetchone()
    assert entities["cnt"] == 21, f"期望 21 个实体, 实际 {entities['cnt']}"
    print(f"[PASS] graph_entities: {entities['cnt']} 个")

    # 图谱关系
    rels = conn.execute("SELECT COUNT(*) as cnt FROM graph_relations").fetchone()
    assert rels["cnt"] == 24, f"期望 24 个关系, 实际 {rels['cnt']}"
    print(f"[PASS] graph_relations: {rels['cnt']} 个")

    # 4. 验证异常注入 — TR-001 在第 46 条 (0-indexed 45) 有异常负载
    rows_tr001 = conn.execute(
        "SELECT current_load FROM telemetry WHERE device_id='TR-001' ORDER BY timestamp ASC"
    ).fetchall()
    anomaly_value = rows_tr001[45]["current_load"]
    normal_values = [r["current_load"] for i, r in enumerate(rows_tr001) if i not in (45,)]
    avg_normal = sum(normal_values) / len(normal_values)
    if anomaly_value > avg_normal * 1.2:
        print(f"[PASS] TR-001 异常注入: idx=45 load={anomaly_value:.1f} (avg_normal={avg_normal:.1f})")
    else:
        print(f"[WARN] TR-001 异常不明显: idx=45 load={anomaly_value:.1f}, avg_normal={avg_normal:.1f}")

    # 5. 验证 BB-002 严重过载
    rows_bb002 = conn.execute(
        "SELECT current_load FROM telemetry WHERE device_id='BB-002' ORDER BY timestamp ASC"
    ).fetchall()
    bb_anomaly = rows_bb002[50]["current_load"]
    bb_normal = sum(r["current_load"] for i, r in enumerate(rows_bb002) if i != 50) / (len(rows_bb002) - 1)
    if bb_anomaly > bb_normal * 1.5:
        print(f"[PASS] BB-002 异常注入: idx=50 load={bb_anomaly:.1f} (normal={bb_normal:.1f})")
    else:
        print(f"[WARN] BB-002 异常不明显: idx=50 load={bb_anomaly:.1f}, normal={bb_normal:.1f}")

    # 5. 验证安规条款
    rule = conn.execute(
        "SELECT content FROM safety_rules WHERE rule_code='DL/T-572-2010-1'"
    ).fetchone()
    assert rule and "操作票" in rule["content"], "安规条款内容验证失败"
    print(f"[PASS] 安规条款 DL/T-572-2010-1: {rule['content'][:30]}...")

finally:
    conn.close()

print(f"\n{'='*40}")
if errors:
    print(f"结果: {len(errors)} 个失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("结果: ✅ 数据库层全部通过")
