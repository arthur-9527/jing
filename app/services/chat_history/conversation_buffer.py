"""Redis 聊天记录缓冲区

功能：
1. 使用 Redis List 存储聊天记录（活动队列）
2. 20秒内两次 user 消息（无 assistant 干隔）自动合并
3. 保留最近 N 条消息（可配置）
4. 格式化历史（去时间戳）供 Prompt 使用
5. 双队列机制：满阈值时推送到持久化队列，定时任务写入数据库
6. 持久化队列永久保存，每小时由定时任务批量写入 PostgreSQL
"""

import asyncio
import json
import time
from typing import Optional, Callable, Awaitable
from loguru import logger

import redis.asyncio as aioredis

from app.config import settings


# 记忆提取配置
MEMORY_THRESHOLD = 80       # 当记录超过此数量时触发提取
EXTRACTION_BATCH = 50      # 每次提取的批量大小
MAX_HISTORY_SIZE = 100     # 最大保留消息数

# ASR 合并配置：400ms内的新ASR结果（中间/最终）覆盖上一条
USER_MERGE_WINDOW = 0.4   # user 消息覆盖时间窗口（秒）

# Assistant 消息合并配置：500ms内的连续输出合并为一条
ASSISTANT_MERGE_WINDOW = 0.5   # assistant 消息合并时间窗口（秒）


