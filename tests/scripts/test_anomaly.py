"""测试 2: 异常检测引擎 — 所有设备健康评分 + 异常清单"""
import sys, os
# D6：脚本已移至 tests/scripts/，需向上两级到项目根
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.anomaly_detection import AnomalyDetectionService

errors = []
detector = AnomalyDetectionService()

# 1. 检测单台正常设备
try:
    result = detector.detect_device("TR-002")
    assert result is not None, "detect_device 返回 None"
    print(f"[PASS] TR-002 (二号主变): health={result.health_score}, level={result.health_level.value}, anomalies={len(result.anomalies)}")
    assert 0 <= result.health_score <= 100, f"健康分超出范围: {result.health_score}"
except Exception as e:
    errors.append(f"TR-002 检测失败: {e}")
    print(f"[FAIL] TR-002: {e}")

# 2. 检测异常设备 (TR-001 注入过载)
try:
    result = detector.detect_device("TR-001")
    assert result is not None, "detect_device 返回 None"
    print(f"[PASS] TR-001 (一号主变): health={result.health_score}, level={result.health_level.value}, anomalies={len(result.anomalies)}")
    if result.anomalies:
        print(f"       首个异常: {result.anomalies[0].metric} z={result.anomalies[0].z_score}")
except Exception as e:
    errors.append(f"TR-001 检测失败: {e}")
    print(f"[FAIL] TR-001: {e}")

# 3. 检测严重设备 (BB-002 注入严重过载)
try:
    result = detector.detect_device("BB-002")
    assert result is not None
    print(f"[PASS] BB-002 (35kV母线): health={result.health_score}, level={result.health_level.value}, anomalies={len(result.anomalies)}")
except Exception as e:
    errors.append(f"BB-002 检测失败: {e}")
    print(f"[FAIL] BB-002: {e}")

# 4. 未知设备
try:
    result = detector.detect_device("UNKNOWN")
    assert result is None, "未知设备应返回 None"
    print(f"[PASS] 未知设备检测: 返回 None")
except Exception as e:
    errors.append(f"未知设备检测: {e}")
    print(f"[FAIL] 未知设备: {e}")

# 5. 全部设备检测
try:
    results = detector.detect_all()
    assert len(results) == 8, f"期望 8 个结果, 实际 {len(results)}"
    
    levels = {}
    for r in results:
        levels[r.health_level.value] = levels.get(r.health_level.value, 0) + 1
    
    print(f"[PASS] detect_all(): {len(results)} 设备, 分布: {levels}")
    
    # 验证每个结果都有健康分
    for r in results:
        assert 0 <= r.health_score <= 100, f"{r.device_id} 健康分异常: {r.health_score}"
except Exception as e:
    errors.append(f"全部检测失败: {e}")
    print(f"[FAIL] detect_all(): {e}")

# 6. 验证 AnomalyItem 结构
try:
    result = detector.detect_device("TR-001")
    if result and result.anomalies:
        a = result.anomalies[0]
        assert hasattr(a, 'device_id'), "缺少 device_id"
        assert hasattr(a, 'metric'), "缺少 metric"
        assert hasattr(a, 'z_score'), "缺少 z_score"
        assert hasattr(a, 'severity'), "缺少 severity"
        assert hasattr(a, 'description'), "缺少 description"
        print(f"[PASS] AnomalyItem 结构完整: {a.metric} z={a.z_score} severity={a.severity.value}")
except Exception as e:
    errors.append(f"AnomalyItem 结构验证失败: {e}")
    print(f"[FAIL] AnomalyItem: {e}")

print(f"\n{'='*40}")
if errors:
    print(f"结果: {len(errors)} 个失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("结果: ✅ 异常检测全部通过")
