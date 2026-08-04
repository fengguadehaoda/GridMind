"""GridMind 知识图谱 M3b · 性能基准主入口（独立运行）。

用法::

    python -m benchmarks.kg_benchmark
    python -m benchmarks.kg_benchmark --runs 50 --warmup 5
    python -m benchmarks.kg_benchmark --output docs/kg-m3b-perf-report.md

设计约束
--------
- **独立进程**：不 fork uvicorn、不改 settings、不写 sync_log
- **沙箱友好**：Neo4j 不可用时显式 SKIP，不假装 PASS
- **零回归**：仅依赖 M0/M1/M2/M3a 已交付模块
- **可重放**：固定合成数据集（seed=42）+ 固定运行次数
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# 允许 `python -m benchmarks.kg_benchmark` 从项目根目录直接运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.scenarios import get_scenarios  # noqa: E402
from benchmarks.runner import BenchmarkRunner, BenchmarkResult  # noqa: E402
from benchmarks.reporter import ReportGenerator  # noqa: E402
from benchmarks.baseline_data import (  # noqa: E402
    build_baseline_graph,
    inject_into_networkx_backend,
    get_dataset_summary,
)


# ═════════════════════════════════════════════════════════════════════════════
# 1. 后端探测
# ═════════════════════════════════════════════════════════════════════════════

def _detect_backends() -> tuple[Any, Any, bool]:
    """探测 NetworkX + Neo4j 后端。

    Returns:
        (networkx_backend, neo4j_backend_or_None, neo4j_available)
    """
    from core.kg_client import NetworkXBackend, KGClient, NEO4J_AVAILABLE
    from api.config import settings

    # 1. NetworkX 后端（始终可用）
    nx_backend = NetworkXBackend()

    # 2. Neo4j 后端（条件性）
    neo4j_backend: Any = None
    neo4j_available = False

    if not settings.neo4j_enabled:
        logger.info("Neo4j 探测：neo4j_enabled=False，跳过连接")
    elif not NEO4J_AVAILABLE:
        logger.warning("Neo4j 驱动未安装（NEO4J_AVAILABLE=False）")
    else:
        try:
            from core.kg_client import Neo4jBackend
            backend = Neo4jBackend(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
            if backend.ping():
                neo4j_backend = backend
                neo4j_available = True
                logger.info("Neo4j 探测：✅ 可用 ({})", settings.neo4j_uri)
            else:
                logger.warning("Neo4j 探测：ping 失败")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j 探测失败：{}", exc)

    return nx_backend, neo4j_backend, neo4j_available


# ═════════════════════════════════════════════════════════════════════════════
# 2. 注入合成数据集
# ═════════════════════════════════════════════════════════════════════════════

def _inject_synthetic_data() -> int:
    """将合成数据集注入到 NetworkX backend（直接构造图，不经过 SQLite）。

    Returns:
        注入的节点数
    """
    from core.kg_client import KGClient

    client = KGClient()
    injected = inject_into_networkx_backend(client, seed=42)
    logger.info(
        "合成数据集已注入：{} 节点 / NetworkX backend",
        injected,
    )
    return injected


# ═════════════════════════════════════════════════════════════════════════════
# 3. 跑单个场景
# ═════════════════════════════════════════════════════════════════════════════

def _run_scenario(
    scenario: Any,
    backend: Any,
    backend_name: str,
    *,
    n_runs: int,
    n_warmup: int,
) -> BenchmarkResult:
    """对单个场景 × 单个后端执行基准。"""
    runner = BenchmarkRunner(backend=backend, scenario=scenario)
    try:
        runner.warmup(n=n_warmup)
    except NotImplementedError:
        return BenchmarkResult(
            scenario_id=scenario.scenario_id,
            backend="skip",
            p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, mean_ms=0.0,
            min_ms=0.0, max_ms=0.0, peak_mem_mb=0.0,
            throughput_qps=0.0, error_count=n_runs,
            total_runs=n_runs, successful_runs=0,
            notes=f"backend {backend_name} 不支持方法 {scenario.method}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("warmup 失败（场景 {}）: {}", scenario.scenario_id, exc)

    return runner.run(n=n_runs)


# ═════════════════════════════════════════════════════════════════════════════
# 4. 主流程
# ═════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GridMind 知识图谱 M3b 性能基准（独立运行）",
    )
    parser.add_argument(
        "--runs", type=int, default=50,
        help="每个场景的运行次数（默认 50）",
    )
    parser.add_argument(
        "--warmup", type=int, default=5,
        help="预热次数（默认 5）",
    )
    parser.add_argument(
        "--output", type=str,
        default="docs/kg-m3b-perf-report.md",
        help="报告输出路径（默认 docs/kg-m3b-perf-report.md）",
    )
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="只跑指定场景 ID（调试用）",
    )
    parser.add_argument(
        "--results-dir", type=str,
        default="benchmarks/results",
        help="报告副本输出目录（默认 benchmarks/results）",
    )
    args = parser.parse_args(argv)

    logger.info("=" * 60)
    logger.info("GridMind 知识图谱 M3b 性能基准")
    logger.info("=" * 60)
    logger.info("运行参数：runs={}, warmup={}", args.runs, args.warmup)

    # 1. 探测后端
    nx_backend, n4j_backend, n4j_available = _detect_backends()
    if not n4j_available:
        logger.warning("⚠️ Neo4j 不可用（沙箱无 Docker）；Neo4j 列将显示 SKIP")

    # 2. 注入合成数据集（500 节点 / 5000 关系）
    _inject_synthetic_data()

    # 3. 加载场景
    scenarios = get_scenarios()
    if args.scenario:
        scenarios = [s for s in scenarios if s.scenario_id == args.scenario]
        if not scenarios:
            logger.error("找不到场景：{}", args.scenario)
            return 1
    logger.info("场景总数：{}", len(scenarios))

    # 4. 跑所有场景
    results: list[BenchmarkResult] = []
    for i, sc in enumerate(scenarios, start=1):
        # NetworkX（始终跑）
        try:
            r_nx = _run_scenario(
                sc, nx_backend, "networkx",
                n_runs=args.runs, n_warmup=args.warmup,
            )
            results.append(r_nx)
        except Exception as exc:  # noqa: BLE001
            logger.error("NetworkX 场景 {} 执行异常：{}", sc.scenario_id, exc)

        # Neo4j（条件性）
        if n4j_available and n4j_backend is not None:
            try:
                r_n4j = _run_scenario(
                    sc, n4j_backend, "neo4j",
                    n_runs=args.runs, n_warmup=args.warmup,
                )
                results.append(r_n4j)
            except Exception as exc:  # noqa: BLE001
                logger.error("Neo4j 场景 {} 执行异常：{}", sc.scenario_id, exc)
        else:
            results.append(BenchmarkResult(
                scenario_id=sc.scenario_id,
                backend="skip",
                p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, mean_ms=0.0,
                min_ms=0.0, max_ms=0.0, peak_mem_mb=0.0,
                throughput_qps=0.0, error_count=args.runs,
                total_runs=args.runs, successful_runs=0,
                notes="Neo4j: SKIP（沙箱无 Docker）",
            ))

        if i % 10 == 0 or i == len(scenarios):
            logger.info("进度：{}/{} 场景完成", i, len(scenarios))

    # 5. 优化建议
    from core.kg_perf_hints import get_optimization_hints
    hints = get_optimization_hints(results, dataset_summary=get_dataset_summary())
    logger.info("优化建议数：{}", len(hints))

    # 6. 生成报告
    report = ReportGenerator(
        neo4j_available=n4j_available,
        environment={
            "runs": str(args.runs),
            "warmup": str(args.warmup),
            "synthetic_dataset": "500 nodes / 5000 relations (seed=42)",
        },
    )
    report.add_results(results)
    report.set_hints(hints)

    primary = report.write_to(args.output, fmt="both")
    logger.info("报告已写入：{}", primary)

    # 副本到 results 目录
    results_dir = Path(args.results_dir)
    if results_dir.exists() or True:
        try:
            primary_name = primary.name
            extra = report.write_to(
                str(results_dir / primary_name), fmt="both",
            )
            logger.info("报告副本：{}", extra)
        except Exception as exc:  # noqa: BLE001
            logger.debug("副本写入失败：{}", exc)

    # 7. 摘要打印
    total = len(scenarios)
    n4j_runs = sum(1 for r in results if r.backend == "neo4j" and r.error_count == 0)
    nx_runs = sum(1 for r in results if r.backend == "networkx" and r.error_count == 0)
    skip = sum(1 for r in results if r.backend == "skip")
    logger.info("=" * 60)
    logger.info("基准完成：")
    logger.info("  - 场景数：{}", total)
    logger.info("  - Neo4j 成功：{} / SKIP：{}", n4j_runs, skip)
    logger.info("  - NetworkX 成功：{}", nx_runs)
    logger.info("  - 优化建议：{} 条", len(hints))
    logger.info("  - 报告路径：{}", primary)
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
