"""Redis 增强版 LLMUserAggregator

继承 pipecat 新版的 LLMUserAggregator，增加 Redis 聊天记录功能：
1. 接收 ASR 输出并写入 Redis
2. 从 Redis 读取完整历史构建 Prompt
3. 支持 user_mute_strategies 等新特性

优化：
- P2-17: 上下文 getter 并行化（asyncio.gather）
- 使用新版 LLMContext 和 LLMUserAggregator（消除废弃警告）
"""

import asyncio
import time
from typing import Optional, Callable, Awaitable

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    StartFrame,
    EndFrame,
    CancelFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
)
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    LLMUserAggregatorParams,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from app.services.chat_history.conversation_buffer import ConversationBuffer


def _extract_item_id(result) -> Optional[str]:
    """从 ASR result 中提取唯一标识符，兼容多种 ASR 服务
    
    支持的 ASR 服务：
    - Qwen ASR: result 是 dict，使用 item_id 字段
    - Deepgram: result 是 Pydantic 模型 (ListenV1Results)，使用 metadata.request_id
    
    Args:
        result: ASR 返回的结果对象（dict 或 Pydantic 模型）
        
    Returns:
        str: 提取的唯一标识符，如果无法提取则返回 None
    """
    if result is None:
        return None
    
    # 字典格式（Qwen ASR 等）
    if isinstance(result, dict):
        return result.get('item_id')
    
    # Pydantic 模型格式（Deepgram ListenV1Results）
    # Deepgram 使用 metadata.request_id 作为唯一标识
    try:
        if hasattr(result, 'metadata') and result.metadata:
            metadata = result.metadata
            # metadata 可能是 Pydantic 模型或 dict
            if hasattr(metadata, 'request_id'):
                return metadata.request_id
            elif isinstance(metadata, dict):
                return metadata.get('request_id')
    except Exception:
        pass
    
    # 尝试直接获取 item_id 属性（其他可能的格式）
    try:
        if hasattr(result, 'item_id'):
            return result.item_id
    except Exception:
        pass
    
    return None


