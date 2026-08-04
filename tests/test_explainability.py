"""P0 可解释性 AI 三层架构 · 5 场景端到端测试。

覆盖 explainability-architecture.md 附录 B 关键场景：
1. 变压器过载（机理校验拦截）
2. 主变油温 > 95℃（规则护栏强制 HITL）
3. 电压偏差分级（VoltageCheck）
4. 正常诊断（三层全通过）
5. LLM 幻觉 vs 机理矛盾（强制人工复核）

运行：
    PYTHONPATH=. python tests/test_explainability.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 在导入 api 之前开启 Mock 模式（无需 LLM Key）
os.environ.setdefault("MOCK_ENABLED", "true")
os.environ.setdefault("EXPLAINABILITY_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.diagnosis_orchestrator import (
    DiagnosisOrchestrator,
    FUSION_STORE,
    parse_diagnosis_fence,
    fallback_diagnosis,
)
from core.mechanical_checker import (
    MechanicalChecker,
    OverloadCheck,
    VoltageCheck,
    TemperatureCheck,
    CHECKER_REGISTRY,
)
from core.rules_guard import RulesGuard
from core.schemas.diagnosis import (
    DiagnosisOutput,
    DiagnosisFusionResult,
    MechanicalCheckResult,
    RulesGuardResult,
    ReasoningStep,
)


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        _fail(msg)


# ═══════════════════════════════════════════════════════
# 场景 1: 变压器过载（机理校验拦截）
# ═══════════════════════════════════════════════════════


async def test_scenario_1_overload() -> None:
    """场景 1: TR-001 电流 1.6×额定 → 机理过载 critical + 规则 OC-001/OC-002 HITL"""
    print("\n=== 场景 1: 变压器过载（机理校验拦截） ===")

    orch = DiagnosisOrchestrator()
    llm_text = """一号主变存在过载风险，建议安排减载。
```diagnosis
{
  "fault_type": "overload",
  "fault_location": "TR-001",
  "confidence": 0.82,
  "evidence_refs": [{"type":"telemetry","id":"t-1234","summary":"电流 192A 偏高"}],
  "reasoning_text": "电流为额定 1.6 倍，建议减载",
  "severity": "warning",
  "requires_human_review": false,
  "suggested_action": "dispatch"
}
```"""
    result = await orch.fuse(
        llm_text=llm_text,
        user_msg="TR-001 电流是不是过高了？",
        telemetry={"current_load": 192.0, "temperature": 70.0, "voltage": 220.0},
        device={"device_id": "TR-001", "device_type": "transformer",
                "rated_current": 120.0, "short_impedance": 8.5, "rated_voltage": 220.0},
        thread_id="expl-s1-overload",
    )
    FUSION_STORE.put("expl-s1-overload", result)

    _assert(result.mechanical_check.critical_failures >= 1, "机理应至少 1 critical")
    _ok(f"机理校验 critical_failures={result.mechanical_check.critical_failures}")

    # 找到 OC-01 校验项
    oc01 = next((c for c in result.mechanical_check.checks if c.rule_id == "OC-01"), None)
    _assert(oc01 is not None, "OC-01 校验项应存在")
    _assert(not oc01.passed, "OC-01 应不通过")
    _assert(oc01.severity == "critical", f"OC-01 severity 应为 critical: {oc01.severity}")
    _ok(f"OC-01 触发: {oc01.explanation[:60]}")

    # 规则应触发 OC-001/OC-002
    triggered_ids = [t.rule_id for t in result.rules_guard.triggered]
    _assert("OC-001" in triggered_ids, f"OC-001 应触发: {triggered_ids}")
    _ok(f"规则触发: {triggered_ids}")

    # 最终 severity 至少 warning
    _assert(result.final_severity in ("warning", "critical"), f"final_severity={result.final_severity}")
    _assert(result.requires_human_review, "应触发 HITL")
    _ok(f"final_severity={result.final_severity}, requires_human_review=True")


# ═══════════════════════════════════════════════════════
# 场景 2: 主变油温 > 95℃（规则护栏强制停运）
# ═══════════════════════════════════════════════════════


async def test_scenario_2_overtemp() -> None:
    """场景 2: TR-001 油温 97℃ → 机理 OT-01 critical + 规则 OT-001 force_shutdown"""
    print("\n=== 场景 2: 主变油温 97℃（规则护栏强制 HITL） ===")

    orch = DiagnosisOrchestrator()
    llm_text = """油温异常高，建议紧急处理。
