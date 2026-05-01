"""ASR Provider 抽象层

支持多种 ASR 后端：
- QwenASRProvider: 千问实时语音识别

使用示例：
    from app.providers.asr import get_asr_provider
    
    # 获取全局 ASR Provider 实例（根据配置自动创建）
    asr = get_asr_provider()
    
    # Provider 本身继承 WebsocketSTTService，可直接用于 Pipecat Pipeline
    pipeline = Pipeline([transport.input(), asr, ...])
"""

from typing import Optional

from app.providers.asr.base import BaseASRProvider
from app.providers.asr.frames import TranscriptionFilteredFrame
from app.providers.asr.qwen import QwenASRSettings
from app.providers.asr.registry import (
    register_asr_provider,
    get_asr_provider_class,
    create_asr_provider,
    list_asr_providers,
)


# 全局实例（懒加载）
_global_asr_provider: Optional[BaseASRProvider] = None


def create_asr_provider_from_config() -> BaseASRProvider:
    """根据配置创建 ASR Provider
    
    从 app.config.settings 读取配置：
    - ASR_PROVIDER: Provider 名称（如 "qwen"）
    - DASHSCOPE_API_KEY: API Key
    - QWEN_ASR_MODEL: 模型名称
    - QWEN_ASR_LANGUAGE: 语言
    - QWEN_ASR_ENABLE_VAD: 是否启用 VAD
    - AUDIO_SAMPLE_RATE: 采样率
    
    Returns:
        ASR Provider 实例
    """
    from app.config import settings
    import os
    
    # 读取 API Key
    api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY not configured")
    
    # 创建 Provider
    return create_asr_provider(
        name=settings.ASR_PROVIDER,
        api_key=api_key,
        model=settings.QWEN_ASR_MODEL,
        sample_rate=settings.AUDIO_SAMPLE_RATE,
        language=settings.QWEN_ASR_LANGUAGE,
        enable_server_vad=settings.QWEN_ASR_ENABLE_VAD,
        vad_threshold=settings.QWEN_ASR_VAD_THRESHOLD,
        vad_silence_duration_ms=settings.QWEN_ASR_VAD_SILENCE_MS,
    )


def get_asr_provider() -> BaseASRProvider:
    """获取全局 ASR Provider 实例（懒加载）
    
    首次调用时根据配置创建实例，后续调用返回同一实例。
    
    Returns:
        ASR Provider 实例（继承 WebsocketSTTService）
    """
    global _global_asr_provider
    
    if _global_asr_provider is None:
        _global_asr_provider = create_asr_provider_from_config()
    
    return _global_asr_provider


def reset_asr_provider() -> None:
    """重置全局 ASR Provider 实例（用于测试）"""
    global _global_asr_provider
    _global_asr_provider = None


# 自动注册 Provider
from app.providers.asr.qwen import QwenASRProvider
register_asr_provider("qwen", QwenASRProvider)

# 向后兼容别名
QwenASRService = QwenASRProvider


__all__ = [
    "BaseASRProvider",
    "TranscriptionFilteredFrame",
    "QwenASRSettings",
    "register_asr_provider",
    "get_asr_provider_class",
    "create_asr_provider",
    "create_asr_provider_from_config",
    "get_asr_provider",
    "reset_asr_provider",
    "list_asr_providers",
    "QwenASRProvider",
    "QwenASRService",  # 向后兼容别名
]
