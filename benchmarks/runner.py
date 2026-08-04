"""GridMind 知识图谱 M3b · 基准执行器（warmup + N 次 + 统计）。

设计（kg-m3-split.md §4.2 / §4.4）
--------
- ``BenchmarkRunner(backend, scenario)`` → ``run(n)`` → ``BenchmarkResult``
- ``warmup(n=10)`` 跳过冷启动（JIT / 缓存 / 索引预热）
- ``run(n=100)`` 收集延迟、内存、错误数 → 计算 P50/P95/P99
- 内存通过 ``tracemalloc`` 测量（跨平台 / 沙箱安全）
- 错误**不熔断**：单次异常仅记入 ``error_count``，保证后续迭代可继续
- 线程安全：不假设并发（验收 6：基准独立进程运行）
"""
from __future__ import annotations

import gc
import math
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from benchmarks.scenarios import Scenario


# ═════════════════════════════════════════════════════════════════════════════
# 1. 数据类
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkResult:
    """单场景 × 单后端的基准结果。

    Attributes:
        scenario_id: 场景 ID
        backend: ``"neo4j"`` / ``"networkx"`` / ``"skip"``（不可用）
        p50_ms / p95_ms / p99_ms / mean_ms: 延迟分位数（毫秒）
        min_ms / max_ms: 延迟范围
        peak_mem_mb: 峰值内存增量（tracemalloc 测得，仅本次执行的新分配）
        throughput_qps: 吞吐（次/秒；按 mean_ms 推算）
        error_count: 错误次数（异常被吞，记录在案）
        total_runs: 总执行次数
        successful_runs: 成功次数
        notes: 备注（如"沙箱无 Neo4j"）
    """

    scenario_id: str
    backend: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    min_ms: float
    max_ms: float
    peak_mem_mb: float
    throughput_qps: float
    error_count: int
    total_runs: int
    successful_runs: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "backend": self.backend,
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "peak_mem_mb": round(self.peak_mem_mb, 3),
            "throughput_qps": round(self.throughput_qps, 3),
            "error_count": self.error_count,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "notes": self.notes,
        }


@dataclass
class Comparison:
    """两个 BenchmarkResult 的对比。

    Attributes:
        scenario_id: 场景 ID
        neo4j: Neo4j result（None 时为 SKIP）
        networkx: NetworkX result
        p95_speedup: Neo4j P95 / NetworkX P95（<1 = Neo4j 更快）
        winner: ``"neo4j"`` / ``"networkx"`` / ``"tie"`` / ``"skip"``
    """

    scenario_id: str
    neo4j: BenchmarkResult | None
    networkx: BenchmarkResult
    p95_speedup: float
    winner: str


# ═════════════════════════════════════════════════════════════════════════════
# 2. 统计工具
# ═════════════════════════════════════════════════════════════════════════════

