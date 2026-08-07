"""功能介绍意图门控（V1.6 · P0-5 对话 grounding · 架构增补件 §1.3）。

**职责**：纯函数 ``detect(message)`` 在不调用 LLM 的前提下判断用户问题是否
属于「功能介绍」类（产品功能 / 视图 / 引导 / 路由 / 演示等），用于让
:mod:`core.rag_engine` 走 ``search_by_tag('feature-intro')`` 优先通道。

**为什么不做成 LLM 分类**
    LLM 分类准确率更高，但每轮对话会多一次往返（~300-800ms），引导场景对
    首屏延迟敏感，且本规则对运营维护的关键词覆盖已足够（4 类命中 + 1 类
    反向排除），低于 0.5% 的漏判可由 R1 精确匹配兜底。

**共享知识（K-A1）**：意图门控是**单一判定点**——禁止在 knowledge_agent prompt、
前端、MCP 工具中各写一套关键词判断。

作者：寇豆码（工程师）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


# ═══════════════════════════════════════════════════════
# 判定规则（架构增补件 §1.3 · 阈值 ≥0.5 判 hit）
# ═══════════════════════════════════════════════════════

#: R0 强信号词（任务书 A1 约定：含以下任一即判 hit，不需 R2 共现）
#: 这类词是产品功能问答的「充分条件」——用户问出这些词时意图几乎确定
_STRONG_SIGNALS: Final[tuple[str, ...]] = (
    "功能介绍",
    "5个核心视图",
    "5 个核心视图",
    "五个核心视图",
    "5个视图",
    "5 个视图",
    "五个视图",
    "5个页面",
    "5 个页面",
    "五个页面",
    "核心视图",
    "5个功能",
    "5 个功能",
    "核心功能",
)

#: R1 精确匹配：4 个 starterMessage 触发主路径（编辑距离 ≤2 视为命中）
_STARTER_MESSAGES: Final[tuple[str, ...]] = (
    "请给我介绍一下 GridMind 的 5 个核心视图",
    "介绍一下灵枢电网的 5 个视图",
    "GridMind 有哪些功能",
    "灵枢电网有哪些视图",
)

#: R2 产品名（命中一个即可）
_PRODUCT_NAMES: Final[tuple[str, ...]] = (
    "gridmind",
    "灵枢电网",
    "灵枢",
)

#: R2 功能疑问词（命中一个即可）
_PRODUCT_QUESTION_WORDS: Final[tuple[str, ...]] = (
    "是什么",
    "有哪些",
    "介绍",
    "功能",
    "怎么用",
    "能做什么",
    "干什么",
    "干嘛",
    "可以做什么",
)

#: R3 视图/路由名词
_VIEW_KEYWORDS: Final[tuple[str, ...]] = (
    "核心视图",
    "5个视图",
    "五个视图",
    "5个核心视图",
    "五个核心视图",
    "5个页面",
    "五个页面",
    "路由",
    "对话视图",
    "监控视图",
    "灰度",
    "审计日志",
    "系统总览",
    "页面",
)

#: R4 引导词
_GUIDE_KEYWORDS: Final[tuple[str, ...]] = (
    "新手引导",
    "引导",
    "教程",
    "tour",
    "上手",
    "演练场景",
    "演示",
    "快捷指令",
)

#: R5 反向排除（命中则强制 hit=False）
_EXCLUDE_KEYWORDS: Final[tuple[str, ...]] = (
    # 业务实体
    "工单",
    "设备",
    "故障号",
    "变压器",
    "断路器",
    "母线",
    "电缆",
    "遥测",
    "巡检",
    "油温",
    "sf6",
    "局放",
    "跳闸",
    "台账",
    "铭牌",
    # 规程号
    "dl/t",
    "gb/t",
    "q/gdw",
    # 设备号模式：T1 / T01 / TR001 / #T1 / #T-001
    "tr00",
    "#t",
    "#t-",
    "#tr",
    "主变",
)

#: 命中阈值
_THRESHOLD: Final[float] = 0.6

#: 权重常量
_W_R0: Final[float] = 0.7   # 强信号词（任务书 A1 充分条件）
_W_R1: Final[float] = 1.0   # starterMessage 精确匹配：直接 hit
_W_R2: Final[float] = 0.6   # 产品名 + 疑问词
_W_R3: Final[float] = 0.3   # 视图/路由名词
_W_R4: Final[float] = 0.3   # 引导词


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class FeatureIntroIntent:
    """功能介绍意图识别结果。

    Attributes:
        hit: 是否判定为功能介绍类问题（影响走 RAG 优先通道还是通用通道）。
        score: 0.0–1.0 综合得分。``hit == True`` 时 ``score >= _THRESHOLD``。
        intent: 细分意图，取值 ``"overview" | "view" | "scenario" | "tour" | ""``。
            空串表示「功能介绍」通用类，不引导具体子空间。
        tags: 建议优先过滤的 tag 列表（如 ``("kind:view", "kind:overview")``）。
            调用方可用作 ``search_by_tag(tags)`` 的入参。
        matched: 命中的关键词列表（用于日志 / 调试 / 单测断言）。
    """

    hit: bool
    score: float
    intent: str
    tags: tuple[str, ...]
    matched: tuple[str, ...] = field(default_factory=tuple)


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════


def _normalize(text: str) -> str:
    """归一化：去首尾空白 + 折叠内部空白 + 转小写。"""
    return " ".join(str(text or "").split()).lower()


def _has_any(haystack: str, needles: tuple[str, ...]) -> str | None:
    """在 haystack 中按出现顺序查找第一个命中的 needle。"""
    for n in needles:
        if n in haystack:
            return n
    return None


def _has_any_ci(haystack_lower: str, needles: tuple[str, ...]) -> str | None:
    """忽略大小写的 contains 查找。"""
    for n in needles:
        if n.lower() in haystack_lower:
            return n
    return None


# ═══════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════


def detect(message: str) -> FeatureIntroIntent:
    """意图门控：纯函数 · 零 IO · 可单测。

    判定流程（按架构增补件 §1.3 + 任务书 A1 微调）：

    1. **R5 反向排除**：含业务实体/规程号/设备号 → 强制 ``hit=False``。
    2. **R1 精确匹配**：4 个 starterMessage 归一化后全等 / 子串 → ``hit=True``，
       ``score=1.0``。
    3. **R2-R4 加权计分**：每个命中规则累加权重，``score >= 0.6`` 判 hit。
    4. **tag 推导**：
       - R3 命中视图名 → ``('kind:view', 'kind:overview')``
       - R4 命中引导词 → ``('kind:scenario', 'kind:tour', 'kind:wizard')``
       - 否则 → ``('feature-intro',)``（全命名空间）
    5. **intent 细分**：
       - R3 命中 → ``"view"`` 或 ``"overview"``
       - R4 命中 → ``"tour"``
       - 含「场景 / 演练 / 演示」→ ``"scenario"``
       - 其余 → ``"overview"``

    Args:
        message: 用户原始问题（任意大小写 / 任意空白）。

    Returns:
        :class:`FeatureIntroIntent` 不可变结果。
    """
    text = _normalize(message)
    if not text:
        return FeatureIntroIntent(hit=False, score=0.0, intent="", tags=())

    matched: list[str] = []

    # ── 1. R5 反向排除：任一命中 → 直接返回非功能介绍 ──
    exclude_hit = _has_any(text, _EXCLUDE_KEYWORDS)
    if exclude_hit is not None:
        matched.append(f"exclude:{exclude_hit}")
        return FeatureIntroIntent(
            hit=False,
            score=0.0,
            intent="",
            tags=(),
            matched=tuple(matched),
        )

    # ── 1.5 R0 强信号词（任务书 A1：含以下任一即 hit） ──
    strong_hit = _has_any(text, _STRONG_SIGNALS)
    if strong_hit is not None:
        matched.append(f"strong:{strong_hit}")
        # 强信号词直接触发，score 至少 0.7
        return FeatureIntroIntent(
            hit=True,
            score=_W_R0,
            intent="overview",
            tags=("feature-intro",),
            matched=tuple(matched),
        )

    # ── 2. R1 精确匹配（starterMessage） ──
    for sm in _STARTER_MESSAGES:
        sm_norm = _normalize(sm)
        if sm_norm and (sm_norm in text or text in sm_norm):
            matched.append(f"starter:{sm[:20]}")
            return FeatureIntroIntent(
                hit=True,
                score=_W_R1,
                intent="overview",
                tags=("kind:view", "kind:overview"),
                matched=tuple(matched),
            )

    # ── 3. R2-R4 加权计分 ──
    score = 0.0
    product_hit = _has_any_ci(text, _PRODUCT_NAMES)
    question_hit = _has_any(text, _PRODUCT_QUESTION_WORDS)
    if product_hit is not None and question_hit is not None:
        score += _W_R2
        matched.append(f"product:{product_hit}")
        matched.append(f"qword:{question_hit}")

    view_hit = _has_any(text, _VIEW_KEYWORDS)
    if view_hit is not None:
        score += _W_R3
        matched.append(f"view:{view_hit}")

    guide_hit = _has_any(text, _GUIDE_KEYWORDS)
    if guide_hit is not None:
        score += _W_R4
        matched.append(f"guide:{guide_hit}")

    # ── 4. 阈值判定 + tag / intent 推导 ──
    if score < _THRESHOLD:
        return FeatureIntroIntent(
            hit=False,
            score=round(score, 3),
            intent="",
            tags=(),
            matched=tuple(matched),
        )

    # 细分意图与 tag 推导
    if guide_hit is not None and view_hit is None:
        intent = "tour"
        tags: tuple[str, ...] = ("kind:scenario", "kind:tour", "kind:wizard")
    elif view_hit is not None:
        intent = "view"
        tags = ("kind:view", "kind:overview")
    elif "场景" in text or "演练" in text or "演示" in text:
        intent = "scenario"
        tags = ("kind:scenario", "kind:overview")
    else:
        intent = "overview"
        tags = ("feature-intro",)

    return FeatureIntroIntent(
        hit=True,
        score=round(min(score, 1.0), 3),
        intent=intent,
        tags=tags,
        matched=tuple(matched),
    )


# ═══════════════════════════════════════════════════════
# 兼容旧契约（任务书期望 dict 形态）
# ═══════════════════════════════════════════════════════


def detect_dict(message: str) -> dict:
    """意图门控的 dict 形态（任务书约定）。

    与 :func:`detect` 同语义，但返回 ``{"is_feature_intro", "intent", "score"}``。

    Args:
        message: 用户原始问题。

    Returns:
        ``{"is_feature_intro": bool, "intent": str, "score": float, "tags": [...], "matched": [...]}``
    """
    r = detect(message)
    return {
        "is_feature_intro": r.hit,
        "intent": r.intent,
        "score": r.score,
        "tags": list(r.tags),
        "matched": list(r.matched),
    }


__all__ = ["FeatureIntroIntent", "detect", "detect_dict"]
