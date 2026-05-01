"""TTS Provider 抽象层

支持多种 TTS 后端：
- CosyVoiceTTSProvider: 阿里云 CosyVoice WebSocket 流式合成

使用示例：
    from app.providers.tts import get_tts_provider
    
    tts = get_tts_provider()
    # Provider 本身继承 WebsocketTTSService，可直接用于 Pipecat Pipeline
    pipeline = Pipeline([..., tts, ...])
"""

from typing import Optional

from app.providers.tts.base import BaseTTSProvider
from app.providers.tts.registry import (
    register_tts_provider,
    get_tts_provider_class,
    create_tts_provider,
    list_tts_providers,
)


# 全局实例（懒加载）
_global_tts_provider: Optional[BaseTTSProvider] = None


def create_tts_provider_from_config() -> BaseTTSProvider:
    """根据配置创建 TTS Provider
    
    从 app.config.settings 读取配置：
    - TTS_PROVIDER: Provider 类型（如 "cosyvoice"）
    - DASHSCOPE_API_KEY: API Key
    - COSYVOICE_MODEL: 模型名称
    - COSYVOICE_VOICE_ID: 音色 ID（优先使用）
    - COSYVOICE_CLONE_AUDIO: 克隆音色音频路径（自动转换为 voice_id）
    - TTS_SAMPLE_RATE: 输出采样率
    
    音色参数优先级：voice_id > clone_audio
    
    Returns:
        TTS Provider 实例
    """
    from app.config import settings
    import os
    
    # API Key
    api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY not configured")
    
    # 音色配置（优先 voice_id）
    voice_id = settings.COSYVOICE_VOICE_ID if hasattr(settings, 'COSYVOICE_VOICE_ID') else None
    clone_audio = settings.COSYVOICE_CLONE_AUDIO
    
    # 创建 Provider
    return create_tts_provider(
        name=settings.TTS_PROVIDER,
        api_key=api_key,
        model=settings.COSYVOICE_MODEL,
        clone_voice_id=voice_id,           # 优先
        clone_voice_audio_path=clone_audio,  # 兜底
        sample_rate=settings.TTS_SAMPLE_RATE,
    )


def get_tts_provider() -> BaseTTSProvider:
    """获取全局 TTS Provider 实例（懒加载）
    
    首次调用时根据配置创建，后续调用返回同一实例。
    
    Returns:
        TTS Provider 实例（继承 WebsocketTTSService）
    """
    global _global_tts_provider
    
    if _global_tts_provider is None:
        _global_tts_provider = create_tts_provider_from_config()
    
    return _global_tts_provider


def reset_tts_provider() -> None:
    """重置全局 TTS Provider 实例（用于测试）"""
    global _global_tts_provider
    _global_tts_provider = None


# 自动注册
from app.providers.tts.cosyvoice import CosyVoiceTTSProvider
register_tts_provider("cosyvoice", CosyVoiceTTSProvider)


__all__ = [
    "BaseTTSProvider",
    "register_tts_provider",
    "get_tts_provider_class",
    "create_tts_provider",
    "create_tts_provider_from_config",
    "get_tts_provider",
    "reset_tts_provider",
    "list_tts_providers",
    "CosyVoiceTTSProvider",
]