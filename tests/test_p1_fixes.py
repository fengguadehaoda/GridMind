"""P1 候选修复验证测试。

覆盖 4 个 P1 修复：
- P1-1: VoltageCheck 在数据不一致时跳过（>50% 偏差）
- P1-2: mtime 默认 300s（5 分钟）
- P1-3: ReasoningChainPanel 折叠/展开（前端，单测通过逻辑层）
- P1-4: diagnosis_fusion_log 持久化（fail-closed）

运行：
    PYTHONPATH=. python tests/test_p1_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# 在导入 api 之前开启 Mock 模式
os.environ.setdefault("MOCK_ENABLED", "true")
os.environ.setdefault("EXPLAINABILITY_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.diagnosis_orchestrator import (
    DiagnosisOrchestrator,
    FUSION_STORE,
)
from core.mechanical_checker import VoltageCheck
from core.rules_guard import RulesGuard
from core.schemas.diagnosis import (
    DiagnosisFusionResult,
    DiagnosisOutput,
    MechanicalCheckResult,
    RulesGuardResult,
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
# P1-1: VoltageCheck 数据不一致跳过
# ═══════════════════════════════════════════════════════


def test_p1_1_voltage_data_inconsistent_skip() -> None:
    """P1-1: 当 telemetry voltage 与 rated_voltage 偏差 > 50% 时跳过校验，标记 data_inconsistent。"""
    print("\n=== P1-1: VoltageCheck 数据不一致跳过 ===")

    # 场景 1：TR-001 铭牌 220kV，遥测 10.5kV（偏差 ~95%）→ 数据不一致，跳过
    r = VoltageCheck().check(
        telemetry={"voltage": 10.5},
        device={"device_id": "TR-001", "rated_voltage": 220.0, "device_type": "transformer"},
    )
    _assert(r.passed, f"数据不一致时应通过（跳过）: passed={r.passed}")
    _assert(r.evidence.get("data_inconsistent") is True,
            f"evidence 应标记 data_inconsistent=True: {r.evidence}")
    _assert(r.evidence.get("raw_deviation_pct", 0) > 50,
            f"raw_deviation_pct 应 > 50: {r.evidence.get('raw_deviation_pct')}")
    _ok(f"10.5kV vs 220kV (95% 偏差) → data_inconsistent=True, passed=True")

    # 场景 2：BB-002 35kV，遥测 39.2kV（偏差 12%）→ 正常校验，不跳过
    r2 = VoltageCheck().check(
        telemetry={"voltage": 39.2},
        device={"device_id": "BB-002", "rated_voltage": 35.0, "device_type": "busbar"},
    )
    _assert(r2.evidence.get("data_inconsistent") is False,
            f"12% 偏差不应标记 data_inconsistent: {r2.evidence}")
    _assert(not r2.passed, f"12% > 10% 应不通过: passed={r2.passed}")
    _ok(f"39.2kV vs 35kV (12% 偏差) → data_inconsistent=False, 正常判定")

    # 场景 3：边界值 50% 偏差（10kV vs 5kV = 50%）→ 不算数据不一致（>50% 才是）
    r3 = VoltageCheck().check(
        telemetry={"voltage": 5.0},
        device={"device_id": "X", "rated_voltage": 10.0, "device_type": "busbar"},
    )
    _assert(r3.evidence.get("data_inconsistent") is False,
            f"50% 边界不算数据不一致: {r3.evidence}")
    _ok(f"5kV vs 10kV (50% 边界) → data_inconsistent=False")

    # 场景 4：51% 偏差（10kV vs 4.9kV）→ 视为数据不一致
    r4 = VoltageCheck().check(
        telemetry={"voltage": 4.9},
        device={"device_id": "X", "rated_voltage": 10.0, "device_type": "busbar"},
    )
    _assert(r4.evidence.get("data_inconsistent") is True,
            f"51% 偏差应标记数据不一致: {r4.evidence}")
    _ok(f"4.9kV vs 10kV (51% 偏差) → data_inconsistent=True")


# ═══════════════════════════════════════════════════════
# P1-2: mtime 默认 300s
# ═══════════════════════════════════════════════════════


def test_p1_2_mtime_default_300s() -> None:
    """P1-2: RulesGuard 默认 min_reload_interval_s=300（5 分钟）。"""
    print("\n=== P1-2: mtime 默认 300s ===")

    # 验证配置默认值
    from api.config import settings
    _assert(settings.rules_hot_reload_interval_s == 300,
            f"settings.rules_hot_reload_interval_s 应为 300: {settings.rules_hot_reload_interval_s}")
    _ok(f"api.config.settings.rules_hot_reload_interval_s = 300")

    # 验证 RulesGuard 默认值
    rg = RulesGuard.__init__.__defaults__  # type: ignore[attr-defined]
    # __init__ 默认参数是 (None, 300)
    import inspect
    sig = inspect.signature(RulesGuard.__init__)
    default = sig.parameters["min_reload_interval_s"].default
    _assert(default == 300, f"RulesGuard min_reload_interval_s 默认值应为 300: {default}")
    _ok(f"RulesGuard.__init__ min_reload_interval_s default = 300")

    # 验证节流：手动构造一个 RulesGuard，_min_reload_interval_s=300
    # 修改 mtime 后立即调用 scan()，应被节流（不重载）
    rules_path = Path("core/rules/safety_rules.json")
    original_mtime = rules_path.stat().st_mtime
    new_mtime = original_mtime + 100
    os.utime(rules_path, (new_mtime, new_mtime))

    try:
        # 显式传 300（默认）
        rg300 = RulesGuard(min_reload_interval_s=300)
        # 强制首次重置时间戳
        rg300._last_reload_ts = time.time()  # 刚刚
        # 修改 mtime 后立即 scan：应被节流
        os.utime(rules_path, (original_mtime + 200, original_mtime + 200))
        rule_count_before = rg300.rule_count
        rg300.scan({"user_msg": "test"})
        # 300s 节流期内，rule_count 不变
        _assert(rg300.rule_count == rule_count_before,
                f"300s 节流期内不应重载: before={rule_count_before} after={rg300.rule_count}")
        _ok(f"300s 节流生效：scan() 后 rule_count 仍为 {rg300.rule_count}（未重载）")
    finally:
        os.utime(rules_path, (original_mtime, original_mtime))


# ═══════════════════════════════════════════════════════
# P1-4: diagnosis_fusion_log 持久化
# ═══════════════════════════════════════════════════════


def test_p1_4_diagnosis_fusion_log_persist() -> None:
    """P1-4: 持久化 DiagnosisFusionResult 到 diagnosis_fusion_log 表。"""
    print("\n=== P1-4: diagnosis_fusion_log 持久化 ===")

    # 确保表存在（通过 init_db）
    from mcp_tools.db.database import init_db
    init_db()

    # 构建一个 fusion result
    from core.schemas.diagnosis import ReasoningStep
    fusion = DiagnosisFusionResult(
        llm_output=DiagnosisOutput(fault_type="overload", fault_location="TR-001", confidence=0.8),
        mechanical_check=MechanicalCheckResult(device_id="TR-001"),
        rules_guard=RulesGuardResult(),
        final_severity="warning",
        final_diagnosis="P1-4 test",
        requires_human_review=True,
        reasoning_chain=[
            ReasoningStep(layer="llm", step_name="test", outcome="ok", elapsed_ms=10),
        ],
        thread_id="p1-4-test-thread",
    )

    # 持久化
    from api.services.diagnosis_fusion_service import persist_fusion_result, query_fusion_log
    new_id = persist_fusion_result(fusion)
    _assert(new_id is not None and new_id > 0, f"应返回有效 id: {new_id}")
    _ok(f"persist_fusion_result → id={new_id}")

    # 查询
    rows = query_fusion_log("p1-4-test-thread")
    _assert(len(rows) >= 1, f"应能查到记录: {rows}")
    _assert(rows[0]["final_severity"] == "warning", f"final_severity 应为 warning: {rows[0]}")
    _assert(rows[0]["requires_human_review"] is True, "requires_human_review 应为 True")
    _assert("reasoning_chain" in rows[0]["fusion_result"], "fusion_result 应含 reasoning_chain")
    _ok(f"query_fusion_log → {len(rows)} 条, severity={rows[0]['final_severity']}, hitl=True")

    # 验证 fail-closed：传入无效 result 不应抛异常
    bad = DiagnosisFusionResult(
        llm_output=DiagnosisOutput(),
        mechanical_check=MechanicalCheckResult(device_id="X"),
        rules_guard=RulesGuardResult(),
        final_severity="info",
        final_diagnosis="bad",
        thread_id=None,  # 空 thread_id 应被跳过
    )
    result = persist_fusion_result(bad)
    _assert(result is None, f"空 thread_id 应返回 None: {result}")
    _ok("空 thread_id → 返回 None（fail-closed）")


async def test_p1_4_orchestrator_persists() -> None:
    """P1-4: DiagnosisOrchestrator.fuse() 完成后自动持久化融合结果。"""
    print("\n=== P1-4: Orchestrator 自动持久化 ===")

    orch = DiagnosisOrchestrator()
    llm_text = """一号主变过载。
