"""情绪服务模块

提供物理模拟版的 PAD 情绪系统：
- 状态管理（初始化、更新）
- 动力学（速度、加速度、快速变化检测）
- LLM 接口（动态上下文）
- 记忆接口（情绪事件）
- 持久化接口（保存、加载）
"""

from .models import PADState, PADDynamics, EmotionEvent, EmotionBaseline
from .engine import PADEngine
from .service import EmotionService
from .config import EmotionConfig, DEFAULT_EMOTION_CONFIG

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
    # 配置
    "EmotionConfig",
    "DEFAULT_EMOTION_CONFIG",
]