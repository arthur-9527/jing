"""TTS Provider 抽象基类

继承 Pipecat WebsocketTTSService，提供统一接口层。
所有 TTS Provider 必须继承此类。

提供两类接口：
1. 实时接口（继承自 WebsocketTTSService）：用于 Pipecat Pipeline 实时语音合成
2. 非实时接口（synthesize）：用于一次性文字转语音
"""

from abc import abstractmethod
from typing import Callable, Awaitable, Optional

from pipecat.services.tts_service import WebsocketTTSService, TextAggregationMode
from pipecat.services.settings import TTSSettings


class BaseTTSProvider(WebsocketTTSService):
    """TTS Provider 抽象基类
    
    继承 WebsocketTTSService，直接用于 Pipecat Pipeline。
    子类只需实现提供商特定逻辑。
    
    使用示例:
        provider = CosyVoiceTTSProvider(api_key="sk-xxx")
        # Provider 本身就是 Pipecat Service，可直接用于 Pipeline
        pipeline = Pipeline([..., tts, ...])
    
    Attributes:
        NAME: Provider 名称（用于 registry）
    """
    
    NAME: str = "base"
    
    # ===== 回调设置方法（子类应实现） =====
    
    def set_on_audio_data(self, callback: Callable[[bytes], int]) -> None:
        """设置音频数据推送回调
        
        Args:
            callback: 回调函数，接收 bytes，返回推入的字节数
        """
        self._on_audio_data = callback
    
    def set_on_word_timestamps(self, callback: Callable) -> None:
        """设置字级时间戳回调"""
        self._on_word_timestamps = callback
    
    def set_on_tts_started(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """设置 TTS 开始回调（用于 StateManager 状态转换）"""
        self._on_tts_started = callback
    
    def set_on_tts_stopped(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """设置 TTS 停止回调（用于 StateManager 状态转换）"""
        self._on_tts_stopped = callback
    
    def set_on_tts_finished(self, callback: Callable) -> None:
        """设置 TTS 完成回调（用于追加闭嘴帧）"""
        self._on_tts_finished = callback
    
    def set_on_lip_morphs(self, callback: Callable) -> None:
        """设置口型数据回调（兼容旧接口）"""
        self._on_lip_morphs = callback
    
    # ===== 情绪控制 =====
    
    def set_emotion_from_pad(self, pad: dict) -> None:
        """根据 PAD 状态设置情绪指令
        
        Args:
            pad: {"P": float, "A": float, "D": float} 范围 -1.0 到 1.0
        """
        pass  # 子类实现
    
    # ===== Pipecat 集成 =====
    
    def can_generate_metrics(self) -> bool:
        """支持生成处理指标"""
        return True
    
    # ===== 非实时接口 =====
    
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        sample_rate: int = 16000,
        format: str = "pcm",
    ) -> bytes:
        """非实时文字转语音（HTTP API）
        
        一次性提交文字，返回合成的音频数据。
        适用于语音消息生成、音频文件合成等非实时场景。
        
        Args:
            text: 要合成的文字
            sample_rate: 输出采样率，默认 16000
            format: 输出格式，默认 "pcm"
            
        Returns:
            音频数据（PCM bytes）
            
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("子类必须实现 synthesize 方法")
