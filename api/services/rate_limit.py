"""V1.8.0 认证（T01）· 共享 slowapi Limiter 实例。

**为什么必须共享单例**（架构 auth-architecture §1.1 + §3.3）：

- ``api/main.py`` 需要把 ``app.state.limiter`` 挂到应用（slowapi 中间件 /
  异常处理器 / 既有测试 ``main_module.app.state.limiter._storage.reset()``
  都引用它）；
- ``api/routers/auth.py`` 需要 ``@limiter.limit("10/minute")`` 装饰登录端点
  （per-IP 限流）。

如果 main 与 auth router 各自 ``Limiter(...)`` 新建实例，装饰器与中间件
各自维护计数，per-IP 限流会失效（装饰器内检查的 storage 与中间件不一致）。
因此两处必须 import **同一个**模块级实例。

用法::

    from api.services.rate_limit import limiter

    @router.post("/login")
    @limiter.limit(lambda: f"{settings.login_rate_limit_per_minute}/minute")
    async def login(request: Request, ...): ...

作者：寇豆码（工程师）
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

#: 全局唯一 Limiter 实例（key_func = 客户端 IP）
limiter: Limiter = Limiter(key_func=get_remote_address)

__all__ = ["limiter"]
