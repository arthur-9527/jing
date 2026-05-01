"""IM Channel LLM 处理器

独立于 ASR→LLM→TTS 实时流的非实时 IM 消息处理流程：

IM IN (多模态消息)
    ↓
多模态预处理（ASR/Vision）→ 统一文本输入 + 媒体描述
    ↓
写入 Redis（user 消息，含媒体描述）
    ↓
从 Redis 读取历史 + 短期记忆
    ↓
⭐ 构建 PAD 动态上下文 + 好感度语境 ← 新增
    ↓
构建 LLM Prompt（含历史、PAD、好感度上下文）
    ↓
LLM 调用（JSON 输出）→ 判断响应类型
    ↓
⭐ 更新 PAD 状态 + 评估好感度增量 ← 新增
    ↓
⭐ 添加好感度感性事件 ← 新增
    ↓
写入 Redis（assistant 消息，含媒体描述）
    ↓
⭐ 情绪状态持久化 ← 新增
    ↓
响应执行（text/audio/image/video）→ 异步生成
    ↓
IM OUT (多模态回复)

特点：
- ⭐ 集成 Redis 记忆系统（与实时音频流一致）
- ⭐ 集成情绪系统（EmotionService，与实时音频流一致）
- ⭐ 集成好感度系统（AffectionService，与实时音频流一致）
- ⭐ 媒体描述随文本一起存入 Redis
- ⭐ 情绪好感度内核与实时音频流共享
- 非流式，一次性 JSON 输出
- 各响应独立推送（生成什么推送什么）
- 异步任务完成后自动推送
- 视频生成：先生成第一帧图片，再生成视频
- 不涉及动作系统和前端内容
"""

import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from app.agent.llm.client import LLMClient
from app.agent.prompt.im_prompt import build_im_system_prompt, build_im_user_prompt
from app.agent.prompt.system_prompt import build_dynamic_context
from app.agent.memory.retriever import retrieve_short_term_memories
from app.channel.types import InboundMessage, OutboundMessage, ContentBlock, MediaType
from app.providers.asr import get_asr_provider
from app.providers.image_gen import get_image_gen_provider
from app.providers.video_gen import get_video_gen_provider
from app.providers.tts import get_tts_provider
from app.providers.llm import get_llm_provider
from app.services.chat_history import get_conversation_buffer
from app.services.emotion import get_emotion_service
from app.services.affection import AffectionService, AffectionContextManager
from app.config import settings
from app.task_system import get_task_system  # ⭐ 新增：任务系统

logger = logging.getLogger(__name__)


@dataclass
class IMResponse:
    """IM Channel LLM 响应"""
    response_type: str  # text/audio/image/video
    text_content: str
    emotion_delta: Dict[str, float]
    inner_monologue: str
    media_prompt: Optional[str] = None
    tool_prompt: Optional[str] = None  # 工具调用提示，null 表示不需要工具调用


@dataclass
class TaskInfo:
    """异步任务信息"""
    task_id: str
    user_id: str
    openid: str
    bot_id: str
    channel_id: str
    task_type: str  # audio/image/video
    created_at: datetime


def parse_llm_response(content: str | dict) -> IMResponse:
    """解析 LLM JSON 响应
    
    Args:
        content: LLM 返回的原始内容（str 或已解析的 dict）
    
    Returns:
        IMResponse 对象
    
    Raises:
        ValueError: JSON 解析失败
    """
    # 如果已经是 dict，直接使用
    if isinstance(content, dict):
        data = content
    else:
        # 字符串处理逻辑
        content = content.strip()
        
        # 移除可能的代码块包裹
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        # 尝试解析 JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    raise ValueError(f"JSON 解析失败: {content[:200]}")
            else:
                raise ValueError(f"未找到 JSON: {content[:200]}")
    
    # 验证必要字段
    if "response_type" not in data:
        raise ValueError(f"缺少 response_type 字段: {data}")
    
    response_type = data["response_type"]
    if response_type not in ("text", "audio", "image", "video"):
        raise ValueError(f"无效的 response_type: {response_type}")
    
    # 构建 IMResponse
    return IMResponse(
        response_type=response_type,
        text_content=data.get("text_content", ""),
        emotion_delta=data.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0}),
        inner_monologue=data.get("inner_monologue", ""),
        media_prompt=data.get("media_prompt"),
        tool_prompt=data.get("tool_prompt"),  # 工具调用提示
    )


