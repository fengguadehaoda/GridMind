# 可解释性 AI 三层架构 · 开发者扩展指南

> 版本：v1.0 · 2026
> 配套代码：`core/mechanical_checker.py` / `core/rules_guard.py` / `core/diagnosis_orchestrator.py`
> 适用读者：后端 / 算法工程师（新增校验类、规则、融合策略）

---

## 1. 架构概览

诊断结论由**三层**融合得出，任一层都可独立扩展：

```
┌─────────────────────────────────────────────┐
│  顶层 LLM  (DiagnosisOutput)                │  ← 新增：修改 prompt
│  Pydantic 解析 ```diagnosis``` 围栏 JSON    │
├─────────────────────────────────────────────┤
│  中层 机理校验 (MechanicalCheckResult)       │  ← 新增：注册 CHECKER_REGISTRY
│  5 种轻量校验，asyncio.gather 并行           │
├─────────────────────────────────────────────┤
│  底层 规则护栏 (RulesGuardResult)            │  ← 新增：编辑 safety_rules.json
│  关键词 + 条件 + 融合，mtime 5 分钟热加载     │
├─────────────────────────────────────────────┤
│  融合 (DiagnosisFusionResult)                │  ← 修改 fusion 策略
│  冲突检测 / severity max / 强制 HITL         │
└─────────────────────────────────────────────┘
```

---

## 2. 如何新增机理校验类

### 2.1 场景示例

需求：增加"谐波畸变率"校验（>5% 告警，>8% 紧急）。

### 2.2 实现步骤

#### 步骤 1：在 `core/mechanical_checker.py` 中实现新类

```python
from core.mechanical_checker import MechanicalCheckBase
from core.schemas.diagnosis import MechanicalCheckItem

class HarmonicDistortionCheck(MechanicalCheckBase):
    """谐波畸变率校验：THD >5% 告警，>8% 紧急。"""

    rule_id: ClassVar[str] = "HD-01"
    rule_name: ClassVar[str] = "谐波畸变校验"
    description: ClassVar[str] = "依据 GB/T 14549-1993 电能质量 公用电网谐波"

    def check(
        self,
        telemetry: dict[str, Any],
        device: dict[str, Any],
    ) -> MechanicalCheckItem:
        thd = telemetry.get("thd_percent")  # 假设 telemetry 含此字段
        if thd is None:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=True,
                severity=None,
                explanation="缺少 THD 数据，跳过校验。",
            )

        if thd > 8.0:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="critical",
                observed_value=thd,
                threshold="THD > 8%",
                evidence={"device_id": device.get("device_id"), "thd": thd},
                explanation=f"谐波畸变率 {thd}% 超过 8% 紧急限值。",
            )
        if thd > 5.0:
            return MechanicalCheckItem(
                rule_id=self.rule_id,
                rule_name=self.rule_name,
                passed=False,
                severity="warning",
                observed_value=thd,
                threshold="THD > 5%",
                evidence={"device_id": device.get("device_id"), "thd": thd},
                explanation=f"谐波畸变率 {thd}% 超过 5% 告警限值。",
            )
        return MechanicalCheckItem(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=True,
            severity="info",
            observed_value=thd,
            threshold="THD ≤ 5%",
            evidence={"device_id": device.get("device_id"), "thd": thd},
            explanation=f"谐波畸变率 {thd}% 在正常范围。",
        )
```

#### 步骤 2：注册到 `CHECKER_REGISTRY`

```python
# 在 core/mechanical_checker.py 末尾
CHECKER_REGISTRY: dict[str, type[MechanicalCheckBase]] = {
    "overload": OverloadCheck,
    "short_circuit": ShortCircuitCheck,
    "power_flow": PowerFlowCheck,
    "voltage": VoltageCheck,
    "temperature": TemperatureCheck,
    "harmonic_distortion": HarmonicDistortionCheck,  # 新增
}
```

#### 步骤 3：（可选）配置开关

```python
# api/config.py
explainability_checker_enabled: dict[str, bool] = {
    # ...
    "harmonic_distortion": True,  # 新增
}
```

**就这样！** Orchestrator 会自动调用，无需修改 `diagnosis_orchestrator.py`。

### 2.3 测试新校验

```python
import asyncio
from core.mechanical_checker import HarmonicDistortionCheck

def test_harmonic():
    # 5% 边界
    r1 = HarmonicDistortionCheck().check(
        {"thd_percent": 5.5},
        {"device_id": "T1"},
    )
    assert r1.severity == "warning"

    # 8% 边界
    r2 = HarmonicDistortionCheck().check(
        {"thd_percent": 8.5},
        {"device_id": "T1"},
    )
    assert r2.severity == "critical"
    print("Harmonic check OK")
```

