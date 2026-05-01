#!/usr/bin/env python3
"""CosyVoice TTS Provider 子包

阿里云 CosyVoice WebSocket API 流式语音合成实现。

模块结构：
- provider.py: CosyVoiceTTSProvider 主类
- voice_enrollment.py: 音色克隆 HTTP API 管理
- pad_emotion.py: PAD 情绪模型到 instruct_text 映射
"""

from app.providers.tts.cosyvoice.provider import (
    CosyVoiceTTSProvider,
    CosyVoiceTTSService,  # 向后兼容别名
)
from app.providers.tts.cosyvoice.pad_emotion import pad_to_emotion_instruction
from app.providers.tts.cosyvoice.voice_enrollment import (
    VoiceEnrollmentService,
    VoiceEnrollmentError,
    get_or_create_voice,
    load_voice_cache,
    save_voice_cache,
    compute_audio_md5,
    generate_prefix_from_audio,
)

__all__ = [
    # Provider
    "CosyVoiceTTSProvider",
    "CosyVoiceTTSService",  # 向后兼容别名
    # 情绪映射
    "pad_to_emotion_instruction",
    # 音色克隆
    "VoiceEnrollmentService",
    "VoiceEnrollmentError",
    "get_or_create_voice",
    "load_voice_cache",
    "save_voice_cache",
    "compute_audio_md5",
    "generate_prefix_from_audio",
]