#!/usr/bin/env python3
"""
音频静音过滤器 - 在 STT 之前阻断音频输入

功能：
1. 集成 mute_strategy 的静音状态
2. 静音时丢弃 InputAudioRawFrame，阻止音频进入 ASR
3. 防止无 AEC 麦克风在 TTS 播放时被 ASR 打断

位置：Pipeline 中 transport.input() 和 stt 之间

Pipeline 结构：
音频输入 → [AudioMuteFilter] → STT → StateManager → UserAggregator → LLM → TTS
              ↑ 阻断点（丢弃 InputAudioRawFrame）
"""

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection


class AudioMuteFilter(FrameProcessor):
    """音频静音过滤器 - 在 STT 之前阻断音频
    
    工作原理：
    - 检查全局静音状态（mute_strategy.is_muted()）
    - 静音时丢弃 InputAudioRawFrame，不传递给下游（STT）
    - 解除静音时正常传递音频帧
    
    效果：
    - 阻止音频进入 ASR 服务，节省 API 调用
    - 防止 TTS 播放期间的音频回声被 ASR 识别
    - 配合 DynamicMuteStrategy（Aggregator 层）实现双层静音保护
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_mute_state: bool = False  # 用于日志输出优化
        
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """处理帧
        
        Args:
            frame: 流经 Pipeline 的帧
            direction: 帧流向
        
        逻辑：
        1. InputAudioRawFrame：检查静音状态，静音时丢弃
        2. 其他帧：正常传递
        """
        await super().process_frame(frame, direction)
        
        # ⭐ 只处理 InputAudioRawFrame（麦克风音频）
        if isinstance(frame, InputAudioRawFrame):
            from app.services.mute_strategy import is_muted
            
            muted = is_muted()
            
            # 状态变化时输出日志（避免频繁日志）
            if muted != self._last_mute_state:
                self._last_mute_state = muted
                if muted:
                    logger.info("[AudioMuteFilter] 静音启用，音频帧将被丢弃")
                else:
                    logger.info("[AudioMuteFilter] 静音解除，音频正常传递给 ASR")
            
            if muted:
                # ⭐ 静音时丢弃音频帧，不传递给 STT
                logger.trace(f"[AudioMuteFilter] 丢弃音频帧: {len(frame.audio)} bytes")
                return  # 不调用 push_frame，直接丢弃
            
            # 解除静音，正常传递
            await self.push_frame(frame, direction)
        else:
            # 其他帧正常传递
            await self.push_frame(frame, direction)