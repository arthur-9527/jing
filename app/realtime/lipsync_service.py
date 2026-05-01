#!/usr/bin/env python3
"""
口型同步服务 - 基于 TTS 音频频谱分析

原理：
1. TTS 输出音频流
2. 实时分析音频频谱 (FFT)
3. 根据共振峰 (formant) 特征识别元音
4. 映射到日语口型 (あ/い/う/え/お)

优点：时间精确，与实际音频同步
缺点：计算量大，需要实时处理

优化：使用线程池卸载 FFT 运算，避免阻塞事件循环。
"""

import asyncio
import numpy as np
from typing import AsyncGenerator, Callable, Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from loguru import logger
from app.config import settings


@dataclass
class LipMorph:
    """口型 morph 数据"""
    name: str  # あ/い/う/え/お
    weight: float  # 0.0 - 1.0


# ⭐ 专用线程池（FFT 运算卸载）
_fft_executor: ThreadPoolExecutor | None = None


def _get_fft_executor() -> ThreadPoolExecutor:
    """获取专用线程池（延迟创建）"""
    global _fft_executor
    if _fft_executor is None:
        _fft_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="lipsync_fft"
        )
        logger.info("[LipSync] 创建 FFT 专用线程池 (max_workers=2)")
    return _fft_executor


class LipSyncService:
    """口型同步服务 - 基于音频频谱分析"""

    # 频段划分（基于共振峰特征，考虑性别/年龄差异）
    # F1: 200-900Hz - 下巴开合（低频能量）
    # F2: 900-2500Hz - 舌位前后（中频能量，扩展到 2500Hz 覆盖"い"的高 F2）
    # F3: 2500-4500Hz - 嘴唇圆展（高频能量）
    E1_LOW = 200
    E1_HIGH = 900
    E2_LOW = 900
    E2_HIGH = 2500
    E3_LOW = 2500
    E3_HIGH = 4500
    
    # 日语元音共振峰参考值（用于连续权重映射）
    VOWEL_FORMANTS = {
        "a": {"F1": 700, "F2": 1200},   # あ：开口，舌位低
        "i": {"F1": 300, "F2": 2400},   # い：闭口，舌位前，F2 可达 2800Hz
        "u": {"F1": 350, "F2": 1100},   # う：闭口，舌位后
        "e": {"F1": 500, "F2": 1900},   # え：半开，舌位前
        "o": {"F1": 500, "F2": 800},    # お：半开，舌位后
    }

    # 采样率
    SAMPLE_RATE = settings.TTS_SAMPLE_RATE
    FFT_SIZE = 1024

    # 元音邻接关系（基于日语元音梯形图）
    VOWEL_NEIGHBORS = {
        "a": ["e", "o"],
        "i": ["e"],
        "u": ["o"],
        "e": ["a", "i"],
        "o": ["a", "u"],
    }

    def __init__(
        self,
        sensitivity: float = 1.0,
        smoothing_factor: float = 0.55,
        silence_decay: float = 0.7,
        min_volume_threshold: float = 0.01,
        neighbor_weight: float = 0.15,
    ):
        """
        初始化口型同步服务

        Args:
            sensitivity: 灵敏度，控制口型变化的敏感程度
            smoothing_factor: 平滑因子，用于平滑口型变化
            silence_decay: 静音时的快速衰减因子
            min_volume_threshold: 最小音量阈值，低于此值不触发口型
            neighbor_weight: 邻居元音的权重比例（相对于 winner）
        """
        self.sensitivity = sensitivity
        self.smoothing_factor = smoothing_factor
        self.silence_decay = silence_decay
        self.min_volume_threshold = min_volume_threshold
        self.neighbor_weight = neighbor_weight

        # 当前权重（用于平滑）
        self._current_weights = {
            "a": 0.0,  # あ
            "i": 0.0,  # い
            "u": 0.0,  # う
            "e": 0.0,  # え
            "o": 0.0,  # お
        }

        # 窗口函数（汉宁窗）
        self._window = np.hanning(self.FFT_SIZE)

    def _get_rms(self, audio_data: np.ndarray) -> float:
        """
        计算音频的 RMS（均方根）值
        
        Args:
            audio_data: 归一化音频数据 (-1.0 到 1.0)
        
        Returns:
            RMS 值 0.0 - 1.0
        """
        return float(np.sqrt(np.mean(audio_data ** 2)))

    def _get_band_energy(self, fft_data: np.ndarray, low: float, high: float) -> float:
        """
        获取指定频率范围的相对能量（使用 RMS 避免频段宽度偏差）

        Args:
            fft_data: FFT 数据（复数形式）
            low: 最低频率 (Hz)
            high: 最高频率 (Hz)

        Returns:
            相对能量值（RMS）
        """
        bin_size = self.SAMPLE_RATE / self.FFT_SIZE
        start_bin = int(low / bin_size)
        end_bin = int(high / bin_size)

        # 计算幅度谱
        magnitudes = np.abs(fft_data[start_bin:end_bin])
        
        if len(magnitudes) == 0:
            return 0.0

        # 修复：使用 RMS 而非平方和，消除频段宽度偏差
        # E1 宽 600Hz, E3 宽 2000Hz，平方和会导致 E3 自然偏大
        return float(np.sqrt(np.mean(magnitudes ** 2)))

    def _analyze_single_frame(self, audio_np: np.ndarray, frame_idx: int = 0) -> dict[str, float]:
        """
        分析单帧音频数据，识别元音口型（Winner-Take-All 算法）

        Args:
            audio_np: 归一化后的音频数据（float32）
            frame_idx: 帧索引（用于日志）

        Returns:
            各元音的目标权重 dict（归一化后，总和 <= 1.0）
        """
        volume = self._get_rms(audio_np) * self.sensitivity

        target_weights = {"a": 0.0, "i": 0.0, "u": 0.0, "e": 0.0, "o": 0.0}

        if volume > self.min_volume_threshold:
            # 加窗 + FFT
            windowed = audio_np * self._window
            fft_result = np.fft.rfft(windowed)

            # 获取各频段能量（RMS）
            e1 = self._get_band_energy(fft_result, self.E1_LOW, self.E1_HIGH)
            e2 = self._get_band_energy(fft_result, self.E2_LOW, self.E2_HIGH)
            e3 = self._get_band_energy(fft_result, self.E3_LOW, self.E3_HIGH)

            total = e1 + e2 + e3
            if total < 1e-10:
                return target_weights

            r1 = e1 / total  # 低频比例
            r2 = e2 / total  # 中频比例
            r3 = e3 / total  # 高频比例

            # 元音亲和度评分（基于共振峰频段能量比例）
            scores = {
                "a": r1 * 0.6 + r2 * 0.3,
                "i": r3 * 0.5 + r2 * 0.4,
                "u": r1 * 0.4 + (1.0 - r2) * 0.3,
                "e": r2 * 0.5 + r1 * 0.3,
                "o": r1 * 0.5 + (1.0 - r2) * 0.3,
            }

            # Winner-Take-All: 选出分数最高的元音
            winner = max(scores, key=scores.get)
            volume_scale = min(1.0, volume * 1.5)

            target_weights[winner] = volume_scale

            # 邻居元音少量权重（让过渡更自然）
            for nb in self.VOWEL_NEIGHBORS[winner]:
                target_weights[nb] = volume_scale * self.neighbor_weight

            # 归一化保护：确保总和 <= 1.0
            w_total = sum(target_weights.values())
            if w_total > 1.0:
                scale = 1.0 / w_total
                for k in target_weights:
                    target_weights[k] *= scale

        return target_weights

    def _analyze_vowels(self, audio_data: bytes) -> dict[str, float]:
        """
        分析音频数据，识别元音口型（支持滑动窗口分帧）

        ⭐ 此方法在线程池中执行，避免阻塞事件循环。

        Args:
            audio_data: PCM 音频数据（16 位有符号）

        Returns:
            各元音的目标权重 dict
        """
        # 转换为 numpy 数组并归一化
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

        # 修复：滑动窗口分帧处理，避免丢弃数据
        hop_size = self.FFT_SIZE // 2  # 512 samples, 50% 重叠
        frames = []
        
        # 分帧处理
        if len(audio_np) >= self.FFT_SIZE:
            for start in range(0, len(audio_np) - self.FFT_SIZE + 1, hop_size):
                frame = audio_np[start:start + self.FFT_SIZE]
                frames.append(frame)
        else:
            # 数据不足一帧，补零
            padded = np.pad(audio_np, (0, self.FFT_SIZE - len(audio_np)), mode='constant')
            frames = [padded]

        # 分析所有帧，加权平均（后面的帧权重更大，更接近当前时刻）
        all_weights = []
        for i, frame in enumerate(frames):
            weights = self._analyze_single_frame(frame, frame_idx=i)
            all_weights.append(weights)

        # 聚合策略：线性加权平均（越靠后权重越大）
        result = {"a": 0.0, "i": 0.0, "u": 0.0, "e": 0.0, "o": 0.0}
        n = len(all_weights)
        if n > 0:
            weight_sum = 0.0
            for i, weights in enumerate(all_weights):
                w = (i + 1)  # 线性递增权重: 1, 2, 3, ...
                weight_sum += w
                for k in result:
                    result[k] += weights[k] * w
            for k in result:
                result[k] /= weight_sum
        
        return result

    def analyze_frame(self, audio_data: bytes) -> list[LipMorph]:
        """
        分析单帧音频，返回口型数据（同步版本，供线程池调用）

        Args:
            audio_data: PCM 音频数据

        Returns:
            口型 morph 列表
        """
        target_weights = self._analyze_vowels(audio_data)

        # 检查是否静音（所有目标权重接近 0）
        is_silent = all(v < 0.01 for v in target_weights.values())

        name_map = {
            "a": "あ",
            "i": "い",
            "u": "う",
            "e": "え",
            "o": "お",
        }

        morphs = []
        if is_silent:
            # 静音时快速闭嘴
            for vowel in target_weights:
                self._current_weights[vowel] *= (1.0 - self.silence_decay)
                if self._current_weights[vowel] < 0.01:
                    self._current_weights[vowel] = 0.0
                morphs.append(LipMorph(
                    name=name_map[vowel],
                    weight=self._current_weights[vowel],
                ))
        else:
            # 正常平滑插值
            for vowel, target in target_weights.items():
                self._current_weights[vowel] += (target - self._current_weights[vowel]) * self.smoothing_factor
                self._current_weights[vowel] = max(0.0, min(1.0, self._current_weights[vowel]))

            # 平滑后归一化保护
            w_total = sum(self._current_weights.values())
            if w_total > 1.0:
                scale = 1.0 / w_total
                for vowel in self._current_weights:
                    self._current_weights[vowel] *= scale

            for vowel in target_weights:
                morphs.append(LipMorph(
                    name=name_map[vowel],
                    weight=self._current_weights[vowel],
                ))

        return morphs

    def reset(self):
        """重置所有权重到 0"""
        for k in self._current_weights:
            self._current_weights[k] = 0.0


