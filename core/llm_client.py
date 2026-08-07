"""多模型 LLM 客户端抽象。

支持的 Provider:
- DashScope（千问）：使用 dashscope SDK 直连
- DeepSeek：使用 OpenAI 兼容 API（base_url=https://api.deepseek.com/v1）
- Mock（无 Key 时自动降级）

主要 API:
- ``chat_completion(messages, model_id, temperature)`` —— 统一聊天完成
- ``AVAILABLE_MODELS`` —— 前端下拉用的模型清单
- ``get_default_model()`` —— 配置中的默认模型
- ``get_current_model() / set_current_model()`` —— 运行时切换
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.error
import urllib.request
from enum import Enum
from typing import Any

from loguru import logger

# ── DashScope 可用性检测（模块加载时执行一次）──────────────
# dashscope 是可选依赖：生产环境安装后走真实 LLM；未安装时标准模式
# 自动降级到 mock，避免 "ImportError: No module named 'dashscope'"。
DASHSCOPE_AVAILABLE: bool = False
try:
    from dashscope import Generation as _DashGeneration  # noqa: F401
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    logger.warning("dashscope 未安装，标准模式下 LLM 调用将自动降级到 mock")


def is_dashscope_available() -> bool:
    """DashScope SDK 是否可用（未安装时返回 False，用于 mock 降级判定）。"""
    return DASHSCOPE_AVAILABLE


class ModelProvider(str, Enum):
    """模型提供商。"""

    DASHSCOPE = "dashscope"  # 千问
    DEEPSEEK = "deepseek"  # DeepSeek
    MOCK = "mock"  # 模拟


# 每个 provider 的模型清单（前端下拉用）
AVAILABLE_MODELS: list[dict[str, str]] = [
    {
        "id": "qwen-plus",
        "provider": ModelProvider.DASHSCOPE.value,
        "label": "通义千问 Plus",
        "description": "通用场景 · 中文增强",
    },
    {
        "id": "qwen-turbo",
        "provider": ModelProvider.DASHSCOPE.value,
        "label": "通义千问 Turbo",
        "description": "更快 · 成本更低",
    },
    {
        "id": "deepseek-chat",
        "provider": ModelProvider.DEEPSEEK.value,
        "label": "DeepSeek Chat",
        "description": "深度求索 · 推理强",
    },
    {
        "id": "deepseek-coder",
        "provider": ModelProvider.DEEPSEEK.value,
        "label": "DeepSeek Coder",
        "description": "代码专用",
    },
]


# ── 运行时当前模型（线程安全）─────────────────────────
_lock = threading.RLock()
_current_model: str | None = None


def get_current_model() -> str:
    """获取当前运行时模型（进程级全局，影响所有会话；非 per-session）。"""
    with _lock:
        if _current_model is not None:
            return _current_model
    return get_default_model()


def set_current_model(model_id: str) -> None:
    """设置当前运行时模型（仅在 AVAILABLE_MODELS 内）。"""
    global _current_model
    if model_id not in {m["id"] for m in AVAILABLE_MODELS}:
        raise ValueError(f"Unknown model: {model_id}")
    with _lock:
        _current_model = model_id
    logger.info(f"LLM current model switched to: {model_id}")


def get_default_model() -> str:
    """从配置读默认模型。"""
    # 延迟导入避免循环
    try:
        from api.config import settings

        return getattr(settings, "default_model", "qwen-plus") or "qwen-plus"
    except Exception:
        return "qwen-plus"


def get_provider_for_model(model_id: str) -> ModelProvider:
    """根据模型 ID 推断 provider。"""
    for m in AVAILABLE_MODELS:
        if m["id"] == model_id:
            return ModelProvider(m["provider"])
    return ModelProvider.DASHSCOPE  # fallback


def has_key_for(provider: ModelProvider) -> bool:
    """检查 provider 是否有可用 API Key。"""
    if provider == ModelProvider.MOCK:
        return True
    try:
        from api.config import settings

        if provider == ModelProvider.DASHSCOPE:
            key = getattr(settings, "dashscope_api_key", "") or ""
            # T1 修复：dashscope 未安装时视为无真实能力（走 mock 降级），
            # 避免 standard 模式误判有 Key → 真实 LLM 路径 → ImportError
            return (
                DASHSCOPE_AVAILABLE
                and bool(key)
                and key != "sk-placeholder"
            )
        elif provider == ModelProvider.DEEPSEEK:
            key = getattr(settings, "deepseek_api_key", "") or ""
            # P1-3 修复：与 DASHSCOPE 分支一致，排除 sk-placeholder/空值占位符，
            # 避免 standard/无 header 时误判有 Key → Supervisor 走真实 LLM 路由
            # → mock 回退决策不可路由 → /chat 空响应「处理完成」。
            return bool(key) and key != "sk-placeholder"
    except Exception:
        pass
    return False


# ── 主入口 ────────────────────────────────────────────
def chat_completion(
    messages: list[dict[str, str]] | None = None,
    model_id: str | None = None,
    temperature: float = 0.1,
    **kwargs: Any,
) -> tuple[bool, str]:
    """统一聊天完成接口。

    Args:
        messages: OpenAI 风格消息列表 [{"role": "user", "content": "..."}]
        model_id: 模型 ID（默认用当前运行时模型）
        temperature: 温度

    Returns:
        (success, content_or_error)
    """
    if messages is None:
        messages = []
    if model_id is None:
        model_id = get_current_model()

    try:
        from api.config import settings

        mock = getattr(settings, "mock_enabled", False)
    except Exception:
        mock = False

    provider = get_provider_for_model(model_id)

    # Mock 模式
    if mock or not has_key_for(provider):
        logger.debug(f"LLM mock mode (provider={provider}, model={model_id})")
        # 拼接一个合理的 mock 回应
        user_msg = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        return True, f"[Mock {model_id}] 已收到：{user_msg[:80]}"

    try:
        if provider == ModelProvider.DASHSCOPE:
            return _call_dashscope(model_id, messages, temperature, **kwargs)
        elif provider == ModelProvider.DEEPSEEK:
            return _call_deepseek(model_id, messages, temperature, **kwargs)
        else:
            return False, f"Unknown provider: {provider}"
    except Exception as e:
        logger.exception(f"LLM call failed ({provider}/{model_id})")
        return False, f"{type(e).__name__}: {e}"


# ── async 包装（B1：防止同步 LLM 调用阻塞事件循环）────────
async def achat_completion(
    messages: list[dict[str, str]] | None = None,
    model_id: str | None = None,
    temperature: float = 0.1,
    **kwargs: Any,
) -> tuple[bool, str]:
    """async 版 ``chat_completion``——同步 LLM 调用在线程中执行，不阻塞事件循环。

    B1 修复：LangGraph async 节点 / RAG async 链路内直接调用同步
    ``chat_completion``（DashScope SDK / urllib 60s 超时）会冻结整个事件循环
    10-60s。本函数用 ``asyncio.to_thread`` 把同步调用移到工作线程，事件循环
    保持可响应（并发下其他请求 / SSE / 定时器不被拖死）。
    """
    return await asyncio.to_thread(
        chat_completion, messages, model_id, temperature, **kwargs
    )


# ── Provider 实现 ──────────────────────────────────────
def _call_dashscope(
    model_id: str,
    messages: list[dict],
    temperature: float,
    **kwargs: Any,
) -> tuple[bool, str]:
    """调用 DashScope SDK（同步）。"""
    from api.config import settings

    # T1 兜底：未安装 dashscope 时返回友好错误（chat_completion 的
    # has_key_for 已拦截该路径，这里仅作防御）
    if not DASHSCOPE_AVAILABLE:
        return False, "dashscope 未安装，无法调用真实 LLM（已自动降级 mock）"
    from dashscope import Generation

    response = Generation.call(
        model=model_id,
        messages=messages,
        api_key=settings.dashscope_api_key,
        temperature=temperature,
        result_format="message",
    )
    if response.status_code != 200:
        return False, f"DashScope error ({response.status_code}): {response.message}"
    if response.output and response.output.choices:
        choice = response.output.choices[0]
        if choice and hasattr(choice, "message") and hasattr(choice.message, "content"):
            return True, (choice.message.content or "").strip()
    return False, "No content in DashScope response"


def _call_deepseek(
    model_id: str,
    messages: list[dict],
    temperature: float,
    **kwargs: Any,
) -> tuple[bool, str]:
    """调用 DeepSeek（OpenAI 兼容 API）。"""
    from api.config import settings

    url = "https://api.deepseek.com/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "choices" in data and data["choices"]:
                content = data["choices"][0]["message"]["content"]
                return True, (content or "").strip()
            return False, f"No choices in DeepSeek response: {data}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return False, f"DeepSeek HTTP {e.code}: {body[:200]}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"DeepSeek network error: {e}"


__all__ = [
    "ModelProvider",
    "AVAILABLE_MODELS",
    "DASHSCOPE_AVAILABLE",
    "is_dashscope_available",
    "get_current_model",
    "set_current_model",
    "get_default_model",
    "get_provider_for_model",
    "has_key_for",
    "chat_completion",
    "achat_completion",
]