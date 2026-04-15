"""
STT (Speech-to-Text) 服务模块

支持的 ASR Provider：
- Deepgram: DeepgramSTTService（pipecat 内置）
- Qwen: QwenASRService（千问实时语音识别）
"""

from app.services.stt.qwen_asr import QwenASRService

__all__ = ["QwenASRService"]