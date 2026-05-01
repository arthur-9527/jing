"""ASR Provider 抽象基类

继承 Pipecat WebsocketSTTService，提供统一接口层。
所有 ASR Provider 必须继承此类。

提供两类接口：
1. 实时接口（继承自 WebsocketSTTService）：用于 Pipecat Pipeline 实时语音识别
2. 非实时接口（transcribe）：用于一次性音频文件识别
"""

from abc import abstractmethod
from typing import Optional

from pipecat.services.stt_service import WebsocketSTTService
from pipecat.services.settings import STTSettings


class BaseASRProvider(WebsocketSTTService):
    """ASR Provider 抽象基类
    
    继承 WebsocketSTTService，直接用于 Pipecat Pipeline。
    子类只需实现提供商特定逻辑。
    
    使用示例：
        provider = QwenASRProvider(api_key="sk-xxx")
        # Provider 本身就是 Pipecat Service，可直接用于 Pipeline
        pipeline = Pipeline([transport.input(), provider, ...])
        
    Attributes:
        NAME: Provider 名称（用于 registry 注册）
    """
    
    NAME: str = "base"
    
    @property
    def vad_enabled(self) -> bool:
        """服务端 VAD 是否启用
        
        子类可覆盖此属性以返回实际 VAD 状态。
        """
        s = self._settings
        if hasattr(s, 'enable_server_vad'):
            return getattr(s, 'enable_server_vad', True)
        return True
    
    # ===== Pipecat 事件处理器（已由基类提供） =====
    # 子类可通过 event_handler 装饰器注册以下事件：
    # - on_connected: WebSocket 连接成功
    # - on_disconnected: WebSocket 断开
    # - on_connection_error: 连接错误
    # - on_speech_started: 检测到语音开始（VAD 模式）
    # - on_speech_stopped: 检测到语音停止（VAD 模式）
    
    def can_generate_metrics(self) -> bool:
        """支持生成处理指标"""
        return True
    
    # ===== 非实时接口 =====
    
    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int = 16000,
        format: str = "pcm",
    ) -> str:
        """非实时语音转文字（HTTP API）
        
        一次性提交音频数据，返回识别的文字文本。
        适用于语音消息、音频文件等非实时场景。
        
        Args:
            audio_data: 音频数据（PCM 或 WAV 格式）
            sample_rate: 采样率，默认 16000
            format: 音频格式，默认 "pcm"
            
        Returns:
            识别的文字文本
            
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("子类必须实现 transcribe 方法")
