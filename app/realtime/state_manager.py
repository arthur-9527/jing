#!/usr/bin/env python3
"""
状态管理处理器 - 监控系统状态并管理状态转换

基于 pipecat Frame 机制实现状态监控：
- 监听关键 Frame（UserStartedSpeakingFrame, TTSStartedFrame 等）
- 维护状态机（IDLE → LISTENING → THINKING → SPEAKING）
- 处理打断和任务播报的特殊转换
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Awaitable, TYPE_CHECKING
from loguru import logger

from pipecat.frames.frames import (
    Frame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSSpeakFrame,
    InterruptionFrame,
    StartFrame,
    EndFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

# ⭐ 导入自定义帧：无效转录事件
from app.providers.asr.frames import TranscriptionFilteredFrame

if TYPE_CHECKING:
    from app.realtime.agent_ws_manager import AgentWSManager
    from app.realtime.frame_queue.frame_queue import FrameQueueManager
    from app.realtime.frame_queue.idle_scheduler import IdleScheduler

# ⭐ 直接使用 AgentWSManager 的 AgentStatus，避免重复定义
from app.realtime.agent_ws_manager import AgentStatus

# AgentState 作为 AgentStatus 的别名，便于外部使用
AgentState = AgentStatus


@dataclass
class StateTransitionEvent:
    """状态转换事件"""
    from_state: AgentState
    to_state: AgentState
    timestamp: float
    reason: str = ""
    frame_type: str = ""


class StateManagerProcessor(FrameProcessor):
    """
    状态管理处理器 - 监控关键 Frame 并管理状态转换
    
    状态流转规则：
    - 正常流程：IDLE → LISTENING → THINKING → SPEAKING → IDLE
    - 打断：任何状态 → LISTENING（收到 InterruptionFrame）
    - 任务播报：IDLE → SPEAKING（收到 TTSSpeakFrame 且当前为 IDLE）
    
    与其他组件的协作：
    - AgentWSManager：广播状态给客户端
    - FrameQueueManager：监听口型帧耗尽事件（SPEAKING → IDLE 的判定条件）
    - AgentService：接收打断通知、触发任务播报
    """
    
    def __init__(
        self,
        ws_manager: "AgentWSManager",
        frame_queue: Optional["FrameQueueManager"] = None,
        max_history: int = 100,
        **kwargs,
    ):
        """
        Args:
            ws_manager: WebSocket 管理器，用于广播状态
            frame_queue: 帧队列管理器，用于检测口型帧耗尽
            max_history: 状态历史最大记录数
            **kwargs: FrameProcessor 其他参数
        """
        super().__init__(**kwargs)
        self._ws_manager = ws_manager
        self._frame_queue = frame_queue
        self._idle_scheduler: Optional["IdleScheduler"] = None  # ⭐ IdleScheduler 引用
        
        # 当前状态（从 INITING 开始，等待初始化完成后转换到 IDLE）
        self._current_state: AgentState = AgentState.INITING
        self._state_lock = asyncio.Lock()
        
        # 状态历史
        self._state_history: deque[StateTransitionEvent] = deque(maxlen=max_history)
        
        # 状态进入时间（用于统计）
        self._state_enter_time: float = time.monotonic()
        
        # TTS 计数器（处理分段 TTS）
        self._tts_start_count: int = 0
        self._tts_stop_count: int = 0
        
        # 等待口型帧耗尽的标记
        self._waiting_for_lip_empty: bool = False
        self._lip_empty_event: asyncio.Event = asyncio.Event()
        
        # 状态变化回调列表
        self._on_state_change_callbacks: list[Callable[[AgentState, AgentState], Awaitable[None]]] = []
        
        logger.info(f"[StateManager] 初始化，当前状态: {self._current_state.value}")
    
    # ===== 属性 =====
    
    @property
    def current_state(self) -> AgentState:
        """获取当前状态"""
        return self._current_state
    
    @property
    def state_duration(self) -> float:
        """获取当前状态持续时间（秒）"""
        return time.monotonic() - self._state_enter_time
    
    @property
    def is_idle(self) -> bool:
        """是否处于空闲状态"""
        return self._current_state == AgentState.IDLE
    
    @property
    def is_speaking(self) -> bool:
        """是否处于说话状态"""
        return self._current_state == AgentState.SPEAKING
    
    # ===== 回调注册 =====
    
    def register_state_change_callback(self, callback: Callable[[AgentState, AgentState], Awaitable[None]]):
        """注册状态变化回调"""
        self._on_state_change_callbacks.append(callback)
    
    def set_frame_queue(self, frame_queue: "FrameQueueManager"):
        """设置帧队列管理器（延迟注入）"""
        self._frame_queue = frame_queue
        # 注册口型帧耗尽回调
        if frame_queue:
            frame_queue.set_buffer_empty_callback(self._on_lip_frames_empty)
            logger.info("[StateManager] 帧队列管理器已设置")
    
    def set_idle_scheduler(self, idle_scheduler: "IdleScheduler"):
        """设置 IdleScheduler（延迟注入）"""
        self._idle_scheduler = idle_scheduler
        logger.info("[StateManager] IdleScheduler 已设置")
    
    # ===== Frame 处理 =====
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """处理 Frame，触发状态转换"""
        await super().process_frame(frame, direction)
        
        # 根据 Frame 类型触发状态转换
        frame_type = frame.__class__.__name__
        
        try:
            # 用户开始说话 → LISTENING
            if isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
                await self._transition_to(
                    AgentState.LISTENING,
                    reason="user_started_speaking",
                    frame_type=frame_type,
                )
            
            # 用户停止说话 → THINKING
            elif isinstance(frame, (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)):
                # 只有在 LISTENING 状态才转换到 THINKING
                if self._current_state == AgentState.LISTENING:
                    await self._transition_to(
                        AgentState.THINKING,
                        reason="user_stopped_speaking",
                        frame_type=frame_type,
                    )
            
            # LLM 开始响应 → 确保在 THINKING（或保持 SPEAKING 如果 TTS 已开始）
            elif isinstance(frame, LLMFullResponseStartFrame):
                if self._current_state == AgentState.LISTENING:
                    # 可能 VAD 和 LLM 响应有延迟，直接跳到 THINKING
                    await self._transition_to(
                        AgentState.THINKING,
                        reason="llm_response_start",
                        frame_type=frame_type,
                    )
                logger.debug(f"[StateManager] LLM 响应开始，当前状态: {self._current_state.value}")
            
            # LLM 响应结束
            elif isinstance(frame, LLMFullResponseEndFrame):
                logger.debug(f"[StateManager] LLM 响应结束，当前状态: {self._current_state.value}")
            
            # TTS 开始 → SPEAKING
            elif isinstance(frame, TTSStartedFrame):
                self._tts_start_count += 1
                await self._transition_to(
                    AgentState.SPEAKING,
                    reason="tts_started",
                    frame_type=frame_type,
                )
                logger.debug(f"[StateManager] TTS 开始 #{self._tts_start_count}")
            
            # TTS 停止 → 准备回到 IDLE（等待口型帧耗尽）
            elif isinstance(frame, TTSStoppedFrame):
                self._tts_stop_count += 1
                logger.debug(f"[StateManager] TTS 停止 #{self._tts_stop_count}")
                
                # 只有在 SPEAKING 状态才处理
                if self._current_state == AgentState.SPEAKING:
                    # 等待口型帧耗尽
                    self._waiting_for_lip_empty = True
                    self._lip_empty_event.clear()
                    logger.debug("[StateManager] 等待口型帧耗尽...")
            
            # 异步播报任务（从 IDLE 直接到 SPEAKING）
            elif isinstance(frame, TTSSpeakFrame):
                if self._current_state == AgentState.IDLE:
                    await self._transition_to(
                        AgentState.SPEAKING,
                        reason="async_playback",
                        frame_type=frame_type,
                    )
                    logger.info("[StateManager] 异步播报任务启动，IDLE → SPEAKING")
            
            # 打断 → LISTENING（任何状态都可以）
            elif isinstance(frame, InterruptionFrame):
                await self._transition_to(
                    AgentState.LISTENING,
                    reason="interruption",
                    frame_type=frame_type,
                )
                # 重置计数器
                self._tts_start_count = 0
                self._tts_stop_count = 0
                self._waiting_for_lip_empty = False
                logger.info("[StateManager] 收到打断信号，切换到 LISTENING")
            
            # ⭐ 无效转录 → IDLE（从 THINKING 恢复）
            elif isinstance(frame, TranscriptionFilteredFrame):
                if self._current_state == AgentState.THINKING:
                    logger.info(
                        f"[StateManager] 收到无效转录帧，THINKING → IDLE "
                        f"(text: {frame.text}, reason: {frame.reason})"
                    )
                    await self._transition_to(
                        AgentState.IDLE,
                        reason="transcription_filtered",
                        frame_type=frame_type,
                    )
                    # 重置计数器
                    self._tts_start_count = 0
                    self._tts_stop_count = 0
                    self._waiting_for_lip_empty = False
                else:
                    logger.debug(
                        f"[StateManager] 收到无效转录帧，当前状态: {self._current_state.value}，不转换"
                    )
            
            # 传递 Frame 到下一个处理器
            await self.push_frame(frame, direction)
            
        except Exception as e:
            logger.error(f"[StateManager] 处理 Frame 失败: {e}")
            await self.push_frame(frame, direction)
    
    # ===== 状态转换 =====
    
    async def _transition_to(
        self,
        new_state: AgentState,
        reason: str = "",
        frame_type: str = "",
    ):
        """执行状态转换
        
        ⭐ P1-11: 状态锁外执行 I/O
        - 原代码在锁内执行 broadcast 和回调，如果 broadcast 慢会阻塞所有状态转换
        - 现在在锁内只执行状态变更（修改变量、记录历史）
        - I/O 操作移到锁外，使用 asyncio.create_task 异步执行
        """
        # 收集需要在锁外执行的操作
        broadcast_state = None
        callbacks_to_run = []
        load_thinking = False
        
        # ===== 锁内：只执行状态变更逻辑 =====
        async with self._state_lock:
            old_state = self._current_state
            
            # 验证转换合法性
            if not self._is_valid_transition(old_state, new_state):
                logger.warning(
                    f"[StateManager] 非法状态转换: {old_state.value} → {new_state.value} "
                    f"(reason: {reason})"
                )
                return
            
            # 相同状态不转换
            if old_state == new_state:
                logger.debug(f"[StateManager] 状态未变化: {new_state.value}")
                return
            
            # 执行转换
            self._current_state = new_state
            self._state_enter_time = time.monotonic()
            
            # 记录历史
            event = StateTransitionEvent(
                from_state=old_state,
                to_state=new_state,
                timestamp=time.time(),
                reason=reason,
                frame_type=frame_type,
            )
            self._state_history.append(event)
            
            # 日志
            logger.info(
                f"[StateManager] 状态转换: {old_state.value} → {new_state.value} "
                f"(reason: {reason}, frame: {frame_type})"
            )
            
            # 收集锁外需要执行的操作
            broadcast_state = new_state
            callbacks_to_run = self._on_state_change_callbacks.copy()
            
            # ⭐ IDLE → LISTENING 时标记需要加载 thinking 动作
            if old_state == AgentState.IDLE and new_state == AgentState.LISTENING:
                load_thinking = True
        
        # ===== 锁外：执行 I/O 操作 =====
        
        # 异步广播状态（不阻塞）
        if broadcast_state:
            try:
                await self._ws_manager.broadcast_status(broadcast_state)
            except Exception as e:
                logger.error(f"[StateManager] 广播状态失败: {e}")
        
        # 触发回调
        for callback in callbacks_to_run:
            try:
                await callback(old_state, new_state)
            except Exception as e:
                logger.error(f"[StateManager] 回调执行失败: {e}")
        
        # ⭐ IDLE → LISTENING 时触发 thinking 动作加载
        if load_thinking and self._idle_scheduler:
            try:
                await self._idle_scheduler.load_random_thinking()
                logger.info("[StateManager] 已触发 thinking 动作加载")
            except Exception as e:
                logger.error(f"[StateManager] thinking 动作加载失败: {e}")
        
        # ⭐ 自动静音逻辑（不支持打断时）
        # 当 INTERRUPTION_ENABLED=false 时，SPEAKING 状态自动静音，IDLE 状态解除静音
        from app.config import settings
        if not settings.INTERRUPTION_ENABLED:
            from app.realtime.mute_strategy import set_mute
            
            if new_state == AgentState.SPEAKING:
                # 进入 SPEAKING → 自动静音（防止无 AEC 麦克风误打断）
                set_mute(True)
                logger.info("[StateManager] 不支持打断，SPEAKING 时自动静音")
            elif new_state == AgentState.IDLE:
                # 进入 IDLE → 解除静音
                set_mute(False)
                logger.info("[StateManager] 不支持打断，IDLE 时解除静音")
    
    def _is_valid_transition(self, from_state: AgentState, to_state: AgentState) -> bool:
        """验证状态转换合法性"""
        # ⭐ INITING 只能转换到 IDLE（初始化完成）
        if from_state == AgentState.INITING:
            return to_state == AgentState.IDLE
        
        # 打断：任何状态都可以到 LISTENING
        if to_state == AgentState.LISTENING:
            return True
        
        # 任务播报：IDLE 可以直接到 SPEAKING
        if from_state == AgentState.IDLE and to_state == AgentState.SPEAKING:
            return True
        
        # 正常流程
        valid_transitions = {
            AgentState.IDLE: [AgentState.LISTENING, AgentState.SPEAKING],
            AgentState.LISTENING: [AgentState.THINKING, AgentState.LISTENING],
            # ⭐ THINKING 可以转换到 IDLE（无效转录恢复）
            AgentState.THINKING: [AgentState.SPEAKING, AgentState.LISTENING, AgentState.IDLE],
            AgentState.SPEAKING: [AgentState.IDLE, AgentState.LISTENING],
        }
        
        allowed = valid_transitions.get(from_state, [])
        return to_state in allowed
    
    # ===== TTS 回调接口 =====
    
    async def on_tts_started(self, context_id: str = None):
        """TTS 开始回调（由 TTS 服务或 AgentService 调用）
        
        触发 THINKING → SPEAKING 转换，或 IDLE → SPEAKING（任务播报）
        """
        # 只有 THINKING 或 IDLE 状态才转换到 SPEAKING
        if self._current_state in (AgentState.THINKING, AgentState.IDLE):
            await self._transition_to(
                AgentState.SPEAKING,
                reason="tts_started_callback",
                frame_type="callback",
            )
            self._tts_start_count += 1
            logger.info(f"[StateManager] TTS 开始回调，{self._current_state.value} → SPEAKING, context={context_id}")
        else:
            logger.debug(f"[StateManager] TTS 开始回调，当前状态={self._current_state.value}, 不转换")
    
    async def on_tts_stopped(self, context_id: str = None):
        """TTS 停止回调（由 TTS 服务调用）
        
        设置等待口型帧耗尽标记，不立即转换状态
        """
        self._tts_stop_count += 1
        if self._current_state == AgentState.SPEAKING:
            self._waiting_for_lip_empty = True
            self._lip_empty_event.clear()
            logger.debug(f"[StateManager] TTS 停止回调，等待口型帧耗尽，context={context_id}")
    
    # ===== 外部接口 =====
    
    async def force_transition(self, new_state: AgentState, reason: str = "forced"):
        """强制状态转换（用于外部控制）"""
        await self._transition_to(new_state, reason=reason, frame_type="external")
    
    async def force_to_listening(self):
        """强制切换到 LISTENING（用于打断处理）"""
        await self._transition_to(
            AgentState.LISTENING,
            reason="forced_interrupt",
            frame_type="external",
        )
        self._tts_start_count = 0
        self._tts_stop_count = 0
        self._waiting_for_lip_empty = False
    
    async def force_to_idle(self):
        """强制切换到 IDLE（用于错误恢复）"""
        await self._transition_to(
            AgentState.IDLE,
            reason="forced_reset",
            frame_type="external",
        )
        self._tts_start_count = 0
        self._tts_stop_count = 0
        self._waiting_for_lip_empty = False
    
    async def start_async_playback(self):
        """启动异步播报（从 IDLE 到 SPEAKING）"""
        if self._current_state == AgentState.IDLE:
            await self._transition_to(
                AgentState.SPEAKING,
                reason="async_playback_external",
                frame_type="external",
            )
    
    # ===== 口型帧耗尽回调 =====
    
    async def _on_lip_frames_empty(self):
        """口型帧耗尽时的回调（由 FrameQueueManager 触发）"""
        if self._waiting_for_lip_empty and self._current_state == AgentState.SPEAKING:
            logger.info("[StateManager] 口型帧耗尽，SPEAKING → IDLE")
            self._waiting_for_lip_empty = False
            await self._transition_to(
                AgentState.IDLE,
                reason="lip_frames_empty",
                frame_type="callback",
            )
    
    # ===== 生命周期 =====
    
    async def start(self, frame: StartFrame):
        """启动处理器"""
        await super().start(frame)
        logger.info("[StateManager] 处理器已启动")
    
    async def stop(self, frame: EndFrame):
        """停止处理器"""
        await super().stop(frame)
        logger.info("[StateManager] 处理器已停止")
    
    # ===== 查询接口 =====
    
    def get_state_history(self, limit: int = 10) -> list[dict]:
        """获取状态历史记录"""
        events = list(self._state_history)[-limit:]
        return [
            {
                "from": e.from_state.value,
                "to": e.to_state.value,
                "timestamp": e.timestamp,
                "reason": e.reason,
                "frame_type": e.frame_type,
            }
            for e in events
        ]
    
    def get_stats(self) -> dict:
        """获取状态统计信息"""
        history = list(self._state_history)
        
        # 计算各状态持续时间
        state_durations = {s.value: 0.0 for s in AgentState}
        for i, event in enumerate(history[:-1]):
            next_event = history[i + 1]
            duration = next_event.timestamp - event.timestamp
            state_durations[event.to_state.value] += duration
        
        # 添加当前状态的持续时间
        if history:
            current_duration = time.time() - history[-1].timestamp
            state_durations[self._current_state.value] += current_duration
        
        return {
            "current_state": self._current_state.value,
            "state_duration": round(self.state_duration, 2),
            "tts_start_count": self._tts_start_count,
            "tts_stop_count": self._tts_stop_count,
            "waiting_for_lip_empty": self._waiting_for_lip_empty,
            "total_transitions": len(history),
            "state_durations": state_durations,
        }