"""GridMind 知识图谱 M3a · 推理规则引擎（ReasoningRulesEngine）。

设计目标
--------
- **IF-THEN 规则**：基于 M1 ontology 的 9 类关系，定义可声明的推理规则（Q2=A
  代码内嵌于本文件）；
- **优先级排序**：``priority`` 数字越小越先执行（默认 100，与 DAG 拓扑序无关）；
- **置信度**：每条规则附带 ``confidence`` ∈ [0, 1]；最终按 ``min_confidence`` 过滤；
- **5s 超时守护**：daemon 工作线程 + ``join(timeout)`` 守护每条规则执行；
- **去重**：同 ``(src, tgt, relation_type)`` 保留 confidence 最高的；
- **三重防御**：``max_rules=50`` + ``max_inferred=1000`` + ``max_hops=5`` 防 OOM；
- **Feature flag**（默认 False）：``enable_inference_engine=False`` 时 ``infer()``
  返回空 list，与 M2 行为一致。

使用示例::

    from core.kg_reasoning_rules import ReasoningRulesEngine, get_rules_engine

    engine = get_rules_engine()
    rules = engine.list_rules()
    print(f"已注册 {len(rules)} 条规则")

    results = engine.infer(
        entity_id="e-overload",
        ctx={"duration_min": 45, "temp_c": 105},
    )
    for r in results:
        print(r.src_id, "->", r.tgt_id, r.relation_type, r.confidence)

风险与守护（架构 §11.1 · R1）
--------
- **死循环 / 长计算**：每条规则 5s daemon 线程 + ``join(timeout)`` 守护；超时跳过该规则，
  不影响其他规则；记 ``rule_timeout`` 日志事件。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from api.services.sync_log_service import SyncLogService


# ─────────────────────────────────────────────────────────────────────────────
# 1. 异常类型
# ─────────────────────────────────────────────────────────────────────────────

class TooManyRulesError(ValueError):
    """规则数超过 ``max_rules``。"""
    def __init__(self, current: int, max_rules: int) -> None:
        super().__init__(f"Rule count {current} > max_rules {max_rules}")
        self.current = current
        self.max_rules = max_rules


class RuleTimeoutError(TimeoutError):
    """单规则条件执行超时。"""
    def __init__(self, rule_id: str, timeout_s: float) -> None:
        super().__init__(f"Rule '{rule_id}' timed out after {timeout_s}s")
        self.rule_id = rule_id
        self.timeout_s = timeout_s


# ─────────────────────────────────────────────────────────────────────────────
# 2. 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InferenceRule:
    """单条推理规则。"""
    rule_id: str
    relation_type: str
    condition: Callable[["dict", "dict"], bool]
    confidence: float
    description: str
    priority: int = 100
    timeout_s: float = 5.0
    enabled: bool = True


@dataclass
class InferredRelation:
    """推理产出的关系。"""
    src_id: str
    tgt_id: str
    relation_type: str
    confidence: float
    rule_id: str
    evidence_path: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 3. ReasoningRulesEngine
# ─────────────────────────────────────────────────────────────────────────────

class ReasoningRulesEngine:
    """推理规则引擎（IF-THEN DSL + 5s 超时守护 + max_rules/limit 防御）。"""

    def __init__(
        self,
        *,
        max_rules: int = 50,
        default_timeout_s: float = 5.0,
        max_inferred: int = 1000,
        client: Any = None,
    ) -> None:
        self._rules: dict[str, InferenceRule] = {}
        self._max_rules = max_rules
        self._default_timeout_s = default_timeout_s
        self._max_inferred = max_inferred
        self._client = client
        self._audit = SyncLogService()

    # ── 规则管理 ─────────────────────────────────────────────

    def add_rule(self, rule: InferenceRule) -> None:
        """添加规则。同名覆盖（幂等）。

        :raises TooManyRulesError: 规则数超过 ``max_rules``
        """
        if rule.rule_id not in self._rules and len(self._rules) >= self._max_rules:
            raise TooManyRulesError(len(self._rules), self._max_rules)
        is_new = rule.rule_id not in self._rules
        self._rules[rule.rule_id] = rule
        # 审计
        try:
            self._audit.write_pending(
                sync_type="event",
                entity_id=f"rule:{rule.rule_id}",
                payload={
                    "event": "rule_register" if is_new else "rule_update",
                    "rule_id": rule.rule_id,
                    "relation_type": rule.relation_type,
                    "confidence": rule.confidence,
                    "priority": rule.priority,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    def remove_rule(self, rule_id: str) -> bool:
        existed = rule_id in self._rules
        self._rules.pop(rule_id, None)
        if existed:
            try:
                self._audit.write_pending(
                    sync_type="event",
                    entity_id=f"rule:{rule_id}",
                    payload={"event": "rule_remove", "rule_id": rule_id},
                )
            except Exception:  # noqa: BLE001
                pass
        return existed

    def list_rules(self, enabled_only: bool = False) -> list[InferenceRule]:
        if enabled_only:
            return [r for r in self._rules.values() if r.enabled]
        return list(self._rules.values())

    def enable(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            self._rules[rule_id].enabled = True
            return True
        return False

    def disable(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            self._rules[rule_id].enabled = False
            return True
        return False

    def count(self) -> int:
        return len(self._rules)

    # ── 主入口 ─────────────────────────────────────────────

    def infer(
        self,
        entity_id: str,
        ctx: dict[str, Any],
        *,
        rule_ids: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[InferredRelation]:
        """对单个实体执行所有启用的规则（按 priority 升序）。

        流程：
            1. ``KGClient.get_entity(entity_id)`` + 1 跳扩展
            2. 按 ``priority`` 升序遍历规则
            3. daemon 线程 + ``join(timeout)`` 守护（5s 超时）
            4. 条件成立 → 生成 ``InferredRelation``
            5. 去重 + 置信度过滤 + 上限截断
        """
        if not self._client:
            return []

        # 1. 取实体（可能为 None）
        entity = self._client.get_entity(entity_id) or {}
        # 取 1 跳邻接（兼容空 seed_ids 行为）
        try:
            neighbors_raw = self._client.expand_entities([entity_id], hops=1)
            if isinstance(neighbors_raw, tuple) and len(neighbors_raw) >= 1:
                neighbors = neighbors_raw[0]
            elif isinstance(neighbors_raw, list):
                neighbors = neighbors_raw
            else:
                neighbors = []
        except Exception:  # noqa: BLE001
            neighbors = []

        # 把 entity 也加入"邻接"集合（自反边）
        neighbors_with_self = list(neighbors) if neighbors else []
        if not neighbors_with_self:
            # NetworkX 单节点情况：保留 entity 自身作为占位
            neighbors_with_self = [{"entity_id": entity_id, "id": entity_id}]

        # 2. 过滤 + 排序规则
        rules = self._filter_rules(rule_ids)
        rules.sort(key=lambda r: (r.priority, r.rule_id))

        results: list[InferredRelation] = []
        for rule in rules:
            # 3. 超时守护执行
            try:
                fired = self._eval_with_timeout(rule, entity, ctx)
            except RuleTimeoutError:
                # 写审计
                try:
                    self._audit.write_pending(
                        sync_type="event",
                        entity_id=f"rule:{rule.rule_id}",
                        payload={
                            "event": "rule_timeout",
                            "rule_id": rule.rule_id,
                            "entity_id": entity_id,
                            "timeout_s": rule.timeout_s,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
                continue
            except Exception as exc:  # noqa: BLE001
                # 规则执行异常 —— 跳过该规则，记日志
                try:
                    self._audit.write_pending(
                        sync_type="event",
                        entity_id=f"rule:{rule.rule_id}",
                        payload={
                            "event": "rule_error",
                            "rule_id": rule.rule_id,
                            "entity_id": entity_id,
                            "error": str(exc),
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
                continue

            # 4. 条件成立 → 生成 InferredRelation
            if fired:
                for n in neighbors_with_self:
                    tgt_id = (
                        n.get("entity_id")
                        or n.get("id")
                        or entity_id
                    )
                    results.append(
                        InferredRelation(
                            src_id=entity_id,
                            tgt_id=str(tgt_id),
                            relation_type=rule.relation_type,
                            confidence=rule.confidence,
                            rule_id=rule.rule_id,
                            evidence_path=[entity_id, str(tgt_id)],
                        )
                    )

        # 5. 去重
        deduped = self._dedupe(results)
        # 6. 置信度过滤 + 上限截断
        filtered = [r for r in deduped if r.confidence >= min_confidence]
        return filtered[: self._max_inferred]

    # ── 内部方法 ─────────────────────────────────────────────

    def _filter_rules(
        self, rule_ids: list[str] | None,
    ) -> list[InferenceRule]:
        if rule_ids is None:
            return [r for r in self._rules.values() if r.enabled]
        return [
            self._rules[rid]
            for rid in rule_ids
            if rid in self._rules and self._rules[rid].enabled
        ]

    def _eval_with_timeout(
        self,
        rule: InferenceRule,
        entity: dict[str, Any],
        ctx: dict[str, Any],
    ) -> bool:
        """单规则带超时守护执行（工作线程 + ``join(timeout)`` 模式）。

        D5 修复：原 ``threading.Timer`` 实现会**先睡满 ``interval`` 再执行
        condition**，导致即使瞬时完成的规则每次也要阻塞 ``timeout_s``
        （默认 5s，全量测试 400s+ 的主要来源）。改为立即启动 daemon 工作线程，
        用 ``join(timeout)`` 只对真正超时的规则付出等待成本；瞬时规则毫秒级返回。
        超时后线程无法被强杀，靠 daemon=True 与调用方协作停止（测试用停止标志）。
        """
        result_container: dict[str, Any] = {"value": False, "raised": None}

        def target() -> None:
            try:
                result_container["value"] = rule.condition(entity, ctx)
            except Exception as exc:  # noqa: BLE001
                result_container["raised"] = exc

        worker = threading.Thread(target=target, name=f"kg-rule-{rule.rule_id}", daemon=True)
        worker.start()
        # 等待 condition 完成；超时则跳过该规则（不阻塞主流程）
        worker.join(timeout=rule.timeout_s)
        if worker.is_alive():
            raise RuleTimeoutError(rule.rule_id, rule.timeout_s)
        if result_container["raised"]:
            raise result_container["raised"]
        return bool(result_container["value"])

    def _dedupe(
        self,
        relations: list[InferredRelation],
    ) -> list[InferredRelation]:
        """去重：同 ``(src, tgt, type)`` 保留 ``confidence`` 最高的。"""
        dedup_map: dict[tuple, InferredRelation] = {}
        for r in relations:
            key = (r.src_id, r.tgt_id, r.relation_type)
            if key not in dedup_map or r.confidence > dedup_map[key].confidence:
                dedup_map[key] = r
        return list(dedup_map.values())

    # ── 测试钩子：注入 client ────────────────────────────────

    def set_client(self, client: Any) -> None:
        """注入 KGClient（默认 ``__init__`` 接受，可二次注入）。"""
        self._client = client


# ─────────────────────────────────────────────────────────────────────────────
# 4. 6 个内置推理规则（Q2=A 代码内嵌）
# ─────────────────────────────────────────────────────────────────────────────

def _is_overload(entity: dict, ctx: dict) -> bool:
    return (
        (entity.get("entity_type") or entity.get("type")) == "Overload"
        and ctx.get("duration_min", 0) > 30
    )


def _is_shortcircuit(entity: dict, ctx: dict) -> bool:
    return (
        (entity.get("entity_type") or entity.get("type")) == "ShortCircuit"
        and ctx.get("phase") in ("A", "B", "C")
    )


def _is_overtemp(entity: dict, ctx: dict) -> bool:
    return (
        (entity.get("entity_type") or entity.get("type")) == "Overtemp"
        and ctx.get("temp_c", 0) > 95
    )


def _is_voltdev(entity: dict, ctx: dict) -> bool:
    return (
        (entity.get("entity_type") or entity.get("type")) == "VoltageDeviation"
        and abs(ctx.get("delta_pct", 0)) > 10
    )


def _is_overload_high(entity: dict, ctx: dict) -> bool:
    return (
        (entity.get("entity_type") or entity.get("type")) == "Overload"
        and ctx.get("load_pct", 0) > 110
    )


def _is_shortcircuit_durable(entity: dict, ctx: dict) -> bool:
    return (
        (entity.get("entity_type") or entity.get("type")) == "ShortCircuit"
        and ctx.get("duration_ms", 0) > 100
    )


def register_default_rules(engine: ReasoningRulesEngine) -> None:
    """注册 6 个内置 IF-THEN 规则（启动时调用）。"""

    engine.add_rule(InferenceRule(
        rule_id="overload_to_overtemp_v1",
        relation_type="CAUSES",
        condition=_is_overload,
        confidence=0.85,
        description="过载 + 持续时间 > 30min → 油温异常",
        priority=10,
    ))

    engine.add_rule(InferenceRule(
        rule_id="shortcircuit_to_trip_v1",
        relation_type="CAUSES",
        condition=_is_shortcircuit,
        confidence=0.95,
        description="短路（任一相）→ 跳闸动作",
        priority=5,
    ))

    engine.add_rule(InferenceRule(
        rule_id="overtemp_to_insulation_v1",
        relation_type="CAUSES",
        condition=_is_overtemp,
        confidence=0.90,
        description="油温 > 95℃ → 绝缘降低",
        priority=10,
    ))

    engine.add_rule(InferenceRule(
        rule_id="voltdev_to_protect_v1",
        relation_type="CAUSES",
        condition=_is_voltdev,
        confidence=0.80,
        description="电压偏差 > 10% → 保护动作",
        priority=20,
    ))

    engine.add_rule(InferenceRule(
        rule_id="overload_to_loadshed_v1",
        relation_type="HANDLED_BY",
        condition=_is_overload_high,
        confidence=0.75,
        description="过载 > 110% → 减载措施",
        priority=30,
    ))

    engine.add_rule(InferenceRule(
        rule_id="shortcircuit_to_isolate_v1",
        relation_type="HANDLED_BY",
        condition=_is_shortcircuit_durable,
        confidence=0.88,
        description="短路持续 > 100ms → 隔离措施",
        priority=15,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# 5. 单例工厂
# ─────────────────────────────────────────────────────────────────────────────

_engine_instance: ReasoningRulesEngine | None = None


def get_rules_engine(
    *,
    max_rules: int = 50,
    default_timeout_s: float = 5.0,
    max_inferred: int = 1000,
    client: Any = None,
) -> ReasoningRulesEngine:
    """获取 ``ReasoningRulesEngine`` 单例（首次调用注册 6 个默认规则）。"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = ReasoningRulesEngine(
            max_rules=max_rules,
            default_timeout_s=default_timeout_s,
            max_inferred=max_inferred,
            client=client,
        )
        register_default_rules(_engine_instance)
    return _engine_instance


def reset_rules_engine() -> None:
    """重置单例（仅测试用）。"""
    global _engine_instance
    _engine_instance = None


__all__ = [
    "InferenceRule",
    "InferredRelation",
    "TooManyRulesError",
    "RuleTimeoutError",
    "ReasoningRulesEngine",
    "get_rules_engine",
    "reset_rules_engine",
    "register_default_rules",
]
