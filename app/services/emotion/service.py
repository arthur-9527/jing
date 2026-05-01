"""情绪服务统一入口

EmotionService 是情绪系统的对外统一接口，提供：
- 状态管理（初始化、更新）
- 动力学（速度、加速度）
- LLM 接口（动态上下文）
- 记忆接口（情绪事件）
- 持久化（Redis 存储）

⭐ 改造：角色级别 + Redis 存储
- Key: emotion:{character_id}
- 不包含 user_id，角色情绪独立
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Callable, Any

from .models import PADState, PADDynamics, EmotionEvent, EmotionBaseline
from .engine import PADEngine
from .config import EmotionConfig, DEFAULT_EMOTION_CONFIG

logger = logging.getLogger(__name__)


class EmotionService:
    """情绪服务统一入口
    
    ⭐ 角色级别设计：
    - 一个角色一个情绪状态
    - 存储在 Redis: emotion:{character_id}
    - 不受 user_id 影响
    
    功能列表：
    ├─ 状态管理（初始化、更新）
    ├─ 动力学（速度、加速度、快速变化检测）
    ├─ LLM 接口（动态上下文）
    ├─ 记忆接口（情绪事件）
    └─ 持久化接口（Redis 保存、加载）
    """
    
    def __init__(
        self,
        character_id: str,
        baseline: dict | EmotionBaseline,
        emotion_repo: Optional[Any] = None,  # ⭐ Stone Repository
        config: EmotionConfig = None,
    ):
        """初始化情绪服务
        
        ⭐ Stone 迁移：使用 EmotionStateRepository
        
        Args:
            character_id: 角色ID（用于 Redis Key）
            baseline: 情绪基线（角色的默认情绪状态）
            emotion_repo: EmotionStateRepository 实例（Stone 数据层）
            config: 引擎配置
        """
        self._character_id = character_id

        # ⭐ Stone Repository
        self._emotion_repo = emotion_repo
        
        self._engine = PADEngine(baseline, config)
        self._config = config or DEFAULT_EMOTION_CONFIG
        
        # 事件记录
        self._last_event: Optional[EmotionEvent] = None
        self._event_history: list[EmotionEvent] = []
        self._max_history: int = 20
        
        # 回调
        self._rapid_change_callbacks: list[Callable[[EmotionEvent], None]] = []

        # ⭐ 心动事件回调（仅用于通知，好感度评估已内置）
        self._heart_event_callbacks: list[Callable[[EmotionEvent, dict], Any]] = []

        # ⭐ 心动事件阈值
        self._heart_event_threshold: float = 0.2

        # ⭐ 好感度评估依赖（由外部注入）
        self._affection_service = None       # AffectionService
        self._affection_llm_chat_fn = None   # async fn(messages, temperature) -> str
        self._personality_text = ""
        self._emotion_traits_text = ""
        self._emotion_triggers_text = ""

        logger.info(f"EmotionService 初始化完成，character_id={character_id}")
    
    # === Redis 持久化 ===
    
    async def load_state(self) -> bool:
        """从 Stone 加载情绪状态
        
        Returns:
            是否成功加载（如果没有存储则返回 False）
        """
        if not self._emotion_repo:
            logger.warning("[EmotionService] EmotionRepo 未配置，无法加载状态")
            return False
        
        try:
            state_dict = await self._emotion_repo.load_state(self._character_id)
            if state_dict:
                self._engine.restore_full_state(state_dict)
                logger.info(
                    f"[EmotionService] 已从 Stone 加载状态: character={self._character_id}, "
                    f"P={self._engine._state.p:.3f}, A={self._engine._state.a:.3f}, D={self._engine._state.d:.3f}"
                )
                return True
            else:
                logger.info(f"[EmotionService] Stone 无存储状态，使用基线: character={self._character_id}")
                return False
        except Exception as e:
            logger.warning(f"[EmotionService] 加载状态失败: {e}")
            return False
    
    async def save_state(self) -> bool:
        """保存情绪状态到 Stone
        
        Returns:
            是否成功保存
        """
        if not self._emotion_repo:
            logger.warning("[EmotionService] EmotionRepo 未配置，无法保存状态")
            return False
        
        try:
            state_dict = self._engine.get_full_state()
            await self._emotion_repo.save_state(self._character_id, state_dict)
            logger.info(
                f"[EmotionService] 已保存状态到 Stone: character={self._character_id}, "
                f"P={self._engine._state.p:.3f}, A={self._engine._state.a:.3f}, D={self._engine._state.d:.3f}"
            )
            return True
        except Exception as e:
            logger.warning(f"[EmotionService] 保存状态失败: {e}")
            return False
    
    def set_emotion_repo(self, emotion_repo: Any) -> None:
        """设置 EmotionStateRepository（延迟注入）- ⭐ Stone 数据层"""
        self._emotion_repo = emotion_repo
        logger.info(f"[EmotionService] EmotionRepo 已设置: character={self._character_id}")
    
    @property
    def character_id(self) -> str:
        """获取角色ID"""
        return self._character_id
    
    @property
    def redis_key(self) -> str:
        """获取 Redis Key"""
        if self._emotion_repo:
            from app.stone.key_builder import RedisKeyBuilder
            return RedisKeyBuilder().emotion_state(self._character_id)
        from app.stone.key_builder import RedisKeyBuilder
        return RedisKeyBuilder().emotion_state(self._character_id)
    
    # === 核心操作 ===
    
    def update(
        self,
        delta: dict,
        trigger_keywords: list[str] = None,
        inner_monologue: str = "",
        context: dict = None,
    ) -> EmotionEvent:
        """更新情绪状态
        
        Args:
            delta: 情绪变化 {"P": float, "A": float, "D": float}
            trigger_keywords: 触发关键词
            inner_monologue: 内心独白
            context: 上下文信息（user_id, user_input, expression, metadata）
            
        Returns:
            EmotionEvent: 情绪事件（包含 is_heart_event 标记）
        """
        # 调用引擎更新
        event = self._engine.update(delta)
        
        # 计算 intensity = √(ΔP² + ΔA² + ΔD²)
        event.intensity = self.intensity(delta)
        
        # 补充触发信息
        event.trigger_keywords = trigger_keywords or []
        event.inner_monologue = inner_monologue
        
        # ⭐ 检测 emotion_peak 事件
        if event.intensity >= self._heart_event_threshold:
            event.is_heart_event = True
            logger.info(
                "❤️ emotion_peak 事件! intensity=%.3f >= %.3f",
                event.intensity,
                self._heart_event_threshold,
            )
            # 外部观察者回调（保留用于扩展）
            self._trigger_heart_event_callbacks(event, context)
            # ⭐ 内置好感度评估（仅一次，解决双重注册问题）
            self._handle_heart_event_affection(event, context)
        
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
    
    def tick(self, steps: int = 1) -> None:
        """执行物理模拟步（用于定时回归）
        
        ⭐ 新增方法：供定时任务调用
        
        Args:
            steps: 执行步数（默认 1 步）
        """
        for _ in range(steps):
            # 只执行衰减和回归，不接收新输入
            self._engine._tick()
        
        logger.debug(
            f"[EmotionService] 执行 {steps} 步物理模拟: "
            f"P={self._engine._state.p:.3f}, A={self._engine._state.a:.3f}, D={self._engine._state.d:.3f}"
        )
    
    def reset(self) -> None:
        """重置到基线状态"""
        self._engine.reset()
        self._last_event = None
        self._event_history.clear()
        logger.info(f"[EmotionService] 已重置到基线: character={self._character_id}")
    
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
            "character_id": self._character_id,
            "redis_key": self.redis_key,
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
    
    # ⭐ 心动事件回调机制
    
    def on_heart_event(self, callback: Callable[[EmotionEvent, dict], Any]) -> None:
        """注册心动事件回调
        
        Args:
            callback: 回调函数，签名为 async def callback(event, context)
                - event: EmotionEvent（is_heart_event=True）
                - context: 包含 user_id, user_input, expression, metadata 等上下文
        """
        self._heart_event_callbacks.append(callback)
        logger.info(f"[EmotionService] 心动事件回调已注册: character={self._character_id}")
    
    def remove_heart_event_callback(self, callback: Callable[[EmotionEvent, dict], Any]) -> None:
        """移除心动事件回调"""
        if callback in self._heart_event_callbacks:
            self._heart_event_callbacks.remove(callback)

    # ⭐ 好感度评估（内置于 EmotionService，避免重复回调）

    def set_affection_deps(
        self,
        affection_service,
        llm_chat_fn,
        personality_text: str = "",
        emotion_traits_text: str = "",
        emotion_triggers_text: str = "",
    ) -> None:
        """注入好感度评估所需依赖

        由 IMProcessor / EmotionalAgent 在初始化时调用，
        确保好感度评估只注册一次。

        Args:
            affection_service: AffectionService 实例
            llm_chat_fn: LLM 调用函数，签名 async fn(messages, temperature) -> str
            personality_text: 角色人设文本
            emotion_traits_text: 情绪特点文本
            emotion_triggers_text: 敏感词/触发词文本
        """
        self._affection_service = affection_service
        self._affection_llm_chat_fn = llm_chat_fn
        self._personality_text = personality_text
        self._emotion_traits_text = emotion_traits_text
        self._emotion_triggers_text = emotion_triggers_text
        logger.info(
            "[EmotionService] 好感度依赖已注入: character=%s, has_affection=%s, has_llm=%s",
            self._character_id,
            affection_service is not None,
            llm_chat_fn is not None,
        )

    def _handle_heart_event_affection(self, event: EmotionEvent, context: dict = None) -> None:
        """处理心动事件的好感度评估（异步，不阻塞主流程）

        由 update() 在检测到 is_heart_event 时调用。
        """
        if not self._affection_service or not self._affection_llm_chat_fn:
            logger.debug("[EmotionService] 好感度依赖未注入，跳过评估")
            return

        try:
            import asyncio
            asyncio.get_running_loop()
        except RuntimeError:
            return

        asyncio.create_task(self._assess_heart_event_affection(event, context))

    async def _assess_heart_event_affection(self, event: EmotionEvent, context: dict = None) -> None:
        """异步执行好感度评估（LLM 调用 + 写入）"""
        context = context or {}
        character_id = context.get("character_id", self._character_id)
        user_id = context.get("user_id", "")
        user_input = context.get("user_input", "")
        expression = context.get("expression", "")

        try:
            # 1. 保存心动事件到 Redis
            await self._save_heart_event(
                event=event,
                user_id=user_id,
                user_input=user_input,
                expression=expression,
            )

            # 2. 获取当前好感度状态
            affection_state = await self._affection_service.get_state(character_id, user_id)

            # 3. 构建评估 prompt
            from app.services.affection.prompts import build_emotional_assessment_prompt
            from app.services.affection.models import AffectionAssessment

            assessment_prompt = build_emotional_assessment_prompt(
                affection_state=affection_state,
                inner_monologue=event.inner_monologue,
                emotion_delta=event.delta.to_dict(),
                emotion_intensity=event.intensity,
                user_input=user_input,
                expression=expression,
                personality_text=self._personality_text,
                emotion_traits_text=self._emotion_traits_text,
                emotion_triggers_text=self._emotion_triggers_text,
            )

            # 4. 调用 LLM 评估
            assessment_response = await self._affection_llm_chat_fn(
                [{"role": "user", "content": assessment_prompt}],
                temperature=0.3,
            )

            # 5. 解析并写入
            try:
                assessment_dict = json.loads(assessment_response.strip())
                assessment = AffectionAssessment.from_dict(assessment_dict)

                if assessment.has_any_delta(threshold=0.5):
                    await self._affection_service.add_emotional_deltas_batch(
                        character_id, user_id, assessment,
                    )
                    logger.info(
                        "❤️ [EmotionService] 好感度评估完成: trust=%.2f, intimacy=%.2f, respect=%.2f | reasoning=%s",
                        assessment.trust_delta,
                        assessment.intimacy_delta,
                        assessment.respect_delta,
                        assessment.reasoning or "无",
                    )
                else:
                    logger.debug("[EmotionService] 心动事件但好感度无显著变化，跳过写入")

            except json.JSONDecodeError:
                logger.warning("[EmotionService] 好感度评估结果解析失败: %s", assessment_response[:100])

        except Exception as e:
            logger.warning("[EmotionService] 心动事件好感度评估失败: %s", e)

    def _trigger_heart_event_callbacks(self, event: EmotionEvent, context: dict = None) -> None:
        """触发所有心动事件回调
        
        ⭐ 注意：此方法在 update() 的同步上下文中调用
        - 回调函数如果是异步的，需要通过 asyncio.create_task 调度
        - 回调失败不影响主流程
        
        Args:
            event: 情绪事件（is_heart_event=True）
            context: 上下文信息（user_id, user_input, expression, metadata）
        """
        if not self._heart_event_callbacks:
            return
        
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行的事件循环，无法调度异步任务
            logger.warning("[EmotionService] 没有运行的事件循环，无法触发心动事件回调")
            return
        
        for callback in self._heart_event_callbacks:
            try:
                # 判断是否为协程函数
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(event, context))
                else:
                    callback(event, context)
            except Exception as e:
                logger.warning("[EmotionService] 心动事件回调执行失败: %s", e)

    async def _save_heart_event(
        self,
        event: EmotionEvent,
        user_id: str,
        user_input: str = "",
        expression: str = "",
    ) -> str:
        """保存心动事件到 Stone
        
        Args:
            event: 情绪事件
            user_id: 用户ID
            user_input: 用户输入
            expression: 角色台词
            
        Returns:
            event_id: 事件ID
        """
        if not self._emotion_repo:
            logger.warning("[EmotionService] EmotionRepo 未配置，无法保存心动事件")
            return ""
        
        event_id = str(uuid.uuid4())
        
        try:
            event_data = {
                "event_id": event_id,
                "timestamp": event.timestamp.isoformat(),
                "intensity": round(event.intensity, 4),
                "emotion_delta": event.delta.to_dict(),
                "pad_state": event.state.to_dict(),
                "inner_monologue": event.inner_monologue,
                "user_input": user_input,
                "expression": expression,
            }
            await self._emotion_repo.save_heart_event(
                self._character_id, user_id, event_id, event_data
            )
            await self._emotion_repo.add_heartbeat_event(
                self._character_id, user_id, event_id, event.intensity
            )
            logger.info(
                f"[EmotionService] ❤️ 心动事件已保存: character={self._character_id}, "
                f"user={user_id}, event_id={event_id}, intensity={event.intensity:.3f}"
            )
            return event_id
        except Exception as e:
            logger.warning(f"[EmotionService] 保存心动事件失败: {e}")
            return ""
    
    # === 持久化接口 ===

    def get_full_state(self) -> dict:
        """获取完整状态（用于持久化）"""
        return self._engine.get_full_state()

    def restore_full_state(self, data: dict) -> None:
        """恢复完整状态（从数据库加载）"""
        self._engine.restore_full_state(data)

    async def save_state_to_redis(self) -> bool:
        """保存完整状态到 Redis（使用 Stone EmotionStateRepository）

        Returns:
            是否成功保存
        """
        if not self._emotion_repo:
            logger.warning("[EmotionService] EmotionRepo 未配置，无法保存状态")
            return False

        try:
            state = self.get_full_state()
            state["character_id"] = self._character_id
            await self._emotion_repo.save_state(self._character_id, state)
            logger.debug(f"[EmotionService] 状态已保存: character={self._character_id}")
            return True
        except Exception as e:
            logger.warning(f"[EmotionService] 保存状态失败: {e}")
            return False

    async def load_state_from_redis(self) -> bool:
        """从 Redis 加载完整状态（使用 Stone EmotionStateRepository）

        Returns:
            是否成功加载
        """
        if not self._emotion_repo:
            logger.warning("[EmotionService] EmotionRepo 未配置，无法加载状态")
            return False

        try:
            state = await self._emotion_repo.load_state(self._character_id)
            if state:
                self.restore_full_state(state)
                logger.info(f"[EmotionService] 状态已从 Redis 加载: character={self._character_id}")
                return True
            logger.debug(f"[EmotionService] Redis 中无状态: character={self._character_id}")
            return False
        except Exception as e:
            logger.warning(f"[EmotionService] 加载状态失败: {e}")
            return False
    
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
            f"EmotionService(character={self._character_id}, "
            f"state=({state.p:.3f}, {state.a:.3f}, {state.d:.3f}), "
            f"accel={dynamics.intensity():.3f})"
        )


# ---------------------------------------------------------------------------
# 全局实例管理（角色级别）
# ---------------------------------------------------------------------------

_emotion_services: dict[str, EmotionService] = {}


def get_emotion_service(
    character_id: str,
    baseline: dict | EmotionBaseline,
    config: EmotionConfig = None,
    emotion_repo: Optional[Any] = None,  # ⭐ EmotionStateRepository
) -> EmotionService:
    """获取或创建 EmotionService（角色级别）
    
    ⭐ 一个角色一个实例，按 character_id 存储
    ⭐ Stone 迁移：使用 EmotionStateRepository
    
    Args:
        character_id: 角色ID
        baseline: 情绪基线
        config: 引擎配置
        emotion_repo: EmotionStateRepository 实例（Stone 数据层）
    
    Returns:
        EmotionService 实例
    """
    if character_id not in _emotion_services:
        service = EmotionService(
            character_id=character_id,
            baseline=baseline,
            emotion_repo=emotion_repo,
            config=config,
        )
        _emotion_services[character_id] = service
        logger.info(f"[get_emotion_service] 创建新实例: character={character_id}")
    
    return _emotion_services[character_id]


def reset_emotion_service(character_id: str = None) -> None:
    """重置 EmotionService
    
    Args:
        character_id: 如果指定，只重置该角色的服务；否则重置所有
    """
    if character_id:
        if character_id in _emotion_services:
            del _emotion_services[character_id]
            logger.info(f"[reset_emotion_service] 已重置: character={character_id}")
    else:
        _emotion_services.clear()
        logger.info("[reset_emotion_service] 已重置所有实例")