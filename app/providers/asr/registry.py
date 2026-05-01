"""ASR Provider 注册表

与 LLM Provider registry 模式一致：
- 一次定义：Provider 类定义时注册 NAME
- 配置驱动：根据 ASR_PROVIDER 配置自动创建
- 易扩展：新增 Provider 只需实现类并注册
"""

from typing import Dict, Type

from app.providers.asr.base import BaseASRProvider


# Provider 注册表
_asr_providers: Dict[str, Type[BaseASRProvider]] = {}


def register_asr_provider(name: str, provider_class: Type[BaseASRProvider]) -> None:
    """注册 ASR Provider
    
    Args:
        name: Provider 名称（如 "qwen"）
        provider_class: Provider 类（必须继承 BaseASRProvider）
    """
    _asr_providers[name.lower()] = provider_class


def get_asr_provider_class(name: str) -> Type[BaseASRProvider]:
    """获取 ASR Provider 类
    
    Args:
        name: Provider 名称
        
    Returns:
        Provider 类
        
    Raises:
        ValueError: 未知的 Provider 名称
    """
    if name.lower() not in _asr_providers:
        raise ValueError(f"Unknown ASR provider: {name}. Available: {list(_asr_providers.keys())}")
    return _asr_providers[name.lower()]


def create_asr_provider(name: str, **kwargs) -> BaseASRProvider:
    """创建 ASR Provider 实例
    
    Args:
        name: Provider 名称
        **kwargs: Provider 构造参数
        
    Returns:
        Provider 实例
    """
    provider_class = get_asr_provider_class(name)
    return provider_class(**kwargs)


def list_asr_providers() -> list[str]:
    """列出所有已注册的 ASR Provider"""
    return list(_asr_providers.keys())