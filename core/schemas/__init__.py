"""GridMind 核心层数据模型（领域模型）。

与 ``api/schemas/`` 平级：
- ``api/schemas/``  — HTTP 协议层（ChatRequest / ChatResponse / HITL 编辑等）
- ``core/schemas/`` — 领域模型层（DiagnosisOutput / MechanicalCheckResult 等）

本包不导出任何运行时对象，仅作为命名空间。子模块 ``diagnosis`` 才是真正的实现。
"""

from __future__ import annotations

__all__: list[str] = []