---

## 3. 如何新增安全 / 业务规则

### 3.1 场景示例

需求：增加"变压器振动异常"规则（关键词 + 条件）。

### 3.2 编辑 `core/rules/safety_rules.json`

```json
{
  "id": "VB-001",
  "source": "emergency_threshold",
  "code": "GB/T-1094.10-2022",
  "title": "变压器振动异常需立即停机检查",
  "match": {
    "type": "condition",
    "field": "telemetry.vibration_mm_s",
    "operator": ">",
    "value": 7.1,
    "device_type_in": ["transformer"]
  },
  "action": "force_shutdown",
  "severity": "critical",
  "description": "依据 GB/T 1094.10，振动幅值 >7.1mm/s 应立即停机。"
}
```

**修改后无需重启！** `RulesGuard` 会在下一次 `scan()` 时检测 mtime 变化并自动重载（5 分钟内生效）。

### 3.3 三种 match.type

| type | 适用场景 | 配置示例 |
|------|---------|---------|
| `keyword` | 关键词匹配（用户问题 / LLM 文本） | `{"type": "keyword", "keywords": ["倒闸", "分合闸"]}` |
| `condition` | 遥测 / 铭牌数值比较 | `{"type": "condition", "field": "telemetry.temperature", "operator": ">", "value": 95}` |
| `fusion` | Orchestrator 阶段判断 | `{"type": "fusion", "condition": "conflict_detected == true"}` |

### 3.4 高级：扩展 condition 字段

`condition` 处理器支持：
- `field`: 点分路径（`"telemetry.temperature"`、`"device.rated_current"`）
- `operator`: `>` / `>=` / `<` / `<=` / `==` / `!=`
- `value`: 静态阈值
- `ratio_field` + `ratio`: 比例阈值（`field > ratio_field * ratio`）
- `device_type_in`: 设备类型白名单

### 3.5 添加自定义 match.type

如果你需要 `regex` / `range` / `cross_field` 等新型匹配器：

```python
# 在 core/rules_guard.py 中：
class RulesGuard:
    def _match_regex(self, rule, match, context):
        pattern = match.get("pattern", "")
        if not pattern:
            return None
        import re
        text = str(context.get("user_msg", "")) + str(context.get("llm_text", ""))
        if re.search(pattern, text):
            return TriggeredRule(
                rule_id=rule["id"],
                source=rule["source"],
                # ... 其它字段
            )
        return None

# 然后注册：
_MATCH_HANDLERS["regex"] = RulesGuard._match_regex
```

---

## 4. 如何修改融合策略

`core/diagnosis_orchestrator.py` 暴露 4 个纯函数，可独立替换：

| 函数 | 职责 | 默认行为 |
|------|------|---------|
| `_detect_conflict` | LLM vs 机理矛盾检测 | LLM normal + 机理 critical → 矛盾 |
| `_decide_final_severity` | 严重度融合 | 取 max（Q9 决策） |
| `_decide_requires_human` | HITL 决策 | 矛盾 / 规则触发 → 必须 HITL |
| `_decide_forced_action` | 强制动作 | 规则 force_shutdown → shutdown |
| `_build_final_diagnosis` | 中文结论文本 | 三段式拼接 |

### 4.1 示例：调整矛盾检测阈值

```python
# 旧版本（默认）：LLM confidence < 0.5 即视为矛盾
def _detect_conflict(llm, mc, rg):
    if llm.fault_type == "normal" and mc.critical_failures > 0:
        return True
    if llm.confidence < 0.5 and mc.critical_failures > 0:
        return True
    return False

# 新版本：要求 confidence < 0.3 更严格
def _detect_conflict(llm, mc, rg):
    if llm.fault_type == "normal" and mc.critical_failures > 0:
        return True
    if llm.confidence < 0.3 and mc.critical_failures > 0:  # 改这里
        return True
    return False
```

> ⚠️ 修改融合策略会影响所有诊断结论，**务必同步更新 `tests/test_explainability.py` 中的预期值**。

---

## 5. 如何临时关闭可解释性 AI（回滚）

### 5.1 环境变量（推荐）

```bash
# 关闭后 Orchestrator 跳过融合，直接透传 LLM 输出
EXPLAINABILITY_ENABLED=false uvicorn api.main:app
```

### 5.2 配置项

```python
# api/config.py
explainability_enabled: bool = False  # 默认关闭
```

### 5.3 端到端回滚验证

```bash
curl -X POST http://localhost:9900/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "TR-001 异常吗？"}'
# 返回结果应不含 "🔍 [可解释性推理链]" 后缀
```

---

