"""ConversationBuffer Repository - 对话历史缓冲区数据访问

提供对话历史的 Redis 操作：
- append_user_message: 追加用户消息
- append_assistant_message: 追加 AI 消息
- get_formatted_history: 获取格式化历史
- get_recent_messages: 获取最近消息
- push_to_persistent: 推送到持久化队列

基于 services/chat_history/conversation_buffer.py 迁移
"""

from typing import Optional, Dict, Any, List, Callable, Awaitable
import json
import time

from loguru import logger

from app.stone.redis_pool import RedisPool, get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository


# 配置常量
MAX_HISTORY_SIZE = 100       # 最大保留消息数
MEMORY_THRESHOLD = 80       # 触发推送阈值
EXTRACTION_BATCH = 50       # 每次提取批量大小
USER_MERGE_WINDOW = 0.4     # user 消息覆盖时间窗口（秒）


class ConversationBufferRepository(RedisRepository):
    """对话历史缓冲区 Repository (Redis)

    双队列架构：
    - 活动队列（chat:{character_id}:{user_id}）：保留最近 N 条消息
    - 久化队列（chat:persistent:{character_id}:{user_id}）：永久保存
    """

    def __init__(
        self,
        redis: RedisPool = None,
        key_builder: RedisKeyBuilder = None,
        max_size: int = MAX_HISTORY_SIZE,
        memory_threshold: int = MEMORY_THRESHOLD,
    ):
        """初始化

        Args:
            redis: RedisPool 实例
            key_builder: RedisKeyBuilder 实例
            max_size: 最大保留消息数
            memory_threshold: 触发推送阈值
        """
        super().__init__(redis or get_redis_pool(), "agent")
        self._key_builder = key_builder or RedisKeyBuilder()
        self._max_size = max_size
        self._memory_threshold = memory_threshold

    # ============================================================
    # Key 构建方法
    # ============================================================

    def _active_key(self, character_id: str, user_id: str) -> str:
        """活动队列 Key"""
        return self._key_builder.conversation(character_id=character_id, user_id=user_id)

    def _persistent_key(self, character_id: str, user_id: str) -> str:
        """持久化队列 Key"""
        return self._key_builder.conversation_persistent(character_id=character_id, user_id=user_id)

    # ============================================================
    # 消息追加
    # ============================================================

    async def append_user_message(
        self,
        character_id: str,
        user_id: str,
        text: str,
        timestamp: Optional[float] = None,
        item_id: Optional[str] = None,
    ) -> None:
        """追加用户消息（自动合并）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            text: 消息文本
            timestamp: 时间戳
            item_id: ASR item_id，用于识别同一句话
        """
        key = self._active_key(character_id, user_id)
        await self._append_message(key, "user", text, timestamp, item_id)

    async def append_assistant_message(
        self,
        character_id: str,
        user_id: str,
        text: str,
        inner_monologue: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """追加 AI 消息（合并到上一条 assistant 消息）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            text: 消息文本
            inner_monologue: 心理活动内容
            timestamp: 时间戳
        """
        if timestamp is None:
            timestamp = time.time()

        if not text.strip():
            return

        key = self._active_key(character_id, user_id)

        # 获取最后一条消息
        last_msg = await self.lrange(key, -1, -1)

        if last_msg:
            last_data = json.loads(last_msg[0])
            last_role = last_data.get("role", "")

            # 如果最后一条是 assistant，直接追加内容
            if last_role == "assistant":
                new_content = last_data.get("content", "") + text
                last_data["content"] = new_content
                last_data["ts"] = timestamp

                if inner_monologue:
                    existing_monologue = last_data.get("inner_monologue", "")
                    last_data["inner_monologue"] = existing_monologue + inner_monologue

                await self._redis.lset(key, -1, json.dumps(last_data, ensure_ascii=False))
                logger.debug(f"[ConvBufferRepo] Assistant 追加: {text[:30]}...")
                return

        # 如果最后一条不是 assistant，创建新消息
        msg_data = {
            "ts": timestamp,
            "role": "assistant",
            "content": text,
            "character_id": character_id,
            "user_id": user_id,
        }
        if inner_monologue:
            msg_data["inner_monologue"] = inner_monologue

        await self.rpush(key, msg_data)
        await self._redis.ltrim(key, -self._max_size, -1)
        logger.info(f"[ConvBufferRepo] Assistant 新消息: {text[:50]}...")

    async def _append_message(
        self,
        key: str,
        role: str,
        text: str,
        timestamp: Optional[float] = None,
        item_id: Optional[str] = None,
    ) -> None:
        """追加消息（基于 item_id 的覆盖逻辑）"""
        if timestamp is None:
            timestamp = time.time()

        if not text.strip():
            return

        # 获取最后一条消息
        last_msg = await self.lrange(key, -1, -1)
        should_overwrite = False

        if last_msg:
            last_data = json.loads(last_msg[0])
            last_role = last_data.get("role", "")
            last_item_id = last_data.get("item_id")

            # 优先使用 item_id 判断
            if item_id and last_item_id == item_id:
                should_overwrite = True
            elif role == "user" and last_role == "user":
                time_diff = timestamp - last_data.get("ts", 0)
                if time_diff < USER_MERGE_WINDOW:
                    should_overwrite = True

        if should_overwrite:
            # 覆盖最后一条
            merged_data = {
                "ts": timestamp,
                "role": role,
                "content": text,
            }
            if item_id:
                merged_data["item_id"] = item_id

            await self._redis.lset(key, -1, json.dumps(merged_data, ensure_ascii=False))
            logger.debug(f"[ConvBufferRepo] 覆盖: text={text[:30]}...")
        else:
            # 创建新消息
            msg_data = {
                "ts": timestamp,
                "role": role,
                "content": text,
            }
            if item_id:
                msg_data["item_id"] = item_id

            await self.rpush(key, msg_data)
            await self._redis.ltrim(key, -self._max_size, -1)
            logger.info(f"[ConvBufferRepo] 新消息: role={role}, text={text[:50]}...")

    # ============================================================
    # 历史获取
    # ============================================================

    async def get_formatted_history(
        self,
        character_id: str,
        user_id: str,
        max_items: Optional[int] = None,
    ) -> str:
        """获取格式化历史（去时间戳）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            max_items: 最大条目数

        Returns:
            格式化后的历史文本
        """
        key = self._active_key(character_id, user_id)

        if max_items:
            messages = await self.lrange(key, -max_items * 2, -1)
        else:
            messages = await self.lrange(key, 0, -1)

        if not messages:
            return ""

        parsed = []
        for msg in messages:
            try:
                parsed.append(json.loads(msg))
            except json.JSONDecodeError:
                continue

        if max_items:
            parsed = parsed[-max_items * 2:]

        # 格式化
        lines = []
        for msg in parsed:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")

        return "\n".join(lines)

    async def get_recent_messages(
        self,
        character_id: str,
        user_id: str,
        count: int = 10,
    ) -> List[dict]:
        """获取最近 N 条消息"""
        key = self._active_key(character_id, user_id)
        messages = await self.lrange(key, -count * 2, -1)

        parsed = []
        for msg in messages:
            try:
                parsed.append(json.loads(msg))
            except json.JSONDecodeError:
                continue

        return parsed

    async def get_length(self, character_id: str, user_id: str) -> int:
        """获取消息条数"""
        key = self._active_key(character_id, user_id)
        return await self.llen(key)

    async def clear(self, character_id: str, user_id: str) -> None:
        """清空历史"""
        key = self._active_key(character_id, user_id)
        await self.delete(key)
        logger.info(f"[ConvBufferRepo] 已清空历史: {user_id}")

    # ============================================================
    # 持久化队列
    # ============================================================

    async def check_and_push_to_persistent(
        self,
        character_id: str,
        user_id: str,
        batch_size: int = EXTRACTION_BATCH,
    ) -> int:
        """检查是否需要推送到持久化队列

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            batch_size: 每次推送数量

        Returns:
            推送的消息数量
        """
        active_key = self._active_key(character_id, user_id)
        current_length = await self.llen(active_key)

        if current_length < self._memory_threshold:
            return 0

        logger.info(
            f"[ConvBufferRepo] 消息数量 {current_length} >= 阈值 {self._memory_threshold}，"
            f"开始推送 {batch_size} 条消息到持久化队列"
        )

        # 批量弹出消息
        popped_messages = []
        for _ in range(batch_size):
            msg = await self.lpop(active_key)
            if msg is None:
                break
            popped_messages.append(msg)

        if popped_messages:
            # 批量推送到持久化队列
            persistent_key = self._persistent_key(character_id, user_id)
            for msg in popped_messages:
                await self.rpush(persistent_key, msg)

            logger.info(
                f"[ConvBufferRepo] 已推送 {len(popped_messages)} 条消息到持久化队列"
            )

        return len(popped_messages)

    async def get_persistent_queue_length(self, character_id: str, user_id: str) -> int:
        """获取持久化队列消息条数"""
        key = self._persistent_key(character_id, user_id)
        return await self.llen(key)

    async def get_all_persistent_messages(
        self,
        character_id: str,
        user_id: str,
    ) -> List[dict]:
        """获取持久化队列所有消息"""
        key = self._persistent_key(character_id, user_id)
        all_messages = await self.lrange(key, 0, -1)

        parsed = []
        for msg in all_messages:
            try:
                parsed.append(json.loads(msg))
            except json.JSONDecodeError:
                continue

        return parsed

    async def clear_persistent_queue(self, character_id: str, user_id: str) -> None:
        """清空持久化队列"""
        key = self._persistent_key(character_id, user_id)
        await self.delete(key)
        logger.info(f"[ConvBufferRepo] 已清空持久化队列")

    async def pop_persistent_messages(
        self,
        character_id: str,
        user_id: str,
        count: int = 100,
    ) -> List[dict]:
        """从持久化队列弹出消息"""
        key = self._persistent_key(character_id, user_id)
        messages = []

        for _ in range(count):
            msg = await self.lpop(key)
            if msg is None:
                break
            try:
                messages.append(json.loads(msg))
            except json.JSONDecodeError:
                continue

        return messages


# ============================================================
# 全局实例（懒加载）
# ============================================================

_conversation_buffer_repo: Optional[ConversationBufferRepository] = None


def get_conversation_buffer_repo() -> ConversationBufferRepository:
    """获取 ConversationBufferRepository 实例"""
    global _conversation_buffer_repo
    if _conversation_buffer_repo is None:
        _conversation_buffer_repo = ConversationBufferRepository()
    return _conversation_buffer_repo