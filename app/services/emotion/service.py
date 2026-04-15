"""情绪服务统一入口

EmotionService 是情绪系统的对外统一接口，提供：
- 状态管理（初始化、更新）
- 动力学（速度、加速度）
- LLM 接口（动态上下文）
- 记忆接口（情绪事件）
- 持久化（保存、加载）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Callable, Any

from .models import PADState, PADDynamics, EmotionEvent, EmotionBaseline
from .engine import PADEngine
from .config import EmotionConfig, DEFAULT_EMOTION_CONFIG

logger = logging.getLogger(__name__)


class EmotionService:
    """情绪服务统一入口
    
    功能列表：
    ├─ 状态管理（初始化、更新）
    ├─ 动力学（速度、加速度、快速变化检测）
    ├─ LLM 接口（动态上下文）
    ├─ 记忆接口（情绪事件）
    └─ 持久化接口（保存、加载）
    """
    
    def __init__(
        self,
        baseline: dict | EmotionBaseline,
        config: EmotionConfig = None,
    ):
        """初始化情绪服务
        
        Args:
            baseline: 情绪基线（角色的默认情绪状态）
            config: 引擎配置
        """
        self._engine = PADEngine(baseline, config)
        self._config = config or DEFAULT_EMOTION_CONFIG
        
        # 事件记录
        self._last_event: Optional[EmotionEvent] = None
        self._event_history: list[EmotionEvent] = []
        self._max_history: int = 20
        
        # 回调
        self._rapid_change_callbacks: list[Callable[[EmotionEvent], None]] = []
        
        logger.info("EmotionService 初始化完成")
    
    # === 核心操作 ===
    
    def update(
        self,
        delta: dict,
        trigger_keywords: list[str] = None,
        inner_monologue: str = "",
    ) -> EmotionEvent:
        """更新情绪状态
        
        Args:
            delta: 情绪变化 {"P": float, "A": float, "D": float}
            trigger_keywords: 触发关键词
            inner_monologue: 内心独白
            
        Returns:
            EmotionEvent: 情绪事件
        """
        # 调用引擎更新
        event = self._engine.update(delta)
        
        # 补充触发信息
        event.trigger_keywords = trigger_keywords or []
        event.inner_monologue = inner_monologue
        
        # 检测快速变化
        if event.dynamics.is_rapid_change(self._config.RAPID_CHANGE_THRESHOLD):
            logger.info(
                "检测到快速情绪变化，加速度强度: %.3f",
                event.dynamics.intensity()
            )
            # 触发回调
            for callback in self._rapid_change_callbacks:
                try:
                    callback(event)
                except Exception as e:
                    logger.warning("快速变化回调执行失败: %s", e)
        
        # 记录事件
        self._last_event = event
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
        
        return event
    
    def reset(self) -> None:
        """重置到基线状态"""
        self._engine.reset()
        self._last_event = None
        self._event_history.clear()
        logger.info("EmotionService 已重置")
    
    # === 状态查询 ===
    
    def get_state(self) -> PADState:
        """获取当前 PAD 状态"""
        return self._engine.get_state()
    
    def get_dynamics(self) -> PADDynamics:
        """获取当前动力学状态"""
        return self._engine.get_dynamics()
    
    def get_baseline(self) -> EmotionBaseline:
        """获取情绪基线"""
        return self._engine.get_baseline()
    
    # === LLM 接口 ===
    
    def get_dynamic_context(self) -> str:
        """获取动态上下文（供 System Prompt）
        
        极简设计：
        - 只提供 PAD 数值和变化趋势
        - LLM 根据角色性格描述自主生成台词
        """
        state = self.get_state()
        dynamics = self.get_dynamics()
        
        # 动力学趋势描述（简洁）
        accel_intensity = dynamics.intensity()
        trend = ""
        if accel_intensity > self._config.RAPID_CHANGE_THRESHOLD:
            trend = "（剧烈波动）"
        elif accel_intensity > self._config.MODERATE_CHANGE_THRESHOLD:
            trend = "（有明显变化）"
        
        return f"""## 当前情绪状态
愉悦度(P)={state.p:.2f} | 激活度(A)={state.a:.2f} | 支配度(D)={state.d:.2f}{trend}"""
    
    def get_summary(self) -> dict:
        """获取情绪概览（用于可视化/调试）"""
        state = self.get_state()
        dynamics = self.get_dynamics()
        
        return {
            "state": state.to_dict(),
            "dynamics": dynamics.to_dict(),
            "baseline": self._engine.get_baseline().to_dict(),
            "rapid_change_detected": dynamics.is_rapid_change(self._config.RAPID_CHANGE_THRESHOLD),
        }
    
    # === 记忆接口 ===
    
    def get_last_event(self) -> Optional[EmotionEvent]:
        """获取最近情绪事件"""
        return self._last_event
    
    def get_event_history(self, limit: int = 10) -> list[EmotionEvent]:
        """获取情绪事件历史"""
        return self._event_history[-limit:]
    
    def get_significant_events(
        self,
        intensity_threshold: float = 0.15,
    ) -> list[EmotionEvent]:
        """获取显著情绪事件（加速度强度超过阈值）"""
        return [
            e for e in self._event_history
            if e.dynamics.intensity() >= intensity_threshold
        ]
    
    # === 回调机制 ===
    
    def on_rapid_change(self, callback: Callable[[EmotionEvent], None]) -> None:
        """注册快速变化回调"""
        self._rapid_change_callbacks.append(callback)
    
    def remove_rapid_change_callback(self, callback: Callable[[EmotionEvent], None]) -> None:
        """移除快速变化回调"""
        if callback in self._rapid_change_callbacks:
            self._rapid_change_callbacks.remove(callback)
    
    # === 持久化接口 ===
    
    def get_full_state(self) -> dict:
        """获取完整状态（用于持久化）"""
        return self._engine.get_full_state()
    
    def restore_full_state(self, data: dict) -> None:
        """恢复完整状态（从数据库加载）"""
        self._engine.restore_full_state(data)
    
    def to_dict(self) -> dict:
        """转换为字典（兼容旧接口）"""
        return self.get_state().to_dict()
    
    # === 兼容旧 PADState 接口 ===
    
    def intensity(self, delta: dict) -> float:
        """计算情绪变化强度（兼容旧接口）"""
        dp = delta.get("P", 0.0)
        da = delta.get("A", 0.0)
        dd = delta.get("D", 0.0)
        return (dp ** 2 + da ** 2 + dd ** 2) ** 0.5
    
    def __repr__(self) -> str:
        state = self.get_state()
        dynamics = self.get_dynamics()
        return (
            f"EmotionService(state=({state.p:.3f}, {state.a:.3f}, {state.d:.3f}), "
            f"accel={dynamics.intensity():.3f})"
        )