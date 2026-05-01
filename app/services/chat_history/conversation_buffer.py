"""Redis 聊天记录缓冲区

功能：
1. 使用 Redis List 存储聊天记录（活动队列）
2. 400ms内两次 user 消息（无 assistant 干隔）自动合并
3. 保留最近 N 条消息（可配置）
4. 格式化历史（去时间戳）供 Prompt 使用
5. 双队列机制：满阈值时推送到持久化队列，定时任务写入数据库
6. 持久化队列永久保存，每小时由定时任务批量写入 PostgreSQL

⭐ Stone 迁移：所有 Redis 操作通过 ConversationBufferRepository
"""

import asyncio
import json
import time
from typing import Optional, Callable, Awaitable, Any
from loguru import logger

from app.config import settings
from app.stone.key_builder import RedisKeyBuilder


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
    - 活动队列（conv:{character_id}:{user_id}）：保留最近 N 条消息，供实时对话使用
    - 持久化队列（conv:persistent:{character_id}:{user_id}）：永久保存，等待定时任务写入数据库

    ⭐ Stone 迁移：通过 ConversationBufferRepository 操作数据
    """

    def __init__(
        self,
        user_id: str = "default_user",
        character_id: str = "default",
        max_size: int = MAX_HISTORY_SIZE,
        merge_window: float = 2.0,
        memory_threshold: int = MEMORY_THRESHOLD,
        extraction_batch: int = EXTRACTION_BATCH,
        stone_repo: Optional[Any] = None,  # ⭐ ConversationBufferRepository
    ):
        """初始化

        Args:
            user_id: 用户标识
            character_id: 角色标识
            max_size: 最大保留消息数（活动队列）
            merge_window: 合并时间窗口（秒）
            memory_threshold: 触发推送到持久化队列的阈值
            extraction_batch: 每次推送的批量大小
            stone_repo: Stone ConversationBufferRepository 实例
        """
        self.user_id = user_id
        self.character_id = character_id
        self.max_size = max_size
        self.merge_window = merge_window
        self.memory_threshold = memory_threshold
        self.extraction_batch = extraction_batch

        # ⭐ Stone Repository
        self._stone_repo = stone_repo

        # Redis keys
        _kb = RedisKeyBuilder()
        self._key = _kb.conversation(character_id=character_id, user_id=user_id)
        self._persistent_key = _kb.conversation_persistent(character_id=character_id, user_id=user_id)

        # 连接状态
        self._connected = False

        # 回调函数
        self._on_message_updated: Optional[Callable[[], Awaitable[None]]] = None

        # 记忆提取回调
        self._memory_extractor: Optional[Callable[[list[dict]], Awaitable[None]]] = None

        # Assistant 消息合并状态
        self._pending_assistant_content: str = ""
        self._pending_assistant_start: Optional[float] = None

    async def _get_repo(self):
        """懒加载 Stone ConversationBufferRepository"""
        if self._stone_repo is None:
            from app.stone.repositories.conversation_buffer import get_conversation_buffer_repo
            self._stone_repo = get_conversation_buffer_repo()
        return self._stone_repo

    async def _ensure_connected(self) -> None:
        """确保已连接（Stone 模式下连接由 RedisPool 统一管理）"""
        if not self._connected:
            from app.stone import init_redis_pool
            self._connected = True
            logger.debug("[ConvBuffer] 已连接(Stone)")

    def set_stone_repo(self, stone_repo: Any) -> None:
        """设置 Stone ConversationBufferRepository（延迟注入）"""
        self._stone_repo = stone_repo
        logger.info("[ConvBuffer] Stone Repository 已设置")

    # === 兼容旧接口 ===

    async def connect(self) -> None:
        """连接（兼容旧接口，Stone 模式下为 no-op）"""
        await self._ensure_connected()

    async def disconnect(self) -> None:
        """断开连接（兼容旧接口）"""
        self._connected = False
        logger.info("[ConvBuffer] 已断开")

    # === 消息追加 ===

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
        await self._ensure_connected()

        if timestamp is None:
            timestamp = time.time()

        if not text.strip():
            return

        repo = await self._get_repo()

        try:
            # 获取最后一条消息
            last_msg = await repo.lrange(self._key, -1, -1)

            if last_msg:
                last_data = json.loads(last_msg[0])
                last_role = last_data.get("role", "")

                # 如果最后一条是 assistant，直接追加内容
                if last_role == "assistant":
                    new_content = last_data.get("content", "") + text
                    last_data["content"] = new_content
                    last_data["ts"] = timestamp

                    # 合并心理活动
                    if inner_monologue:
                        existing_monologue = last_data.get("inner_monologue", "")
                        last_data["inner_monologue"] = existing_monologue + inner_monologue

                    await repo.lset(self._key, -1, json.dumps(last_data, ensure_ascii=False))
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

            await repo.rpush(self._key, json.dumps(msg_data, ensure_ascii=False))
            await repo.ltrim(self._key, -self.max_size, -1)
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
        await self._ensure_connected()

        if timestamp is None:
            timestamp = time.time()

        if not text.strip():
            return

        repo = await self._get_repo()

        try:
            # 1. 获取最后一条消息
            last_msg = await repo.lrange(self._key, -1, -1)
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

                await repo.lset(self._key, -1, json.dumps(merged_data, ensure_ascii=False))
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

                await repo.rpush(self._key, json.dumps(msg_data, ensure_ascii=False))

                # 裁剪到最大长度
                await repo.ltrim(self._key, -self.max_size, -1)

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

    # === 历史获取 ===

    async def get_formatted_history(
        self,
        max_items: Optional[int] = None,
        format_style: str = "you_me"
    ) -> str:
        """
        获取格式化历史（去时间戳）

        Args:
            max_items: 最大条目数（None = 全部）
            format_style: 格式化风格
                - "you_me": "你：... me：..."
                - "user_assistant": "用户：... AI：..."

        Returns:
            格式化后的历史文本
        """
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            if max_items:
                messages = await repo.lrange(self._key, -max_items * 2, -1)
            else:
                messages = await repo.lrange(self._key, 0, -1)

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
                parsed = parsed[-max_items * 2:]

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
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            messages = await repo.lrange(self._key, -count * 2, -1)

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
        repo = await self._get_repo()
        try:
            await repo.delete(self._key)
            logger.info(f"[ConvBuffer] 已清空历史: {self.user_id}")
        except Exception as e:
            logger.error(f"[ConvBuffer] 清空历史失败: {e}")

    async def get_length(self) -> int:
        """获取消息条数"""
        repo = await self._get_repo()
        try:
            return await repo.llen(self._key)
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取长度失败: {e}")
            return 0

    def set_memory_extractor(self, extractor: Callable[[list[dict]], Awaitable[None]]) -> None:
        """设置记忆提取回调函数"""
        self._memory_extractor = extractor
        logger.info(f"[ConvBuffer] 记忆提取器已设置: {self.user_id}")

    # === 持久化队列 ===

    async def check_and_push_to_persistent(self) -> None:
        """检查是否需要推送到持久化队列

        双队列机制：
        当活动队列消息数量超过阈值时：
        1. 从活动队列左侧弹出最早的 N 条消息
        2. 推送到持久化队列（永久保存）
        3. 持久化队列由定时任务每小时批量写入 PostgreSQL
        """
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            current_length = await repo.llen(self._key)

            if current_length < self.memory_threshold:
                logger.debug(
                    f"[ConvBuffer] 消息数量 {current_length} < 阈值 {self.memory_threshold}，跳过推送"
                )
                return

            logger.info(
                f"[ConvBuffer] 消息数量 {current_length} >= 阈值 {self.memory_threshold}，"
                f"开始推送 {self.extraction_batch} 条消息到持久化队列"
            )

            # 使用原始客户端 pipeline 批量操作（Stone RedisPool 提供）
            from app.stone import get_redis_pool
            client = await get_redis_pool().get_client()

            async with client.pipeline(transaction=False) as pipe:
                pipe.lpop(self._key, self.extraction_batch)
                results = await pipe.execute()

            popped_messages = results[0] if results else []

            if popped_messages:
                async with client.pipeline(transaction=False) as pipe:
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
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            return await repo.llen(self._persistent_key)
        except Exception as e:
            logger.error(f"[ConvBuffer] 获取持久化队列长度失败: {e}")
            return 0

    async def get_all_persistent_messages(self) -> list[dict]:
        """获取持久化队列所有消息（用于定时任务写入数据库）"""
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            all_messages = await repo.lrange(self._persistent_key, 0, -1)

            if not all_messages:
                return []

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
        repo = await self._get_repo()
        try:
            await repo.delete(self._persistent_key)
            logger.info(f"[ConvBuffer] 已清空持久化队列: {self._persistent_key}")
        except Exception as e:
            logger.error(f"[ConvBuffer] 清空持久化队列失败: {e}")

    async def pop_persistent_messages(self, count: int = 100) -> list[dict]:
        """从持久化队列弹出消息（写入数据库后删除）"""
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            messages = []
            for _ in range(count):
                msg = await repo.lpop(self._persistent_key)
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

    # === 兼容旧接口 ===

    async def check_and_extract_memories(self) -> None:
        """检查是否需要触发推送（兼容旧接口名）"""
        await self.check_and_push_to_persistent()

    async def extract_all_and_clear(self) -> list[dict]:
        """提取所有消息并清空"""
        await self._ensure_connected()
        repo = await self._get_repo()

        try:
            all_messages = await repo.lrange(self._key, 0, -1)

            if not all_messages:
                return []

            parsed = []
            for msg in all_messages:
                try:
                    parsed.append(json.loads(msg))
                except json.JSONDecodeError:
                    continue

            await repo.delete(self._key)
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
