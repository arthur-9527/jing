"""TTS 时间戳处理器。

用途：
1. 在 Cartesia 路径下消费 TTSTextFrame（词级时间戳）触发动作；
2. 在 Cartesia 路径下消费 TTSAudioRawFrame 做本地口型分析；
3. 其余帧原样透传。
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class WordTimestampProcessor(FrameProcessor):
    """监听 TTS 输出中的音频帧与词时间戳帧。"""

    def __init__(self, *, agent_service, **kwargs):
        super().__init__(**kwargs)
        self._agent_service = agent_service

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        try:
            # Cartesia 路径：音频帧用于本地口型分析与起始时间锚定
            if isinstance(frame, TTSAudioRawFrame):
                await self._agent_service.on_tts_audio_frame(frame)

            # Cartesia 路径：词级时间戳（TTSTextFrame）用于动作触发
            if isinstance(frame, TTSTextFrame):
                await self._agent_service.on_tts_text_frame(frame)
        except Exception as e:
            logger.debug(f"[WordTimestampProcessor] 处理帧失败: {e}")

        await self.push_frame(frame, direction)