```diagnosis
{
  "fault_type": "overtemp",
  "fault_location": "TR-001",
  "confidence": 0.95,
  "evidence_refs": [],
  "reasoning_text": "油温 97℃ 超过 95℃ 紧急限值",
  "severity": "critical",
  "requires_human_review": true,
  "suggested_action": "shutdown"
}
```"""
    result = await orch.fuse(
        llm_text=llm_text,
        user_msg="TR-001 油温异常高",
        telemetry={"temperature": 97.0, "current_load": 80.0, "voltage": 220.0},
        device={"device_id": "TR-001", "device_type": "transformer",
                "rated_current": 120.0, "short_impedance": 8.5, "rated_voltage": 220.0},
        thread_id="expl-s2-overtemp",
    )
    FUSION_STORE.put("expl-s2-overtemp", result)

    triggered_ids = [t.rule_id for t in result.rules_guard.triggered]
    _assert("OT-001" in triggered_ids, f"OT-001 应触发: {triggered_ids}")
    _ok(f"规则 OT-001 触发: {[t.title for t in result.rules_guard.triggered if t.rule_id == 'OT-001']}")

    # OT-001 是 force_shutdown
    ot001 = next((t for t in result.rules_guard.triggered if t.rule_id == "OT-001"), None)
    _assert(ot001 is not None, "OT-001 rule 应存在")
    _assert(ot001.action == "force_shutdown", f"OT-001 action 应为 force_shutdown: {ot001.action}")
    _ok(f"OT-001.action = {ot001.action}")

    _assert(result.rules_guard.forced_shutdown, "forced_shutdown 应为 True")
    _assert(result.forced_action == "shutdown", f"forced_action 应为 shutdown: {result.forced_action}")
    _ok(f"forced_shutdown=True, forced_action={result.forced_action}")

    _assert(result.final_severity == "critical", f"final_severity 应为 critical: {result.final_severity}")
    _ok(f"final_severity=critical")


# ═══════════════════════════════════════════════════════
# 场景 3: 电压偏差分级
# ═══════════════════════════════════════════════════════


async def test_scenario_3_voltage() -> None:
    """场景 3: BB-002 35kV 母线电压 +12%（超过 10% 阈值）"""
    print("\n=== 场景 3: 电压偏差分级（VoltageCheck） ===")

    orch = DiagnosisOrchestrator()
    llm_text = """35kV 母线电压偏高。
```diagnosis
{
  "fault_type": "voltage_deviation",
  "fault_location": "BB-002",
  "confidence": 0.75,
  "evidence_refs": [],
  "reasoning_text": "电压 39.2kV 偏差 12%",
  "severity": "warning",
  "requires_human_review": false,
  "suggested_action": "monitor"
}
```"""
    result = await orch.fuse(
        llm_text=llm_text,
        user_msg="BB-002 电压情况",
        telemetry={"voltage": 39.2, "current_load": 800.0, "temperature": 45.0},
        device={"device_id": "BB-002", "device_type": "busbar",
                "rated_current": 1500.0, "short_impedance": 0.15, "rated_voltage": 35.0},
        thread_id="expl-s3-voltage",
    )
    FUSION_STORE.put("expl-s3-voltage", result)

    vl01 = next((c for c in result.mechanical_check.checks if c.rule_id == "VL-01"), None)
    _assert(vl01 is not None, "VL-01 应存在")
    _assert(not vl01.passed, "VL-01 应不通过")
    _ok(f"VL-01 不通过: {vl01.explanation[:80]}")

    # 12% 偏差 > 1.5 × 10% = 15%? 实际是 12 < 15 所以是 warning
    # 但 12 > 10 所以应该不通过
    _assert(vl01.severity in ("warning", "critical"), f"VL-01 severity={vl01.severity}")
    _ok(f"VL-01.severity = {vl01.severity}")


# ═══════════════════════════════════════════════════════
# 场景 4: 正常诊断（三层全通过）
# ═══════════════════════════════════════════════════════


async def test_scenario_4_normal() -> None:
    """场景 4: TR-001 全部指标正常 → 三层全部通过"""
    print("\n=== 场景 4: 正常诊断（三层全通过） ===")

    orch = DiagnosisOrchestrator()
    llm_text = """一号主变运行正常。
