"""TTS 服务模块"""

from app.services.tts.cosyvoice_ws import CosyVoiceTTSService, create_cosyvoice_tts_service
from app.services.tts.voice_enrollment import VoiceEnrollmentService, get_or_create_voice, VoiceEnrollmentError

__all__ = [
    "CosyVoiceTTSService",
    "create_cosyvoice_tts_service",
    "VoiceEnrollmentService",
    "get_or_create_voice",
    "VoiceEnrollmentError",
]
