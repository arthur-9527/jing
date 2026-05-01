"""统一 Provider 层

Provider 架构：
- LLM Provider - 文字对话 + 可选视觉能力（image/video）
- ASR Provider - 语音识别
- TTS Provider - 语音合成
- Image Gen Provider - 图片生成（异步模式：submit + poll）
- Video Gen Provider - 视频生成（异步模式：submit + poll）

视觉能力通过配置 VISION_MODEL_TYPE 启用，LLM Provider 直接支持 analyze_image/analyze_video 方法。
"""

from app.providers.llm.base import (
    BaseLLMProvider,
    VisionCapability,
    VisionResult,
)

# 延迟导入，避免循环依赖和 httpx 未安装问题
_llm_providers = None
_image_gen_providers = None
_video_gen_providers = None


def _get_llm_providers():
    global _llm_providers
    if _llm_providers is None:
        from app.providers.llm import (
            create_llm_provider,
            get_llm_provider,
            reset_llm_provider,
        )
        _llm_providers = {
            "create_llm_provider": create_llm_provider,
            "get_llm_provider": get_llm_provider,
            "reset_llm_provider": reset_llm_provider,
        }
    return _llm_providers


def _get_image_gen_providers():
    global _image_gen_providers
    if _image_gen_providers is None:
        from app.providers.image_gen import (
            BaseImageGenProvider,
            ImageGenItem,
            ImageGenResult,
            ImageGenStatus,
            ImageGenTask,
            DashScopeImageGenProvider,
            create_image_gen_provider,
            get_image_gen_provider,
        )
        _image_gen_providers = {
            "BaseImageGenProvider": BaseImageGenProvider,
            "ImageGenItem": ImageGenItem,
            "ImageGenResult": ImageGenResult,
            "ImageGenStatus": ImageGenStatus,
            "ImageGenTask": ImageGenTask,
            "DashScopeImageGenProvider": DashScopeImageGenProvider,
            "create_image_gen_provider": create_image_gen_provider,
            "get_image_gen_provider": get_image_gen_provider,
        }
    return _image_gen_providers


def _get_video_gen_providers():
    global _video_gen_providers
    if _video_gen_providers is None:
        from app.providers.video_gen import (
            BaseVideoGenProvider,
            VideoGenItem,
            VideoGenResult,
            VideoGenStatus,
            VideoGenTask,
            DashScopeVideoGenProvider,
            create_video_gen_provider,
            get_video_gen_provider,
        )
        _video_gen_providers = {
            "BaseVideoGenProvider": BaseVideoGenProvider,
            "VideoGenItem": VideoGenItem,
            "VideoGenResult": VideoGenResult,
            "VideoGenStatus": VideoGenStatus,
            "VideoGenTask": VideoGenTask,
            "DashScopeVideoGenProvider": DashScopeVideoGenProvider,
            "create_video_gen_provider": create_video_gen_provider,
            "get_video_gen_provider": get_video_gen_provider,
        }
    return _video_gen_providers


def __getattr__(name):
    # 按顺序查找：LLM -> ImageGen -> VideoGen
    providers = _get_llm_providers()
    if name in providers:
        return providers[name]
    
    providers = _get_image_gen_providers()
    if name in providers:
        return providers[name]
    
    providers = _get_video_gen_providers()
    if name in providers:
        return providers[name]
    
    raise AttributeError(f"module 'app.providers' has no attribute '{name}'")


__all__ = [
    # LLM Base
    "BaseLLMProvider",
    "VisionCapability",
    "VisionResult",
    # LLM Providers
    "create_llm_provider",
    "get_llm_provider",
    "reset_llm_provider",
    # Image Gen Base
    "BaseImageGenProvider",
    "ImageGenItem",
    "ImageGenResult",
    "ImageGenStatus",
    "ImageGenTask",
    # Image Gen Providers
    "DashScopeImageGenProvider",
    "create_image_gen_provider",
    "get_image_gen_provider",
    # Video Gen Base
    "BaseVideoGenProvider",
    "VideoGenItem",
    "VideoGenResult",
    "VideoGenStatus",
    "VideoGenTask",
    # Video Gen Providers
    "DashScopeVideoGenProvider",
    "create_video_gen_provider",
    "get_video_gen_provider",
]