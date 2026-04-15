"""
口型帧池 - 基于音频时长的口型帧管理

核心思路：
1. TTS 收到音频 chunk 后，根据音频时长计算需要生成的口型帧数
2. 将口型帧推入池子
3. FrameQueue 每 33ms 从池子 pop 一帧口型
4. 若池子为空，则不推送口型（或推送静音口型）

示例：
- 8000 bytes 音频 = ~181ms = 约 6 帧口型 (30fps, 33ms/帧)
"""

import asyncio
from collections import deque
from typing import Optional, List
from loguru import logger

from .types import MorphFrame


# 归零帧常量 - 所有口型 morph 权重设为 0（用于口型归零/闭嘴）
LIP_RESET_MORPHS = [
    MorphFrame(name="あ", weight=0.0),
    MorphFrame(name="い", weight=0.0),
    MorphFrame(name="う", weight=0.0),
    MorphFrame(name="え", weight=0.0),
    MorphFrame(name="お", weight=0.0),
]


class LipFramePool:
    """
    口型帧池 - 存储待发送的口型帧
    
    设计原则：
    - TTS 生产口型帧（根据音频时长计算数量）
    - FrameQueue 消费口型帧（每 33ms 一帧）
    - 池子为空时，不推送口型（让动作继续播放）
    """

    def __init__(
        self,
        sample_rate: int = 22050,
        target_fps: int = 30,
    ):
        """
        Args:
            sample_rate: 音频采样率，默认 22050 (阿里云 TTS)
            target_fps: 目标帧率，默认 30fps
        """
        self._sample_rate = sample_rate
        self._target_fps = target_fps
        self._frame_duration_ms = 1000.0 / target_fps  # 33.33ms
        self._bytes_per_sample = 2  # 16bit PCM = 2 bytes
        
        self._queue: deque[List[MorphFrame]] = deque()
        self._lock = asyncio.Lock()
        
        # 统计
        self._pushed_count = 0
        self._popped_count = 0
        self._dropped_count = 0

    def _calculate_frame_count(self, audio_bytes: int) -> int:
        """
        根据音频字节数计算需要生成的口型帧数
        
        Args:
            audio_bytes: 音频数据字节数
            
        Returns:
            需要生成的口型帧数
        """
        # 计算音频时长
        samples = audio_bytes / self._bytes_per_sample
        duration_ms = (samples / self._sample_rate) * 1000
        
        # 计算帧数（四舍五入）
        frame_count = round(duration_ms / self._frame_duration_ms)
        
        # logger.debug(
        #     f"[LipFramePool] 音频: {audio_bytes} bytes -> "
        #     f"{duration_ms:.1f}ms -> {frame_count} 帧口型"
        # )
        
        return max(1, frame_count)  # 至少 1 帧

    async def push_frames(self, morphs: List[MorphFrame]) -> int:
        """
        根据音频数据长度，计算并推入口型帧
        
        Args:
            morphs: 当前音频分析出的口型数据
            
        Returns:
            实际推入的帧数
        """
        # 计算这个 morph 对应的音频时长需要多少帧
        # 注意：这里需要外部传入音频字节数，暂时用 morphs 数量估算
        # 实际使用时应传入 audio_bytes 参数
        
        async with self._lock:
            # 每个 morph 推入 1 帧（假设外部已经按音频时长计算好了）
            for _ in range(len(morphs) if morphs else 1):
                self._queue.append(morphs.copy() if morphs else [])
                self._pushed_count += 1
            
            return len(morphs) if morphs else 0

    async def push_frames_by_bytes(self, audio_bytes: int, morphs: List[MorphFrame]) -> int:
        """
        根据音频字节数和口型数据，推入对应数量的口型帧
        
        Args:
            audio_bytes: 音频数据字节数
            morphs: 当前音频分析出的口型数据
            
        Returns:
            实际推入的帧数
            
        注意：不再在此处追加归零帧，改为 TTS 结束时统一追加
        """
        frame_count = self._calculate_frame_count(audio_bytes)
        
        if frame_count == 0:
            return 0
            
        async with self._lock:
            for _ in range(frame_count):
                self._queue.append(morphs.copy() if morphs else [])
                self._pushed_count += 1
            
            # logger.debug(
            #     f"[LipFramePool] 推入 {frame_count} 帧口型, "
            #     f"池子当前: {len(self._queue)} 帧"
            # )
            
            return frame_count

    async def pop_frame(self) -> Optional[List[MorphFrame]]:
        """
        弹出一帧口型数据
        
        Returns:
            口型数据，若池子为空则返回 None
        """
        async with self._lock:
            if self._queue:
                morphs = self._queue.popleft()
                self._popped_count += 1
                return morphs
            return None

    async def peek(self) -> Optional[List[MorphFrame]]:
        """
        查看下一帧口型数据（不弹出）
        
        Returns:
            口型数据，若池子为空则返回 None
        """
        async with self._lock:
            if self._queue:
                return self._queue[0]
            return None

    async def push_reset_frame(self) -> None:
        """
        推入一帧归零帧（用于打断/结束时让口型归零）
        
        归零帧包含所有口型 morph 且权重均为 0，前端收到后会闭嘴。
        """
        async with self._lock:
            self._queue.append([MorphFrame(name=m.name, weight=m.weight) for m in LIP_RESET_MORPHS])
            self._pushed_count += 1
            logger.debug("[LipFramePool] 推入归零帧，池子当前: {} 帧".format(len(self._queue)))

    async def clear(self) -> None:
        """清空池子"""
        async with self._lock:
            self._dropped_count += len(self._queue)
            self._queue.clear()
            logger.debug("[LipFramePool] 池子已清空")

    async def clear_and_reset(self) -> None:
        """
        清空池子并推入归零帧（用于打断时立即闭嘴）
        
        清空所有待播放的口型帧，然后推入一帧归零帧让前端立即闭嘴。
        """
        async with self._lock:
            self._dropped_count += len(self._queue)
            self._queue.clear()
            # 推入归零帧
            self._queue.append([MorphFrame(name=m.name, weight=m.weight) for m in LIP_RESET_MORPHS])
            self._pushed_count += 1
            logger.info("[LipFramePool] 清空并推入归零帧，池子当前: {} 帧".format(len(self._queue)))

    @property
    def count(self) -> int:
        """当前池子中的帧数"""
        return len(self._queue)

    @property
    def is_empty(self) -> bool:
        """池子是否为空"""
        return len(self._queue) == 0

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "pushed": self._pushed_count,
            "popped": self._popped_count,
            "dropped": self._dropped_count,
            "current": self.count,
        }
