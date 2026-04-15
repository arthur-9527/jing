"""PAD 情绪引擎（物理模拟版）

物理模型：
- 输入 = 油门/刹车力度
- 加速度 = 驱动力，快速衰减
- 速度 = 情绪变化率，慢速衰减
- 状态 = 情绪值，向基线回归

特性：
- 指数增长：连续输入累积加速度 → 速度 → 状态
- 独立衰减：加速度衰减快、速度衰减慢、状态回归基线
- PAD 三维独立：每个维度都有独立的状态、速度、加速度
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import PADState, PADDynamics, EmotionBaseline, EmotionEvent
from .config import EmotionConfig, DEFAULT_EMOTION_CONFIG

logger = logging.getLogger(__name__)


class PADEngine:
    """PAD 情绪引擎（物理模拟版）
    
    物理模拟类比：
    - PAD 情绪值 = 车的位置（当前情绪状态）
    - PAD 速度   = 车的速度（情绪变化趋势）
    - PAD 加速度 = 油门/刹车力度（输入驱动力）
    
    物理规律：
    - 有输入时：加速度增加 → 速度累积 → 情绪指数级变化
    - 无输入时：加速度快速衰减 → 速度慢慢衰减 → 情绪慢慢回归基线
    """
    
    def __init__(
        self,
        baseline: dict | EmotionBaseline,
        config: EmotionConfig = None,
    ):
        """初始化引擎
        
        Args:
            baseline: 情绪基线（角色的默认情绪状态）
            config: 引擎配置
        """
        if isinstance(baseline, dict):
            baseline = EmotionBaseline.from_dict(baseline)
        
        self._baseline = baseline
        self._config = config or DEFAULT_EMOTION_CONFIG
        
        # 情绪状态（位置）
        self._state = PADState(p=baseline.p, a=baseline.a, d=baseline.d)
        
        # 速度
        self._velocity = PADState()
        
        # 加速度
        self._acceleration = PADState()
        
        logger.info(
            "PADEngine 初始化完成，基线: P=%.2f, A=%.2f, D=%.2f",
            baseline.p, baseline.a, baseline.d
        )
    
    def update(self, delta: dict) -> EmotionEvent:
        """应用输入并更新状态
        
        完整更新流程：
        1. 应用输入（踩油门/刹车）
        2. 加速度累积到速度
        3. 速度累积到状态
        4. 钳制状态在 [-1, 1]
        5. 加速度快速衰减
        6. 速度慢慢衰减
        7. 状态向基线回归
        
        Args:
            delta: 情绪变化输入 {"P": float, "A": float, "D": float}
            
        Returns:
            EmotionEvent: 情绪事件（包含状态、动力学、变化量）
        """
        # 记录变化量
        delta_state = PADState(
            p=delta.get("P", 0.0),
            a=delta.get("A", 0.0),
            d=delta.get("D", 0.0),
        )
        
        # 1. 应用输入（踩油门/刹车）
        self._apply_input(delta_state)
        
        # 2-7. 物理模拟一步
        self._tick()
        
        # 构建动力学状态
        dynamics = self._build_dynamics()
        
        # 构建事件
        event = EmotionEvent(
            state=PADState(p=self._state.p, a=self._state.a, d=self._state.d),
            dynamics=dynamics,
            delta=delta_state,
        )
        
        logger.debug(
            "PAD 更新: state=(%.3f, %.3f, %.3f) delta=(%.3f, %.3f, %.3f) accel=%.3f",
            self._state.p, self._state.a, self._state.d,
            delta_state.p, delta_state.a, delta_state.d,
            dynamics.intensity(),
        )
        
        return event
    
    def _apply_input(self, delta: PADState) -> None:
        """应用输入（踩油门/刹车）
        
        输入转化为加速度：
        - 正值 = 油门（加速正向）
        - 负值 = 刹车（减速或反向）
        """
        response = self._config.INPUT_RESPONSE
        
        self._acceleration.p += delta.p * response
        self._acceleration.a += delta.a * response
        self._acceleration.d += delta.d * response
    
    def _tick(self) -> None:
        """物理模拟一步
        
        顺序：加速度 → 速度 → 状态 → 衰减
        """
        cfg = self._config
        
        # 1. 加速度累积到速度
        self._velocity.p += self._acceleration.p
        self._velocity.a += self._acceleration.a
        self._velocity.d += self._acceleration.d
        
        # 2. 速度累积到状态
        self._state.p += self._velocity.p
        self._state.a += self._velocity.a
        self._state.d += self._velocity.d
        
        # 3. 钳制状态在 [-1, 1]
        self._state = self._state.clamp()
        
        # ⭐ 修复边界粘滞：当状态在边界时，钳制velocity避免粘滞
        # 如果状态在边界且velocity继续指向边界外，清零velocity
        if abs(self._state.p) >= 0.99:
            if (self._state.p > 0 and self._velocity.p > 0) or (self._state.p < 0 and self._velocity.p < 0):
                self._velocity.p = 0.0
        if abs(self._state.a) >= 0.99:
            if (self._state.a > 0 and self._velocity.a > 0) or (self._state.a < 0 and self._velocity.a < 0):
                self._velocity.a = 0.0
        if abs(self._state.d) >= 0.99:
            if (self._state.d > 0 and self._velocity.d > 0) or (self._state.d < 0 and self._velocity.d < 0):
                self._velocity.d = 0.0
        
        # 4. 加速度快速衰减（松油门）
        self._acceleration.p *= cfg.ACCEL_DECAY
        self._acceleration.a *= cfg.ACCEL_DECAY
        self._acceleration.d *= cfg.ACCEL_DECAY
        
        # 5. 速度慢慢衰减（车子减速）
        self._velocity.p *= cfg.VELOCITY_DECAY
        self._velocity.a *= cfg.VELOCITY_DECAY
        self._velocity.d *= cfg.VELOCITY_DECAY
        
        # 6. 状态向基线回归（慢慢停回基线位置）
        drift_p = self._baseline.p - self._state.p
        drift_a = self._baseline.a - self._state.a
        drift_d = self._baseline.d - self._state.d
        
        decay_rate = 1 - cfg.STATE_DECAY
        self._state.p += drift_p * decay_rate
        self._state.a += drift_a * decay_rate
        self._state.d += drift_d * decay_rate
    
    def _build_dynamics(self) -> PADDynamics:
        """构建动力学状态"""
        return PADDynamics(
            velocity_p=self._velocity.p,
            velocity_a=self._velocity.a,
            velocity_d=self._velocity.d,
            accel_p=self._acceleration.p,
            accel_a=self._acceleration.a,
            accel_d=self._acceleration.d,
        )
    
    def get_state(self) -> PADState:
        """获取当前状态"""
        return PADState(p=self._state.p, a=self._state.a, d=self._state.d)
    
    def get_dynamics(self) -> PADDynamics:
        """获取当前动力学状态"""
        return self._build_dynamics()
    
    def set_state(self, state: dict | PADState) -> None:
        """设置状态（用于从数据库恢复）"""
        if isinstance(state, dict):
            state = PADState.from_dict(state, self._baseline.to_dict())
        self._state = state.clamp()
    
    def set_velocity(self, velocity: dict | PADState) -> None:
        """设置速度（用于从数据库恢复）"""
        if isinstance(velocity, dict):
            velocity = PADState.from_dict(velocity, {"P": 0.0, "A": 0.0, "D": 0.0})
        self._velocity = velocity
    
    def set_acceleration(self, acceleration: dict | PADState) -> None:
        """设置加速度（用于从数据库恢复）"""
        if isinstance(acceleration, dict):
            acceleration = PADState.from_dict(acceleration, {"P": 0.0, "A": 0.0, "D": 0.0})
        self._acceleration = acceleration
    
    def get_baseline(self) -> EmotionBaseline:
        """获取基线"""
        return self._baseline
    
    def reset(self) -> None:
        """重置到基线状态"""
        self._state = PADState(p=self._baseline.p, a=self._baseline.a, d=self._baseline.d)
        self._velocity = PADState()
        self._acceleration = PADState()
        logger.info("PADEngine 已重置到基线状态")
    
    def get_full_state(self) -> dict:
        """获取完整状态（用于持久化）"""
        return {
            "state": self._state.to_dict(),
            "velocity": self._velocity.to_dict(),
            "acceleration": self._acceleration.to_dict(),
            "baseline": self._baseline.to_dict(),
        }
    
    def restore_full_state(self, data: dict) -> None:
        """恢复完整状态（从数据库加载）"""
        baseline_dict = self._baseline.to_dict()
        if "state" in data:
            self._state = PADState.from_dict(data["state"], baseline_dict)
        if "velocity" in data:
            self._velocity = PADState.from_dict(data["velocity"], {"P": 0.0, "A": 0.0, "D": 0.0})
        if "acceleration" in data:
            self._acceleration = PADState.from_dict(data["acceleration"], {"P": 0.0, "A": 0.0, "D": 0.0})
        logger.info(
            "PADEngine 状态已恢复: state=(%.3f, %.3f, %.3f)",
            self._state.p, self._state.a, self._state.d
        )
    
    def __repr__(self) -> str:
        return (
            f"PADEngine(state=({self._state.p:.3f}, {self._state.a:.3f}, {self._state.d:.3f}), "
            f"vel=({self._velocity.p:.3f}, {self._velocity.a:.3f}, {self._velocity.d:.3f}), "
            f"accel=({self._acceleration.p:.3f}, {self._acceleration.a:.3f}, {self._acceleration.d:.3f}))"
        )