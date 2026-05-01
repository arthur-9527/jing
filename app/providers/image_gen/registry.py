"""Image Gen Provider 注册表

与 TTS/ASR/LLM Provider registry 模式一致：
- 一次定义：Provider 类定义时注册 NAME
- 配置驱动：根据 IMAGE_GEN_PROVIDER 配置自动创建
- 易扩展：新增 Provider 只需实现类并注册
"""

from typing import Dict, Type

from app.providers.image_gen.base import BaseImageGenProvider


# 注册表
_image_gen_providers: Dict[str, Type[BaseImageGenProvider]] = {}


def register_image_gen_provider(name: str, provider_class: Type[BaseImageGenProvider]) -> None:
    """注册 Image Gen Provider

    Args:
        name: Provider 名称（如 "dashscope"）
        provider_class: Provider 类（继承 BaseImageGenProvider）
    """
    _image_gen_providers[name.lower()] = provider_class


def get_image_gen_provider_class(name: str) -> Type[BaseImageGenProvider]:
    """获取 Image Gen Provider 类

    Args:
        name: Provider 名称

    Returns:
        Provider 类

    Raises:
        ValueError: 未知的 Provider 名称
    """
    if name.lower() not in _image_gen_providers:
        raise ValueError(
            f"Unknown image_gen provider: {name}. "
            f"Available: {list(_image_gen_providers.keys())}"
        )
    return _image_gen_providers[name.lower()]


def create_image_gen_provider(name: str, **kwargs) -> BaseImageGenProvider:
    """创建 Image Gen Provider 实例

    Args:
        name: Provider 名称
        **kwargs: Provider 构造参数

    Returns:
        Provider 实例
    """
    provider_class = get_image_gen_provider_class(name)
    return provider_class(**kwargs)


def list_image_gen_providers() -> list[str]:
    """列出所有已注册的 Image Gen Provider 名称"""
    return list(_image_gen_providers.keys())