class LipSyncProcessor:
    """
    Pipecat 处理器 - 实时口型同步
    
    作为 pipecat pipeline 中的一个处理器，
    接收 TTS 音频帧，分析口型，推送到 WebSocket
    
    ⭐ 优化：使用线程池卸载 FFT 运算。
    """

    def __init__(
        self,
        ws_manager,
        lip_sync_service: Optional[LipSyncService] = None,
    ):
        """
        初始化口型同步处理器

        Args:
            ws_manager: WebSocket 管理器，用于推送口型帧
            lip_sync_service: 口型同步服务实例
        """
        self.ws_manager = ws_manager
        self.lip_sync_service = lip_sync_service or LipSyncService()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    async def process_audio_frame(self, audio_data: bytes) -> Optional[list[LipMorph]]:
        """
        处理音频帧

        ⭐ 使用线程池卸载 FFT 运算，避免阻塞事件循环。

        Args:
            audio_data: TTS 音频数据

        Returns:
            口型 morph 列表（如果有）
        """
        if not self._enabled or not audio_data:
            return None

        try:
            # ⭐ 将 FFT 运算卸载到线程池
            executor = _get_fft_executor()
            loop = asyncio.get_running_loop()
            morphs = await loop.run_in_executor(
                executor,
                self.lip_sync_service.analyze_frame,
                audio_data
            )
            
            # 推送到所有连接的客户端
            if self.ws_manager:
                await self.ws_manager.broadcast_lip_frame(morphs)
            
            return morphs
        except Exception as e:
            logger.error(f"[LipSync] 分析音频帧失败: {e}")
            return None

    def reset(self):
        """重置状态"""
        self.lip_sync_service.reset()


async def shutdown_lipsync():
    """关闭线程池（应用关闭时调用）"""
    global _fft_executor
    if _fft_executor is not None:
        _fft_executor.shutdown(wait=True)
        _fft_executor = None
        logger.info("[LipSync] FFT 线程池已关闭")