## 6. 如何调试推理链

### 6.1 直接调用 Orchestrator

```python
import asyncio
from core.diagnosis_orchestrator import DiagnosisOrchestrator

async def debug():
    orch = DiagnosisOrchestrator()
    result = await orch.fuse(
        llm_text="LLM 原始文本（含 ```diagnosis 围栏）",
        user_msg="用户问题",
        telemetry={"current_load": 200, "temperature": 70},
        device={"device_id": "TR-001", "device_type": "transformer",
                "rated_current": 100, "rated_voltage": 220},
        thread_id="debug-1",
    )
    print(result.model_dump_json(indent=2))

asyncio.run(debug())
```

### 6.2 通过 API 端点

```bash
# 1. 先触发一次诊断
curl -X POST http://localhost:9900/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "TR-001 异常吗？"}'

# 2. 拉取推理链
curl http://localhost:9900/diagnosis/{thread_id}/reasoning | jq
```

返回结构：

```json
{
  "llm_output": { "fault_type": "overload", "confidence": 0.82, ... },
  "mechanical_check": {
    "device_id": "TR-001",
    "checks": [
      {"rule_id": "OC-01", "passed": false, "severity": "critical", ...}
    ],
    "critical_failures": 1
  },
  "rules_guard": {
    "triggered": [{"rule_id": "OC-001", "action": "hitl_required", ...}],
    "forced_hitl": true
  },
  "final_severity": "critical",
  "conflict_detected": false,
  "requires_human_review": true,
  "reasoning_chain": [
    {"layer": "llm", "step_name": "LLM 结构化解析", "outcome": "通过", "elapsed_ms": 0},
    {"layer": "mechanical", "step_name": "机理校验", "outcome": "失败 1 critical", "elapsed_ms": 2},
    {"layer": "rules", "step_name": "规则护栏扫描", "outcome": "触发 2 条", "elapsed_ms": 2},
    {"layer": "fusion", "step_name": "三层融合决策", "outcome": "severity=critical, hitl=True", "elapsed_ms": 0}
  ]
}
```

---

## 7. 性能基线

| 阶段 | 耗时预算 | 实测 |
|------|----------|------|
| LLM 调用（含工具） | ≤ 4.0s | 取决于 dashscope 响应 |
| 机理校验 5 项 | ≤ 0.3s | ~2ms（asyncio.gather） |
| 规则扫描 + 热加载检查 | ≤ 0.1s | ~1ms（mtime 缓存命中） |
| 融合 + 冲突检测 | ≤ 0.1s | <1ms（纯函数） |
| **合计 P95** | **≤ 6.0s** | — |
| 旧基线 | 3.5s | 仅 LLM |

> 性能瓶颈仍在 LLM 端，三层融合额外开销 ≤ 5ms。

---

## 8. 共享知识（跨文件约定）

| 约定 | 出处 | 实现位置 |
|------|------|---------|
| 融合策略（LLM vs 机理矛盾 → 机理优先 + 强制 HITL） | PRD §5 | `core/diagnosis_orchestrator.py::_detect_conflict` |
| 规则热加载（mtime + version 去重，5 分钟） | 架构 §3.5 | `core/rules_guard.py::_maybe_reload` |
| 推理链三段式（LLM → MC → RG → Fusion） | 架构 §4.1 | `core/diagnosis_orchestrator.py::fuse` |
| 设备 ID 引用（fault_location = device_id） | 架构 §7.2 #2 | `prompts/system_prompts.py::DIAGNOSIS_AGENT_PROMPT` |
| severity 升级策略（取 max） | 架构 §8 Q9 | `core/diagnosis_orchestrator.py::_decide_final_severity` |
| Feature Flag 回滚 | 架构 §7.2 #8 | `api/config.py::explainability_enabled` |

---

## 9. 常见问题

**Q1：修改了 safety_rules.json 但 5 分钟还没生效？**
A：检查 `RulesGuard` 是否被重新实例化（应为单例）。可通过 `rg._last_reload_ts` 和 `rg._mtime` 调试。

**Q2：LLM 没有输出 ```diagnosis 围栏？**
A：系统会自动 fallback 到 `fault_type=unknown, requires_human_review=True`，但建议检查 prompt 是否包含围栏要求。

**Q3：前端看不到推理链面板？**
A：确认消息 metadata.has_reasoning_chain=true（仅 diagnosis_agent 触发），且 `GET /diagnosis/{tid}/reasoning` 返回 200。

**Q4：如何验证 mtime 热加载？**
A：见 `tests/test_explainability.py::test_mtime_hot_reload`。

---

> 文档变更请联系：架构师 Bob（gaojy@gridmind.example.com）
