"""底层 · 规则护栏（RulesGuard）。

依据 explainability-architecture.md §3.5 设计：
- 从 ``core/rules/safety_rules.json`` 加载规则
- 关键词 + 场景标签匹配（type=keyword / type=condition / type=fusion）
- mtime 热加载（``os.path.getmtime`` 轮询 + ``version`` 字段去重，5 分钟内生效）
- 返回 ``RulesGuardResult``

设计原则：
- **零 I/O 阻塞**：规则数据始终在内存中，磁盘读取只发生在 mtime 变化时
- **纯函数** + **可重入** ``scan()``：单次调用读一次 mtime（避免每秒 100+ 次 IO）
- **fallback 容错**：JSON 损坏或字段缺失时静默回退到空规则 + 警告
- **P1 扩展点**：可注册新的 ``match.type`` 处理器到 ``_MATCH_HANDLERS`` 字典
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from core.schemas.diagnosis import (
    RulesGuardResult,
    TriggeredRule,
)


# ═══════════════════════════════════════════════════════
# 路径常量（与文件结构一致）
# ═══════════════════════════════════════════════════════


def _default_rules_path() -> Path:
    """获取默认规则 JSON 路径（与 core/rules/safety_rules.json 一致）。"""
    return Path(__file__).resolve().parent / "rules" / "safety_rules.json"


# ═══════════════════════════════════════════════════════
# 规则 Guard
# ═══════════════════════════════════════════════════════


class RulesGuard:
    """规则护栏（关键词 / 条件 / 融合匹配 + mtime 热加载）。

    用法：
        rg = RulesGuard()                       # 默认路径 + 5 分钟轮询
        result = rg.scan(context_dict)          # 扫描一次（内部 mtime 检测）
    """

    def __init__(
        self,
        rules_path: Path | str | None = None,
        min_reload_interval_s: int = 300,
    ) -> None:
        """Args:
            rules_path:           规则 JSON 路径（None = 使用默认 ``core/rules/safety_rules.json``）
            min_reload_interval_s: 两次重载最小间隔（默认 300s = 5 分钟，遵循架构要求；P1-2）
        """
        from api.config import settings
        # P1-2: 默认值取自配置（架构要求 5 分钟 = 300s），便于全局调整。
        # 显式传入的 min_reload_interval_s 仍然优先（测试场景可传 0 强制即时重载）。
        if min_reload_interval_s == 300 and settings.rules_hot_reload_interval_s != 300:
            min_reload_interval_s = settings.rules_hot_reload_interval_s
        self._path = Path(rules_path) if rules_path else _default_rules_path()
        self._min_reload_interval_s = min_reload_interval_s
        self._rules: list[dict[str, Any]] = []
        self._version: str = ""
        self._mtime: float = 0.0
        self._last_reload_ts: float = 0.0
        # 首次同步加载
        self._load(force=True)

    # ── 公开方法 ───────────────────────────────────────

    def scan(self, context: dict[str, Any]) -> RulesGuardResult:
        """扫描上下文，返回触发的规则集合。

        Args:
            context: 融合上下文，建议字段：
                - ``user_msg``    : 用户原始问题（str）
                - ``llm_output``  : ``DiagnosisOutput`` 序列化 dict（含 fault_type/fault_location）
                - ``telemetry``   : 最新遥测 dict
                - ``device``      : 设备铭牌 dict
                - ``conflict``    : bool，LLM 与机理是否矛盾
                - ``stage``       : 触发阶段（"user_input" / "llm_output" / "mechanical_check" / "fusion"）

        Returns:
            ``RulesGuardResult`` 包含所有触发的规则 + 汇总标志位。
        """
        # 1) 轮询 mtime（每次 scan 一次，命中率 99%+，IO 成本低）
        self._maybe_reload()

        triggered: list[TriggeredRule] = []
        for rule in self._rules:
            matched = self._match_rule(rule, context)
            if matched is not None:
                triggered.append(matched)

        # 汇总
        forced_hitl = any(r.action == "hitl_required" for r in triggered)
        forced_shutdown = any(r.action == "force_shutdown" for r in triggered)

        if triggered:
            logger.info(
                "RulesGuard: {} rule(s) triggered, forced_hitl={}, forced_shutdown={}",
                len(triggered), forced_hitl, forced_shutdown,
            )

        return RulesGuardResult(
            triggered=triggered,
            forced_hitl=forced_hitl,
            forced_shutdown=forced_shutdown,
        )

    @property
    def version(self) -> str:
        """当前加载的规则版本号。"""
        return self._version

    @property
    def rule_count(self) -> int:
        """当前加载的规则条数。"""
        return len(self._rules)

    # ── 私有方法：热加载 ───────────────────────────────

    def _maybe_reload(self) -> None:
        """检查 mtime，必要时重新加载（带最小间隔节流）。"""
        try:
            current_mtime = os.path.getmtime(self._path)
        except OSError as e:
            logger.warning("RulesGuard: cannot stat '{}': {}", self._path, e)
            return

        if current_mtime == self._mtime:
            return  # 未变，跳过

        now = time.time()
        if (now - self._last_reload_ts) < self._min_reload_interval_s:
            # 命中频率过高，跳过（除非强制）
            return

        self._load(force=False, current_mtime=current_mtime)

    def _load(self, force: bool, current_mtime: float | None = None) -> None:
        """实际加载：读 JSON → 校验 → 替换内存规则。"""
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("RulesGuard: failed to load '{}': {}", self._path, e)
            return  # 保留旧规则（fail-safe）

        if not isinstance(data, dict) or "rules" not in data:
            logger.error("RulesGuard: invalid schema (no 'rules' key)")
            return

        new_version = data.get("version", "")
        new_rules = data.get("rules", [])

        # version 去重
        if not force and new_version == self._version and new_version != "":
            logger.debug("RulesGuard: version unchanged ({})", new_version)
            return

        # 简单 schema 校验（每条必含 id/source/title/action/severity）
        valid: list[dict[str, Any]] = []
        for r in new_rules:
            if not isinstance(r, dict):
                continue
            if not all(k in r for k in ("id", "source", "title", "action", "severity")):
                logger.warning("RulesGuard: rule missing required fields: {}", r.get("id"))
                continue
            valid.append(r)

        self._rules = valid
        self._version = new_version
        self._mtime = current_mtime if current_mtime is not None else os.path.getmtime(self._path)
        self._last_reload_ts = time.time()
        logger.info(
            "RulesGuard: reloaded {} rules, version={}, mtime={}",
            len(self._rules), self._version, self._mtime,
        )

    # ── 私有方法：单条规则匹配 ────────────────────────

    def _match_rule(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
    ) -> TriggeredRule | None:
        """根据 ``match.type`` 分发到对应处理器。"""
        match = rule.get("match", {})
        if not isinstance(match, dict):
            return None
        mtype = match.get("type", "keyword")
        handler = _MATCH_HANDLERS.get(mtype)
        if handler is None:
            logger.debug("RulesGuard: unknown match type '{}'", mtype)
            return None
        try:
            return handler(self, rule, match, context)
        except Exception as e:
            logger.error("RulesGuard: handler '{}' error: {}", mtype, e)
            return None

    def _match_keyword(
        self,
        rule: dict[str, Any],
        match: dict[str, Any],
        context: dict[str, Any],
    ) -> TriggeredRule | None:
        """关键词匹配：扫描 user_msg / llm_text 中的中英文关键词。"""
        keywords: list[str] = match.get("keywords", [])
        if not keywords:
            return None
        haystacks = [
            str(context.get("user_msg", "")),
            str(context.get("llm_text", "")),
        ]
        matched = [kw for kw in keywords if any(kw in h for h in haystacks)]
        if not matched:
            return None
        return TriggeredRule(
            rule_id=rule["id"],
            source=rule["source"],
            code=rule.get("code"),
            title=rule["title"],
            matched_keywords=matched,
            action=rule["action"],
            severity=rule["severity"],
            description=rule.get("description", ""),
            trigger_source=context.get("stage", "user_input"),
        )

    def _match_condition(
        self,
        rule: dict[str, Any],
        match: dict[str, Any],
        context: dict[str, Any],
    ) -> TriggeredRule | None:
        """条件匹配：field/operator/value 或 ratio_field/ratio 的遥测阈值。"""
        field = match.get("field", "")
        operator = match.get("operator", ">")
        value = match.get("value")

        # 解析 field（支持 "telemetry.temperature" / "device.rated_current"）
        actual = self._resolve_field(field, context)
        if actual is None:
            return None

        # 阈值 = 直接 value 或 ratio_field × ratio
        if "ratio_field" in match and "ratio" in match:
            ratio_field = match["ratio_field"]
            ratio = float(match["ratio"])
            base = self._resolve_field(ratio_field, context)
            if base is None or base == 0:
                return None
            threshold = base * ratio
        else:
            threshold = value

        if threshold is None:
            return None

        # 数值比较
        try:
            actual_v = float(actual)
            threshold_v = float(threshold)
        except (TypeError, ValueError):
            return None

        op_map = {
            ">":  actual_v > threshold_v,
            ">=": actual_v >= threshold_v,
            "<":  actual_v < threshold_v,
            "<=": actual_v <= threshold_v,
            "==": math_isclose(actual_v, threshold_v),
            "!=": not math_isclose(actual_v, threshold_v),
        }
        if not op_map.get(operator, False):
            return None

        # device_type_in 过滤
        if "device_type_in" in match:
            device = context.get("device", {}) or {}
            if device.get("device_type") not in match["device_type_in"]:
                return None

        return TriggeredRule(
            rule_id=rule["id"],
            source=rule["source"],
            code=rule.get("code"),
            title=rule["title"],
            matched_keywords=[f"{field} {operator} {threshold}"],
            action=rule["action"],
            severity=rule["severity"],
            description=rule.get("description", ""),
            trigger_source=context.get("stage", "mechanical_check"),
        )

    def _match_fusion(
        self,
        rule: dict[str, Any],
        match: dict[str, Any],
        context: dict[str, Any],
    ) -> TriggeredRule | None:
        """融合条件：``conflict_detected == true`` 等 Orchestrator 阶段判断。"""
        condition = match.get("condition", "")
        # 简化求值：仅支持 "field == value" / "field != value"
        try:
            left, op, right = [s.strip() for s in condition.split()]
            actual = self._resolve_field(left, context)
            if actual is None:
                return None
            if op == "==":
                ok = str(actual).lower() == str(right).lower()
            elif op == "!=":
                ok = str(actual).lower() != str(right).lower()
            else:
                return None
        except ValueError:
            return None
        if not ok:
            return None
        return TriggeredRule(
            rule_id=rule["id"],
            source=rule["source"],
            code=rule.get("code"),
            title=rule["title"],
            matched_keywords=[condition],
            action=rule["action"],
            severity=rule["severity"],
            description=rule.get("description", ""),
            trigger_source=context.get("stage", "fusion"),
        )

    @staticmethod
    def _resolve_field(field: str, context: dict[str, Any]) -> Any:
        """解析 ``"telemetry.temperature"`` → ``context["telemetry"]["temperature"]``。"""
        cur: Any = context
        for part in field.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
            if cur is None:
                return None
        return cur


def math_isclose(a: float, b: float, rel_tol: float = 1e-9) -> bool:
    """包装 math.isclose，避免导入 math 顶层。"""
    import math
    return math.isclose(a, b, rel_tol=rel_tol)


# ═══════════════════════════════════════════════════════
# 匹配处理器注册表
# ═══════════════════════════════════════════════════════


_MATCH_HANDLERS: dict[str, Callable[..., TriggeredRule | None]] = {
    "keyword": RulesGuard._match_keyword,
    "condition": RulesGuard._match_condition,
    "fusion": RulesGuard._match_fusion,
}


__all__ = ["RulesGuard"]
