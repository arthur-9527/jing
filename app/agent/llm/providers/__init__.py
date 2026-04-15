"""LLM Provider 抽象层

支持多种 LLM 后端：
- LiteLLMProvider: 使用 httpx 调用 OpenAI 兼容 API
- CerebrasProvider: 使用 Cerebras SDK 直连官方 API
"""

from app.agent.llm.providers.base import BaseLLMProvider
from app.agent.llm.providers.cerebras import CerebrasProvider
from app.agent.llm.providers.litellm import LiteLLMProvider
from app.config import settings


def create_llm_provider() -> BaseLLMProvider:
    """根据配置创建 LLM Provider"""
    provider_type = getattr(settings, 'LLM_PROVIDER', 'litellm').lower()
    
    if provider_type == 'cerebras':
        return CerebrasProvider()
    else:
        # 默认使用 LiteLLM Provider（向后兼容）
        return LiteLLMProvider()


__all__ = [
    "BaseLLMProvider",
    "CerebrasProvider", 
    "LiteLLMProvider",
    "create_llm_provider",
]
