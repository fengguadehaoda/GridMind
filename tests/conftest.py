"""全局测试夹具（V1.8.0 认证引入）· 复位共享 slowapi limiter 状态。

**背景**：V1.8.0 将 slowapi ``Limiter`` 提升为模块级单例
（``api/services/rate_limit.py``，main 的 ``app.state.limiter`` 与 auth router
的 ``@limiter.limit`` 共享同一实例）。基线版本在 ``api/main.py`` 内定义
Limiter，每次 ``importlib.reload(api.main)`` 都会**重建实例**（注册表与计数
自然清零）；单例化后 reload 只**追加**路由限流注册（``_route_limits`` /
``_dynamic_route_limits``），导致跨测试累积：

- 单次请求被检查 N 次 → 存储计数 N 倍递增 → 合法请求被假 429；
- 限流端点每次请求多做 N 次 storage 查询 → 延迟升高（P95 超标）。

**方案**：autouse（函数级）夹具在**每个测试结束后**清空共享 limiter 的
计数与注册表，使下一个测试以「仅自身 fixture reload 产生的 1 条注册」开始
——与基线行为等价。现有涉及限流断言的测试（test_security_patch /
test_backend_integration_e2e / test_admin_endpoints / test_auth_api 等）均使用
函数级、reload 型 client fixture，因此每个测试 setup 都会重新注册其端点限流。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import pytest


def _reset_shared_limiter_state() -> None:
    """清空共享 limiter 的计数 + 路由限流注册（幂等，失败忽略）。"""
    try:
        import api.main as main_mod

        limiter = main_mod.app.state.limiter
        try:
            limiter._storage.reset()
        except AttributeError:
            pass
        limiter._route_limits.clear()
        limiter._dynamic_route_limits.clear()
    except Exception:  # noqa: BLE001 — 复位失败不应让任何测试失败
        pass


@pytest.fixture(autouse=True)
def _reset_shared_limiter_state_fixture() -> None:
    """每个测试结束后复位共享 limiter（防跨测试假 429 / 限流延迟累积）。"""
    yield
    _reset_shared_limiter_state()