```diagnosis
{
  "fault_type": "normal",
  "fault_location": "TR-001",
  "confidence": 0.92,
  "evidence_refs": [],
  "reasoning_text": "电流 60A、油温 60℃、电压 220kV 均在正常范围",
  "severity": "info",
  "requires_human_review": false,
  "suggested_action": "monitor"
}
```"""
    result = await orch.fuse(
        llm_text=llm_text,
        user_msg="TR-001 正常吗",
        telemetry={"current_load": 60.0, "temperature": 60.0, "voltage": 220.0},
        device={"device_id": "TR-001", "device_type": "transformer",
                "rated_current": 120.0, "short_impedance": 8.5, "rated_voltage": 220.0},
        thread_id="expl-s4-normal",
    )
    FUSION_STORE.put("expl-s4-normal", result)

    _assert(result.mechanical_check.overall_pass, "机理校验应全通过")
    _assert(result.mechanical_check.critical_failures == 0, f"critical_failures 应为 0: {result.mechanical_check.critical_failures}")
    _ok(f"机理校验全部通过")

    _assert(not result.rules_guard.triggered, f"应无规则触发: {[t.rule_id for t in result.rules_guard.triggered]}")
    _ok("规则护栏无触发")

    _assert(result.final_severity == "info", f"final_severity 应为 info: {result.final_severity}")
    _assert(not result.requires_human_review, "不应需要 HITL")
    _assert(not result.conflict_detected, "不应检测到矛盾")
    _ok(f"final_severity=info, hitl=False, conflict=False")


# ═══════════════════════════════════════════════════════
# 场景 5: LLM 幻觉 vs 机理矛盾（强制人工复核）
# ═══════════════════════════════════════════════════════


async def test_scenario_5_illusion() -> None:
    """场景 5: LLM 说"正常"但实际过载 → 矛盾检测 + 强制 HITL"""
    print("\n=== 场景 5: LLM 幻觉 vs 机理矛盾（强制人工复核） ===")

    orch = DiagnosisOrchestrator()
    llm_text = """一号主变运行正常，无需处理。