def _percentile(sorted_values: list[float], p: float) -> float:
    """计算分位数（p ∈ [0, 100]）。

    使用线性插值（与 numpy.percentile 默认行为一致）。
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    # 索引位置
    k = (n - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return float(d0 + d1)


def _summarize(latencies: list[float]) -> dict[str, float]:
    """从延迟列表计算 P50/P95/P99/mean/min/max。"""
    if not latencies:
        return {
            "p50": 0.0, "p95": 0.0, "p99": 0.0,
            "mean": 0.0, "min": 0.0, "max": 0.0,
        }
    s = sorted(latencies)
    return {
        "p50": _percentile(s, 50),
        "p95": _percentile(s, 95),
        "p99": _percentile(s, 99),
        "mean": sum(s) / len(s),
        "min": float(s[0]),
        "max": float(s[-1]),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. BenchmarkRunner
# ═════════════════════════════════════════════════════════════════════════════

class BenchmarkRunner:
    """单场景 × 单后端的基准执行器。

    用法::

        runner = BenchmarkRunner(backend=nx_backend, scenario=sc)
        runner.warmup(n=10)
        result = runner.run(n=100)

    设计约束：
    - **不修改 backend 状态**：仅读取（除 warmup 预热外）
    - **不跨进程**：单进程内完成 warmup + run
    - **错误隔离**：单次异常仅记入 error_count，不影响后续迭代
    """

    def __init__(self, backend: Any, scenario: Scenario) -> None:
        self.backend = backend
        self.scenario = scenario
        self._latencies: list[float] = []
        self._errors: int = 0
        self._peak_mem_bytes: int = 0

    # ── 调度：根据 scenario.method 调用 backend 相应方法 ──

    def _invoke(self) -> Any:
        """根据 ``scenario.method`` 调度到 backend。"""
        method_name = self.scenario.method
        mp = self.scenario.params.get("method_params", {})

        method = getattr(self.backend, method_name, None)
        if method is None:
            # backend 不支持此方法（NetworkX 不支持 cypher_query）
            raise NotImplementedError(
                f"backend {self.backend.name!r} 不支持方法 {method_name!r}"
            )

        # 根据方法签名分发参数
        if method_name == "get_entity":
            return method(mp["entity_id"])
        if method_name == "search_entities":
            return method(mp["query"], limit=mp.get("limit", 10))
        if method_name == "get_relations":
            return method(mp["entity_id"], relation_type=mp.get("relation_type"))
        if method_name == "expand_entities":
            return method(
                list(mp["seed_entity_ids"]),
                hops=mp.get("hops", 2),
            )
        if method_name == "expand_with_optimizer":
            return method(
                list(mp["seeds"]),
                hops=mp.get("hops", 2),
                limit=mp.get("limit", 100),
            )
        if method_name == "execute_template":
            return method(mp["name"], mp.get("params", {}))
        raise ValueError(f"未知方法: {method_name}")

    def _single_run(self) -> bool:
        """执行单次；返回 True=成功，False=异常。"""
        gc.collect()  # 减少上一次分配干扰
        t0 = time.perf_counter()
        try:
            self._invoke()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._latencies.append(elapsed_ms)
            return True
        except NotImplementedError:
            # backend 不支持 → 立即熔断（避免 100 次 NotImplementedError）
            self._errors = self._total_target
            return False
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            # 失败也记录（便于查看尾部延迟异常）
            self._latencies.append(elapsed_ms)
            self._errors += 1
            logger.debug(
                "BenchmarkRunner: 场景 {} 第 {} 次执行失败: {}",
                self.scenario.scenario_id, len(self._latencies), exc,
            )
            return False

    # ── warmup / run ─────────────────────────────────────

    def warmup(self, n: int = 10) -> None:
        """预热 N 次（不计入统计）。"""
        for _ in range(n):
            try:
                self._invoke()
            except NotImplementedError:
                # 不支持的方法跳过预热
                return
            except Exception:  # noqa: BLE001
                pass

    def run(self, n: int = 100) -> BenchmarkResult:
        """执行 N 次并返回统计结果。

        使用 ``tracemalloc`` 测量本次执行期间的峰值内存增量。
        """
        self._latencies = []
        self._errors = 0
        self._peak_mem_bytes = 0
        self._total_target = n

        # 内存追踪
        tracemalloc.start()
        try:
            for _ in range(n):
                self._single_run()
                # 采样峰值
                current, peak = tracemalloc.get_traced_memory()
                self._peak_mem_bytes = max(self._peak_mem_bytes, peak)
        finally:
            tracemalloc.stop()

        stats = _summarize(self._latencies)
        mean_ms = stats["mean"] if stats["mean"] > 0 else 0.001
        peak_mem_mb = self._peak_mem_bytes / (1024.0 * 1024.0)
        throughput_qps = 1000.0 / mean_ms if mean_ms > 0 else 0.0

        # 检查是否全部 NotImplementedError
        if self._errors >= n:
            notes = f"backend {self.backend.name} 不支持方法 {self.scenario.method}"
            return BenchmarkResult(
                scenario_id=self.scenario.scenario_id,
                backend="skip",
                p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, mean_ms=0.0,
                min_ms=0.0, max_ms=0.0,
                peak_mem_mb=peak_mem_mb,
                throughput_qps=0.0,
                error_count=self._errors,
                total_runs=n,
                successful_runs=0,
                notes=notes,
            )

        return BenchmarkResult(
            scenario_id=self.scenario.scenario_id,
            backend=self.backend.name,
            p50_ms=stats["p50"],
            p95_ms=stats["p95"],
            p99_ms=stats["p99"],
            mean_ms=stats["mean"],
            min_ms=stats["min"],
            max_ms=stats["max"],
            peak_mem_mb=peak_mem_mb,
            throughput_qps=throughput_qps,
            error_count=self._errors,
            total_runs=n,
            successful_runs=n - self._errors,
        )

    # ── 对比 ─────────────────────────────────────

    def compare(self, other: "BenchmarkResult") -> Comparison:
        """与另一后端的结果对比。"""
        # self 是当前后端，other 是参照
        networkx_result: BenchmarkResult
        neo4j_result: BenchmarkResult | None

        if self.backend.name == "networkx":
            networkx_result = self.run(n=1) if False else self._snapshot_or_run(other)  # 简化
            neo4j_result = other
        else:
            neo4j_result = other
            networkx_result = self._snapshot_or_run(other)

        # 简化为：使用 self + other 推断
        # 调用方约定：self=Neo4j, other=NetworkX
        if self.backend.name == "neo4j" and other.backend in ("networkx", "skip"):
            neo4j_r = self._to_result() if False else None  # 不再调用
            networkx_r = other
            if neo4j_r is None:
                # 没法重构；用 current + other
                neo4j_r = self._current_result()  # 类型忽略
            neo4j_result = neo4j_r
            networkx_result = networkx_r
        else:
            networkx_result = other
            neo4j_result = self._current_result()

        return _make_comparison(
            scenario_id=self.scenario.scenario_id,
            neo4j=neo4j_result,
            networkx=networkx_result,
        )

    # ── 内部辅助：构造当前已收集的 result ─────────────

    def _current_result(self) -> BenchmarkResult:
        """用当前 ``_latencies`` 构造 result（用于 compare 时复用 run 的结果）。"""
        if not self._latencies:
            return BenchmarkResult(
                scenario_id=self.scenario.scenario_id,
                backend=self.backend.name,
                p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, mean_ms=0.0,
                min_ms=0.0, max_ms=0.0, peak_mem_mb=0.0,
                throughput_qps=0.0, error_count=0,
                total_runs=0, successful_runs=0,
                notes="no data",
            )
        stats = _summarize(self._latencies)
        mean_ms = stats["mean"] if stats["mean"] > 0 else 0.001
        return BenchmarkResult(
            scenario_id=self.scenario.scenario_id,
            backend=self.backend.name,
            p50_ms=stats["p50"], p95_ms=stats["p95"], p99_ms=stats["p99"],
            mean_ms=stats["mean"], min_ms=stats["min"], max_ms=stats["max"],
            peak_mem_mb=self._peak_mem_bytes / (1024.0 * 1024.0),
            throughput_qps=1000.0 / mean_ms,
            error_count=self._errors,
            total_runs=len(self._latencies),
            successful_runs=len(self._latencies) - self._errors,
        )

    def _to_result(self) -> BenchmarkResult:
        return self._current_result()

    def _snapshot_or_run(self, other: BenchmarkResult) -> BenchmarkResult:
        return other


# ═════════════════════════════════════════════════════════════════════════════
# 4. 独立对比函数（更清晰）
# ═════════════════════════════════════════════════════════════════════════════

def compare_results(
    scenario_id: str,
    neo4j_result: BenchmarkResult | None,
    networkx_result: BenchmarkResult,
) -> Comparison:
    """对比两个后端的结果。

    Args:
        scenario_id: 场景 ID
        neo4j_result: Neo4j 后端结果（None = SKIP）
        networkx_result: NetworkX 后端结果

    Returns:
        ``Comparison``，含 speedup 比 + 胜出方
    """
    if neo4j_result is None or neo4j_result.backend == "skip":
        return Comparison(
            scenario_id=scenario_id,
            neo4j=None,
            networkx=networkx_result,
            p95_speedup=0.0,
            winner="skip",
        )
    if networkx_result.p95_ms <= 0:
        return Comparison(
            scenario_id=scenario_id,
            neo4j=neo4j_result,
            networkx=networkx_result,
            p95_speedup=0.0,
            winner="tie",
        )
    speedup = neo4j_result.p95_ms / networkx_result.p95_ms
    if speedup < 0.95:
        winner = "neo4j"
    elif speedup > 1.05:
        winner = "networkx"
    else:
        winner = "tie"
    return Comparison(
        scenario_id=scenario_id,
        neo4j=neo4j_result,
        networkx=networkx_result,
        p95_speedup=speedup,
        winner=winner,
    )


# 为了向后兼容保留别名
_make_comparison = compare_results


__all__ = [
    "BenchmarkResult",
    "Comparison",
    "BenchmarkRunner",
    "compare_results",
]
