"""V1.5.1 T06 安全补丁：统一异常处理 wrapper（QA R-X3）。

**修复背景**（QA §5.4）：

原 7 个写端点的 ``except Exception as e: raise HTTPException(500, detail=str(e))``
或 ``return ChatResponse(response=f"处理出错: {e!s}")`` 会将完整 ``str(e)``
（含文件路径 / 变量值 / 内部 token / stack 行号）泄漏到客户端响应体。

本模块提供 :func:`safe_endpoint` 装饰器统一处理：

1. ``HTTPException`` → 透传（让 FastAPI 标准化 401/403/404 等语义）
2. ``SessionLockTimeout`` → 503 + 通用 message（已有逻辑，移到这里更一致）
3. 其它 ``Exception`` → loguru 记录完整 traceback + ``HTTPException(500)`` 仅
   返回通用 message，**不**包含 ``str(e)`` 或 stack trace

**使用模式**（FastAPI endpoint decorator）：

::

    from api.services.error_handler import safe_endpoint

    @app.post("/sessions/{thread_id}/pause")
    @safe_endpoint
    async def pause_session(thread_id: str, req: PauseRequest):
        ...

注意：``safe_endpoint`` 必须在 ``@app.post`` **下**面（装饰器栈调用顺序）。
"""

from __future__ import annotations

import traceback
from functools import wraps
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from loguru import logger

from api.services.session_lock import SessionLockTimeout


# ═══════════════════════════════════════════════════════
# 用户侧通用错误响应（架构 §7.6 错误码规范）
# ═══════════════════════════════════════════════════════

#: 用户侧返回的统一错误响应结构（架构 §7.6）
#: 当前 @safe_endpoint 走 HTTPException(500) 路径，FastAPI 自动序列化为
#: ``{"detail": "..."}``；如需 ``{"status", "code", "message"}`` 自定义结构，
#: 可改为 ``JSONResponse(status_code=500, content={...})``
_GENERIC_500_MESSAGE = "Internal server error, please retry later"
_SESSION_BUSY_MESSAGE = "Session is busy, please retry later"


def safe_endpoint(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """统一异常处理装饰器（QA R-X3 修复）。

    行为矩阵：

    +-----------------------+-----------------------------------------------+
    | 异常类型              | 处理                                          |
    +=======================+===============================================+
    | ``HTTPException``     | 透传（保留 status_code / detail / headers）   |
    +-----------------------+-----------------------------------------------+
    | ``SessionLockTimeout``| log warning（含 thread_id / timeout）         |
    |                       | → HTTPException(503, SESSION_BUSY_MESSAGE)    |
    +-----------------------+-----------------------------------------------+
    | 其它 ``Exception``    | log.error 含完整 traceback                     |
    |                       | → HTTPException(500, _GENERIC_500_MESSAGE)    |
    |                       | （**不**包含 str(e)，防信息泄漏）              |
    +-----------------------+-----------------------------------------------+

    Args:
        func: 被装饰的 async endpoint 函数。

    Returns:
        包装后的 async 函数，行为与原函数相同但异常处理被统一接管。
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except HTTPException:
            # FastAPI 标准异常 → 透传（保留 401/403/404/422 等语义）
            raise
        except SessionLockTimeout as e:
            # 已知的可恢复错误 → 503 + 通用 message
            logger.warning(
                "SessionLockTimeout in {}: thread_id={!r}, timeout={}s",
                func.__name__,
                e.thread_id,
                e.timeout,
            )
            raise HTTPException(
                status_code=503,
                detail=_SESSION_BUSY_MESSAGE,
            ) from None
        except Exception as e:
            # 未知异常 → 服务侧记完整 traceback，用户侧仅通用 message
            logger.error(
                "Unhandled exception in {}: {}\n{}",
                func.__name__,
                e,
                traceback.format_exc(),
            )
            raise HTTPException(
                status_code=500,
                detail=_GENERIC_500_MESSAGE,
            ) from None

    return wrapper
