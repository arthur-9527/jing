"""app/realtime - 实时语音流模块

封装 ASR → LLM → TTS 实时语音管线，对标 app/channel/ 的 IM 消息管线。

对外暴露：
- RealtimeManager: 生命周期管理器（start/stop）
- router: FastAPI 路由（WebSocket + Agent State API）
"""

from app.realtime.manager import RealtimeManager, get_realtime_manager, reset_realtime_manager
from app.realtime.router import router

__all__ = [
    "RealtimeManager",
    "get_realtime_manager",
    "reset_realtime_manager",
    "router",
]
