"""情绪服务数据模型

⭐ 统一 PADState：从 app.agent.emotion.pad 导入，避免两个同名类接口不兼容问题。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import math

# ⭐ 统一导入 PADState（来自 pad.py，已修复数学错误）
from app.agent.emotion.pad import PADState


@dataclass
class PADDynamics:
    """PAD 情绪动力学
    
    物理模拟：
    - velocity: 速度（情绪变化趋势）
    - acceleration: 加速度（情绪变化剧烈程度）
    """
    # 速度
    velocity_p: float = 0.0
    velocity_a: float = 0.0
    velocity_d: float = 0.0
    
    # 加速度
    accel_p: float = 0.0
    accel_a: float = 0.0
    accel_d: float = 0.0
    
    def intensity(self) -> float:
        """计算加速度强度（L2范数）"""
        return (
            self.accel_p ** 2 + 
            self.accel_a ** 2 + 
            self.accel_d ** 2
        ) ** 0.5
    
    def velocity_intensity(self) -> float:
        """计算速度强度（L2范数）"""
        return (
            self.velocity_p ** 2 + 
            self.velocity_a ** 2 + 
            self.velocity_d ** 2
        ) ** 0.5
    
    def is_rapid_change(self, threshold: float = 0.25) -> bool:
        """是否发生快速变化"""
        return self.intensity() > threshold
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "velocity": {
                "P": round(self.velocity_p, 4),
                "A": round(self.velocity_a, 4),
                "D": round(self.velocity_d, 4),
            },
            "acceleration": {
                "P": round(self.accel_p, 4),
                "A": round(self.accel_a, 4),
                "D": round(self.accel_d, 4),
            },
            "intensity": round(self.intensity(), 4),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PADDynamics":
        """从字典创建"""
        velocity = data.get("velocity", {})
        acceleration = data.get("acceleration", {})
        return cls(
            velocity_p=velocity.get("P", 0.0),
            velocity_a=velocity.get("A", 0.0),
            velocity_d=velocity.get("D", 0.0),
            accel_p=acceleration.get("P", 0.0),
            accel_a=acceleration.get("A", 0.0),
            accel_d=acceleration.get("D", 0.0),
        )


@dataclass
class EmotionEvent:
    """情绪事件
    
    记录一次情绪变化的完整信息，供记忆系统使用。
    """
    # 状态
    state: PADState
    dynamics: PADDynamics
    
    # 变化量
    delta: PADState = field(default_factory=PADState)
    
    # ⭐ 心动事件标记（intensity >= 0.5）
    is_heart_event: bool = False
    intensity: float = 0.0
    
    # 触发信息
    trigger_keywords: list[str] = field(default_factory=list)
    inner_monologue: str = ""
    
    # 时间戳
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_memory_record(self) -> dict:
        """转换为记忆系统可存储格式"""
        return {
            "pad_state": self.state.to_dict(),
            "pad_delta": self.delta.to_dict(),
            "acceleration_intensity": self.dynamics.intensity(),
            "trigger_keywords": self.trigger_keywords,
            "inner_monologue": self.inner_monologue,
            "timestamp": self.timestamp.isoformat(),
        }
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "state": self.state.to_dict(),
            "delta": self.delta.to_dict(),
            "dynamics": self.dynamics.to_dict(),
            "is_heart_event": self.is_heart_event,
            "intensity": round(self.intensity, 4),
            "trigger_keywords": self.trigger_keywords,
            "inner_monologue": self.inner_monologue,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class EmotionBaseline:
    """情绪基线
    
    角色的默认情绪状态。
    """
    p: float = 0.0
    a: float = 0.0
    d: float = 0.0
    
    def to_dict(self) -> dict:
        return {"P": self.p, "A": self.a, "D": self.d}
    
    @classmethod
    def from_dict(cls, data: dict) -> "EmotionBaseline":
        return cls(
            p=data.get("P", 0.0),
            a=data.get("A", 0.0),
            d=data.get("D", 0.0),
        )