"""情绪服务配置"""

from dataclasses import dataclass


@dataclass
class EmotionConfig:
    """情绪引擎配置
    
    物理模拟参数：
    - ACCEL_DECAY: 加速度衰减系数（松油门后力快速消失）
    - VELOCITY_DECAY: 速度衰减系数（车子慢慢减速）
    - STATE_DECAY: 状态衰减系数（慢慢回归基线）
    - INPUT_RESPONSE: 输入转化为加速度的效率
    """
    
    # 加速度衰减快（0.7 = 每轮保留70%）
    ACCEL_DECAY: float = 0.7
    
    # 速度衰减慢（0.95 = 每轮保留95%）
    VELOCITY_DECAY: float = 0.95
    
    # 状态衰减最慢（0.98 = 每轮保留98%）
    STATE_DECAY: float = 0.98
    
    # 输入转化为加速度的效率
    INPUT_RESPONSE: float = 0.5
    
    # 快速变化阈值（加速度强度超过此值视为剧烈波动）
    RAPID_CHANGE_THRESHOLD: float = 0.25
    
    # 明显变化阈值
    MODERATE_CHANGE_THRESHOLD: float = 0.1


# 默认配置实例
DEFAULT_EMOTION_CONFIG = EmotionConfig()