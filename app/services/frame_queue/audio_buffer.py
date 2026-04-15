"""
音频缓冲区 - 用于口型同步的音频数据存储

核心思路：
1. TTS 收到音频 chunk 后，推入音频缓冲区
2. FrameQueue 每 33ms 从缓冲区取出一帧所需的音频数据
3. 对该音频数据进行 FFT 分析，生成口型帧
4. 口型帧与音频播放完全同步（使用同一音频源）

优势：
- 每帧口型独立分析，消除"定格顿感"
- 精确时间对应：33ms 音频 → 33ms 口型
- 配置灵活：采样率从 env 读取
"""

import asyncio
from collections import deque
from typing import Optional
from loguru import logger

from app.config import settings


class AudioBuffer:
    """
    音频缓冲区 - 存储 TTS 推送的 PCM 音频数据
    
    设计原则：
    - TTS 生产音频数据（不定大小的 chunk）
    - FrameQueue 消费音频数据（固定 33ms 大小）
    - 使用 deque 存放 chunk，支持跨 chunk 取数据
    """
    
    def __init__(
        self,
        sample_rate: int = None,
        frame_duration_ms: float = None,
        max_size: int = 100,  # 最大 chunk 数（防止内存溢出）
    ):
        """
        Args:
            sample_rate: 音频采样率，默认从配置读取
            frame_duration_ms: 每帧时长（ms），默认 33.33ms (30fps)
            max_size: 最大存储 chunk 数
        """
        self._sample_rate = sample_rate or settings.TTS_SAMPLE_RATE
        self._frame_duration_ms = frame_duration_ms or (1000.0 / settings.FRAME_TARGET_FPS)
        self._bytes_per_sample = 2  # 16-bit PCM
        
        # 计算每帧需要的音频字节数
        # 例如：16000Hz × 33.33ms × 2 bytes ≈ 1066 bytes
        self._frame_bytes = int(
            self._sample_rate * self._frame_duration_ms / 1000.0 * self._bytes_per_sample
        )
        
        # 缓冲区：存储 TTS 推入的 chunk
        self._chunks: deque[bytes] = deque(maxlen=max_size)
        # 当前 chunk 的读取位置
        self._current_pos: int = 0
        # 锁
        self._lock = asyncio.Lock()
        
        # 统计
        self._pushed_chunks = 0
        self._pushed_bytes = 0
        self._popped_frames = 0
        
        logger.info(
            f"[AudioBuffer] 初始化: sample_rate={self._sample_rate}, "
            f"frame_bytes={self._frame_bytes} ({self._frame_duration_ms:.1f}ms), "
            f"max_chunks={max_size}"
        )
    
    def push(self, audio_data: bytes) -> int:
        """
        推入音频数据（同步方法，供 TTS 回调使用）
        
        Args:
            audio_data: PCM 音频数据（16-bit）
            
        Returns:
            推入的字节数
        """
        if not audio_data:
            return 0
        
        self._chunks.append(audio_data)
        self._pushed_chunks += 1
        self._pushed_bytes += len(audio_data)
        
        # 不在此处打印日志，避免高频输出
        return len(audio_data)
    
    async def push_async(self, audio_data: bytes) -> int:
        """
        推入音频数据（异步方法）
        
        Args:
            audio_data: PCM 音频数据
            
        Returns:
            推入的字节数
        """
        async with self._lock:
            return self.push(audio_data)
    
    async def pop_frame_audio(self) -> Optional[bytes]:
        """
        弹出一帧所需的音频数据
        
        从缓冲区中收集 frame_bytes 大小的音频数据。
        如果数据不足，返回 None（静音帧）。
        
        Returns:
            一帧音频数据（约 33ms），或 None
        """
        async with self._lock:
            # 收集足够的数据
            result = bytearray()
            remaining = self._frame_bytes
            
            while remaining > 0 and self._chunks:
                # 获取当前 chunk
                current_chunk = self._chunks[0]
                available = len(current_chunk) - self._current_pos
                
                if available <= remaining:
                    # 当前 chunk 全部取走
                    result.extend(current_chunk[self._current_pos:])
                    remaining -= available
                    self._chunks.popleft()  # 移除已消耗的 chunk
                    self._current_pos = 0
                else:
                    # 当前 chunk 取一部分
                    result.extend(current_chunk[self._current_pos:self._current_pos + remaining])
                    self._current_pos += remaining
                    remaining = 0
            
            # 如果数据不足，返回已收集的部分（静音帧处理）
            if len(result) < self._frame_bytes:
                # 数据不足时，可以：
                # 1. 返回 None（跳过口型帧）
                # 2. 补零返回（保持帧节奏）
                if len(result) == 0:
                    return None
                # 补零到 frame_bytes（保持帧节奏）
                result.extend(b'\x00' * (self._frame_bytes - len(result)))
            
            self._popped_frames += 1
            return bytes(result)
    
    async def clear(self) -> None:
        """清空缓冲区（打断时调用）"""
        async with self._lock:
            self._chunks.clear()
            self._current_pos = 0
            logger.debug("[AudioBuffer] 缓冲区已清空")
    
    @property
    def available_bytes(self) -> int:
        """当前可用的音频字节数"""
        total = 0
        for chunk in self._chunks:
            total += len(chunk)
        # 减去已读取的部分
        if self._chunks:
            total -= self._current_pos
        return total
    
    @property
    def available_frames(self) -> int:
        """当前可生成的口型帧数"""
        return self.available_bytes // self._frame_bytes
    
    @property
    def is_empty(self) -> bool:
        """缓冲区是否为空"""
        return len(self._chunks) == 0
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "pushed_chunks": self._pushed_chunks,
            "pushed_bytes": self._pushed_bytes,
            "popped_frames": self._popped_frames,
            "available_bytes": self.available_bytes,
            "available_frames": self.available_frames,
            "frame_bytes": self._frame_bytes,
        }