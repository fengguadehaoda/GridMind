"""灰度管理业务服务（GrayscaleAdminService）—— M2 阶段 admin 端点后端。

职责
----
- 权限校验（admin token）
- 切流（set_ratio）
- 手动回滚（trigger_rollback）
- 历史查询

Q10 = A 决策：单一 admin_token（环境变量 ADMIN_TOKEN + X-Admin-Token header）
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.config import settings
from api.services.sync_log_service import get_sync_log_service
from core.grayscale_router import get_grayscale_router


class GrayscaleAdminService:
    """灰度管理业务服务（无状态，所有方法可静态或实例调用）。"""

    @staticmethod
    def verify_admin_token(token: str | None) -> bool:
        """校验 admin token（Q10 = A：环境变量 ADMIN_TOKEN）。"""
        if not token:
            return False
        expected = str(getattr(settings, "admin_token", "") or "")
        if not expected:
            logger.warning("ADMIN_TOKEN 环境变量未配置")
            return False
        # 严格等值比较（避免时序攻击的恒定时间比较）
        return _constant_time_eq(token, expected)

    @staticmethod
    def set_ratio(ratio: int, actor: str = "admin") -> dict[str, Any]:
        """执行切流。"""
        router = get_grayscale_router()
        result = router.set_ratio(ratio, actor=actor)
        return {
            "ok": True,
            "ratio": result["ratio"],
            "state": result["state"],
            "started_at": result["started_at"],
            "actor": actor,
        }

    @staticmethod
    def manual_rollback(reason: str, actor: str = "admin") -> dict[str, Any]:
        """手动回滚。"""
        router = get_grayscale_router()
        result = router.trigger_rollback(reason=reason or "manual")
        return {
            "ok": True,
            "ratio": result["ratio"],
            "state": result["state"],
            "rollback_reason": result.get("rollback_reason"),
            "rollback_count": result.get("rollback_count"),
            "actor": actor,
        }

    @staticmethod
    def get_status() -> dict[str, Any]:
        """获取当前状态。"""
        router = get_grayscale_router()
        return router.get_status()

    @staticmethod
    def get_history(limit: int = 20) -> list[dict[str, Any]]:
        """获取切换历史。"""
        router = get_grayscale_router()
        return router.get_history(limit=limit)

    @staticmethod
    def get_metrics() -> dict[str, Any]:
        """聚合灰度统计指标（端点用）。

        来源：复用 ``GrayscaleRouter.get_status()`` 快照 + sync_log 统计。
        返回字段：
        - ok: bool
        - state / ratio / neo4j_enabled / started_at
        - rollback_count / rollback_reason
        - switch_count: 累计切换次数（含 off→gray10 等）
        - last_switch: 最近一次切换记录（含 actor / timestamp / prev state）
        - monitor: 5 分钟滚动窗口统计（samples / error_rate / p95）
        - sync_log_stats: ChromaSync 状态分布（pending/synced/failed）
        """
        router = get_grayscale_router()
        status = router.get_status()
        history = status.get("history", [])
        last_switch = history[-1] if history else None
        return {
            "ok": True,
            "state": status.get("state"),
            "ratio": status.get("ratio"),
            "neo4j_enabled": status.get("neo4j_enabled"),
            "started_at": status.get("started_at"),
            "rollback_count": status.get("rollback_count"),
            "rollback_reason": status.get("rollback_reason"),
            "switch_count": len(history),
            "last_switch": last_switch,
            "monitor": status.get("monitor", {}),
            "sync_log_stats": GrayscaleAdminService.get_sync_log_stats(),
        }

    @staticmethod
    def get_sync_log_recent(limit: int = 50) -> list[dict[str, Any]]:
        """获取 sync_log 最近记录。"""
        return get_sync_log_service().get_recent(limit=limit)

    @staticmethod
    def get_sync_log_stats() -> dict[str, int]:
        """获取 sync_log 状态统计。"""
        return get_sync_log_service().count_by_status()


def _constant_time_eq(a: str, b: str) -> bool:
    """恒定时间字符串比较（防止时序攻击）。"""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def get_grayscale_admin_service() -> GrayscaleAdminService:
    """获取 GrayscaleAdminService（无状态，直接返回类即可）。"""
    return GrayscaleAdminService()


__all__ = [
    "GrayscaleAdminService",
    "get_grayscale_admin_service",
]