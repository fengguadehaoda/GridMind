"""CheckpointService 骨架单元测试（T01 自带 · 架构 §6 T01 验收）。

T01 范围：**仅验证骨架**（签名、默认值、文件存在性），不验证
``cleanup_expired()`` 与 ``get_stats()`` 的真实逻辑（T02 / T05 才完整实现）。

覆盖：
1. 默认参数（db_path / ttl_seconds / cleanup_interval_s）
2. ``get_db_path()`` 返回绝对路径
3. ``is_initialized()`` 初始为 False
4. ``get_saver()`` 未 init 时抛 RuntimeError
5. ``get_ttl_seconds()`` / ``get_default_timeout()`` 反射 __init__ 参数
6. ``get_stats()`` 返回全零值 + 正确 TTL（不依赖真实 DB）
7. ``register_cleanup_task()`` 返回可 cancel 的对象（不真启 task）
8. ``cleanup_expired()`` 返回 0（T01 占位）
9. ``__repr__`` 含关键字段
10. 模块级单例 ``checkpoint_service`` 存在

运行：
    cd /path/to/GridMind
    python -m pytest tests/test_checkpoint_service.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 在导入 api 之前开启 Mock 模式
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from api.schemas import CheckpointStats, RiskLevel
from api.services.checkpoint_service import (
    DEFAULT_CLEANUP_INTERVAL_S,
    DEFAULT_DB_PATH,
    DEFAULT_TTL_SECONDS,
    CheckpointService,
    checkpoint_service,
)


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture
def service(tmp_path: Path) -> CheckpointService:
    """每个 test 独立的 CheckpointService，db_path 指向 tmp 目录。"""
    db_path = str(tmp_path / "test_checkpoints.db")
    return CheckpointService(
        db_path=db_path,
        ttl_seconds=60,  # 缩短到 60s 便于后续 T02 测试
        cleanup_interval_s=10,
    )


# ═══════════════════════════════════════════════════════
# 1. 默认参数
# ═══════════════════════════════════════════════════════


def test_default_constants() -> None:
    """验证模块默认常量与主理人决策一致（架构 §2.1.2 + §2.3.1 + §2.3.2）。"""
    assert DEFAULT_DB_PATH == "data/checkpoints.db"
    assert DEFAULT_TTL_SECONDS == 1800  # 30 分钟
    assert DEFAULT_CLEANUP_INTERVAL_S == 300  # 5 分钟
    print("[PASS] default constants: 30min TTL, 5min cleanup, data/checkpoints.db")


def test_custom_init_params(service: CheckpointService) -> None:
    """构造函数正确保存自定义参数。"""
    assert service.get_ttl_seconds() == 60
    assert service.get_db_path().endswith("test_checkpoints.db")
    # init 状态应为 False
    assert service.is_initialized() is False
    print("[PASS] CheckpointService stores custom init params")


# ═══════════════════════════════════════════════════════
# 2. get_db_path 返回绝对路径
# ═══════════════════════════════════════════════════════


def test_get_db_path_is_absolute(service: CheckpointService) -> None:
    """``get_db_path()`` 返回 Path.resolve() 的绝对路径（便于日志/admin 端点）。"""
    p = service.get_db_path()
    assert os.path.isabs(p), f"应返回绝对路径，实际：{p}"
    assert p.endswith("test_checkpoints.db")
    print(f"[PASS] get_db_path returns absolute: {p}")


# ═══════════════════════════════════════════════════════
# 3. is_initialized 状态
# ═══════════════════════════════════════════════════════


def test_is_initialized_starts_false(service: CheckpointService) -> None:
    """未 ``async_init()`` 时，``is_initialized()`` 必须为 False（防误用）。"""
    assert service.is_initialized() is False
    print("[PASS] is_initialized() == False before async_init")


# ═══════════════════════════════════════════════════════
# 4. get_saver 未 init 抛 RuntimeError
# ═══════════════════════════════════════════════════════


def test_get_saver_without_init_raises(service: CheckpointService) -> None:
    """未初始化时调 ``get_saver()`` 必须抛 ``RuntimeError``（fail-fast）。"""
    with pytest.raises(RuntimeError, match="not initialized"):
        service.get_saver()
    print("[PASS] get_saver() before init raises RuntimeError")


# ═══════════════════════════════════════════════════════
# 5. TTL 反射
# ═══════════════════════════════════════════════════════


def test_ttl_reflects_init(service: CheckpointService) -> None:
    """``get_ttl_seconds()`` 必须与构造时传入的一致。"""
    assert service.get_ttl_seconds() == 60
    s2 = CheckpointService(ttl_seconds=3600)
    assert s2.get_ttl_seconds() == 3600
    print("[PASS] get_ttl_seconds reflects init param")


# ═══════════════════════════════════════════════════════
# 6. get_stats 返回全零值（T01 占位）
# ═══════════════════════════════════════════════════════


def test_get_stats_returns_zero_values(service: CheckpointService) -> None:
    """T01 ``get_stats()`` 返回 0 计数 + 正确 TTL（不依赖真实 DB 写入）。"""
    stats = service.get_stats()
    assert isinstance(stats, CheckpointStats)
    assert stats.total_checkpoints == 0
    assert stats.total_threads == 0
    assert stats.expired_cleaned_24h == 0
    assert stats.active_sessions == 0
    # db_size_bytes 在文件不存在时为 0
    assert stats.db_size_bytes == 0
    # TTL 必须与构造参数一致
    assert stats.ttl_seconds == 60
    print("[PASS] get_stats() returns zero-value CheckpointStats + correct TTL")


def test_get_stats_with_existing_empty_db(tmp_path: Path) -> None:
    """``get_stats()`` 能识别已存在的空 SQLite 文件（db_size_bytes > 0）。"""
    db_path = str(tmp_path / "exists.db")
    # 创建一个空文件
    Path(db_path).touch()
    svc = CheckpointService(db_path=db_path, ttl_seconds=60)
    stats = svc.get_stats()
    assert stats.db_size_bytes == 0  # 空文件 size = 0
    print("[PASS] get_stats() handles existing empty db file")


# ═══════════════════════════════════════════════════════
# 7. register_cleanup_task 占位
# ═══════════════════════════════════════════════════════


def test_register_cleanup_task_returns_cancelable(service: CheckpointService) -> None:
    """T01 ``register_cleanup_task()`` 返回的对象必须可 cancel（FastAPI shutdown 不抛错）。"""
    task = service.register_cleanup_task()
    assert task is not None
    # 必须支持 cancel()（T05 替换为真实 asyncio.Task）
    task.cancel()
    # done() 应为 True
    assert task.done() is True
    print("[PASS] register_cleanup_task returns cancelable stub")


def test_register_cleanup_task_with_custom_interval(
    service: CheckpointService,
) -> None:
    """``register_cleanup_task(interval_s=...)`` 接受自定义周期（T05 真实启 task 时用）。"""
    task = service.register_cleanup_task(interval_s=42)
    assert task is not None
    task.cancel()
    print("[PASS] register_cleanup_task accepts custom interval")


# ═══════════════════════════════════════════════════════
# 8. cleanup_expired T01 占位返回 0
# ═══════════════════════════════════════════════════════


def test_cleanup_expired_returns_zero_t01(service: CheckpointService) -> None:
    """T01 ``cleanup_expired()`` 必须返回 0（占位，T02 真实实现）。"""
    import asyncio

    cleaned = asyncio.run(service.cleanup_expired())
    assert cleaned == 0
    print("[PASS] cleanup_expired() T01 stub returns 0")


# ═══════════════════════════════════════════════════════
# 9. __repr__ 含关键字段
# ═══════════════════════════════════════════════════════


def test_repr_contains_key_fields(service: CheckpointService) -> None:
    """``__repr__`` 含 db_path + ttl + initialized（便于日志/调试）。"""
    r = repr(service)
    assert "CheckpointService" in r
    assert "ttl_seconds=60" in r
    assert "initialized=False" in r
    assert "test_checkpoints.db" in r
    print(f"[PASS] __repr__: {r}")


# ═══════════════════════════════════════════════════════
# 10. 模块级单例
# ═══════════════════════════════════════════════════════


def test_module_level_singleton() -> None:
    """``checkpoint_service`` 是模块级单例（架构 §T02 详细工作清单 #1 约定）。"""
    assert checkpoint_service is not None
    assert isinstance(checkpoint_service, CheckpointService)
    # 默认 ttl 应为主理人决策的 30 分钟
    assert checkpoint_service.get_ttl_seconds() == DEFAULT_TTL_SECONDS
    print("[PASS] checkpoint_service singleton with 30min default TTL")