class RedisHistoryAggregator(LLMUserAggregator):
    """
    Redis 增强版用户消息聚合器（新版）
    
    继承新版 LLMUserAggregator 的功能，并增强：
    1. 将用户消息写入 Redis（基于 item_id 合并）
    2. 从 Redis 读取完整历史
    3. 构建包含完整上下文的 Prompt
    4. 支持 user_mute_strategies 等新特性
    
    数据流向：
    ASR → TranscriptionFrame → Redis（存储）→ 构建完整 Prompt → LLM
    
    新版特性：
    - 使用 LLMContext 替代 messages list
    - 支持 user_mute_strategies（可从外部控制静音）
    - 支持 on_user_mute_started/stopped 事件
    """

    def __init__(
        self,
        context: LLMContext,
        user_id: str = "default_user",
        conversation_buffer: Optional[ConversationBuffer] = None,
        max_history_items: int = 10,
        system_prompt: str = "",
        dynamic_context_getter: Optional[Callable[[], Awaitable[str]]] = None,
        memory_context_getter: Optional[Callable[[], Awaitable[str]]] = None,
        params: Optional[LLMUserAggregatorParams] = None,
        **kwargs
    ):
        """
        Args:
            context: LLMContext 对象（新版）
            user_id: 用户标识
            conversation_buffer: Redis 缓冲区实例
            max_history_items: 最大历史条目数
            system_prompt: 系统提示词
            dynamic_context_getter: 动态上下文获取函数
            memory_context_getter: 记忆上下文获取函数
            params: LLMUserAggregatorParams（新版参数，含 user_mute_strategies）
        """
        super().__init__(context=context, params=params, **kwargs)
        
        self.user_id = user_id
        self.max_history_items = max_history_items
        self.system_prompt = system_prompt
        self.dynamic_context_getter = dynamic_context_getter
        self.memory_context_getter = memory_context_getter
        
        # 使用提供的 buffer 或创建新的
        self._buffer = conversation_buffer or ConversationBuffer(user_id=user_id)
        
        # 标记是否正在处理
        self._is_processing = False
        
        # 保存原始 system prompt 的引用（用于后续更新）
        self._original_system_prompt = system_prompt
        
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """处理帧，统一处理 ASR 中间结果和最终结果
        
        所有 ASR 结果（Interim + Final）都推入 Redis，
        由 ConversationBuffer 的基于 item_id 的覆盖逻辑处理。
        
        新版父类会自动处理：
        - _maybe_mute_frame()：静音时丢弃音频帧
        - UserTurnController：用户说话状态管理
        """
        # 统一处理所有转录帧（中间结果 + 最终结果）
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            text = frame.text or ""
            if text.strip():
                # 从帧的 result 数据中提取 item_id（兼容多种 ASR 服务）
                item_id = _extract_item_id(frame.result)
                
                # 所有结果都推入 Redis，由 ConversationBuffer 基于 item_id 决定覆盖或新建
                await self._buffer.append_user_message(
                    text=text,
                    timestamp=time.time(),
                    item_id=item_id
                )
                
                # 日志区分中间结果和最终结果
                if isinstance(frame, InterimTranscriptionFrame):
                    logger.debug(f"[RedisAgg] ASR中间结果: {text[:60]}... item_id={item_id}")
                else:
                    logger.info(f"[RedisAgg] ASR最终结果: {text[:60]}... item_id={item_id}")

        # 调用父类处理（新版 LLMUserAggregator 会处理静音、聚合等）
        await super().process_frame(frame, direction)
        
        # ⭐ 在 LLM 响应开始前，更新 system prompt
        if isinstance(frame, LLMFullResponseStartFrame):
            await self._update_system_prompt()
            
    async def _update_system_prompt(self):
        """更新 LLMContext 中的 system prompt
        
        在每次 LLM 响应前：
        1. 从 Redis 读取历史
        2. 构建完整上下文
        3. 更新 context.messages[0] 的 system prompt
        """
        if self._is_processing:
            logger.debug("[RedisAgg] 正在处理中，跳过")
            return
            
        self._is_processing = True
        
        try:
            # 1. 构建动态上下文（并行获取）
            context_text = await self._build_context()
            
            # 2. 从 Redis 获取格式化历史
            history = await self._buffer.get_formatted_history(
                max_items=self.max_history_items,
                format_style="you_me"
            )
            
            # 3. 构建完整的 system prompt
            full_system_prompt = self._build_full_system_prompt(context_text, history)
            
            # 4. 更新 LLMContext 中的 system prompt
            messages = self._context.get_messages()
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = full_system_prompt
                logger.info(f"[RedisAgg] System prompt 已更新，长度: {len(full_system_prompt)}")
            elif messages:
                # 如果没有 system prompt，插入一个
                self._context.add_message({"role": "system", "content": full_system_prompt}, index=0)
                logger.info(f"[RedisAgg] System prompt 已插入，长度: {len(full_system_prompt)}")
            
            # 5. 打印最终消息内容（调试）
            logger.debug(f"[RedisAgg] 最终 messages: {len(messages)} 条")
            for i, msg in enumerate(messages[:3]):  # 只打印前3条
                role = msg.get("role", "?")
                content = str(msg.get("content", ""))[:100]
                logger.debug(f"  [{i}] {role}: {content}...")
                
        except Exception as e:
            logger.error(f"[RedisAgg] 构建上下文失败: {e}")
        finally:
            self._is_processing = False
            
    async def _build_context(self) -> str:
        """构建动态上下文
        
        ⭐ P2-17: 上下文 getter 并行化
        - 使用 asyncio.gather 并行执行，总时间从 2x 减少到 max(x)
        """
        parts = []
        
        # ⭐ 并行获取两个上下文
        async def _get_dynamic():
            if self.dynamic_context_getter:
                try:
                    return await self.dynamic_context_getter()
                except Exception as e:
                    logger.warning(f"[RedisAgg] 获取动态上下文失败: {e}")
                    return None
            return None
        
        async def _get_memory():
            if self.memory_context_getter:
                try:
                    return await self.memory_context_getter()
                except Exception as e:
                    logger.warning(f"[RedisAgg] 获取记忆上下文失败: {e}")
                    return None
            return None
        
        # ⭐ 并行执行
        dynamic_result, memory_result = await asyncio.gather(
            _get_dynamic(),
            _get_memory(),
        )
        
        # 处理结果
        if dynamic_result:
            parts.append(dynamic_result)
        
        if memory_result:
            parts.append(f"## 记忆参考\n{memory_result}")
        
        return "\n\n".join(parts)
    
    def _build_full_system_prompt(self, context: str, history: str) -> str:
        """构建完整的 system prompt"""
        # 系统提示词 + 动态上下文 + 历史
        parts = []
        
        if self.system_prompt:
            parts.append(self.system_prompt)
        
        if context:
            parts.append(context)
        
        if history:
            parts.append(f"## 对话历史\n{history}")
        else:
            parts.append("## 对话历史\n（这是第一轮对话）")
        
        return "\n\n".join(parts)
    
    async def append_assistant_message(
        self,
        text: str,
        inner_monologue: Optional[str] = None
    ) -> None:
        """追加 AI 回复到 Redis
        
        Args:
            text: 消息文本
            inner_monologue: 心理活动内容（将附加在消息中）
        """
        await self._buffer.append_assistant_message(
            text=text,
            inner_monologue=inner_monologue,
            timestamp=time.time()
        )
        
        # 日志输出包含心理活动信息
        if inner_monologue:
            logger.info(f"[RedisAgg] AI 消息已写入 Redis: {text[:50]}... (心理活动: {inner_monologue[:30]}...)")
        else:
            logger.info(f"[RedisAgg] AI 消息已写入 Redis: {text[:50]}...")
    
    async def clear_history(self) -> None:
        """清空历史"""
        await self._buffer.clear()
    
    async def get_history(self) -> str:
        """获取格式化历史"""
        return await self._buffer.get_formatted_history(
            max_items=self.max_history_items,
            format_style="you_me"
        )