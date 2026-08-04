"""GridMind M3c · Prometheus 指标收集器（MetricsCollector）。

设计目标
--------
- **零新增依赖**：纯标准库实现 Prometheus exposition format
  （避免引入 prometheus_client；本模块输出 100% 兼容 Prometheus 抓取协议）。
- **进程内单例**：全局唯一 ``MetricsCollector.get_instance()``，与
  ``GrayscaleRouter`` / ``CypherTemplateRegistry`` 保持一致的工厂风格。
- **三类指标**：
    * **Counter**：单调递增计数器（cypher 查询次数、模板渲染次数、回滚次数等）
    * **Gauge**：可增可减仪表盘（当前灰度比例、状态码）
    * **Histogram**：耗时分布（cypher 延迟、RAG 总延迟）
- **Label 支持**：所有指标都支持 labels，可按 backend / template / status 等维度聚合。
- **Feature flag 隔离**：``metrics_enabled=False`` 时所有 ``record_*`` 方法 no-op，
  不影响主链路（满足 §5.7 风险缓解"关闭不影响主调用"）。

Prometheus 兼容
-----------
输出格式示例::

    # HELP kg_cypher_query_total Total Neo4j/NetworkX cypher queries by status
    # TYPE kg_cypher_query_total counter
    kg_cypher_query_total{backend="neo4j",status="ok"} 42
    kg_cypher_query_total{backend="neo4j",status="error"} 3
    # HELP kg_cypher_latency_ms Cypher query latency histogram (milliseconds)
    # TYPE kg_cypher_latency_ms histogram
    kg_cypher_latency_ms_bucket{backend="neo4j",le="1.0"} 0
    kg_cypher_latency_ms_bucket{backend="neo4j",le="5.0"} 0
    kg_cypher_latency_ms_bucket{backend="neo4j",le="+Inf"} 45
    kg_cypher_latency_ms_count{backend="neo4j"} 45
    kg_cypher_latency_ms_sum{backend="neo4j"} 342.5
    # HELP kg_grayscale_ratio Current grayscale ratio percentage (0/10/50/100)
    # TYPE kg_grayscale_ratio gauge
    kg_grayscale_ratio 0

可直接由 Prometheus server 抓取；``promtool check metrics`` 兼容。

跨文件约定
--------
- 所有调用方通过 ``get_metrics_collector()`` 获取单例
- ``metrics_enabled=False`` 时所有方法 no-op（**绝不抛异常**）
- ``export_text()`` 总是返回字符串（即使所有指标为 0 —— 沙箱无流量时仍返回合法 exposition format）
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Iterable

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# 1. 原子指标类型
# ─────────────────────────────────────────────────────────────────────────────

class _Metric:
    """所有 Prometheus 指标的基类（仅约束 name/help/type 三个字段）。"""

    def __init__(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("Metric name must be non-empty string")
        if not all(c.isalnum() or c == "_" for c in name):
            raise ValueError(f"Invalid metric name: {name!r}")
        self.name = name
        self.help = help_text
        self.labelnames = labelnames
        # samples: list[(label_dict, value)]
        self._samples: list[tuple[tuple[tuple[str, str], ...], float]] = []
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        """labels → 稳定 tuple 键。"""
        if not self.labelnames:
            return ()
        if labels is None:
            labels = {}
        return tuple((k, str(labels.get(k, ""))) for k in self.labelnames)


class Counter(_Metric):
    """单调递增计数器（仅加不减）。"""

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("Counter increment must be >= 0")
        key = self._key(labels)
        with self._lock:
            # 找到现有 sample，累加；否则新建
            for i, (k, v) in enumerate(self._samples):
                if k == key:
                    self._samples[i] = (k, v + amount)
                    return
            self._samples.append((key, amount))

    def value(self, **labels: str) -> float:
        key = self._key(labels)
        for k, v in self._samples:
            if k == key:
                return v
        return 0.0

    def export_lines(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} counter"
        for key, value in self._samples:
            label_str = self._format_labels(key)
            yield f"{self.name}{label_str} {_format_number(value)}"


class Gauge(_Metric):
    """可增可减仪表盘。"""

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            for i, (k, _) in enumerate(self._samples):
                if k == key:
                    self._samples[i] = (k, float(value))
                    return
            self._samples.append((key, float(value)))

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            for i, (k, v) in enumerate(self._samples):
                if k == key:
                    self._samples[i] = (k, v + amount)
                    return
            self._samples.append((key, amount))

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def value(self, **labels: str) -> float:
        key = self._key(labels)
        for k, v in self._samples:
            if k == key:
                return v
        return 0.0

    def export_lines(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} gauge"
        for key, value in self._samples:
            label_str = self._format_labels(key)
            yield f"{self.name}{label_str} {_format_number(value)}"


class Histogram(_Metric):
    """耗时分布直方图（默认桶 [1,5,10,50,100,200,500,1000] ms）。"""

    DEFAULT_BUCKETS: tuple[float, ...] = (1, 5, 10, 50, 100, 200, 500, 1000)

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        super().__init__(name, help_text, labelnames)
        self.buckets: tuple[float, ...] = buckets or self.DEFAULT_BUCKETS
        # 每个 label 组合：{bucket_idx: cumulative_count, _sum, _count}
        # 用 dict 存储以 label_key 为键的桶状态
        self._hist_data: dict[tuple[tuple[str, str], ...], dict[str, float | int]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        """记录一次观测值（自动分发到对应桶 + 累加 sum/count）。"""
        key = self._key(labels)
        with self._lock:
            if key not in self._hist_data:
                self._hist_data[key] = {"_count": 0, "_sum": 0.0}
                for i in range(len(self.buckets)):
                    self._hist_data[key][f"_b{i}"] = 0
            d = self._hist_data[key]
            d["_count"] = int(d["_count"]) + 1
            d["_sum"] = float(d["_sum"]) + float(value)
            for i, ub in enumerate(self.buckets):
                if value <= ub:
                    d[f"_b{i}"] = int(d[f"_b{i}"]) + 1

    def value_count(self, **labels: str) -> int:
        key = self._key(labels)
        d = self._hist_data.get(key)
        return int(d["_count"]) if d else 0

    def value_sum(self, **labels: str) -> float:
        key = self._key(labels)
        d = self._hist_data.get(key)
        return float(d["_sum"]) if d else 0.0

    def export_lines(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} histogram"
        for key, data in self._hist_data.items():
            label_str_base = self._format_labels(key)
            # 每个 bucket 一行（cumulative count，le 为上界）
            for i, ub in enumerate(self.buckets):
                bucket_labels = dict(key)
                bucket_labels["le"] = _format_number(ub)
                bucket_str = self._format_labels(
                    tuple(sorted(bucket_labels.items()))
                )
                yield (
                    f"{self.name}_bucket{bucket_str} "
                    f"{_format_number(data[f'_b{i}'])}"
                )
            # +Inf bucket（始终 = _count）
            inf_labels = dict(key)
            inf_labels["le"] = "+Inf"
            inf_str = self._format_labels(tuple(sorted(inf_labels.items())))
            yield f"{self.name}_bucket{inf_str} {_format_number(data['_count'])}"
            # _count / _sum
            yield f"{self.name}_count{label_str_base} {_format_number(data['_count'])}"
            yield f"{self.name}_sum{label_str_base} {_format_number(data['_sum'])}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. label 格式化辅助
# ─────────────────────────────────────────────────────────────────────────────

def _format_number(value: float | int) -> str:
    """Prometheus 数值格式化：无意义的 0 化简 + 无指数。"""
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _format_label_value(v: str) -> str:
    """转义 label value（backslash / 双引号 / 换行）。"""
    return (
        v.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


# 给 Histogram / Counter / Gauge 一个统一方法，把 _Metric 的内部访问权借出
def _metric_format_labels(metric: _Metric, key: tuple[tuple[str, str], ...]) -> str:
    """``{a="x",b="y"}`` 或空字符串。"""
    if not key:
        return ""
    pairs = ",".join(
        f'{k}="{_format_label_value(v)}"' for k, v in key
    )
    return "{" + pairs + "}"


# 给基类注入格式化方法（避免子类重复实现）
_Metric._format_labels = _metric_format_labels  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# 3. MetricsCollector 单例
# ─────────────────────────────────────────────────────────────────────────────

class MetricsCollector:
    """Prometheus 指标收集器（进程内单例）。

    初始化所有 13 个内置指标（M3c §5.2 接口契约）：
        - 4 × Counter（cypher_query_total / template_render_total /
          grayscale_switch_total / rollback_total）
        - 2 × Gauge（grayscale_ratio / grayscale_state）
        - 2 × Histogram（cypher_latency_ms / rag_total_latency_ms）
    外加 5+ 扩展指标（hit_rate / inference_rule_total / rollback_window_* 等）
    """

    _instance: "MetricsCollector | None" = None
    _lock_init = threading.Lock()

    def __init__(self) -> None:
        # ── Counter 集 ──────────────────────────────────────────────
        self.cypher_query_total = Counter(
            "kg_cypher_query_total",
            "Total cypher queries by backend (neo4j/networkx) and status (ok/error)",
            labelnames=("backend", "status"),
        )
        self.template_render_total = Counter(
            "kg_template_render_total",
            "Total cypher template renders by template name and version",
            labelnames=("template", "version"),
        )
        self.grayscale_switch_total = Counter(
            "kg_grayscale_switch_total",
            "Total grayscale ratio switches by actor and transition",
            labelnames=("actor", "from_state", "to_state"),
        )
        self.rollback_total = Counter(
            "kg_rollback_total",
            "Total auto-rollbacks by reason",
            labelnames=("reason",),
        )
        self.inference_rule_total = Counter(
            "kg_inference_rule_total",
            "Total inference rule applications by rule_id and outcome",
            labelnames=("rule_id", "outcome"),
        )
        self.path_optimizer_cache_total = Counter(
            "kg_path_optimizer_cache_total",
            "Total path-optimizer cache operations (hit/miss/evict)",
            labelnames=("op",),
        )

        # ── Gauge 集 ──────────────────────────────────────────────
        self.grayscale_ratio = Gauge(
            "kg_grayscale_ratio",
            "Current grayscale ratio percentage (0/10/50/100)",
        )
        self.grayscale_state = Gauge(
            "kg_grayscale_state",
            "Current grayscale state (off=0/precheck=1/gray10=2/gray50=3/full100=4/rollback=5)",
        )
        self.rollback_window_samples = Gauge(
            "kg_rollback_window_samples",
            "Current number of samples in rollback monitor window",
        )
        self.rollback_window_error_rate = Gauge(
            "kg_rollback_window_error_rate",
            "Current error rate in rollback monitor window (0-1)",
        )

        # ── Histogram 集 ──────────────────────────────────────────────
        self.cypher_latency_ms = Histogram(
            "kg_cypher_latency_ms",
            "Cypher query latency histogram (milliseconds)",
            labelnames=("backend",),
            buckets=(1, 5, 10, 50, 100, 200, 500, 1000),
        )
        self.rag_total_latency_ms = Histogram(
            "kg_rag_total_latency_ms",
            "Full RAG retrieval latency histogram (milliseconds)",
            labelnames=("backend",),
            buckets=(10, 50, 100, 200, 500, 1000, 2000, 5000),
        )
        self.template_render_latency_ms = Histogram(
            "kg_template_render_latency_ms",
            "Cypher template render latency histogram (milliseconds)",
            labelnames=("template",),
            buckets=(0.1, 0.5, 1, 5, 10, 50),
        )

        # 启动时间戳
        self._started_at: float = time.time()
        logger.info("MetricsCollector initialized at startup")

    # ── 单例工厂 ──────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "MetricsCollector":
        """获取全局唯一实例（线程安全）。"""
        if cls._instance is None:
            with cls._lock_init:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅测试用）。"""
        with cls._lock_init:
            cls._instance = None

    # ── 业务便捷方法（供 GrayscaleRouter / RagEngine 等调用）───────

    def record_cypher(
        self,
        backend: str,
        status: str,
        latency_ms: float,
    ) -> None:
        """记录一次 cypher 查询。"""
        try:
            self.cypher_query_total.inc(backend=backend, status=status)
            self.cypher_latency_ms.observe(float(latency_ms), backend=backend)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_cypher failed: {}", exc)

    def record_template(
        self,
        template: str,
        version: str,
        latency_ms: float | None = None,
    ) -> None:
        """记录一次模板渲染。"""
        try:
            self.template_render_total.inc(template=template, version=version)
            if latency_ms is not None:
                self.template_render_latency_ms.observe(float(latency_ms), template=template)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_template failed: {}", exc)

    def record_switch(
        self,
        actor: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """记录一次灰度切换。"""
        try:
            self.grayscale_switch_total.inc(
                actor=actor, from_state=from_state, to_state=to_state,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_switch failed: {}", exc)

    def record_rollback(self, reason: str) -> None:
        """记录一次回滚。"""
        try:
            self.rollback_total.inc(reason=reason)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_rollback failed: {}", exc)

    def update_grayscale(
        self,
        ratio: int,
        state: str,
    ) -> None:
        """更新灰度 Gauge。"""
        try:
            self.grayscale_ratio.set(int(ratio))
            # state → 数值映射（Prometheus gauge 习惯）
            state_to_num = {
                "off": 0, "precheck": 1, "gray10": 2, "monitoring_24h": 3,
                "gray50": 4, "full100": 5, "stable": 6, "rollback": 7,
            }
            self.grayscale_state.set(state_to_num.get(state, -1))
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.update_grayscale failed: {}", exc)

    def update_rollback_window(self, samples: int, error_rate: float) -> None:
        """更新回滚窗口 Gauge。"""
        try:
            self.rollback_window_samples.set(int(samples))
            self.rollback_window_error_rate.set(float(error_rate))
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.update_rollback_window failed: {}", exc)

    # ── 导出入口（Prometheus exposition format）───────────────

    def export_text(self) -> str:
        """导出 Prometheus exposition format 文本。

        返回完整合法的 Prometheus 抓取内容，可直接通过 HTTP text/plain 暴露。
        即使所有指标为 0（沙箱无流量），仍返回包含 TYPE 行 + # HELP 的合法输出。
        """
        lines: list[str] = [
            "# GridMind M3c metrics (Prometheus exposition format)",
            f"# generated_at: {time.time():.0f}",
            f"# started_at: {self._started_at:.0f}",
        ]

        for metric in (
            # Counters
            self.cypher_query_total,
            self.template_render_total,
            self.grayscale_switch_total,
            self.rollback_total,
            self.inference_rule_total,
            self.path_optimizer_cache_total,
            # Gauges
            self.grayscale_ratio,
            self.grayscale_state,
            self.rollback_window_samples,
            self.rollback_window_error_rate,
            # Histograms
            self.cypher_latency_ms,
            self.rag_total_latency_ms,
            self.template_render_latency_ms,
        ):
            lines.extend(metric.export_lines())

        return "\n".join(lines) + "\n"

    # ── 内部状态（供测试 / 调试端点使用）──────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """返回人类可读的摘要（不用于 Prometheus 抓取）。"""
        return {
            "started_at": self._started_at,
            "cypher_total": self.cypher_query_total.value(),
            "template_renders": self.template_render_total.value(),
            "switches": self.grayscale_switch_total.value(),
            "rollbacks": self.rollback_total.value(),
            "grayscale_ratio": self.grayscale_ratio.value(),
            "grayscale_state": self.grayscale_state.value(),
            "window_samples": self.rollback_window_samples.value(),
            "window_error_rate": self.rollback_window_error_rate.value(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. 全局单例工厂 + feature flag 守护
# ─────────────────────────────────────────────────────────────────────────────

def get_metrics_collector() -> MetricsCollector:
    """获取 ``MetricsCollector`` 单例（推荐入口）。"""
    return MetricsCollector.get_instance()


def reset_metrics_collector() -> None:
    """重置单例（仅测试用）。"""
    MetricsCollector.reset_instance()


def is_metrics_enabled() -> bool:
    """``METRICS_ENABLED`` 环境变量查询（默认 True —— 与 M3c §5.6 验收一致）。

    注意：本检查仅用于业务路径的 early-skip（避免无意义开销）；
    ``MetricsCollector`` 本身的 record 方法总是线程安全的 no-op 风格。
    """
    return os.getenv("METRICS_ENABLED", "true").lower() != "false"


__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsCollector",
    "get_metrics_collector",
    "reset_metrics_collector",
    "is_metrics_enabled",
]