class ConversationBuffer:
    """Redis 聊天记录缓冲区
    
    双队列架构：
    - 活动队列（chat_history:{user_id}）：保留最近 N 条消息，供实时对话使用
    - 持久化队列（memory:buffer:chat:{character_id}:{user_id}）：永久保存，等待定时任务写入数据库
    """

    def __init__(
        self,
        user_id: str = "default_user",
        character_id: str = "default",
        max_size: int = MAX_HISTORY_SIZE,
        merge_window: float = 2.0,
        redis_db: int = 2,
        memory_threshold: int = MEMORY_THRESHOLD,
        extraction_batch: int = EXTRACTION_BATCH,
    ):
        """
        Args:
            user_id: 用户标识
            character_id: 角色标识
            max_size: 最大保留消息数（活动队列）
            merge_window: 合并时间窗口（秒）
            redis_db: Redis 数据库编号
            memory_threshold: 触发推送到持久化队列的阈值
            extraction_batch: 每次推送的批量大小
        
        双队列机制：
        - 活动队列满阈值时，弹出早期消息到持久化队列
        - 持久化队列由定时任务每小时批量写入 PostgreSQL
        """
        self.user_id = user_id
        self.character_id = character_id
        self.max_size = max_size
        self.merge_window = merge_window
        self.redis_db = redis_db
        self.memory_threshold = memory_threshold
        self.extraction_batch = extraction_batch
        
        # Redis keys
        self._key = f"chat_history:{user_id}"  # 活动队列
        self._persistent_key = f"memory:buffer:chat:{character_id}:{user_id}"  # 持久化队列
        
        # Redis 连接
        self._redis: Optional[aioredis.Redis] = None
        self._connected = False
        
        # 回调函数
        self._on_message_updated: Optional[Callable[[], Awaitable[None]]] = None
        
        # 记忆提取回调
        self._memory_extractor: Optional[Callable[[list[dict]], Awaitable[None]]] = None
        
        # Assistant 消息合并状态
        self._pending_assistant_content: str = ""  # 待确认的 assistant 内容
        self._pending_assistant_start: Optional[float] = None  # 开始时间

    async def connect(self) -> None:
        """连接 Redis"""
        if self._connected:
            return
            
        try:
            # 从 settings 获取 Redis URL
            redis_url = settings.REDIS_URL
            # 替换数据库编号
            if "/0" in redis_url:
                redis_url = redis_url.replace("/0", f"/{self.redis_db}")
            elif "/1" in redis_url:
                redis_url = redis_url.replace("/1", f"/{self.redis_db}")
            else:
                redis_url = f"{redis_url}/{self.redis_db}"
            
            self._redis = await aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._redis.ping()
            self._connected = True
            logger.info(f"[ConvBuffer] Redis 已连接: {redis_url}")
        except Exception as e:
            logger.error(f"[ConvBuffer] Redis 连接失败: {e}")
            raise

    async def disconnect(self) -> None:
        """断开 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("[ConvBuffer] Redis 已断开")

    async def append_user_message(self, text: str, timestamp: Optional[float] = None, item_id: Optional[str] = None) -> None:
        """追加用户消息（自动合并）
        
        Args:
            text: 消息文本
            timestamp: 时间戳
            item_id: ASR item_id，用于识别同一句话的中间结果和最终结果
        """
        await self._append_message("user", text, timestamp, item_id)

    async def append_assistant_message(
        self,
        text: str,
        inner_monologue: Optional[str] = None,
        timestamp: Optional[float] = None
    ) -> None:
        """追加 AI 消息（合并到上一条 assistant 消息）
        
        Args:
            text: 消息文本
            inner_monologue: 心理活动内容（将附加在消息中）
            timestamp: 时间戳
        
        逻辑：直接追加到上一条 assistant 消息，不创建新条目
        """
        if not self._connected:
            await self.connect()
            
        if timestamp is None:
            timestamp = time.time()
            
        if not text.strip():
            return

        try:
            # 获取最后一条消息
            last_msg = await self._redis.lrange(self._key, -1, -1)
            
            if last_msg:
                last_data = json.loads(last_msg[0])
                last_role = last_data.get("role", "")
                
                # 如果最后一条是 assistant，直接追加内容
                if last_role == "assistant":
                    new_content = last_data.get("content", "") + text
                    last_data["content"] = new_content
                    last_data["ts"] = timestamp  # 更新时间戳
                    
                    # 合并心理活动
                    if inner_monologue:
                        existing_monologue = last_data.get("inner_monologue", "")
                        last_data["inner_monologue"] = existing_monologue + inner_monologue
                    
                    await self._redis.lset(self._key, -1, json.dumps(last_data, ensure_ascii=False))
                    logger.debug(f"[ConvBuffer] Assistant 追加: {text[:30]}... (总长度: {len(new_content)})")
                    return
            
            # 如果最后一条不是 assistant，创建新消息
            msg_data = {
                "ts": timestamp,
                "role": "assistant",
                "content": text,
                "character_id": self.character_id,
                "user_id": self.user_id,
            }
            if inner_monologue:
                msg_data["inner_monologue"] = inner_monologue
            
            await self._redis.rpush(self._key, json.dumps(msg_data, ensure_ascii=False))
            await self._redis.ltrim(self._key, -self.max_size, -1)
            logger.info(f"[ConvBuffer] Assistant 新消息: {text[:50]}...")
                
        except Exception as e:
            logger.error(f"[ConvBuffer] 追加 assistant 消息失败: {e}")

    async def _append_message(
        self,
        role: str,
        text: str,
        timestamp: Optional[float] = None,
        item_id: Optional[str] = None
    ) -> None:
        """追加消息（基于 item_id 的覆盖逻辑）
        
        核心逻辑：
        1. 如果传入了 item_id，且与最后一条消息的 item_id 相同 → 覆盖（同一句话）
        2. 如果没有 item_id 或 item_id 不同 → 检查时间窗口
           - user → user + 时间差 < 400ms → 覆盖（语音中的短暂停顿）
           - 其他情况 → 创建新消息
        """
        if not self._connected:
            await self.connect()
            
        if timestamp is None:
            timestamp = time.time()
            
        if not text.strip():
            return

        try:
            # 1. 获取最后一条消息
            last_msg = await self._redis.lrange(self._key, -1, -1)
            should_overwrite = False
            overwrite_reason = ""
            
            if last_msg:
                last_data = json.loads(last_msg[0])
                last_role = last_data.get("role", "")
                last_item_id = last_data.get("item_id")
                
                # 优先使用 item_id 判断（同一句话的中间结果和最终结果）
                if item_id and last_item_id == item_id:
                    # 同一个 item_id → 同一句话，覆盖
                    should_overwrite = True
                    overwrite_reason = f"同一item_id={item_id}"
                elif role == "user" and last_role == "user":
                    # 没有 item_id 或不同 item_id，使用时间窗口判断
                    time_diff = timestamp - last_data.get("ts", 0)
                    if time_diff < USER_MERGE_WINDOW:
                        should_overwrite = True
                        overwrite_reason = f"时间覆盖 diff={time_diff*1000:.0f}ms < {USER_MERGE_WINDOW*1000:.0f}ms"
                    else:
                        overwrite_reason = f"超时创建 diff={time_diff*1000:.0f}ms >= {USER_MERGE_WINDOW*1000:.0f}ms"
            
            if should_overwrite:
                # 覆盖最后一条：更新内容和时间戳
                merged_data = {
                    "ts": timestamp,
                    "role": role,
                    "content": text,
                }
                # 只有 user 消息保存 item_id
                if item_id:
                    merged_data["item_id"] = item_id
                    
                await self._redis.lset(self._key, -1, json.dumps(merged_data, ensure_ascii=False))
                logger.debug(f"[ConvBuffer] 覆盖: {overwrite_reason}, text={text[:30]}...")
                
                # 触发回调通知 LLM
                await self._trigger_callback()
            else:
                # 创建新消息
                msg_data = {
                    "ts": timestamp,
                    "role": role,
                    "content": text,
                }
                # 只有 user 消息保存 item_id
                if item_id:
                    msg_data["item_id"] = item_id
                    
                await self._redis.rpush(self._key, json.dumps(msg_data, ensure_ascii=False))
                
                # 裁剪到最大长度
                await self._redis.ltrim(self._key, -self.max_size, -1)
                
                logger.info(f"[ConvBuffer] 新消息: role={role}, text={text[:50]}...")
                
                # 触发回调通知 LLM
                if role == "user":
                    await self._trigger_callback()
                
                # 检查是否需要触发记忆提取
                await self.check_and_extract_memories()
                
        except Exception as e:
            logger.error(f"[ConvBuffer] 追加消息失败: {e}")

    async def _trigger_callback(self) -> None:
        """触发消息更新回调"""
        if self._on_message_updated:
            try:
                await self._on_message_updated()
            except Exception as e:
                logger.error(f"[ConvBuffer] 回调执行失败: {e}")

    def set_message_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """设置消息更新回调"""
        self._on_message_updated = callback

    async def get_formatted_history(
        self,
        max_items: Optional[int] = None,
        format_style: str = "you_me"
    ) -> str:
        """
        获取格式化历史（去时间戳）
        
        ⭐ 优化：直接在 Redis 层限制范围，避免拉取全部数据。
        
        Args:
            max_items: 最大条目数（None = 全部）
            format_style: 格式化风格
                - "you_me": "你：... me：..." 
                - "user_assistant": "用户：... AI：..."
        
        Returns:
            格式化后的历史文本
        """
        if not self._connected:
            await self.connect()
            
        try:
            # ⭐ 优化：直接在 Redis 层限制范围
            if max_items:
                # 每条消息可能是 user 或 assistant，取 max_items * 2 条
                messages = await self._redis.lrange(self._key, -max_items * 2, -1)
            else:
                messages = await self._redis.lrange(self._key, 0, -1)
            
            if not messages:
                return ""
            
            # 解析并限制条数
            parsed = []
            for msg in messages:
                try:
                    data = json.loads(msg)
                    parsed.append(data)
                except json.JSONDecodeError:
                    continue
            
            if max_items:
                parsed = parsed[-max_items * 2:]  # user + assistant 配对
            
            # 格式化
            # 改为 assistant/user 格式以便区分
            role_map = {"user": "user", "assistant": "assistant"}
            
            lines = []
            for msg in parsed:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                role_text = role_map.get(role, role)
                lines.append(f"{role_text}: {content}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取历史失败: {e}")
            return ""

    async def get_recent_messages(self, count: int = 10) -> list[dict]:
        """获取最近 N 条消息（带时间戳）"""
        if not self._connected:
            await self.connect()
            
        try:
            messages = await self._redis.lrange(self._key, -count * 2, -1)
            
            parsed = []
            for msg in messages:
                try:
                    parsed.append(json.loads(msg))
                except json.JSONDecodeError:
                    continue
                    
            return parsed
            
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取最近消息失败: {e}")
            return []

    async def clear(self) -> None:
        """清空历史"""
        if not self._connected:
            await self.connect()
            
        try:
            await self._redis.delete(self._key)
            logger.info(f"[ConvBuffer] 已清空历史: {self.user_id}")
        except Exception as e:
            logger.error(f"[ConvBuffer] 清空历史失败: {e}")

    async def get_length(self) -> int:
        """获取消息条数"""
        if not self._connected:
            await self.connect()
            
        try:
            return await self._redis.llen(self._key)
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取长度失败: {e}")
            return 0

    def set_memory_extractor(self, extractor: Callable[[list[dict]], Awaitable[None]]) -> None:
        """设置记忆提取回调函数
        
        Args:
            extractor: 异步函数，接收消息列表，提取关键信息
                      签名: async def extractor(messages: list[dict]) -> None
        """
        self._memory_extractor = extractor
        logger.info(f"[ConvBuffer] 记忆提取器已设置: {self.user_id}")

    async def check_and_push_to_persistent(self) -> None:
        """检查是否需要推送到持久化队列
        
        双队列机制：
        当活动队列消息数量超过阈值时：
        1. 从活动队列左侧弹出最早的 N 条消息
        2. 推送到持久化队列（永久保存）
        3. 持久化队列由定时任务每小时批量写入 PostgreSQL
        """
        try:
            current_length = await self.get_length()
            
            # 检查是否超过阈值
            if current_length < self.memory_threshold:
                logger.debug(
                    f"[ConvBuffer] 消息数量 {current_length} < 阈值 {self.memory_threshold}，跳过推送"
                )
                return
            
            logger.info(
                f"[ConvBuffer] 消息数量 {current_length} >= 阈值 {self.memory_threshold}，"
                f"开始推送 {self.extraction_batch} 条消息到持久化队列"
            )
            
            # ⭐ 优化：使用 Redis Pipeline 批量操作，减少网络往返
            # 原来：最多 100 次串行 Redis 调用（50 lpop + 50 rpush）
            # 现在：1 次 Pipeline 批量操作
            async with self._redis.pipeline(transaction=False) as pipe:
                # 先批量弹出消息
                pipe.lpop(self._key, self.extraction_batch)
                results = await pipe.execute()
            
            popped_messages = results[0] if results else []
            
            if popped_messages:
                # 批量推送到持久化队列
                async with self._redis.pipeline(transaction=False) as pipe:
                    for msg in popped_messages:
                        pipe.rpush(self._persistent_key, msg)
                    await pipe.execute()
                
                logger.info(
                    f"[ConvBuffer] 已推送 {len(popped_messages)} 条消息到持久化队列: {self._persistent_key}"
                )
                
        except Exception as e:
            logger.error(f"[ConvBuffer] 推送到持久化队列失败: {e}")

    async def get_persistent_queue_length(self) -> int:
        """获取持久化队列消息条数"""
        if not self._connected:
            await self.connect()
            
        try:
            return await self._redis.llen(self._persistent_key)
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取持久化队列长度失败: {e}")
            return 0

    async def get_all_persistent_messages(self) -> list[dict]:
        """获取持久化队列所有消息（用于定时任务写入数据库）
        
        Returns:
            所有消息列表（解析后的 dict）
        """
        if not self._connected:
            await self.connect()
            
        try:
            # 获取所有消息
            all_messages = await self._redis.lrange(self._persistent_key, 0, -1)
            
            if not all_messages:
                return []
            
            # 解析消息
            parsed = []
            for msg in all_messages:
                try:
                    parsed.append(json.loads(msg))
                except json.JSONDecodeError:
                    continue
            
            return parsed
            
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取持久化队列消息失败: {e}")
            return []

    async def clear_persistent_queue(self) -> None:
        """清空持久化队列（写入数据库后调用）"""
        if not self._connected:
            await self.connect()
            
        try:
            await self._redis.delete(self._persistent_key)
            logger.info(f"[ConvBuffer] 已清空持久化队列: {self._persistent_key}")
        except Exception as e:
            logger.error(f"[ConvBuffer] 清空持久化队列失败: {e}")

    async def pop_persistent_messages(self, count: int = 100) -> list[dict]:
        """从持久化队列弹出消息（写入数据库后删除）
        
        Args:
            count: 弹出数量
        
        Returns:
            弹出的消息列表
        """
        if not self._connected:
            await self.connect()
            
        try:
            messages = []
            for _ in range(count):
                msg = await self._redis.lpop(self._persistent_key)
                if msg is None:
                    break
                try:
                    messages.append(json.loads(msg))
                except json.JSONDecodeError:
                    continue
            
            if messages:
                logger.info(f"[ConvBuffer] 从持久化队列弹出 {len(messages)} 条消息")
            
            return messages
            
        except Exception as e:
            logger.error(f"[ConvBuffer] 弹出持久化队列消息失败: {e}")
            return []

    # ============ 兼容旧接口 ============
    
    async def check_and_extract_memories(self) -> None:
        """检查是否需要触发推送（兼容旧接口名）
        
        实际调用 check_and_push_to_persistent
        """
        await self.check_and_push_to_persistent()

    async def extract_all_and_clear(self) -> list[dict]:
        """提取所有消息并清空
        
        Returns:
            所有消息列表
        """
        if not self._connected:
            await self.connect()
            
        try:
            # 获取所有消息
            all_messages = await self._redis.lrange(self._key, 0, -1)
            
            if not all_messages:
                return []
            
            # 解析消息
            parsed = []
            for msg in all_messages:
                try:
                    parsed.append(json.loads(msg))
                except json.JSONDecodeError:
                    continue
            
            # 清空
            await self._redis.delete(self._key)
            logger.info(f"[ConvBuffer] 已提取并清空 {len(parsed)} 条消息: {self.user_id}")
            
            return parsed
            
        except Exception as e:
            logger.error(f"[ConvBuffer] 提取并清空失败: {e}")
            return []


# 全局实例（懒加载）- 使用 (user_id, character_id) 作为 key
_buffers: dict[tuple[str, str], ConversationBuffer] = {}


async def get_conversation_buffer(
    user_id: str = "default_user",
    character_id: str = "default",
) -> ConversationBuffer:
    """获取或创建 ConversationBuffer 实例
    
    Args:
        user_id: 用户标识
        character_id: 角色标识
    """
    key = (user_id, character_id)
    if key not in _buffers:
        buffer = ConversationBuffer(user_id=user_id, character_id=character_id)
        await buffer.connect()
        _buffers[key] = buffer
    return _buffers[key]


async def close_all_buffers() -> None:
    """关闭所有缓冲区"""
    for buffer in _buffers.values():
        await buffer.disconnect()
    _buffers.clear()
