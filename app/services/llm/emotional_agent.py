"""EmotionalAgent LLM 服务 - 将 EmotionalAgent 包装为 pipecat LLMService。

替代 OpenClaw WebSocket LLM，直接使用本地 EmotionalAgent 进行对话。
EmotionalAgent 内部管理情绪状态、记忆检索、流式生成等。

⭐ 统一流处理接口（UnifiedStreamProcessor）：
- 处理四种情况：LLM已完成、LLM运行中、LLM未到达、投机未命中
- 统一推送逻辑：推送到TTS、处理action、处理emotion_delta
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Optional

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesFrame,
    StartFrame,
    EndFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings


class EmotionalAgentLLMService(LLMService):
    """将 EmotionalAgent 包装为 pipecat LLMService。

    Args:
        character_config_path: 角色配置文件路径
        user_id: 用户标识
    """

    def __init__(
        self,
        *,
        character_config_path: str = "config/characters/daji.json",
        user_id: str = "default_user",
        conversation_buffer=None,
        **kwargs,
    ):
        # 初始化所有 LLM 设置字段为 None（EmotionalAgent 不使用外部 LLM API 参数）
        llm_settings = LLMSettings(
            model=None,
            system_instruction=None,
            temperature=None,
            max_tokens=None,
            top_p=None,
            top_k=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            filter_incomplete_user_turns=None,
            user_turn_completion_config=None,
        )
        super().__init__(settings=llm_settings, **kwargs)
        self._character_config_path = character_config_path
        self._user_id = user_id
        self._agent = None
        self._buffer = conversation_buffer  # ConversationBuffer 实例

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._init_agent()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)

    async def _init_agent(self):
        """初始化 EmotionalAgent 实例（严格动静分离）"""
        try:
            from app.agent.main import EmotionalAgent
            from app.agent.character.loader import load_character
            from app.agent.llm.client import LLMClient
            from app.agent.db.connection import init_db
            from app.agent.prompt.system_prompt import build_static_system_prompt
            from app.services.motion_catalog_service import get_motion_catalog_service

            # 确保 agent 表已创建
            try:
                await init_db()
            except Exception as e:
                logger.warning(f"[EmotionalAgentLLM] Agent DB init: {e}")

            config = load_character(self._character_config_path)
            llm_client = LLMClient()

            # 构建完整静态 System Prompt（包含全量动作标签）
            static_system_prompt = build_static_system_prompt(config)
            logger.info(
                "[EmotionalAgentLLM] 静态 System Prompt 已构建，长度: %d chars",
                len(static_system_prompt),
            )

            # 创建 Agent 实例（传入已构建好的静态 prompt）
            self._agent = EmotionalAgent(config, llm_client, static_system_prompt)
            await self._agent.load_state(self._user_id)

            # ⭐ 注入 TaskSystem 到 EmotionalAgent（用于工具调用）
            from app.task_system import get_task_system
            task_system = get_task_system()
            self._agent._task_system = task_system
            logger.info("[EmotionalAgentLLM] TaskSystem 已注入到 Agent")

            # 启动缓存心跳（系统初始化后立即开始保活）
            await self._agent.start_heartbeat()

            # ⭐ 设置投机采样器的 Agent 引用
            from app.services.speculative_sampler import get_speculative_sampler
            sampler = get_speculative_sampler()
            sampler.set_agent(self._agent)
            logger.info("[EmotionalAgentLLM] 投机采样器已设置 Agent 引用")

            logger.info(
                f"[EmotionalAgentLLM] Agent 初始化完成: "
                f"{config.name} (user={self._user_id})"
            )

            # ⭐ 通知初始化门控：LLM Agent 就绪
            from app.services.init_gate import get_init_gate
            get_init_gate().mark_ready("llm_agent")

        except Exception as e:
            logger.error(f"[EmotionalAgentLLM] Agent 初始化失败: {e}")
            import traceback
            traceback.print_exc()

    def _extract_last_user_message(self, messages: list) -> str:
        """从消息列表中提取最后一条用户消息"""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    return " ".join(parts).strip()
        return ""

    async def _process_context(self, messages: list):
        """处理对话上下文，调用 EmotionalAgent 生成回复
        
        ⭐ 统一流处理接口（四种情况）：
        1. 状态A（LLM已完成）：直接推送缓存，执行后续处理
        2. 状态B（LLM运行中）：先推送已有缓存，实时监听后续
        3. 状态C（LLM未到达）：等待数据到达处理
        4. 状态D（投机未命中）：发起新请求，等待数据到达处理
        
        ⭐ 所有情况都走统一的 `_process_stream` 方法
        """
        # 1. 提取当前 user 消息
        current_user_message = self._extract_last_user_message(messages)
        if not current_user_message:
            logger.warning("[EmotionalAgentLLM] 未找到 user 消息，跳过")
            return

        if not self._agent:
            logger.error("[EmotionalAgentLLM] Agent 未初始化")
            return

        logger.info(f"[EmotionalAgentLLM] 收到: {current_user_message[:80]}")

        # ⭐ 获取投机采样器
        from app.services.speculative_sampler import get_speculative_sampler, SpeculativeRequest
        sampler = get_speculative_sampler()
        
        # ⭐ 获取投机结果（简化接口）
        cached_result = None
        speculative_request = None
        
        try:
            cached_result, speculative_request, need_wait = await sampler.get_result_for_final(current_user_message)
        except Exception as e:
            logger.warning(f"[EmotionalAgentLLM] 投机采样器查询失败: {e}")
        
        # ⭐ 确定流来源和后续处理参数
        stream_source: AsyncGenerator = None
        matched_motion_by_phrase: dict = {}
        turn_context: dict = None
        first_pass: dict = None
        is_speculative = False
        
        # 状态A：LLM已完成
        if speculative_request and speculative_request.state == "completed":
            logger.info(f"[EmotionalAgentLLM] 状态A: 使用完整缓存! text={current_user_message[:40]}...")
            stream_source = self._iter_cached_items(speculative_request)
            matched_motion_by_phrase = speculative_request.matched_motion_by_phrase
            turn_context = speculative_request.turn_context
            first_pass = speculative_request.first_pass
            is_speculative = True
        
        # 状态B：LLM运行中
        elif speculative_request and speculative_request.state == "running":
            logger.info(f"[EmotionalAgentLLM] 状态B: 实时监听投机LLM输出! text={current_user_message[:40]}...")
            stream_source = self._listen_running_request(speculative_request)
            # matched_motion_by_phrase、turn_context、first_pass 在流结束后从 request 获取
            is_speculative = True
        
        # 状态D：投机未命中
        else:
            logger.info(f"[EmotionalAgentLLM] 状态D: 无缓存,正常调用LLM")
            stream_source = self._agent.chat_stream(current_user_message, self._user_id)
            is_speculative = False
        
        # ⭐ 统一流处理
        await self._process_stream(
            stream_source=stream_source,
            speculative_request=speculative_request,
            is_speculative=is_speculative,
        )
        
        # ⭐ 清理本轮投机请求
        try:
            await sampler.on_turn_end()
        except Exception:
            pass
    
    async def _iter_cached_items(self, request: "SpeculativeRequest") -> AsyncGenerator:
        """从已完成的投机请求中迭代缓存项
        
        状态A专用：遍历 request.stream_items
        """
        for item in request.stream_items:
            yield item
    
    async def _listen_running_request(self, request: "SpeculativeRequest") -> AsyncGenerator:
        """实时监听正在运行的投机请求
        
        状态B专用：先推送已有缓存，再实时监听后续
        """
        from app.services.speculative_sampler import get_speculative_sampler
        sampler = get_speculative_sampler()
        
        # 使用采样器的 stream_from_running_request 方法
        async for item in sampler.stream_from_running_request(request):
            yield item
    
    async def _process_stream(
        self,
        stream_source: AsyncGenerator,
        speculative_request: Optional["SpeculativeRequest"] = None,
        is_speculative: bool = False,
    ):
        """统一流处理方法
        
        ⭐ 处理所有流式数据（四种情况统一处理）：
        - 文本 chunk → 推送到TTS
        - emotion_delta → 设置TTS情绪
        - action → 收集并使用预匹配结果
        - meta → 保存后续处理参数
        
        Args:
            stream_source: 流数据来源（缓存、实时监听、新请求）
            speculative_request: 投机请求对象（用于获取预匹配结果）
            is_speculative: 是否为投机请求（决定后续处理方式）
        """
        await self.start_ttfb_metrics()
        first_chunk = True
        pending_actions: list[dict] = []
        final_metadata: dict = {}
        
        # ⭐ 收集动作数据（用于投机采样缓存）
        action_data_list: list[dict] = []
        
        try:
            async for item in stream_source:
                # 文本 chunk
                if isinstance(item, str):
                    if first_chunk:
                        await self.stop_ttfb_metrics()
                        first_chunk = False
                    
                    # 推送到 TTS
                    await self._push_llm_text(item)
                    
                    # 写入 Redis（与 TTS 同步）
                    if self._buffer:
                        await self._buffer.append_assistant_message(item)
                
                # metadata chunk
                elif isinstance(item, dict):
                    item_type = item.get("type")
                    
                    # emotion_delta - 设置 TTS 情绪
                    if item_type == "emotion_delta":
                        emotion_delta = item.get("emotion_delta", {})
                        if emotion_delta:
                            await self._set_tts_emotion(emotion_delta)
                        continue
                    
                    # ⭐ action_data 事件 - 新的统一动作处理格式
                    if item_type == "action_data":
                        action_data = item.get("action_data")
                        trigger_context = item.get("trigger_context", "")
                        if action_data:
                            action_data_list.append({
                                "action_data": action_data,
                                "trigger_context": trigger_context,
                            })
                        continue
                    
                    # 旧的 action 事件（兼容）
                    if item_type == "action":
                        logger.debug("[EmotionalAgentLLM] 收到旧的 action 事件，已忽略")
                        continue
                    
                    # tool_prompt - 不推送到TTS
                    if item_type == "tool_prompt":
                        continue
                    
                    # 最终 metadata（包含 turn_context、first_pass 等）
                    if item_type == "meta" or "turn_context" in item:
                        final_metadata = item
                        continue
            
            # ⭐ 流结束后，处理 action_data_list
            # 调用动作处理模块
            if action_data_list:
                try:
                    from app.agent.action.processor import process_action
                    for action_item in action_data_list:
                        # following_text 使用 trigger_context 作为参考
                        following_text = action_item.get("trigger_context", "")
                        await process_action(
                            action_data=action_item["action_data"],
                            following_text=following_text,
                        )
                    logger.info(f"[Motion] 成功处理 {len(action_data_list)} 个动作数据")
                except Exception as e:
                    logger.error(f"[Motion] 动作处理失败: {e}")
            
            # ⭐ 执行后续处理（投机请求专用）
            if is_speculative and speculative_request:
                turn_context = speculative_request.turn_context or final_metadata.get("turn_context")
                first_pass = speculative_request.first_pass or final_metadata.get("first_pass")
                
                if turn_context and first_pass and self._agent:
                    try:
                        followup_scheduled = await self._agent.apply_speculative_result(
                            user_id=self._user_id,
                            turn_context=turn_context,
                            first_pass=first_pass,
                        )
                        logger.info(
                            f"[EmotionalAgentLLM] 投机后续处理完成, "
                            f"followup_scheduled={followup_scheduled}"
                        )
                    except Exception as e:
                        logger.warning(f"[EmotionalAgentLLM] 投机后续处理失败: {e}")
            
            # ⭐ 非投机请求的后续处理（正常调用）
            if not is_speculative and final_metadata:
                if final_metadata.get("followup_scheduled"):
                    logger.info("[EmotionalAgentLLM] 已安排后台工具补播")
        
        except asyncio.CancelledError:
            logger.warning("[EmotionalAgentLLM] 生成被打断")
            raise

    async def _set_tts_emotion(self, emotion_delta: dict):
        """设置 TTS 情绪指令
        
        将 PAD emotion_delta 转换为情绪指令并设置到 TTS 服务。
        通过 agent_service 获取 TTS 服务实例。
        
        Args:
            emotion_delta: {"P": float, "A": float, "D": float}
        """
        try:
            from app.services.agent_service import get_agent_service
            agent_service = get_agent_service()
            
            if not agent_service:
                logger.warning("[EmotionalAgentLLM] agent_service 未初始化，无法设置 TTS 情绪")
                return
            
            # ⭐ 获取 TTS 服务实例（通过 pipeline）
            tts_service = getattr(agent_service, '_tts_service', None)
            if not tts_service:
                # 尝试从 _init_pipeline 的局部变量获取
                # 注意：TTS 服务在 pipeline 初始化时创建，这里需要通过其他方式获取
                # 方案：在 agent_service 中保存 TTS 服务引用
                logger.warning("[EmotionalAgentLLM] TTS 服务引用未保存，无法设置情绪")
                return
            
            # 调用 TTS 的情绪设置方法
            if hasattr(tts_service, 'set_emotion_from_pad'):
                tts_service.set_emotion_from_pad(emotion_delta)
                logger.info(f"[EmotionalAgentLLM] TTS 情绪已设置: PAD={emotion_delta}")
            else:
                logger.warning("[EmotionalAgentLLM] TTS 服务不支持情绪设置")
                
        except Exception as e:
            logger.error(f"[EmotionalAgentLLM] 设置 TTS 情绪失败: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        messages = None
        if isinstance(frame, LLMContextFrame):
            ctx = frame.context
            messages = ctx.messages if hasattr(ctx, "messages") else []
        elif isinstance(frame, OpenAILLMContext):
            messages = frame.messages
        elif isinstance(frame, LLMMessagesFrame):
            messages = frame.messages
        else:
            await self.push_frame(frame, direction)

        if messages is not None:
            try:
                await self.push_frame(LLMFullResponseStartFrame())
                await self.start_processing_metrics()
                await self._process_context(messages)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[EmotionalAgentLLM] 处理异常: {e}")
                # ⭐ 降级处理：推送固定回复文本，让流程正常完成
                fallback_text = "抱歉，我刚才走神了，你能再说一遍吗？"
                await self._push_llm_text(fallback_text)
                logger.info(f"[EmotionalAgentLLM] 已推送降级回复: {fallback_text}")
                # 写入 Redis
                if self._buffer:
                    try:
                        await self._buffer.append_assistant_message(fallback_text)
                    except Exception as buf_err:
                        logger.warning(f"[EmotionalAgentLLM] 降级回复写入 Redis 失败: {buf_err}")
            finally:
                await self.stop_processing_metrics()
                await self.push_frame(LLMFullResponseEndFrame())
