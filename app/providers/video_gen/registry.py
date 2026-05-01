"""Video Gen Provider 注册表

与 TTS/ASR/LLM/ImageGen Provider registry 模式一致：
- 一次定义：Provider 类定义时注册 NAME
- 配置驱动：根据 VIDEO_GEN_PROVIDER 配置自动创建
- 易扩展：新增 Provider 只需实现类并注册
"""

from typing import Dict, Type

from app.providers.video_gen.base import BaseVideoGenProvider


# 注册表
_video_gen_providers: Dict[str, Type[BaseVideoGenProvider]] = {}


def register_video_gen_provider(name: str, provider_class: Type[BaseVideoGenProvider]) -> None:
    """注册 Video Gen Provider

    Args:
        name: Provider 名称（如 "dashscope"）
        provider_class: Provider 类（继承 BaseVideoGenProvider）
    """
    _video_gen_providers[name.lower()] = provider_class


def get_video_gen_provider_class(name: str) -> Type[BaseVideoGenProvider]:
    """获取 Video Gen Provider 类

    Args:
        name: Provider 名称

    Returns:
        Provider 类

    Raises:
        ValueError: 未知的 Provider 名称
    """
    if name.lower() not in _video_gen_providers:
        raise ValueError(
            f"Unknown video_gen provider: {name}. "
            f"Available: {list(_video_gen_providers.keys())}"
        )
    return _video_gen_providers[name.lower()]


def create_video_gen_provider(name: str, **kwargs) -> BaseVideoGenProvider:
    """创建 Video Gen Provider 实例

    Args:
        name: Provider 名称
        **kwargs: Provider 构造参数

    Returns:
        Provider 实例
    """
    provider_class = get_video_gen_provider_class(name)
    return provider_class(**kwargs)


def list_video_gen_providers() -> list[str]:
    """列出所有已注册的 Video Gen Provider 名称"""
    return list(_video_gen_providers.keys())