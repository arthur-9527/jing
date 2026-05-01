"""LLM Provider 抽象层

支持多种 LLM 后端：
- LiteLLMProvider: 使用 httpx 调用 OpenAI 兼容 API
- CerebrasProvider: 使用 Cerebras SDK 直连官方 API
"""

from typing import Optional

from app.providers.llm.base import BaseLLMProvider

# 全局实例（懒加载）
_global_llm_provider: Optional[BaseLLMProvider] = None


def create_llm_provider() -> BaseLLMProvider:
    """根据配置创建 LLM Provider"""
    from app.config import settings

    provider_type = settings.CHAT_PROVIDER.lower()

    if provider_type == 'cerebras':
        from app.providers.llm.cerebras import CerebrasProvider
        return CerebrasProvider()
    else:
        # 默认使用 LiteLLM Provider
        from app.providers.llm.litellm import LiteLLMProvider
        return LiteLLMProvider()


def get_llm_provider() -> BaseLLMProvider:
    """获取全局 LLM Provider 实例（懒加载）"""
    global _global_llm_provider

    if _global_llm_provider is None:
        _global_llm_provider = create_llm_provider()

    return _global_llm_provider


def reset_llm_provider() -> None:
    """重置全局 LLM Provider 实例（用于测试）"""
    global _global_llm_provider
    _global_llm_provider = None


__all__ = [
    "BaseLLMProvider",
    "create_llm_provider",
    "get_llm_provider",
    "reset_llm_provider",
]