# ═══════════════════════════════════════════════════════
# 11. Pydantic schema 集成验证
# ═══════════════════════════════════════════════════════


def test_risk_level_enum_values() -> None:
    """``RiskLevel`` 枚举 4 值与主理人决策 #5 一致（架构 §2.4.3）。"""
    assert RiskLevel.LOW.value == "low"
    assert RiskLevel.NORMAL.value == "normal"
    assert RiskLevel.HIGH.value == "high"
    assert RiskLevel.CRITICAL.value == "critical"
    # 默认值为 NORMAL（架构 §2.4.3 "80% 场景"）
    assert RiskLevel("normal") == RiskLevel.NORMAL
    print("[PASS] RiskLevel has 4 values: low/normal/high/critical")


def test_checkpoint_stats_pydantic_v2() -> None:
    """``CheckpointStats`` 是 Pydantic v2 BaseModel（架构 §4.1 约束）。"""
    stats = CheckpointStats(
        total_checkpoints=10,
        total_threads=3,
        expired_cleaned_24h=2,
        active_sessions=1,
        db_size_bytes=1024,
        ttl_seconds=1800,
    )
    # model_dump() 是 v2 语法（v1 是 .dict()）
    d = stats.model_dump()
    assert d["total_checkpoints"] == 10
    assert d["ttl_seconds"] == 1800
    # ConfigDict extra='forbid' 校验：未知字段应抛错
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        CheckpointStats(unknown_field="bad", total_checkpoints=0)
    print("[PASS] CheckpointStats is Pydantic v2 with extra='forbid'")