```diagnosis
{
  "fault_type": "normal",
  "fault_location": "TR-001",
  "confidence": 0.85,
  "evidence_refs": [],
  "reasoning_text": "无明显异常",
  "severity": "info",
  "requires_human_review": false,
  "suggested_action": "monitor"
}
```"""
    result = await orch.fuse(
        llm_text=llm_text,
        user_msg="TR-001 有异常吗",
        # 实际电流 200A = 额定 1.67× → critical overload
        telemetry={"current_load": 200.0, "temperature": 70.0, "voltage": 220.0},
        device={"device_id": "TR-001", "device_type": "transformer",
                "rated_current": 120.0, "short_impedance": 8.5, "rated_voltage": 220.0},
        thread_id="expl-s5-illusion",
    )
    FUSION_STORE.put("expl-s5-illusion", result)

    # 这是关键验收点（PRD §6 AC-2）：
    _assert(result.conflict_detected, f"应检测到矛盾: {result.conflict_detected}")
    _ok("conflict_detected=True（关键验收点）")

    _assert(result.requires_human_review, "应触发 HITL")
    _ok("requires_human_review=True（强制人工复核）")

    # MS-001 规则应被追加
    ms001 = [t for t in result.rules_guard.triggered if t.rule_id == "MS-001"]
    _assert(len(ms001) >= 1, f"MS-001 应被触发: {[t.rule_id for t in result.rules_guard.triggered]}")
    _ok(f"MS-001 触发: {ms001[0].title if ms001 else ''}")

    # severity 应升级（取 max）
    _assert(result.final_severity == "critical", f"final_severity 应升级到 critical: {result.final_severity}")
    _ok(f"final_severity=critical（机理优先）")

    # 推理链应有 4 步
    _assert(len(result.reasoning_chain) == 4, f"推理链应 4 步: {len(result.reasoning_chain)}")
    layers = [s.layer for s in result.reasoning_chain]
    _assert(layers == ["llm", "mechanical", "rules", "fusion"], f"推理链顺序: {layers}")
    _ok(f"推理链 4 步顺序: {layers}")


# ═══════════════════════════════════════════════════════
# 单元测试：围栏解析 + fallback
# ═══════════════════════════════════════════════════════


def test_fence_parser() -> None:
    """```diagnosis 围栏解析"""
    print("\n=== 单元测试: 围栏解析 ===")

    text = """设备分析结论。
```diagnosis
{"fault_type":"overload","fault_location":"TR-001","confidence":0.8,"reasoning_text":"test","severity":"warning","requires_human_review":false,"suggested_action":"dispatch"}
```"""
    out = parse_diagnosis_fence(text)
    _assert(out is not None, "围栏解析应成功")
    _assert(out.fault_type == "overload", f"fault_type 应为 overload: {out.fault_type}")
    _assert(out.fault_location == "TR-001", f"fault_location 应为 TR-001: {out.fault_location}")
    _ok(f"围栏解析: {out.fault_type} @ {out.fault_location}")

    # 无围栏 → None
    out2 = parse_diagnosis_fence("没有围栏的纯文本")
    _assert(out2 is None, "无围栏应返回 None")
    _ok("无围栏 → None")


def test_fallback() -> None:
    """fallback 默认值"""
    print("\n=== 单元测试: fallback ===")
    fb = fallback_diagnosis("用户问题", "LLM 文本")
    _assert(fb.fault_type == "unknown", f"fault_type 应为 unknown: {fb.fault_type}")
    _assert(fb.requires_human_review, "fallback 应需要 HITL")
    _ok(f"fallback: fault_type=unknown, requires_human_review=True")


def test_overload_threshold() -> None:
    """OverloadCheck 阈值边界"""
    print("\n=== 单元测试: OverloadCheck 阈值边界 ===")

    # 1.5× → critical
    r1 = OverloadCheck().check({"current_load": 150}, {"rated_current": 100, "device_id": "T1"})
    _assert(r1.severity == "critical", f"1.5x: {r1.severity}")

    # 1.2× → warning
    r2 = OverloadCheck().check({"current_load": 120}, {"rated_current": 100, "device_id": "T1"})
    _assert(r2.severity == "warning", f"1.2x: {r2.severity}")

    # 1.0× → info (pass)
    r3 = OverloadCheck().check({"current_load": 100}, {"rated_current": 100, "device_id": "T1"})
    _assert(r3.passed, "1.0x should pass")
    _ok("1.5x→critical / 1.2x→warning / 1.0x→pass")


def test_schema_validation() -> None:
    """Pydantic 模型必填字段校验"""
    print("\n=== 单元测试: Schema 校验 ===")

    # DiagnosisOutput 默认值
    out = DiagnosisOutput()
    _assert(out.fault_type == "unknown", f"default fault_type: {out.fault_type}")
    _assert(out.requires_human_review, "unknown should force review")
    _ok("DiagnosisOutput 默认值: fault_type=unknown, requires_human_review=True")

    # DiagnosisFusionResult
    fusion = DiagnosisFusionResult(
        llm_output=out,
        mechanical_check=MechanicalCheckResult(device_id="T1"),
        rules_guard=RulesGuardResult(),
        final_severity="info",
        final_diagnosis="test",
    )
    _assert(len(fusion.reasoning_chain) == 0, "default reasoning_chain=[]")
    _ok("DiagnosisFusionResult 校验通过")


def test_mtime_hot_reload() -> None:
    """mtime 热加载"""
    print("\n=== 单元测试: mtime 热加载 ===")
    import os
    from pathlib import Path

    rules_path = Path("core/rules/safety_rules.json")
    original_mtime = rules_path.stat().st_mtime
    new_mtime = original_mtime + 100
    os.utime(rules_path, (new_mtime, new_mtime))

    try:
        rg = RulesGuard(min_reload_interval_s=0)
        rg._last_reload_ts = 0
        rg._mtime = 0
        rg.scan({"user_msg": "test"})
        _assert(rg.rule_count == 11, f"应加载 11 条: {rg.rule_count}")
        _ok(f"mtime 变化 → 自动重载 → {rg.rule_count} 条规则")
    finally:
        os.utime(rules_path, (original_mtime, original_mtime))


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════


def test_orchestrator_importable() -> None:
    """Orchestrator 可导入"""
    print("\n=== 验收: Orchestrator 导入 ===")
    from core.diagnosis_orchestrator import DiagnosisOrchestrator
    o = DiagnosisOrchestrator()
    _ok(f"DiagnosisOrchestrator 实例化: {o.__class__.__name__}")


def test_rules_count() -> None:
    """规则库 ≥ 10 条"""
    print("\n=== 验收: 规则数 ===")
    import json
    with open("core/rules/safety_rules.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    n = len(data["rules"])
    _assert(n >= 10, f"应 ≥ 10 条: {n}")
    _ok(f"规则数: {n} 条")


def test_endpoint_model() -> None:
    """FastAPI 端点：GET /diagnosis/{tid}/reasoning"""
    print("\n=== 验收: API 端点 ===")
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    # 先存一条数据
    FUSION_STORE.put("test-endpoint-1", DiagnosisFusionResult(
        llm_output=DiagnosisOutput(),
        mechanical_check=MechanicalCheckResult(device_id="T1"),
        rules_guard=RulesGuardResult(),
        final_severity="info",
        final_diagnosis="test",
    ))

    resp = client.get("/diagnosis/test-endpoint-1/reasoning")
    _assert(resp.status_code == 200, f"端点应 200: {resp.status_code}")
    data = resp.json()
    _assert("reasoning_chain" in data, "应含 reasoning_chain")
    _assert(data["final_severity"] == "info", f"final_severity 应为 info: {data['final_severity']}")
    _ok(f"GET /diagnosis/test-endpoint-1/reasoning → 200, {len(data['reasoning_chain'])} 步")

    # 不存在的 thread → 404
    resp2 = client.get("/diagnosis/no-such-thread/reasoning")
    _assert(resp2.status_code == 404, f"不存在 thread 应 404: {resp2.status_code}")
    _ok("不存在的 thread → 404")


def main() -> None:
    print("=" * 60)
    print("P0 可解释性 AI · 5 场景端到端测试")
    print("=" * 60)

    # 同步单元测试
    test_orchestrator_importable()
    test_rules_count()
    test_fence_parser()
    test_fallback()
    test_overload_threshold()
    test_schema_validation()
    test_mtime_hot_reload()
    test_endpoint_model()

    # 异步场景测试
    asyncio.run(_async_scenarios())

    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED (5 场景 + 7 单元 + 1 端点)")
    print("=" * 60)


async def _async_scenarios() -> None:
    await test_scenario_1_overload()
    await test_scenario_2_overtemp()
    await test_scenario_3_voltage()
    await test_scenario_4_normal()
    await test_scenario_5_illusion()


if __name__ == "__main__":
    main()
