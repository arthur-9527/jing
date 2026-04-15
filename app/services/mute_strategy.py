#!/usr/bin/env python3
"""
动态静音策略 - 可通过 WS 指令控制 ASR 静音

特性：
1. 初始化时默认静音（进入 IDLE 前丢弃所有 ASR 帧）
2. 进入 IDLE 后自动解除初始静音
3. 运行时可动态切换静音状态
4. TTS 帧不受影响（可正常播放）

Pipecat mute 机制：
- LLMUserAggregator 的 _maybe_mute_frame() 会检查 user_mute_strategies
- 返回 True 时丢弃：InputAudioRawFrame, TranscriptionFrame, InterimTranscriptionFrame 等
- TTS 相关帧不在丢弃列表中，因此静音时 TTS 正常播放
"""

from typing import Optional, TYPE_CHECKING

from loguru import logger
from pipecat.frames.frames import Frame
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

if TYPE_CHECKING:
    from app.services.state_manager import StateManagerProcessor


class DynamicMuteStrategy(BaseUserMuteStrategy):
    """可动态控制的静音策略
    
    控制逻辑：
    - _mute=True 时丢弃 ASR 帧
    - _mute=False 时正常处理 ASR 帧
    
    自动解除逻辑：
    - 初始化时 _mute=True（静音）
    - StateManager 进入 IDLE 后，自动解除初始静音
    - 之后由外部（WS 指令）控制
    
    使用场景：
    1. 系统初始化期间静音，防止 ASR 处理未就绪时的输入
    2. 运行时动态静音，如前端需要暂停语音输入
    3. TTS 播放期间可选择性静音（防止用户打断）
    """
    
    def __init__(self):
        """初始化策略，默认静音"""
        super().__init__()
        self._mute: bool = True  # ⭐ 默认静音，等待 IDLE 后解除
        self._state_manager: Optional["StateManagerProcessor"] = None
        self._initial_mute_released: bool = False  # 标记初始静音是否已解除
        
    def set_state_manager(self, state_manager: "StateManagerProcessor"):
        """设置 StateManager 引用
        
        Args:
            state_manager: 状态管理器，用于检测 IDLE 状态
        """
        self._state_manager = state_manager
        logger.info("[MuteStrategy] StateManager 已注入")
    
    def set_mute(self, mute: bool):
        """动态设置静音状态
        
        Args:
            mute: True 表示静音（丢弃 ASR 帧），False 表示正常处理
        
        注意：
        - 此方法由外部（WS 指令）调用
        - 设置后立即生效，下一帧处理时应用新状态
        """
        old_state = self._mute
        self._mute = mute
        
        if old_state != mute:
            logger.info(f"[MuteStrategy] 静音状态切换: {old_state} → {mute}")
    
    def is_muted(self) -> bool:
        """获取当前静音状态
        
        Returns:
            True 表示正在静音，False 表示正常处理
        """
        return self._mute
    
    async def reset(self):
        """重置策略到初始状态"""
        self._mute = True
        self._initial_mute_released = False
        logger.info("[MuteStrategy] 策略已重置，恢复初始静音")
    
    async def process_frame(self, frame: Frame) -> bool:
        """处理帧，决定是否静音
        
        Args:
            frame: 流经 LLMUserAggregator 的帧
            
        Returns:
            True 表示静音（丢弃帧），False 表示正常处理
            
        逻辑：
        1. 如果 StateManager 存在且已进入 IDLE：
           - 第一次检测到 IDLE 时，自动解除初始静音
           - 之后返回 _mute（动态控制的值）
        2. 如果 StateManager 不存在或未进入 IDLE：
           - 返回 True（始终静音）
        """
        # 检查 StateManager 是否进入 IDLE
        if self._state_manager is not None:
            is_idle = self._state_manager.is_idle
            
            # ⭐ 第一次检测到 IDLE，自动解除初始静音
            if is_idle and not self._initial_mute_released:
                self._initial_mute_released = True
                self._mute = False  # 自动解除
                logger.info("[MuteStrategy] 系统已进入 IDLE，自动解除初始静音")
            
            # 返回动态控制的值（可能是 WS 指令设置的）
            return self._mute
        
        # StateManager 未注入，保持静音
        return True


# ===== 全局实例 =====

_global_mute_strategy: Optional[DynamicMuteStrategy] = None


def get_mute_strategy() -> DynamicMuteStrategy:
    """获取全局静音策略实例"""
    global _global_mute_strategy
    if _global_mute_strategy is None:
        _global_mute_strategy = DynamicMuteStrategy()
    return _global_mute_strategy


def set_mute(mute: bool):
    """设置全局静音状态（供 WS 路由调用）
    
    Args:
        mute: True 表示静音，False 表示解除静音
    """
    strategy = get_mute_strategy()
    strategy.set_mute(mute)


def is_muted() -> bool:
    """获取全局静音状态"""
    strategy = get_mute_strategy()
    return strategy.is_muted()