"""Image Gen Provider 抽象层

支持多种图片生成后端（仅支持图生图模式）：
- DashScopeImageGenProvider: 阿里云通义万相

使用示例：
    from app.providers.image_gen import get_image_gen_provider
    
    provider = get_image_gen_provider()
    
    # 参考图（必选，支持 URL / base64 string / bytes）
    reference_image = "https://example.com/cat.jpg"
    # 或 base64: reference_image = "data:image/jpeg;base64,<data>"
    # 或 bytes: reference_image = open("cat.jpg", "rb").read()
    
    # 提交任务（图生图模式）
    task = await provider.submit("水彩风格的猫咪", reference_image)
    
    # 查询状态
    task = await provider.poll(task.task_id)
    
    # 或直接等待结果
    result = await provider.wait_and_get_result("水彩风格的猫咪", reference_image)
"""

from typing import Optional
import os

from app.providers.image_gen.base import (
    BaseImageGenProvider,
    ImageGenItem,
    ImageGenResult,
    ImageGenStatus,
    ImageGenTask,
)
from app.providers.image_gen.registry import (
    register_image_gen_provider,
    get_image_gen_provider_class,
    create_image_gen_provider,
    list_image_gen_providers,
)


# 全局实例（懒加载）
_global_image_gen_provider: Optional[BaseImageGenProvider] = None


def create_image_gen_provider_from_config() -> BaseImageGenProvider:
    """根据配置创建 Image Gen Provider
    
    从 app.config.settings 读取配置：
    - IMAGE_GEN_PROVIDER: Provider 类型（如 "dashscope"）
    - DASHSCOPE_IMAGE_GEN_MODEL: 模型名称
    - DASHSCOPE_IMAGE_GEN_DEFAULT_SIZE: 默认图片尺寸
    - DASHSCOPE_IMAGE_GEN_DEFAULT_NUM: 默认生成数量
    
    注意：API Key 从 DASHSCOPE_API_KEY 获取（复用已有配置）
    
    Returns:
        Image Gen Provider 实例
    """
    from app.config import settings
    import os
    
    # API Key（复用 DashScope 配置）
    api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY not configured")
    
    # Provider 配置
    provider_name = settings.IMAGE_GEN_PROVIDER
    model = settings.DASHSCOPE_IMAGE_GEN_MODEL
    default_size = settings.DASHSCOPE_IMAGE_GEN_DEFAULT_SIZE
    default_num = settings.DASHSCOPE_IMAGE_GEN_DEFAULT_NUM
    
    # 通过 registry 创建（不硬编码服务商）
    return create_image_gen_provider(
        name=provider_name,
        api_key=api_key,
        model=model,
        default_size=default_size,
        default_num=default_num,
    )


def get_image_gen_provider() -> BaseImageGenProvider:
    """获取全局 Image Gen Provider 实例（懒加载）
    
    首次调用时根据配置创建，后续调用返回同一实例。
    
    Returns:
        Image Gen Provider 实例
    """
    global _global_image_gen_provider
    
    if _global_image_gen_provider is None:
        _global_image_gen_provider = create_image_gen_provider_from_config()
    
    return _global_image_gen_provider


def reset_image_gen_provider() -> None:
    """重置全局 Image Gen Provider 实例（用于测试）"""
    global _global_image_gen_provider
    _global_image_gen_provider = None


def is_image_video_gen_enabled() -> bool:
    """检查图片/视频生成功能是否启用
    
    Returns:
        bool: True 表示启用，False 表示禁用
    """
    from app.config import settings
    return settings.IMAGE_VIDEO_GEN_ENABLED


def get_reference_image_path() -> Optional[str]:
    """获取参考图片路径
    
    Returns:
        Optional[str]: 参考图片路径，未配置时返回 None
    """
    from app.config import settings
    return settings.IMAGE_VIDEO_GEN_REFERENCE_IMAGE_PATH


def load_reference_image() -> Optional[bytes]:
    """加载参考图片（用于图生图/图生视频）
    
    从配置路径读取图片文件，返回 bytes 格式。
    
    Returns:
        Optional[bytes]: 图片 bytes，未配置或文件不存在时返回 None
    
    Raises:
        FileNotFoundError: 配置了路径但文件不存在
    """
    from app.config import settings
    
    path = settings.IMAGE_VIDEO_GEN_REFERENCE_IMAGE_PATH
    if not path:
        return None
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"Reference image not found: {path}")
    
    with open(path, "rb") as f:
        return f.read()


# 自动注册（导入即注册）
from app.providers.image_gen.dashscope import DashScopeImageGenProvider
register_image_gen_provider("dashscope", DashScopeImageGenProvider)


__all__ = [
    # Base
    "BaseImageGenProvider",
    "ImageGenItem",
    "ImageGenResult",
    "ImageGenStatus",
    "ImageGenTask",
    # Registry
    "register_image_gen_provider",
    "get_image_gen_provider_class",
    "create_image_gen_provider",
    "create_image_gen_provider_from_config",
    "get_image_gen_provider",
    "reset_image_gen_provider",
    "list_image_gen_providers",
    # Providers
    "DashScopeImageGenProvider",
    # 辅助函数
    "is_image_video_gen_enabled",
    "get_reference_image_path",
    "load_reference_image",
]