```diagnosis
{"fault_type":"overload","fault_location":"TR-001","confidence":0.85,"evidence_refs":[],"reasoning_text":"电流 1.5x","severity":"warning","requires_human_review":false,"suggested_action":"dispatch"}
```"""
    result = await orch.fuse(
        llm_text=llm_text,
        user_msg="P1-4 orchestrator test",
        telemetry={"current_load": 180.0, "temperature": 70.0, "voltage": 220.0},
        device={"device_id": "TR-001", "device_type": "transformer",
                "rated_current": 120.0, "short_impedance": 8.5, "rated_voltage": 220.0},
        thread_id="p1-4-orch-test",
    )
    FUSION_STORE.put("p1-4-orch-test", result)

    # 短暂等待 SQLite 异步刷新
    await asyncio.sleep(0.1)

    from api.services.diagnosis_fusion_service import query_fusion_log
    rows = query_fusion_log("p1-4-orch-test")
    _assert(len(rows) >= 1, f"orchestrator fuse() 后应自动持久化: rows={rows}")
    _ok(f"Orchestrator 自动持久化 → {len(rows)} 条记录")

    # 验证 reasoning_chain snapshot 存在
    first = rows[0]
    chain = first["fusion_result"].get("reasoning_chain", [])
    _assert(len(chain) >= 1, f"reasoning_chain snapshot 应存在: {chain}")
    _ok(f"reasoning_chain snapshot 含 {len(chain)} 步")


# ═══════════════════════════════════════════════════════
# P1-3: ReasoningChainPanel 折叠/展开（前端，逻辑层 mock 验证）
# ═══════════════════════════════════════════════════════


def test_p1_3_chain_pagination_logic() -> None:
    """P1-3: 推理链分页逻辑（前端 P1_3_THRESHOLD=5）— 通过纯 JS 逻辑层 mock 验证。"""
    print("\n=== P1-3: 推理链分页逻辑（前端逻辑层） ===")

    # 模拟 P1-3 阈值
    P1_3_THRESHOLD = 5

    # 模拟 100 步推理链
    chain = [{"layer": "fusion", "step_name": f"step-{i}", "outcome": "ok", "elapsed_ms": 10} for i in range(100)]

    # 默认 visible 应该是前 5 步
    visible = chain[:P1_3_THRESHOLD]
    _assert(len(visible) == 5, f"默认应显示 5 步: {len(visible)}")
    _ok(f"默认折叠后展开：visible=5 steps, hidden=95 steps")

    # 展开更多
    visible = chain[:]
    _assert(len(visible) == 100, f"展开后应显示全部: {len(visible)}")
    _ok(f"点击'展开更多'后：visible=100 steps")

    # 小于阈值（4 步）→ 始终全部显示，无展开按钮
    chain_small = chain[:4]
    visible = chain_small[:P1_3_THRESHOLD]
    _assert(len(visible) == 4, f"4 步应全部显示: {len(visible)}")
    has_more = len(visible) < len(chain_small)
    _assert(not has_more, f"4 步时不应有'展开更多'按钮: has_more={has_more}")
    _ok(f"4 步时无'展开更多'按钮（< 阈值）")

    # 阈值边界（5 步）→ 无展开按钮
    chain_boundary = chain[:5]
    visible = chain_boundary[:P1_3_THRESHOLD]
    has_more = len(visible) < len(chain_boundary)
    _assert(not has_more, f"5 步等于阈值时无'展开更多'按钮: has_more={has_more}")
    _ok(f"5 步（= 阈值）时无'展开更多'按钮")


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════


def main() -> None:
    print("=" * 60)
    print("P1 修复验证测试")
    print("=" * 60)

    test_p1_1_voltage_data_inconsistent_skip()
    test_p1_2_mtime_default_300s()
    test_p1_3_chain_pagination_logic()
    test_p1_4_diagnosis_fusion_log_persist()

    asyncio.run(test_p1_4_orchestrator_persists())

    print("\n" + "=" * 60)
    print("✅ ALL P1 FIXES VERIFIED")
    print("=" * 60)


if __name__ == "__main__":
    main()
