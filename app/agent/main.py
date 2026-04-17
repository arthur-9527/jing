"""入口：对话主循环 + pipecat 集成接口

严格动静分离架构：
- System Prompt: 完全静态（角色定义、标签目录、动作规则、输出格式）
  初始化时构建一次，之后永远不变，可被 Cerebras 缓存
- User Prompt: 只有动态内容（PAD状态、记忆、对话历史）
  每轮重新构建

架构本质：补全模式而非对话模式
- System Prompt = 角色剧本
- User Prompt = 当前场景描述
- LLM 输出 = 按剧本补全当前场景下的角色台词
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from typing import Any

from app.agent.character.loader import CharacterConfig, load_character
from app.agent.db.connection import close_pool, init_db
from app.agent.db.models import get_agent_state, upsert_agent_state
from app.services.emotion import EmotionService
from app.agent.llm.client import LLMClient
from app.agent.llm.unified import generate_unified, generate_unified_stream
from app.agent.memory.retriever import retrieve_memories, retrieve_short_term_memories
from app.agent.memory.writer import write_emotion_memory
from app.agent.prompt.system_prompt import build_static_system_prompt, build_dynamic_context
from app.agent.prompt.tool_rewrite_prompt import build_tool_rewrite_prompt
from app.config import settings
from app.services.motion_catalog_service import get_motion_catalog_service
from app.services.tag_catalog_service import get_tag_catalog_service

# 延迟导入心跳模块避免循环依赖
_cache_heartbeat = None


def _get_cache_heartbeat():
    global _cache_heartbeat
    if _cache_heartbeat is None:
        from app.agent.llm import cache_heartbeat
        _cache_heartbeat = cache_heartbeat
    return _cache_heartbeat

logger = logging.getLogger(__name__)

# ⭐ 动作处理已迁移到 app/agent/action/processor.py
# 以下冗余代码已删除：_ACTION_TAG_PATTERN, _parse_action_json


class EmotionalAgent:
    """情绪 AI Agent 核心类（严格动静分离 + Cerebras Prompt Caching 优化）

    架构设计：
    - 主 LLM (self.llm): 使用 CerebrasProvider，享受低延迟和 Prompt Caching
    - OpenClaw 工具调用 (self.openclaw_llm): 使用 LiteLLMProvider，支持自定义 API

    动静分离：
    - static_system_prompt: 初始化时构建，之后永远不变（包含全量动作标签）
    - 每轮只构建动态 user_prompt（PAD状态、记忆、对话历史）

    这样分离的好处：
    1. 保持 Cerebras 的性能优势（Prompt Caching、低延迟）
    2. OpenClaw 调用独立管理，便于后续改造为 WebSocket 或多任务轮询
    3. 两个 Provider 互不干扰，职责清晰
    """

    def __init__(
        self,
        config: CharacterConfig,
        llm_client: LLMClient,
        static_system_prompt: str,
    ):
        """
        Args:
            config: 角色配置
            llm_client: LLM 客户端
            static_system_prompt: 完整静态系统提示词（初始化时构建，之后不变）
        """
        self.config = config
        self.llm = llm_client
        # 使用新的 EmotionService（物理模拟版）
        self.emotion = EmotionService(config.emotion_baseline)
        self.turn_count: int = 0
        self._bg_tasks: set[asyncio.Task] = set()
        self.motion_catalog = get_motion_catalog_service()

        # 存储完整的静态 System Prompt（初始化时已构建，包含全量动作标签）
        self.static_system_prompt = static_system_prompt
        logger.info("静态 System Prompt 已存储，长度: %d chars", len(self.static_system_prompt))

        # 初始化缓存心跳（如果启用）
        self._init_cache_heartbeat()

        # 立即启动心跳循环（异步任务，不阻塞初始化）
        # 心跳在后台运行，每 4 分钟发送一次保活缓存
        self._heartbeat_task: asyncio.Task | None = None

        # ⭐ 任务系统引用（由 AgentService 在启动时注入）
        # 新任务系统 (TaskSystem) 已在 AgentService.start() 中启动
        # EmotionalAgent 通过 TaskSystem 执行工具调用
        self._task_system = None
    
    def _init_cache_heartbeat(self) -> None:
        """初始化缓存心跳机制"""
        if not settings.CACHE_HEARTBEAT_ENABLED:
            logger.debug("缓存心跳已禁用 (CACHE_HEARTBEAT_ENABLED=False)")
            self.cache_heartbeat = None
            return
        
        try:
            hb_module = _get_cache_heartbeat()
            self.cache_heartbeat = hb_module.CacheHeartbeat(
                llm_client=self.llm,
                static_system_prompt=self.static_system_prompt,
                interval=settings.CACHE_HEARTBEAT_INTERVAL,
                enabled=True,
            )
            logger.info(
                "缓存心跳已初始化，间隔: %d 秒",
                settings.CACHE_HEARTBEAT_INTERVAL
            )
            
            # 立即启动心跳循环（异步任务，不阻塞初始化）
            # 注意：这里需要在事件循环中调度任务
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 如果已经在运行中，使用 create_task
                    self._heartbeat_task = loop.create_task(self.cache_heartbeat.start())
                else:
                    # 如果没有运行，稍后由调用者启动
                    pass
            except RuntimeError:
                # 没有事件循环，稍后启动
                pass
                
        except Exception as e:
            logger.warning("缓存心跳初始化失败: %s，心跳功能将不可用", e)
            self.cache_heartbeat = None
    
    async def _trigger_heartbeat_pulse(self) -> None:
        """触发一次即时心跳以刷新缓存 TTL"""
        if self.cache_heartbeat and settings.CACHE_HEARTBEAT_ENABLED:
            try:
                await self.cache_heartbeat.pulse()
            except Exception as e:
                logger.debug("即时心跳脉冲失败（非关键）: %s", e)

    async def start_heartbeat(self) -> None:
        """启动缓存心跳循环（后台任务）"""
        if self.cache_heartbeat and settings.CACHE_HEARTBEAT_ENABLED:
            try:
                await self.cache_heartbeat.start()
            except Exception as e:
                logger.warning("启动缓存心跳失败: %s", e)

        # ⭐ 新任务系统由 AgentService.start() 启动，不再在此处管理
        # TaskSystem 已在 AgentService 启动时初始化并标记门控就绪

    async def stop_heartbeat(self) -> None:
        """停止缓存心跳循环"""
        if self.cache_heartbeat:
            try:
                await self.cache_heartbeat.stop()
            except Exception as e:
                logger.warning("停止缓存心跳失败: %s", e)

        # ⭐ 新任务系统由 AgentService.stop() 停止，不再在此处管理

    async def match_motion_from_action(self, action_name: str):
        """从动作名匹配动作（用于 <a> 标签流式动作）"""
        try:
            tag_catalog = get_tag_catalog_service()
            return await tag_catalog.match_motion_by_tags(
                action=action_name,
                emotion="",
                desp=action_name,
            )
        except Exception:
            logger.exception("动作匹配失败: %s", action_name)
            return None

    async def load_state(self, user_id: str):
        """从数据库加载对话状态"""
        state = await get_agent_state(self.config.character_id, user_id)
        if state:
            # 使用 EmotionService 恢复状态
            self.emotion.restore_full_state(state["pad_state"])
            self.turn_count = state.get("turn_count", 0)
            logger.info("已恢复对话状态，轮次: %d", self.turn_count)

    async def save_state(self, user_id: str):
        """保存对话状态到数据库"""
        await upsert_agent_state(
            character_id=self.config.character_id,
            user_id=user_id,
            pad_state=self.emotion.get_full_state(),
            turn_count=self.turn_count,
        )

    async def chat(self, user_input: str, user_id: str) -> str:
        """
        统一对话接口

        Args:
            user_input: 用户输入文本
            user_id: 用户标识
        Returns:
            角色台词
        """
        turn_context = await self._build_turn_context(user_input, user_id)
        first_pass = None

        try:
            first_pass = await self._generate_first_pass(turn_context, stream=False)
            return first_pass["expression"]
        finally:
            # 确保状态被保存
            if first_pass:
                await self._finalize_turn(
                    turn_context=turn_context,
                    final_result=first_pass,
                    stream_mode=False,
                )
                await self._schedule_tool_followup_if_needed(turn_context, first_pass)

    async def chat_stream(self, user_input: str, user_id: str):
        """
        流式对话接口，yield 每个文本片段（expression 部分）
        
        ⭐ 真正的流式处理：T 标签完成后立即推送台词到 TTS，不等待整个响应完成。

        Args:
            user_input: 用户输入文本
            user_id: 用户标识
        Yields:
            文本片段字符串或metadata字典
        """
        turn_context = await self._build_turn_context(user_input, user_id)
        
        # ⭐ 收集内部结果用于后续处理
        internal_result = None
        
        try:
            # ⭐ 使用流式生成器，边生成边 yield
            async for item in self._generate_first_pass_stream(turn_context):
                # 检查是否为内部结果（用于后续处理，不推送到 TTS）
                if isinstance(item, dict) and item.get("type") == "_internal_result":
                    internal_result = item
                    continue
                # ⭐ 直接 yield 流式数据到 TTS
                yield item
        finally:
            # 确保状态被保存
            logger.info("[EmotionalAgent] chat_stream finally 块执行")
            
            if internal_result:
                await self._finalize_turn(
                    turn_context=turn_context,
                    final_result=internal_result,
                    stream_mode=True,
                )
                followup_scheduled = await self._schedule_tool_followup_if_needed(
                    turn_context, internal_result
                )
                internal_result["metadata"]["followup_scheduled"] = followup_scheduled
                
                # 注意：如果被打断，最后的 metadata 可能无法送达下游
                # 但状态已经保存，不影响下一轮对话
                try:
                    yield internal_result["metadata"]
                except (GeneratorExit, asyncio.CancelledError):
                    logger.debug("[EmotionalAgent] chat_stream 被中断，metadata 无法发送")
            else:
                logger.warning("[EmotionalAgent] chat_stream 未收到内部结果，跳过后续处理")

    async def chat_stream_speculative(self, user_input: str, user_id: str):
        """
        投机采样专用：执行 LLM 生成 + 预先动作匹配，不执行其他后续处理。
        
        ⭐ 核心改动：真正流式生成，边生成边 yield
        - 文本 chunk 立即 yield（真正流式推送到 TTS）
        - action 事件边生成边收集
        - 流结束后执行动作匹配，保存上下文
        
        ⭐ 关键区别：
        - ✅ 执行 LLM 生成（真正流式）
        - ✅ 预先执行 A 标签动作匹配（消除确认后的动作匹配延迟）
        - ❌ 不更新 PAD 状态（保存 emotion_delta，确认后应用）
        - ❌ 不调用外部工具（保存 tool_prompt，确认后调用）
        - ❌ 不保存轮次/记忆
        
        确认后调用 apply_speculative_result 快速完成剩余处理。
        
        Args:
            user_input: 用户输入文本
            user_id: 用户标识
        Yields:
            文本片段字符串或metadata字典
        """
        turn_context = await self._build_turn_context(user_input, user_id)
        
        # ⭐ 边生成边 yield + 边收集（真正流式）
        expression_chunks: list[str] = []
        action_events: list[dict[str, str | None]] = []
        metadata = None
        tool_prompt = None
        
        try:
            async for item in generate_unified_stream(
                llm_client=self.llm,
                system_prompt=turn_context["system_prompt"],
                memory_context=turn_context["memories"]["combined"],
                dynamic_context=turn_context.get("dynamic_context", ""),
                conversation_history=turn_context.get("conversation_history", ""),
            ):
                if isinstance(item, dict):
                    item_type = item.get("type")
                    
                    # 处理 tool_prompt 类型（内部收集，不 yield）
                    if item_type == "tool_prompt":
                        tool_prompt = item.get("tool_prompt")
                        logger.info("[EmotionalAgent] 投机模式收到 tool_prompt: %s", tool_prompt)
                        continue
                    
                    # 处理 emotion_delta 类型（⭐ 立即 yield）
                    if item_type == "emotion_delta":
                        yield item
                        continue
                    
                    # ⭐ 处理 action_data 类型（新的统一动作处理格式）
                    if item_type == "action_data":
                        action_data = item.get("action_data")
                        trigger_context = item.get("trigger_context", "")
                        if action_data:
                            action_events.append({
                                "action_data": action_data,
                                "trigger_context": trigger_context,
                            })
                        # 不 yield 到下游，确认后统一处理
                        continue
                    
                    # 旧的 action 类型（兼容）
                    if item_type == "action":
                        logger.debug("[EmotionalAgent] 投机模式收到旧的 action 事件，已忽略")
                        continue
                    
                    # meta 类型（最终的情绪元数据，内部收集）
                    if item_type == "meta":
                        metadata = item
                        continue
                    
                    # 未知 dict 类型，透传
                    yield item
                    continue
                
                # 字符串类型：台词文本
                expression_chunks.append(item)
                # ⭐ 立即 yield 到 TTS（真正流式）
                yield item
            
        except (GeneratorExit, asyncio.CancelledError):
            logger.debug("[EmotionalAgent] chat_stream_speculative 被中断")
            raise
        
        # ⭐ 流结束后，执行动作匹配 + 保存上下文
        finally:
            # 构建完整结果
            expression = "".join(expression_chunks)
            
            if metadata is None:
                metadata = {
                    "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
                    "trigger_keywords": [],
                    "inner_monologue": "",
                    "motion": None,
                }
            
            metadata["tool_prompt"] = tool_prompt
            metadata["used_tool"] = False
            metadata["followup_scheduled"] = False
            
            # ⭐ 预先执行动作处理（使用新的统一模块）
            if action_events:
                from app.agent.action.processor import process_actions_batch
                await process_actions_batch(
                    action_events=action_events,  # ⭐ 传完整结构，包含 trigger_context
                    expression=expression,
                )
                logger.info(
                    "[EmotionalAgent] 投机采样动作处理完成，处理数: %d",
                    len(action_events)
                )
            
            # 构建完整 first_pass 结果
            first_pass = {
                "metadata": metadata,
                "expression": expression,
                "action_events": action_events,
                "stream_items": expression_chunks,
            }
            
            # ⭐ 投机模式：保存所有预处理结果，确认后快速应用
            logger.info("[EmotionalAgent] chat_stream_speculative 完成，等待确认")
            
            # 保存上下文和预处理结果
            metadata["turn_context"] = turn_context
            metadata["first_pass"] = first_pass
            metadata["matched_motion_by_phrase"] = {}  # 动作已在 process_actions_batch 中处理
            metadata["expression"] = expression
            
            try:
                yield metadata
            except (GeneratorExit, asyncio.CancelledError):
                logger.debug("[EmotionalAgent] chat_stream_speculative metadata 无法发送")

    async def apply_speculative_result(
        self,
        user_id: str,
        turn_context: dict[str, Any],
        first_pass: dict[str, Any],
    ) -> bool:
        """
        应用投机采样结果：执行后续处理（状态更新、工具调用）。
        
        当投机缓存被确认使用时调用此方法。
        
        ⭐ 优化：使用预先匹配的动作结果，跳过动作匹配延迟。
        
        Args:
            user_id: 用户标识
            turn_context: 从投机结果中恢复的对话上下文
            first_pass: 从投机结果中恢复的第一阶段结果
            
        Returns:
            是否安排了工具补播
        """
        logger.info("[EmotionalAgent] 应用投机结果，执行后续处理")
        
        # 1. 执行 _finalize_turn（使用预先匹配的动作结果，快速处理）
        # ⭐ use_precomputed_match=True 跳过动作匹配，直接使用预计算结果
        await self._finalize_turn(
            turn_context=turn_context,
            final_result=first_pass,
            stream_mode=True,
            use_precomputed_match=True,
        )
        
        # 2. 执行工具调用（如果需要）
        followup_scheduled = await self._schedule_tool_followup_if_needed(
            turn_context, first_pass
        )
        first_pass["metadata"]["followup_scheduled"] = followup_scheduled
        
        # 3. 保存状态
        await self.save_state(user_id)
        
        logger.info(
            "[EmotionalAgent] 投机结果已应用，followup_scheduled=%s",
            followup_scheduled
        )
        
        return followup_scheduled

    async def _build_turn_context(self, user_input: str, user_id: str) -> dict[str, Any]:
        """构建本轮对话所需的动态上下文（严格动静分离）

        静态内容（static_system_prompt）已在初始化时构建，不再变化。
        此方法只构建动态内容：
        - memories: 短期记忆检索（最近聊天、心动时刻、特殊事件）
        - dynamic_context: 当前 PAD 状态
        - conversation_history: 对话历史
        """
        import time
        t0 = time.monotonic()
        logger.info("[Timing] _build_turn_context 开始")

        character_id = self.config.character_id
        
        # Step 1: 短期记忆检索（FTS 匹配 + 上下文获取）
        t1 = time.monotonic()
        memories = await retrieve_short_term_memories(character_id, user_id, user_input)
        logger.info(f"[Timing] retrieve_short_term_memories: {(time.monotonic()-t1)*1000:.0f}ms")
        
        # Step 2: 动态上下文（使用新的 EmotionService）
        t2 = time.monotonic()
        dynamic_context = build_dynamic_context(self.emotion)
        logger.info(f"[Timing] build_dynamic_context: {(time.monotonic()-t2)*1000:.0f}ms")

        # Step 3: 对话历史
        t3 = time.monotonic()
        conversation_history = await self._get_conversation_history(user_id)
        logger.info(f"[Timing] _get_conversation_history: {(time.monotonic()-t3)*1000:.0f}ms")
        
        total_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[Timing] _build_turn_context 总计: {total_ms:.0f}ms")

        return {
            "character_id": character_id,
            "user_id": user_id,
            "user_input": user_input,
            "memories": memories,
            "system_prompt": self.static_system_prompt,  # 静态，不变
            "dynamic_context": dynamic_context,           # 动态，每轮变化
            "conversation_history": conversation_history,  # 动态，每轮变化
        }
    
    async def _get_conversation_history(self, user_id: str) -> str:
        """从 Redis 读取聊天记录"""
        try:
            from app.services.chat_history import get_conversation_buffer
            buffer = await get_conversation_buffer(user_id)
            history = await buffer.get_formatted_history(
                max_items=10,
                format_style="user_assistant"
            )
            if history:
                logger.debug(f"[EmotionalAgent] 聊天记录长度: {len(history)} chars")
            return history
        except Exception as e:
            logger.warning(f"[EmotionalAgent] 读取聊天记录失败: {e}")
            return ""

    async def _generate_first_pass(
        self,
        turn_context: dict[str, Any],
        stream: bool,
    ) -> dict[str, Any]:
        """执行第一阶段：判断是否需要工具（严格动静分离）

        静态 system_prompt 在初始化时构建，不再变化。
        动态 user_prompt 每轮构建（PAD状态、记忆、对话历史）。
        """
        if not stream:
            result = await generate_unified(
                llm_client=self.llm,
                system_prompt=turn_context["system_prompt"],
                memory_context=turn_context["memories"]["combined"],
                dynamic_context=turn_context.get("dynamic_context", ""),
                conversation_history=turn_context.get("conversation_history", ""),
            )
            return {
                "metadata": result,
                "expression": result["expression"],
                "action_events": [],
                "stream_items": [],
            }

        metadata = None
        tool_prompt = None  # ⭐ 新增：单独存储 tool_prompt
        expression_chunks: list[str] = []
        action_events: list[dict[str, str | None]] = []
        visible_stream_items: list[str | dict[str, Any]] = []

        async for item in generate_unified_stream(
            llm_client=self.llm,
            system_prompt=turn_context["system_prompt"],
            memory_context=turn_context["memories"]["combined"],
            dynamic_context=turn_context.get("dynamic_context", ""),
            conversation_history=turn_context.get("conversation_history", ""),
        ):
            if isinstance(item, dict):
                item_type = item.get("type")
                
                # ⭐ 新增：处理 tool_prompt 类型
                if item_type == "tool_prompt":
                    tool_prompt = item.get("tool_prompt")
                    logger.info("[EmotionalAgent] 收到 tool_prompt: %s", tool_prompt)
                    continue
                    
                # ⭐ 处理 action_data 类型（新的统一动作处理格式）
                if item_type == "action_data":
                    action_data = item.get("action_data")
                    trigger_context = item.get("trigger_context", "")
                    if action_data:
                        action_events.append({
                            "action_data": action_data,
                            "trigger_context": trigger_context,
                        })
                    # 不添加到 visible_stream_items，内部收集
                    continue
                
                # 旧的 action 类型（兼容）
                if item_type == "action":
                    logger.debug("[EmotionalAgent] _generate_first_pass 收到旧的 action 事件，已忽略")
                    continue

                # meta 类型（最终的情绪元数据）
                metadata = item
                continue

            expression_chunks.append(item)
            visible_stream_items.append(item)

        if metadata is None:
            metadata = {
                "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
                "trigger_keywords": [],
                "inner_monologue": "",
                "motion": None,
            }
        
        # ⭐ 新增：将 tool_prompt 合并到 metadata 中（兼容原有逻辑）
        metadata["tool_prompt"] = tool_prompt

        expression = "".join(expression_chunks)
        metadata["used_tool"] = False
        metadata["followup_scheduled"] = False
        return {
            "metadata": metadata,
            "expression": expression,
            "action_events": action_events,
            "stream_items": visible_stream_items,
        }

    async def _generate_first_pass_stream(
        self,
        turn_context: dict[str, Any],
    ) -> AsyncGenerator[str | dict, None]:
        """真正的流式生成：边 yield 边收集
        
        ⭐ 关键改进：T 标签完成后立即开始推送台词流，不等待整个响应完成。
        
        Yields:
            str: 台词文本片段（直接推送到 TTS）
            dict: 各种事件
                - {"type": "emotion_delta", "emotion_delta": {...}}: 情绪增量（设置 TTS 情绪）
                - {"type": "action_data", "action_data": str, "trigger_context": str}: 动作数据
                - {"type": "_internal_result", ...}: 内部收集结果（用于后续处理，不推送到 TTS）
        """
        metadata = None
        tool_prompt = None
        expression_chunks: list[str] = []
        action_events: list[dict[str, str | None]] = []
        
        async for item in generate_unified_stream(
            llm_client=self.llm,
            system_prompt=turn_context["system_prompt"],
            memory_context=turn_context["memories"]["combined"],
            dynamic_context=turn_context.get("dynamic_context", ""),
            conversation_history=turn_context.get("conversation_history", ""),
        ):
            if isinstance(item, dict):
                item_type = item.get("type")
                
                # 处理 tool_prompt 类型
                if item_type == "tool_prompt":
                    tool_prompt = item.get("tool_prompt")
                    logger.info("[EmotionalAgent] 收到 tool_prompt: %s", tool_prompt)
                    # ⭐ 不 yield tool_prompt，内部收集
                    continue
                
                # 处理 emotion_delta 类型（⭐ 立即 yield，设置 TTS 情绪）
                if item_type == "emotion_delta":
                    yield item
                    continue
                
                # ⭐ 处理 action_data 类型（新的统一动作处理格式）
                if item_type == "action_data":
                    action_data = item.get("action_data")
                    trigger_context = item.get("trigger_context", "")
                    if action_data:
                        action_events.append({
                            "action_data": action_data,
                            "trigger_context": trigger_context,
                        })
                    # 不 yield 到下游，由 _process_stream 统一处理
                    continue
                
                # 旧的 action 类型（兼容）
                if item_type == "action":
                    logger.debug("[EmotionalAgent] 收到旧的 action 事件，已忽略")
                    continue
                
                # meta 类型（最终的情绪元数据）
                if item_type == "meta":
                    metadata = item
                    # ⭐ 不 yield meta，内部收集
                    continue
                
                # 未知 dict 类型，透传
                yield item
                continue
            
            # 字符串类型：台词文本
            expression_chunks.append(item)
            # ⭐ 立即 yield 到 TTS
            yield item
        
        # 流结束后，构建内部结果用于后续处理
        if metadata is None:
            metadata = {
                "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
                "trigger_keywords": [],
                "inner_monologue": "",
                "motion": None,
            }
        
        # 合并 tool_prompt 到 metadata
        metadata["tool_prompt"] = tool_prompt
        metadata["used_tool"] = False
        metadata["followup_scheduled"] = False
        
        # 构建完整的内部结果
        internal_result = {
            "type": "_internal_result",  # 特殊标记，用于内部通信
            "metadata": metadata,
            "expression": "".join(expression_chunks),
            "action_events": action_events,
        }
        
        # ⭐ yield 内部结果（chat_stream 会识别并收集，不推送到 TTS）
        yield internal_result

    async def _schedule_tool_followup_if_needed(
        self,
        turn_context: dict[str, Any],
        first_pass: dict[str, Any],
    ) -> bool:
        """如有 tool_prompt，则在后台异步执行后续补播。"""
        metadata = first_pass["metadata"]
        tool_prompt = metadata.get("tool_prompt")
        if not tool_prompt:
            metadata["used_tool"] = False
            return False

        metadata["used_tool"] = False
        task = asyncio.create_task(
            self._run_tool_followup(
                turn_context=turn_context,
                first_pass_meta=dict(metadata),
                tool_prompt=tool_prompt,
            )
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        logger.info("检测到工具轮次，已转入后台异步执行: %s", tool_prompt)
        return True

    async def _run_tool_followup(
        self,
        turn_context: dict[str, Any],
        first_pass_meta: dict[str, Any],
        tool_prompt: str,
    ) -> None:
        """后台执行工具调用，并在完成后补播结果。
        
        ⭐ 新任务系统流程：
        - 工具调用和二次处理由 TaskSystem 完成
        - 播报由 PlaybackScheduler 的 Redis 队列 + 1s 定时轮询触发
        - 此方法只负责提交任务，播报由队列机制自动处理
        """
        # ⭐ 使用新任务系统
        if self._task_system:
            try:
                # 提交任务到 TaskSystem
                task_id = await self._task_system.submit(
                    tool_prompt=tool_prompt,
                    provider_name="openclaw",
                    context={
                        "user_input": turn_context.get("user_input", ""),
                        "character_id": turn_context.get("character_id", ""),
                    },
                )
                logger.info(f"[TaskSystem] 任务已提交: {task_id[:8]}...")
                
                # ⭐ 播报由 PlaybackScheduler 自动处理
                # 不需要等待结果，TaskSystem 会自动：
                # 1. 执行工具调用（OpenClaw Provider）
                # 2. 执行二次处理（PostProcessor）
                # 3. 入播报队列（PlaybackScheduler）
                
            except Exception as e:
                logger.error(f"[TaskSystem] 任务提交失败: {e}")
            return
        
        # 任务系统未初始化
        logger.warning("[TaskSystem] 任务系统未初始化，无法执行工具调用")

    async def _execute_single_delegated_tool_call(self, tool_prompt: str) -> dict[str, Any]:
        """执行单次工具委托调用（新任务系统）

        ⭐ 使用 TaskSystem 执行工具调用：
        - 统一的任务管理
        - Provider 协调
        - 自动二次处理
        - 自动播报入队
        """
        if not self._task_system:
            logger.error("[TaskSystem] 任务系统未初始化，无法调用")
            return {
                "ok": False,
                "session_key": "",
                "content": "",
                "panel_html": None,
                "error": "任务系统未初始化",
            }

        try:
            logger.info("[TaskSystem] 提交工具调用任务")

            # 提交任务
            task_id = await self._task_system.submit(
                tool_prompt=tool_prompt,
                provider_name="openclaw",
                context={},
            )
            logger.info(f"[TaskSystem] 任务已提交: {task_id[:8]}...")

            # 等待播报结果
            broadcast = await self._task_system.wait_for_broadcast(
                task_id,
                timeout=settings.OPENCLAW_TIMEOUT,
            )

            logger.info(f"[TaskSystem] 任务完成: {task_id[:8]}...")

            # 返回标准格式
            return {
                "ok": True,
                "session_key": "",
                "content": broadcast.content,
                "panel_html": broadcast.panel_html,
                "error": None,
            }

        except Exception as e:
            logger.exception(f"[TaskSystem] 调用失败: {e}")
            return {
                "ok": False,
                "session_key": "",
                "content": "",
                "panel_html": None,
                "error": str(e),
            }

    async def _run_second_pass_with_tool_result(
        self,
        turn_context: dict[str, Any],
        first_pass_meta: dict[str, Any],
        tool_prompt: str,
        tool_result: dict[str, Any],
    ) -> tuple[str, dict | None] | str:
        """根据工具结果重写最终播报文本。返回 (expression, panel_html) 或仅 expression。"""
        # 获取 panel_html（从 OpenClaw 返回）
        panel_html = tool_result.get("panel_html")
        
        # 提取 panel 的 html 内容（让 AI 理解展示信息）
        panel_html_content = None
        if panel_html:
            panel_html_content = panel_html.get("html", "")
        
        # 简化后的调用：只传入必要参数
        prompt = build_tool_rewrite_prompt(
            user_input=turn_context["user_input"],
            tool_result=tool_result.get("content") or tool_result.get("error") or "",
            panel_html_content=panel_html_content,
            config=self.config,
        )
        
        # 二次处理不使用 system_prompt，避免输出 <meta> 标签
        messages = [
            {"role": "user", "content": prompt},
        ]
        try:
            response = await self.llm.chat(messages, temperature=0.7)
            response = response.strip()
            if response:
                # 解析 <a> 动作标签（不再需要解析 <panel> 标签）
                expression = response
                
                # panel_html 直接使用 OpenClaw 返回的（程序处理位置）
                if expression:
                    if panel_html:
                        return expression, panel_html
                    return expression
        except Exception:
            logger.exception("工具结果二次重写失败")

        if tool_result.get("ok"):
            return tool_result.get("content", "") or "我已经帮你处理好了。"
        return "我刚才尝试处理这件事，但这次没有成功，稍后再试一次吧。"

    async def _finalize_turn(
        self,
        turn_context: dict[str, Any],
        final_result: dict[str, Any],
        stream_mode: bool,
        use_precomputed_match: bool = False,
    ) -> None:
        """统一做动作匹配、PAD、状态保存收尾。
        
        Args:
            turn_context: 对话上下文
            final_result: 第一阶段结果
            stream_mode: 是否流式模式
            use_precomputed_match: 是否使用预先计算的动作匹配结果（投机采样确认后使用）
        """
        metadata = final_result["metadata"]
        expression = final_result["expression"]

        # ⭐ 动作处理已在流式处理时由 emotional_agent.py 完成
        # 如果有预先匹配的结果，直接使用
        matched_motion_by_phrase = metadata.get("matched_motion_by_phrase", {})
        if use_precomputed_match and matched_motion_by_phrase:
            logger.info(
                "[EmotionalAgent] 使用预先匹配的动作结果，匹配数: %d",
                len(matched_motion_by_phrase)
            )
        
        # ⭐ 如果 action_events 存在（新格式），调用统一处理模块
        action_events = final_result.get("action_events", [])
        if action_events and not matched_motion_by_phrase:
            from app.agent.action.processor import process_actions_batch
            await process_actions_batch(
                action_events=action_events,  # ⭐ 传完整结构，包含 trigger_context
                expression=expression,
            )
            logger.info(
                "[EmotionalAgent] _finalize_turn 动作处理完成，处理数: %d",
                len(action_events)
            )
        
        metadata["matched_motion_by_phrase"] = matched_motion_by_phrase
        metadata["expression"] = expression

        emotion_delta = metadata.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0})
        emotion_intensity = self.emotion.intensity(emotion_delta)
        # 使用 EmotionService 更新（物理模拟）
        self.emotion.update(emotion_delta)
        logger.info(
            "PAD 更新: %s | delta=%s | intensity=%.3f",
            self.emotion, emotion_delta, emotion_intensity,
        )

        # 更新轮次计数
        self.turn_count += 1
        logger.info(
            f"[EmotionalAgent] 对话轮次已更新: turn={self.turn_count}"
        )

        task = asyncio.create_task(
            self._write_memories(
                turn_context["character_id"],
                turn_context["user_id"],
                metadata,
                emotion_delta,
                emotion_intensity,
                turn_context["user_input"],
                expression,
            ),
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

        await self.save_state(turn_context["user_id"])

        if stream_mode:
            metadata["type"] = "meta"

    # ⭐ 以下方法已迁移到 app/agent/action/processor.py:
    # - _match_actions
    # - _match_single_action  
    # - _build_action_events_from_expression

    async def _write_memories(
        self,
        character_id: str,
        user_id: str,
        monologue_result: dict,
        emotion_delta: dict,
        emotion_intensity: float,
        user_input: str,
        expression: str,
    ):
        """后台写入情绪记忆（用户信息提取由 Redis 批量机制处理）"""
        try:
            await write_emotion_memory(
                character_id=character_id,
                user_id=user_id,
                inner_monologue=monologue_result.get("inner_monologue", ""),
                pad_delta=emotion_delta,
                emotion_intensity=emotion_intensity,
                trigger_keywords=monologue_result.get("trigger_keywords", []),
            )
            # 用户信息提取已移至 Redis 批量提取机制（conversation_buffer.py）
        except Exception:
            logger.exception("记忆写入失败")


# ──────────── pipecat 集成入口 ────────────

_agent_instances: dict[str, EmotionalAgent] = {}


async def get_agent(user_id: str = "default") -> EmotionalAgent:
    """获取或创建 Agent 实例（严格动静分离）

    初始化流程：
    1. 加载角色配置
    2. 创建 LLM 客户端
    3. 构建完整静态 system_prompt（包含全量动作标签）
    4. 创建 EmotionalAgent 实例
    5. 加载用户状态
    6. 启动缓存心跳

    static_system_prompt 在初始化时构建一次，之后永远不变。
    """
    if user_id not in _agent_instances:
        config = load_character()
        llm = LLMClient()

        # 构建完整静态 System Prompt（包含全量动作标签）
        static_system_prompt = build_static_system_prompt(config)
        logger.info(
            "[get_agent] 静态 System Prompt 已构建，长度: %d chars",
            len(static_system_prompt),
        )

        # 创建 Agent 实例（传入已构建好的静态 prompt）
        agent = EmotionalAgent(config, llm, static_system_prompt)
        await agent.load_state(user_id)
        _agent_instances[user_id] = agent

        # 启动缓存心跳（系统初始化后立即开始保活）
        await agent.start_heartbeat()

    return _agent_instances[user_id]


async def chat(user_input: str, user_id: str = "default") -> str:
    """
    统一调用接口

    Usage:
        from app.agent.main import chat
        reply = await chat("你好呀", user_id="user_123")
    """
    agent = await get_agent(user_id)
    return await agent.chat(user_input, user_id)


# ──────────── CLI 交互模式 ────────────

async def cli_main():
    """命令行交互模式"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("src.llm.unified").setLevel(logging.DEBUG)

    print("正在初始化数据库...")
    await init_db()

    config = load_character()
    print(f"角色已加载: {config.name} ({config.character_id})")

    user_id = "cli_user"
    agent = await get_agent(user_id)

    print(f"\n{'=' * 50}")
    print(f"  {config.name} 已准备好与你对话")
    print("  输入 'quit' 退出 | 输入 'pad' 查看情绪状态")
    print(f"{'=' * 50}\n")

    try:
        while True:
            user_input = input("你：").strip()
            if not user_input:
                continue
            if user_input.lower() == "quit":
                print("再见~")
                break
            if user_input.lower() == "pad":
                print(f"  当前情绪状态: {agent.emotion}")
                print(f"  详细信息: {agent.emotion.get_summary()}")
                continue

            reply = ""
            print(f"\n{config.name}：", end="", flush=True)
            async for chunk in agent.chat_stream(user_input, user_id):
                if isinstance(chunk, str):
                    print(chunk, end="", flush=True)
                    reply += chunk
            print("\n")

    finally:
        if agent._bg_tasks:
            await asyncio.gather(*agent._bg_tasks, return_exceptions=True)
        await close_pool()


if __name__ == "__main__":
    asyncio.run(cli_main())