"""情绪服务模块

提供物理模拟版的 PAD 情绪系统：
- 状态管理（初始化、更新）
- 动力学（速度、加速度、快速变化检测）
- LLM 接口（动态上下文）
- 记忆接口（情绪事件）
- 持久化接口（Redis 存储）

⭐ 改造：角色级别 + Redis 存储
- Key: emotion:{character_id}
- 不包含 user_id，角色情绪独立
"""

from .models import PADState, PADDynamics, EmotionEvent, EmotionBaseline
from .engine import PADEngine
from .service import EmotionService, get_emotion_service, reset_emotion_service
from .config import EmotionConfig, DEFAULT_EMOTION_CONFIG
from .scheduler import (
    start_emotion_scheduler,
    stop_emotion_scheduler,
    get_emotion_scheduler,
    emotion_decay_tick,
    EMOTION_DECAY_INTERVAL,
    EMOTION_DECAY_STEPS,
)

__all__ = [
    # 数据模型
    "PADState",
    "PADDynamics",
    "EmotionEvent",
    "EmotionBaseline",
    # 引擎
    "PADEngine",
    # 服务
    "EmotionService",
    "get_emotion_service",
    "reset_emotion_service",
    # 配置
    "EmotionConfig",
    "DEFAULT_EMOTION_CONFIG",
    # 定时任务
    "start_emotion_scheduler",
    "stop_emotion_scheduler",
    "get_emotion_scheduler",
    "emotion_decay_tick",
    "EMOTION_DECAY_INTERVAL",
    "EMOTION_DECAY_STEPS",
]
