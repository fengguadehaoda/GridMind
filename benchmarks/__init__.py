"""GridMind 知识图谱 M3b · 性能基准（perf benchmark）包。

设计目标（kg-m3-split.md §4）
--------
- 30+ 复杂场景的 Neo4j vs NetworkX 量化对比（P50/P95/P99、内存、吞吐）
- 不破坏 M0/M1/M2/M3a：基准脚本独立进程运行，不影响 API 主链路
- 输出可重跑：合成数据集（``baseline_data``）→ 报告数字可重现
- 沙箱无 Neo4j 时 Neo4j 列显示 SKIP，**不假装 PASS**
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