class IMChannelProcessor:
    """IM Channel 主处理器
    
    负责：
    1. 接收 InboundMessage
    2. 多模态预处理（ASR/Vision）
    3. ⭐ 构建 PAD 动态上下文 + 好感度语境
    4. 构建 LLM Prompt
    5. 调用 LLM（JSON 模式）
    6. ⭐ 更新 PAD 状态 + 评估好感度
    7. 解析响应，执行响应类型
    8. ⭐ 情绪状态持久化
    9. 创建异步任务并追踪
    10. 任务完成后推送
    
    ⭐ 与实时音频流共享情绪好感度内核
    """
    
    def __init__(
        self,
        character_config: Dict[str, Any],
        reference_image_path: Optional[str] = None,
        user_id: str = "default_user",
    ):
        """初始化处理器
        
        Args:
            character_config: 角色配置字典
            reference_image_path: 参考图路径（用于图片/视频生成）
            user_id: 用户标识（用于 Redis 数据隔离）
        """
        self._llm_client = LLMClient()
        self._character_config = character_config
        self._reference_image_path = reference_image_path
        
        # ⭐ 用户和角色标识（用于 Redis 数据隔离）
        self._user_id = user_id
        self._character_id = character_config.get("character_id", "default")
        
        # ⭐ Redis 聊天记录缓冲区（懒加载）
        self._conversation_buffer: Optional[Any] = None
        
        # ⭐ 情绪系统（EmotionService）
        # ⭐ 改造：使用单例模式，与 EmotionalAgent 共享同一个实例
        # ⭐ Stone 迁移：使用 EmotionStateRepository
        emotion_baseline = character_config.get("emotion_baseline", {"P": 0.0, "A": 0.0, "D": 0.0})
        self.emotion = get_emotion_service(
            character_id=self._character_id,
            baseline=emotion_baseline,
        )
        
        # ⭐ 好感度系统（AffectionService + AffectionContextManager，懒加载）
        self._affection_service: Optional[AffectionService] = None
        self._affection_context_manager: Optional[AffectionContextManager] = None

        # 异步任务追踪
        self._pending_tasks: Dict[str, TaskInfo] = {}
        
        # 发送回调
        self._send_callback: Optional[callable] = None
        
        # 轮次计数
        self._turn_count: int = 0
        
        # ⭐ 新增：任务系统引用（用于工具调用）
        self._task_system = None

        # ⭐ 缓存角色基础信息，系统提示词在 handle() 中按用户动态构建
        self._character_name = character_config.get("name", "虚拟助手")
        self._speaking_style = character_config.get("speaking_style", {})
        self._emotion_baseline = emotion_baseline

        logger.info(f"[IMProcessor] 初始化完成，角色: {self._character_name}, character_id: {self._character_id}")
    
    async def _get_conversation_buffer(self):
        """获取或创建 ConversationBuffer（懒加载）
        
        ⭐ 与实时音频流使用相同的 ConversationBuffer，
        按 character_id + user_id 实现数据隔离。
        """
        if self._conversation_buffer is None:
            self._conversation_buffer = await get_conversation_buffer(
                user_id=self._user_id,
                character_id=self._character_id,
            )
            logger.info(f"[IMProcessor] ConversationBuffer 已连接: user={self._user_id}, character={self._character_id}")
        return self._conversation_buffer

    async def _get_affection_service(self) -> AffectionService:
        """获取或创建 AffectionService（懒加载）

        ⭐ 与实时音频流使用相同的好感度服务，
        按 character_id + user_id 实现数据隔离。
        ⭐ Stone 迁移：使用 AffectionRepository
        """
        if self._affection_service is None:
            # 获取 Stone AffectionRepository
            from app.stone import get_affection_repo, get_database
            affection_repo = get_affection_repo()
            db_conn = get_database()
            
            self._affection_service = AffectionService(
                affection_repo=affection_repo,
                llm_client=self._llm_client,
                db_conn=db_conn,
            )
            # ⭐ 注入到 EmotionService，好感度评估由 EmotionService 内置处理
            self.emotion.set_affection_deps(
                affection_service=self._affection_service,
                llm_chat_fn=self._llm_client.chat,
            )
            logger.info(f"[IMProcessor] AffectionService 已初始化(Stone): character={self._character_id}")
        return self._affection_service
    
    async def _get_affection_context_manager(self) -> AffectionContextManager:
        """获取或创建 AffectionContextManager（懒加载）

        ⭐ 用于生成动态好感度语境，注入到 LLM Prompt。
        ⭐ 加载角色人设文本，确保 LLM 生成个性化好感度语境。
        """
        if self._affection_context_manager is None:
            affection_service = await self._get_affection_service()
            personality_text, emotion_traits_text = self._load_character_texts()
            from app.stone.repositories.affection_redis import get_affection_repo
            self._affection_context_manager = AffectionContextManager(
                affection_service=affection_service,
                llm_client=self._llm_client,
                character_config=self._character_config,
                affection_repo=get_affection_repo(),
                personality_text=personality_text,
                emotion_traits_text=emotion_traits_text,
            )
            logger.info("[IMProcessor] AffectionContextManager 已初始化(Stone)")
        return self._affection_context_manager

    def _load_character_texts(self) -> tuple:
        """加载角色人设和情绪特点文本"""
        try:
            from app.agent.character.loader import load_character
            character_dir = self._character_config.get("config_path", "")
            if character_dir:
                config = load_character(character_dir)
                return config.personality_text, config.emotion_traits_text
        except Exception as e:
            logger.warning("[IMProcessor] 加载角色人设失败: %s", e)
        return "", ""
    
    def _get_channel_prompt(self, channel_id: str) -> str | None:
        """获取指定 Channel 的专用提示词

        从 ChannelManager 查找已注册的 Channel 实例，
        返回其 channel_prompt 属性。
        """
        try:
            from app.channel.manager import get_channel_manager
            channel = get_channel_manager().get_channel(channel_id)
            if channel:
                return channel.channel_prompt
        except Exception:
            pass
        return None

    async def _compute_enabled_media_types(self, channel_id: str) -> set:
        """根据 Channel 能力 + 用户好感度分数，计算启用的媒体类型

        最终启用的媒体类型 = Channel 硬件能力 ∩ 好感度门槛

        好感度门槛（三维均达到对应分数才解锁）：
        - TTS (audio):  三维均 ≥ 60
        - Image:         三维均 ≥ 80
        - Video:         三维均 ≥ 90

        若 IMAGE_VIDEO_GEN_ENABLED 为 False，直接返回空集合。
        """
        if not settings.IMAGE_VIDEO_GEN_ENABLED:
            return set()

        # 获取 Channel 硬件能力
        channel_supports_audio = False
        channel_supports_image = False
        channel_supports_video = False

        try:
            from app.channel.manager import get_channel_manager
            channel = get_channel_manager().get_channel(channel_id)
            if channel:
                channel_supports_audio = channel.supports_audio
                channel_supports_image = channel.supports_image
                channel_supports_video = channel.supports_video
        except Exception:
            pass

        if not any([channel_supports_audio, channel_supports_image, channel_supports_video]):
            return set()

        # 获取用户好感度三维分数
        try:
            from app.services.affection.models import AffectionDimension
            affection_service = await self._get_affection_service()
            affection_state = await affection_service.get_state(
                self._character_id, self._user_id
            )
            min_score = min(
                affection_state.get_dimension(AffectionDimension.TRUST).total,
                affection_state.get_dimension(AffectionDimension.INTIMACY).total,
                affection_state.get_dimension(AffectionDimension.RESPECT).total,
            )
            logger.info(
                f"[IMProcessor] 好感度三维最低分: {min_score:.1f} "
                f"(trust={affection_state.get_dimension(AffectionDimension.TRUST).total:.1f}, "
                f"intimacy={affection_state.get_dimension(AffectionDimension.INTIMACY).total:.1f}, "
                f"respect={affection_state.get_dimension(AffectionDimension.RESPECT).total:.1f})"
            )
        except Exception as e:
            logger.warning(f"[IMProcessor] 获取好感度分数失败: {e}")
            return set()

        # Channel 能力 ∩ 好感度门槛
        enabled = set()
        if channel_supports_audio and min_score >= 60:
            enabled.add("tts")
        if channel_supports_image and min_score >= 80:
            enabled.add("image")
        if channel_supports_video and min_score >= 90:
            enabled.add("video")

        logger.info(
            f"[IMProcessor] 启用的媒体类型: {enabled if enabled else '(仅文字)'}"
        )
        return enabled

    def _get_task_system(self):
        """获取任务系统实例（懒加载）
        
        ⭐ 用于执行工具调用任务
        """
        if self._task_system is None:
            self._task_system = get_task_system()
            logger.info(f"[IMProcessor] TaskSystem 已获取")
        return self._task_system
    
    async def _load_state(self) -> None:
        """从 Stone 加载情绪状态

        ⭐ 使用 EmotionService 内置的 Stone 持久化。
        """
        try:
            # 获取 Stone EmotionStateRepository 并注入
            from app.stone import get_emotion_repo
            emotion_repo = get_emotion_repo()
            self.emotion.set_emotion_repo(emotion_repo)
            
            loaded = await self.emotion.load_state()
            if loaded:
                logger.info(
                    f"[IMProcessor] 已从 Stone 恢复情绪状态: "
                    f"P={self.emotion.get_state().p:.3f}, "
                    f"A={self.emotion.get_state().a:.3f}, "
                    f"D={self.emotion.get_state().d:.3f}"
                )
        except Exception as e:
            logger.warning(f"[IMProcessor] 加载状态失败: {e}")

    async def _save_state(self) -> None:
        """保存情绪状态到 Stone

        ⭐ 使用 EmotionService 内置的 Stone 持久化。
        """
        try:
            saved = await self.emotion.save_state()
            if saved:
                logger.debug(f"[IMProcessor] 情绪状态已保存到 Stone，轮次: {self._turn_count}")
        except Exception as e:
            logger.warning(f"[IMProcessor] 保存状态失败: {e}")
    
    def set_send_callback(self, callback: callable):
        """设置发送消息的回调函数
        
        Args:
            callback: 异步函数，接收 OutboundMessage
        """
        self._send_callback = callback
    
    async def handle(self, message: InboundMessage) -> Optional[OutboundMessage]:
        """处理入站消息
        
        ⭐ 与实时音频流共享情绪好感度内核
        
        流程：
        1. ⭐ 加载情绪状态（首次调用时）
        2. 多模态预处理（同步阻塞）→ 拼接媒体描述
        3. 写入 Redis（user 消息，含媒体描述）
        4. ⭐ 构建 PAD 动态上下文 + 好感度语境
        5. 从 Redis 读取历史 + 短期记忆
        6. 构建 LLM Prompt（含历史、PAD、好感度上下文）
        7. LLM 调用（JSON 输出）
        8. 解析响应类型
        9. ⭐ 更新 PAD 状态
        10. ⭐ 评估好感度增量 + 添加感性事件
        11. 写入 Redis（assistant 消息，含媒体描述）
        12. ⭐ 情绪状态持久化
        13. 执行响应（可能创建异步任务）
        
        Args:
            message: 入站消息
        
        Returns:
            如果有立即可以推送的响应，返回 OutboundMessage
            如果需要异步生成，返回 None，生成完成后通过回调推送
        """
        logger.info(f"[IMProcessor] 处理消息: user={message.user_id}, contents={len(message.contents)}")
        
        # ⭐ Step 0: 动态设置 user_id（实现 Redis 数据隔离）
        # 每个 IM 消息来自不同用户，需要在处理时动态切换
        actual_user_id = message.user_id
        if actual_user_id != self._user_id:
            logger.info(f"[IMProcessor] 用户切换: {self._user_id} → {actual_user_id}")
            self._user_id = actual_user_id
            # 重置 ConversationBuffer，按新 user_id 重新创建
            self._conversation_buffer = None
            # 重置轮次计数（新用户）
            self._turn_count = 0
        
        # ⭐ Step 1: 首次调用时加载情绪状态
        if self._turn_count == 0:
            await self._load_state()
        
        # Step 1: 多模态预处理（同步阻塞）
        user_input, media_context = await self._preprocess_message(message)
        
        if not user_input and not media_context:
            logger.warning("[IMProcessor] 消息预处理后无有效内容")
            return None
        
        logger.info(f"[IMProcessor] 预处理完成: input={user_input[:50] if user_input else 'N/A'}...")
        
        # ⭐ Step 2: 构建完整的 user 消息内容（文本 + 媒体描述）
        full_user_content = self._build_user_content(user_input, media_context)
        
        # ⭐ Step 3: 写入 Redis（user 消息）
        try:
            buffer = await self._get_conversation_buffer()
            await buffer.append_user_message(text=full_user_content)
            logger.info(f"[IMProcessor] User 消息已写入 Redis: {full_user_content[:50]}...")
        except Exception as e:
            logger.warning(f"[IMProcessor] 写入 Redis 失败: {e}")
        
        # ⭐ Step 4: 构建 PAD 动态上下文 + 好感度语境
        dynamic_context = ""
        affection_context = ""
        
        try:
            # PAD 动态上下文
            dynamic_context = build_dynamic_context(self.emotion)
            
            # 好感度语境（懒加载）
            try:
                # 注册活跃用户（供好感度语境刷新调度器使用）
                from app.services.affection.scheduler import register_active_user
                register_active_user(self._character_id, self._user_id)

                affection_ctx_manager = await self._get_affection_context_manager()
                affection_context = await affection_ctx_manager.get_affection_context(
                    character_id=self._character_id,
                    user_id=self._user_id,
                    emotion_service=self.emotion,
                )
            except Exception as e:
                logger.warning(f"[IMProcessor] 获取好感度语境失败: {e}")
            
            logger.info(f"[IMProcessor] PAD 上下文: {len(dynamic_context)} chars, 好感度语境: {len(affection_context)} chars")
        except Exception as e:
            logger.warning(f"[IMProcessor] 构建动态上下文失败: {e}")
        
        # ⭐ Step 5: 从 Redis 读取历史 + 短期记忆
        history_context = ""
        memory_context = ""
        
        try:
            buffer = await self._get_conversation_buffer()
            
            # 获取历史聊天记录
            history_context = await buffer.get_formatted_history(max_items=10)
            
            # 获取短期记忆（FTS 匹配）
            memories = await retrieve_short_term_memories(
                character_id=self._character_id,
                user_id=self._user_id,
                user_input=user_input,
            )
            memory_context = memories.get("combined", "")
            
            logger.info(f"[IMProcessor] 历史上下文: {len(history_context)} chars, 短期记忆: {len(memory_context)} chars")
        except Exception as e:
            logger.warning(f"[IMProcessor] 获取记忆失败: {e}")
        
        # ⭐ Step 6: 动态构建系统提示词（按 Channel 能力 + 用户好感度分级）
        enabled_media_types = await self._compute_enabled_media_types(message.channel_id)

        system_prompt = build_im_system_prompt(
            character_name=self._character_name,
            speaking_style=self._speaking_style,
            emotion_baseline=self._emotion_baseline,
            channel_prompt=self._get_channel_prompt(message.channel_id),
            enabled_media_types=enabled_media_types,
        )

        # ⭐ Step 7: 构建用户提示词并调用 LLM（含 PAD + 好感度上下文）
        user_prompt = build_im_user_prompt(
            user_input=user_input,
            media_context=media_context,
            history_context=history_context,
            memory_context=memory_context,
            dynamic_context=dynamic_context,      # PAD 动态上下文
            affection_context=affection_context,   # 好感度语境
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        try:
            # 使用 JSON 模式
            response_text = await self._llm_client.chat_json(
                messages=messages,
                temperature=0.8,
            )
            
            logger.info(f"[IMProcessor] LLM 响应: {response_text}")
            
        except Exception as e:
            logger.error(f"[IMProcessor] LLM 调用失败: {e}")
            return OutboundMessage(
                channel_id=message.channel_id,
                user_id=message.user_id,
            ).add_text("收到你的消息了~")
        
        # Step 7: 解析响应
        try:
            response = parse_llm_response(response_text)
        except ValueError as e:
            logger.error(f"[IMProcessor] 响应解析失败: {e}")
            return OutboundMessage(
                channel_id=message.channel_id,
                user_id=message.user_id,
            ).add_text("收到你的消息了~")
        
        logger.info(
            f"[IMProcessor] 响应类型: {response.response_type}, "
            f"emotion_delta={response.emotion_delta}, "
            f"inner_monologue={response.inner_monologue[:30]}..."
        )
        
        # ⭐ Step 8: 更新 PAD 状态
        # ⭐ 传入 context，供心动事件回调使用
        emotion_event = None
        if response.emotion_delta:
            try:
                emotion_event = self.emotion.update(
                    delta=response.emotion_delta,
                    trigger_keywords=[],  # IM 不提取关键词
                    inner_monologue=response.inner_monologue,
                    context={
                        "character_id": self._character_id,
                        "user_id": self._user_id,
                        "user_input": user_input,
                        "expression": response.text_content,
                    },
                )
                logger.info(
                    f"[IMProcessor] PAD 已更新: P={emotion_event.state.p:.2f}, "
                    f"A={emotion_event.state.a:.2f}, D={emotion_event.state.d:.2f} | "
                    f"intensity={emotion_event.intensity:.3f} | is_heart_event={emotion_event.is_heart_event}"
                )
            except Exception as e:
                logger.warning(f"[IMProcessor] 更新 PAD 状态失败: {e}")
        
        # ⭐ Step 10: 写入 Redis（assistant 消息，含媒体描述）
        try:
            buffer = await self._get_conversation_buffer()
            full_assistant_content = self._build_assistant_content(response)
            await buffer.append_assistant_message(
                text=full_assistant_content,
                inner_monologue=response.inner_monologue,
            )
            logger.info(f"[IMProcessor] Assistant 消息已写入 Redis: {full_assistant_content[:50]}...")
        except Exception as e:
            logger.warning(f"[IMProcessor] 写入 Redis 失败: {e}")
        
        # ⭐ Step 11: 情绪状态持久化
        self._turn_count += 1
        await self._save_state()
        
        # Step 12: 执行响应
        return await self._execute_response(message, response)
    
    def _build_user_content(self, user_input: str, media_context: str) -> str:
        """构建完整的 user 消息内容
        
        格式：
        - 纯文本: "文本内容"
        - 发送图片: "文本内容\n发送的图片描述：xxx"
        - 发送视频: "文本内容\n发送的视频描述：xxx"
        
        Args:
            user_input: 用户文本输入
            media_context: 媒体描述上下文
        
        Returns:
            完整的消息内容
        """
        parts = []
        
        if user_input:
            parts.append(user_input)
        
        if media_context:
            # 将 media_context 转换为 "发送的图片描述：" 格式
            for line in media_context.split("\n"):
                if line.startswith("用户发送了一张图片："):
                    parts.append(f"发送的图片描述：{line[len('用户发送了一张图片：'):]}")
                elif line.startswith("用户发送了一段视频："):
                    parts.append(f"发送的视频描述：{line[len('用户发送了一段视频：'):]}")
                elif line.startswith("用户发送了一段语音"):
                    parts.append("发送了一段语音")
                elif line.strip():
                    parts.append(line)
        
        return "\n".join(parts)
    
    def _build_assistant_content(self, response: IMResponse) -> str:
        """构建完整的 assistant 消息内容
        
        格式：
        - 文本回复: "回复内容"
        - 图片回复: "回复内容\n发送的图片描述：xxx"
        - 视频回复: "回复内容\n发送的视频描述：xxx"
        
        Args:
            response: LLM 响应
        
        Returns:
            完整的消息内容
        """
        parts = []
        
        if response.text_content:
            parts.append(response.text_content)
        
        # 根据 response_type 添加媒体描述
        if response.response_type == "image" and response.media_prompt:
            parts.append(f"发送的图片描述：{response.media_prompt}")
        elif response.response_type == "video" and response.media_prompt:
            parts.append(f"发送的视频描述：{response.media_prompt}")
        elif response.response_type == "audio":
            parts.append("发送了一段语音")
        
        return "\n".join(parts)
    
    async def _preprocess_message(self, message: InboundMessage) -> tuple[str, str]:
        """多模态预处理
        
        处理：
        - TEXT: 直接使用
        - AUDIO: ASR 转文本
        - IMAGE: Vision 分析 → 文本描述
        - VIDEO: Vision 分析 → 文本描述
        """
        text_parts = []
        media_descriptions = []
        
        for content in message.contents:
            if content.type == MediaType.TEXT:
                # 文本直接使用
                if content.content:
                    text_parts.append(content.content)
            
            elif content.type == MediaType.AUDIO:
                # 语音消息 → ASR
                text = await self._process_audio(content)
                if text:
                    text_parts.append(text)
                    media_descriptions.append("用户发送了一段语音")
            
            elif content.type == MediaType.IMAGE:
                # 图片 → Vision 分析
                description = await self._process_image(content)
                if description:
                    media_descriptions.append(f"用户发送了一张图片：{description}")
            
            elif content.type == MediaType.VIDEO:
                # 视频 → Vision 分析
                description = await self._process_video(content)
                if description:
                    media_descriptions.append(f"用户发送了一段视频：{description}")
        
        # 合并结果
        user_input = "\n".join(text_parts) if text_parts else ""
        media_context = "\n".join(media_descriptions) if media_descriptions else ""
        
        return user_input, media_context
    
    async def _process_audio(self, content: ContentBlock) -> Optional[str]:
        """处理语音消息"""
        try:
            asr = get_asr_provider()
            if not asr:
                logger.warning("[IMProcessor] ASR Provider 未配置")
                return None
            
            audio_data = None
            if content.url:
                audio_data = await self._download_media(content.url)
            
            if not audio_data:
                logger.warning("[IMProcessor] 无法获取音频数据")
                return None
            
            text = await asr.transcribe(audio_data)
            logger.info(f"[IMProcessor] ASR 结果: {text[:50]}...")
            return text
            
        except Exception as e:
            logger.error(f"[IMProcessor] ASR 处理失败: {e}")
            return None
    
    async def _process_image(self, content: ContentBlock) -> Optional[str]:
        """处理图片消息
        
        使用 LLM Provider 的 Vision 能力分析图片内容。
        """
        try:
            # 获取 LLM Provider
            llm = get_llm_provider()
            if not llm or not llm.supports_image:
                logger.warning("[IMProcessor] LLM Provider 不支持图片分析")
                return "一张图片（无法分析）"
            
            # 获取图片数据
            image_data = None
            
            if content.content:
                # 如果 content 是 base64 编码的图片数据
                try:
                    # 检查是否是 data URI 格式
                    if content.content.startswith("data:"):
                        # 提取 base64 部分
                        base64_part = content.content.split(",", 1)[1]
                        image_data = base64.b64decode(base64_part)
                    else:
                        # 直接是 base64
                        image_data = base64.b64decode(content.content)
                except Exception as e:
                    logger.warning(f"[IMProcessor] base64 解码失败: {e}")
            
            if not image_data and content.url:
                # 从 URL 下载图片
                image_data = await self._download_media(content.url)
            
            if not image_data:
                logger.warning("[IMProcessor] 无法获取图片数据")
                return "一张图片"
            
            # 调用 Vision 分析
            result = await llm.analyze_image(
                image_data=image_data,
                prompt="请描述这张图片的主要内容，包括场景、人物、物体和情感氛围。用简洁的自然语言描述。",
                detail_level="low",  # IM 场景使用低细节级别以节省 token
            )
            
            if result and result.description:
                logger.info(f"[IMProcessor] 图片分析完成: {result.description[:100]}...")
                return result.description
            
            return "一张图片"
            
        except Exception as e:
            logger.error(f"[IMProcessor] 图片处理失败: {e}")
            return None
    
    async def _process_video(self, content: ContentBlock) -> Optional[str]:
        """处理视频消息
        
        使用 LLM Provider 的 Vision 能力分析视频内容（提取关键帧）。
        """
        try:
            # 获取 LLM Provider
            llm = get_llm_provider()
            if not llm or not llm.supports_video:
                logger.warning("[IMProcessor] LLM Provider 不支持视频分析")
                return "一段视频（无法分析）"
            
            # 获取视频路径或 URL
            video_path = None
            
            if content.url:
                # 需要先下载视频到本地临时文件
                video_data = await self._download_media(content.url)
                if video_data:
                    # 创建临时文件
                    import tempfile
                    temp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    temp_file.write(video_data)
                    temp_file.close()
                    video_path = temp_file.name
                    logger.info(f"[IMProcessor] 视频已下载到临时文件: {video_path}")
            
            if not video_path:
                logger.warning("[IMProcessor] 无法获取视频数据")
                return "一段视频"
            
            # 调用 Vision 分析（提取关键帧）
            result = await llm.analyze_video(
                video_path=video_path,
                prompt="请描述这段视频的主要内容，包括场景变化、人物动作和整体氛围。用简洁的自然语言描述。",
                frame_interval=1.0,  # 每秒提取一帧
                max_frames=8,  # IM 场景限制帧数以节省 token
            )
            
            # 清理临时文件
            try:
                os.unlink(video_path)
            except Exception:
                pass
            
            if result and result.description:
                logger.info(f"[IMProcessor] 视频分析完成: {result.description[:100]}...")
                return result.description
            
            return "一段视频"
            
        except Exception as e:
            logger.error(f"[IMProcessor] 视频处理失败: {e}")
            # 清理可能残留的临时文件
            if video_path and os.path.exists(video_path):
                try:
                    os.unlink(video_path)
                except Exception:
                    pass
            return None
    
    async def _execute_response(
        self,
        message: InboundMessage,
        response: IMResponse,
    ) -> Optional[OutboundMessage]:
        """执行 LLM 响应
        
        ⭐ 新增：处理 tool_prompt
        - 如果有 tool_prompt，先返回立即文本回复
        - 后台提交工具任务到 TaskSystem
        - 工具完成后由 TaskSystem 自动推送结果
        """
        outbound = OutboundMessage(
            channel_id=message.channel_id,
            user_id=message.user_id,
        )
        
        # ⭐ 检测 tool_prompt，如有则后台执行工具调用
        if response.tool_prompt:
            # 先返回立即文本回复
            outbound.add_text(response.text_content)
            logger.info(
                f"[IMProcessor] 文本回复（含工具调用）: {response.text_content[:50]}..., "
                f"tool_prompt={response.tool_prompt[:30]}..."
            )
            
            # 后台提交工具任务
            asyncio.create_task(
                self._execute_tool_call(message, response)
            )
            return outbound
        
        if response.response_type == "text":
            outbound.add_text(response.text_content)
            logger.info(f"[IMProcessor] 文本回复: {response.text_content[:50]}...")
            return outbound
        
        elif response.response_type == "audio":
            asyncio.create_task(
                self._execute_audio_task(message, response)
            )
            logger.info(f"[IMProcessor] 创建音频任务: {response.text_content[:30]}...")
            return None
        
        elif response.response_type == "image":
            if not response.media_prompt:
                logger.warning("[IMProcessor] 图片响应缺少 media_prompt")
                outbound.add_text(response.text_content or "想给你看张照片...")
                return outbound
            
            asyncio.create_task(
                self._execute_image_task(message, response)
            )
            logger.info(f"[IMProcessor] 创建图片任务: {response.media_prompt}")
            return None
        
        elif response.response_type == "video":
            if not response.media_prompt:
                logger.warning("[IMProcessor] 视频响应缺少 media_prompt")
                outbound.add_text(response.text_content or "想给你看段视频...")
                return outbound
            
            asyncio.create_task(
                self._execute_video_task(message, response)
            )
            logger.info(f"[IMProcessor] 创建视频任务: {response.media_prompt}")
            return None
        
        else:
            logger.warning(f"[IMProcessor] 未知的响应类型: {response.response_type}")
            outbound.add_text("收到你的消息了~")
            return outbound
    
    async def _execute_tool_call(
        self,
        message: InboundMessage,
        response: IMResponse,
    ) -> None:
        """后台执行工具调用
        
        ⭐ 通过 TaskSystem 提交工具任务，结果自动推送到 IM Channel
        
        流程：
        1. 获取 TaskSystem 实例
        2. 提交任务（带 IM 渠道信息）
        3. TaskSystem 自动执行：OpenClaw → PostProcessor → ChannelManager 推送
        
        Args:
            message: 入站消息（包含 channel_id 和 user_id）
            response: LLM 响应（包含 tool_prompt）
        """
        try:
            task_system = self._get_task_system()
            
            if not task_system or not task_system._running:
                logger.warning("[IMProcessor] TaskSystem 未初始化或未运行，无法执行工具调用")
                # 推送 fallback 消息
                if self._send_callback:
                    fallback = OutboundMessage(
                        channel_id=message.channel_id,
                        user_id=message.user_id,
                    ).add_text("处理失败了，稍后再试吧~")
                    await self._send_callback(fallback)
                return
            
            # 提交工具任务到 TaskSystem
            task_id = await task_system.submit(
                tool_prompt=response.tool_prompt,
                provider_name="openclaw",
                context={
                    "user_input": response.text_content,  # 用户可见的回复内容
                    "character_id": self._character_id,
                    # ⭐ IM 渠道信息（TaskSystem 会根据这些字段推送结果）
                    "source_channel": "im",
                    "channel_id": message.channel_id,
                    "user_id": message.user_id,
                },
            )
            
            logger.info(
                f"[IMProcessor] 工具任务已提交: task_id={task_id[:8]}..., "
                f"tool_prompt={response.tool_prompt[:30]}..., "
                f"channel={message.channel_id}, user={message.user_id}"
            )
            
            # ⭐ 不需要等待结果，TaskSystem 会自动：
            # 1. 执行工具调用（OpenClaw Provider）
            # 2. 执行二次改写（PostProcessor）
            # 3. 通过 ChannelManager 推送到 IM 用户
            
        except Exception as e:
            logger.error(f"[IMProcessor] 工具任务提交失败: {e}")
            # 推送 fallback 消息
            if self._send_callback:
                fallback = OutboundMessage(
                    channel_id=message.channel_id,
                    user_id=message.user_id,
                ).add_text("处理失败了，稍后再试吧~")
                await self._send_callback(fallback)
    
    async def _execute_audio_task(
        self,
        message: InboundMessage,
        response: IMResponse,
    ):
        """执行音频生成任务"""
        try:
            tts = get_tts_provider()
            if not tts:
                logger.error("[IMProcessor] TTS Provider 未配置")
                return
            
            if hasattr(tts, 'set_emotion_from_pad'):
                tts.set_emotion_from_pad(response.emotion_delta)
            
            audio_data = await tts.synthesize(
                text=response.text_content,
                sample_rate=16000,
                format="pcm",
            )
            
            logger.info(f"[IMProcessor] TTS 完成: {len(audio_data)} bytes")
            
            outbound = OutboundMessage(
                channel_id=message.channel_id,
                user_id=message.user_id,
            )
            
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")
            outbound.add_audio(audio_base64, mime_type="audio/ogg")
            
            if self._send_callback:
                await self._send_callback(outbound)
                logger.info("[IMProcessor] 音频消息已推送")
            
        except Exception as e:
            logger.error(f"[IMProcessor] 音频任务失败: {e}")
            if self._send_callback:
                fallback = OutboundMessage(
                    channel_id=message.channel_id,
                    user_id=message.user_id,
                ).add_text(response.text_content)
                await self._send_callback(fallback)
    
    async def _execute_image_task(
        self,
        message: InboundMessage,
        response: IMResponse,
    ):
        """执行图片生成任务"""
        try:
            image_gen = get_image_gen_provider()
            if not image_gen:
                logger.error("[IMProcessor] ImageGen Provider 未配置")
                return
            
            reference_image = await self._get_reference_image()
            if not reference_image:
                logger.warning("[IMProcessor] 无参考图，使用默认")
                reference_image = ""
            
            task = await image_gen.submit(
                prompt=response.media_prompt,
                reference_image=reference_image,
            )
            
            if task.status.name not in ("PENDING", "RUNNING"):
                logger.error(f"[IMProcessor] 图片任务提交失败: {task.error}")
                return
            
            logger.info(f"[IMProcessor] 图片任务已提交: {task.task_id}")
            
            max_polls = 60
            for i in range(max_polls):
                await asyncio.sleep(2)
                task = await image_gen.poll(task.task_id)
                
                if task.status.name == "SUCCEEDED":
                    break
                elif task.status.name == "FAILED":
                    logger.error(f"[IMProcessor] 图片生成失败: {task.error}")
                    return
            
            if task.status.name != "SUCCEEDED" or not task.result:
                logger.error("[IMProcessor] 图片生成超时")
                return
            
            image_url = task.result.images[0].url if task.result.images else None
            if not image_url:
                logger.error("[IMProcessor] 图片结果无 URL")
                return
            
            logger.info(f"[IMProcessor] 图片生成完成: {image_url}")
            
            outbound = OutboundMessage(
                channel_id=message.channel_id,
                user_id=message.user_id,
            )
            
            outbound.contents.append(ContentBlock(
                type=MediaType.IMAGE,
                content="",
                mime_type="image/png",
                url=image_url,
            ))
            
            if response.text_content:
                outbound.add_text(response.text_content)
            
            if self._send_callback:
                await self._send_callback(outbound)
                logger.info("[IMProcessor] 图片消息已推送")
            
        except Exception as e:
            logger.error(f"[IMProcessor] 图片任务失败: {e}")
    
    async def _execute_video_task(
        self,
        message: InboundMessage,
        response: IMResponse,
    ):
        """执行视频生成任务
        
        流程：
        1. 获取参考图
        2. 先使用参考图生成视频第一帧图片（图生图）
        3. 再使用第一帧图片生成视频（图生视频）
        4. poll 直到完成
        5. 构建视频 OutboundMessage
        6. 调用发送回调
        """
        try:
            video_gen = get_video_gen_provider()
            if not video_gen:
                logger.error("[IMProcessor] VideoGen Provider 未配置")
                return
            
            # 需要 ImageGen Provider 来生成第一帧
            image_gen = get_image_gen_provider()
            if not image_gen:
                logger.error("[IMProcessor] ImageGen Provider 未配置（视频需要先生成第一帧）")
                return
            
            reference_image = await self._get_reference_image()
            if not reference_image:
                logger.warning("[IMProcessor] 无参考图，使用默认")
                reference_image = ""
            
            # Step 1: 先生成视频第一帧图片
            logger.info(f"[IMProcessor] 生成视频第一帧: {response.media_prompt}")
            
            first_frame_task = await image_gen.submit(
                prompt=response.media_prompt,
                reference_image=reference_image,
            )
            
            if first_frame_task.status.name not in ("PENDING", "RUNNING"):
                logger.error(f"[IMProcessor] 第一帧图片任务提交失败: {first_frame_task.error}")
                return
            
            # 轮询第一帧图片生成
            max_polls = 60
            first_frame_url = None
            for i in range(max_polls):
                await asyncio.sleep(2)
                first_frame_task = await image_gen.poll(first_frame_task.task_id)
                
                if first_frame_task.status.name == "SUCCEEDED":
                    if first_frame_task.result and first_frame_task.result.images:
                        first_frame_url = first_frame_task.result.images[0].url
                    break
                elif first_frame_task.status.name == "FAILED":
                    logger.error(f"[IMProcessor] 第一帧图片生成失败: {first_frame_task.error}")
                    return
            
            if not first_frame_url:
                logger.error("[IMProcessor] 第一帧图片生成超时或无结果")
                return
            
            logger.info(f"[IMProcessor] 第一帧图片完成: {first_frame_url}")
            
            # Step 2: 使用第一帧图片生成视频
            first_frame_data = await self._download_media(first_frame_url)
            if not first_frame_data:
                logger.error("[IMProcessor] 无法下载第一帧图片")
                return
            
            first_frame_b64 = base64.b64encode(first_frame_data).decode("utf-8")
            first_frame_uri = f"data:image/jpeg;base64,{first_frame_b64}"
            
            # 提交视频生成任务
            task = await video_gen.submit(
                prompt=response.media_prompt,
                reference_image=first_frame_uri,
            )
            
            if task.status.name not in ("PENDING", "RUNNING"):
                logger.error(f"[IMProcessor] 视频任务提交失败: {task.error}")
                return
            
            logger.info(f"[IMProcessor] 视频任务已提交: {task.task_id}")
            
            # 轮询视频生成
            max_polls = 120
            for i in range(max_polls):
                await asyncio.sleep(2)
                task = await video_gen.poll(task.task_id)
                
                if task.status.name == "SUCCEEDED":
                    break
                elif task.status.name == "FAILED":
                    logger.error(f"[IMProcessor] 视频生成失败: {task.error}")
                    return
            
            if task.status.name != "SUCCEEDED" or not task.result:
                logger.error("[IMProcessor] 视频生成超时")
                return
            
            video_url = task.result.videos[0].url if task.result.videos else None
            if not video_url:
                logger.error("[IMProcessor] 视频结果无 URL")
                return
            
            logger.info(f"[IMProcessor] 视频生成完成: {video_url}")
            
            outbound = OutboundMessage(
                channel_id=message.channel_id,
                user_id=message.user_id,
            )
            
            outbound.contents.append(ContentBlock(
                type=MediaType.VIDEO,
                content="",
                mime_type="video/mp4",
                url=video_url,
            ))
            
            if response.text_content:
                outbound.add_text(response.text_content)
            
            if self._send_callback:
                await self._send_callback(outbound)
                logger.info("[IMProcessor] 视频消息已推送")
            
        except Exception as e:
            logger.error(f"[IMProcessor] 视频任务失败: {e}")

    async def _get_reference_image(self) -> Optional[str]:
        """获取参考图"""
        if not self._reference_image_path:
            return None
        
        try:
            path = Path(self._reference_image_path)
            if not path.exists():
                logger.warning(f"[IMProcessor] 参考图不存在: {path}")
                return None
            
            image_bytes = path.read_bytes()
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            return f"data:image/jpeg;base64,{b64_data}"
            
        except Exception as e:
            logger.error(f"[IMProcessor] 读取参考图失败: {e}")
            return None
    
    async def _download_media(self, url: str) -> Optional[bytes]:
        """下载媒体文件"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
            return None
        except Exception as e:
            logger.error(f"[IMProcessor] 下载媒体失败: {e}")
            return None


# ---------------------------------------------------------------------------
# 全局实例管理
# ---------------------------------------------------------------------------

_im_processors: Dict[str, IMChannelProcessor] = {}


def get_im_processor(character_id: str) -> Optional[IMChannelProcessor]:
    """获取指定角色的 IM Processor"""
    return _im_processors.get(character_id)


def register_im_processor(
    character_id: str,
    character_config: Dict[str, Any],
    reference_image_path: Optional[str] = None,
    user_id: str = "default_user",  # ⭐ 新增：用户标识
) -> IMChannelProcessor:
    """注册 IM Processor
    
    Args:
        character_id: 角色标识
        character_config: 角色配置字典
        reference_image_path: 参考图路径
        user_id: 用户标识（用于 Redis 数据隔离）
    """
    processor = IMChannelProcessor(
        character_config=character_config,
        reference_image_path=reference_image_path,
        user_id=user_id,
    )
    _im_processors[character_id] = processor
    logger.info(f"[IMProcessor] 注册完成: {character_id}, user_id: {user_id}")
    return processor


def reset_im_processors():
    """重置所有 IM Processor（用于测试）"""
    _im_processors.clear()