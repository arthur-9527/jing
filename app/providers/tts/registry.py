"""TTS Provider 注册表

与 LLM Provider registry 模式一致：
- 一次定义：Provider 类定义时注册 NAME
- 配置驱动：根据 TTS_PROVIDER 配置自动创建
- 易扩展：新增 Provider 只需实现类并注册
"""

from typing import Dict, Type

from app.providers.tts.base import BaseTTSProvider


# 注册表
_tts_providers: Dict[str, Type[BaseTTSProvider]] = {}


def register_tts_provider(name: str, provider_class: Type[BaseTTSProvider]) -> None:
    """注册 TTS Provider
    
    Args:
        name: Provider 名称（如 "cosyvoice"）
        provider_class: Provider 类（继承 BaseTTSProvider）
    """
    _tts_providers[name.lower()] = provider_class


def get_tts_provider_class(name: str) -> Type[BaseTTSProvider]:
    """获取 TTS Provider 类
    
    Args:
        name: Provider 名称
        
    Returns:
        Provider 类
        
    Raises:
        ValueError: 未知的 Provider 名称
    """
    if name.lower() not in _tts_providers:
        raise ValueError(f"Unknown TTS provider: {name}. Available: {list(_tts_providers.keys())}")
    return _tts_providers[name.lower()]


def create_tts_provider(name: str, **kwargs) -> BaseTTSProvider:
    """创建 TTS Provider 实例
    
    Args:
        name: Provider 名称
        **kwargs: Provider 构造参数
        
    Returns:
        Provider 实例
    """
    provider_class = get_tts_provider_class(name)
    return provider_class(**kwargs)


def list_tts_providers() -> list[str]:
    """列出所有已注册的 TTS Provider 名称"""
    return list(_tts_providers.keys())