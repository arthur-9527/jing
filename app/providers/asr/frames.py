"""ASR Provider 自定义帧

定义 ASR 相关的自定义 Pipecat Frame 类型。
"""

from pipecat.frames.frames import Frame


class TranscriptionFilteredFrame(Frame):
    """转录被过滤事件帧
    
    当 ASR 检测到无效转录（如单字语气词"嗯"、"啊"）时广播此帧。
    StateManager 收到此帧后应从 THINKING 状态恢复到 IDLE。
    
    Attributes:
        text: 被过滤的转录文本
        reason: 过滤原因（如 "filtered", "empty", "invalid"）
    """
    
    def __init__(self, text: str = "", reason: str = "filtered"):
        super().__init__()
        self.text = text
        self.reason = reason