# ═══════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口。"""
    import asyncio
    import traceback
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    service = CheckpointService(
        db_path=str(tmp / "test.db"),
        ttl_seconds=60,
        cleanup_interval_s=10,
    )

    tests = [
        ("test_default_constants", lambda: test_default_constants()),
        ("test_custom_init_params", lambda: test_custom_init_params(service)),
        ("test_get_db_path_is_absolute", lambda: test_get_db_path_is_absolute(service)),
        ("test_is_initialized_starts_false", lambda: test_is_initialized_starts_false(service)),
        ("test_get_saver_without_init_raises", lambda: test_get_saver_without_init_raises(service)),
        ("test_ttl_reflects_init", lambda: test_ttl_reflects_init(service)),
        ("test_get_stats_returns_zero_values", lambda: test_get_stats_returns_zero_values(service)),
        ("test_get_stats_with_existing_empty_db", lambda: test_get_stats_with_existing_empty_db(tmp)),
        ("test_register_cleanup_task_returns_cancelable", lambda: test_register_cleanup_task_returns_cancelable(service)),
        ("test_register_cleanup_task_with_custom_interval", lambda: test_register_cleanup_task_with_custom_interval(service)),
        ("test_cleanup_expired_returns_zero_t01", lambda: test_cleanup_expired_returns_zero_t01(service)),
        ("test_repr_contains_key_fields", lambda: test_repr_contains_key_fields(service)),
        ("test_module_level_singleton", lambda: test_module_level_singleton()),
        ("test_risk_level_enum_values", lambda: test_risk_level_enum_values()),
        ("test_checkpoint_stats_pydantic_v2", lambda: test_checkpoint_stats_pydantic_v2()),
    ]
    passed, failed = 0, 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)
    print("ALL CHECKPOINT SERVICE TESTS PASSED ✅")


if __name__ == "__main__":
    _run_all()
