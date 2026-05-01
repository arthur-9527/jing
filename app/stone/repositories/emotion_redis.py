"""Emotion State Repository (Redis) - 情绪状态数据访问

提供情绪状态的 Redis 操作：
- get_state: 获取情绪状态
- set_state: 设置情绪状态
- update_pad: 更新 PAD 值
- get_history: 获取历史状态
"""

from typing import Optional, List, Dict, Any
import json
from datetime import datetime

from app.stone.redis_pool import RedisPool, get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository


class EmotionStateRepository(RedisRepository):
    """情绪状态 Repository (Redis)

    使用 Hash 存储 PAD 状态：
    - P: Pleasure (愉悦度)
    - A: Arousal (激活度)
    - D: Dominance (支配度)
    """

    def __init__(self, redis: RedisPool = None, key_builder: RedisKeyBuilder = None):
        """初始化

        Args:
            redis: RedisPool 实例，默认使用全局实例
            key_builder: RedisKeyBuilder 实例
        """
        super().__init__(redis or get_redis_pool(), "emotion")
        self._key_builder = key_builder or RedisKeyBuilder()

    # ============================================================
    # 完整状态读写（String JSON - 兼容 EmotionService）
    # ============================================================

    async def load_state(self, character_id: str) -> Optional[Dict[str, Any]]:
        """加载完整情绪状态（JSON String）

        Args:
            character_id: 角色 ID

        Returns:
            完整状态字典或 None
        """
        key = self._key_builder.emotion_state(character_id)
        data = await self.get(key)
        
        if not data:
            return None
        
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    async def save_state(
        self,
        character_id: str,
        state: Dict[str, Any],
        ex: int = 86400 * 7,  # 7天过期
    ) -> None:
        """保存完整情绪状态（JSON String）

        Args:
            character_id: 角色 ID
            state: 完整状态字典
            ex: 过期时间（秒）
        """
        key = self._key_builder.emotion_state(character_id)
        await self.set(key, json.dumps(state, ensure_ascii=False), ex=ex)

    # ============================================================
    # Hash 状态读写
    # ============================================================

    async def get_state_hash(self, character_id: str) -> Optional[Dict[str, Any]]:
        """获取情绪状态

        Args:
            character_id: 角色 ID

        Returns:
            情绪状态字典，包含 P, A, D 值和元数据
        """
        key = self._key_builder.emotion_state(character_id)
        data = await self.hgetall(key)
        
        if not data:
            return None
        
        # 转换数值类型
        result = {}
        for field, value in data.items():
            if field in ("P", "A", "D", "intensity"):
                result[field] = float(value)
            elif field in ("timestamp", "last_event_time"):
                result[field] = float(value)
            else:
                result[field] = value
        
        return result

    async def set_state_hash(
        self,
        character_id: str,
        P: float,
        A: float,
        D: float,
        intensity: float = 0.5,
        metadata: dict = None,
    ) -> None:
        """设置情绪状态（Hash 存储）

        Args:
            character_id: 角色 ID
            P: Pleasure (愉悦度)
            A: Arousal (激活度)
            D: Dominance (支配度)
            intensity: 情绪强度
            metadata: 其他元数据
        """
        key = self._key_builder.emotion_state(character_id)
        
        data = {
            "P": str(P),
            "A": str(A),
            "D": str(D),
            "intensity": str(intensity),
            "timestamp": str(datetime.now().timestamp()),
        }
        
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (dict, list)):
                    data[k] = json.dumps(v)
                else:
                    data[k] = str(v)
        
        await self.hset(key, mapping=data)

    async def update_pad(
        self,
        character_id: str,
        delta_P: float = 0.0,
        delta_A: float = 0.0,
        delta_D: float = 0.0,
    ) -> Dict[str, float]:
        """更新 PAD 值（增量）

        Args:
            character_id: 角色 ID
            delta_P: P 增量
            delta_A: A 增量
            delta_D: D 增量

        Returns:
            更新后的 PAD 值
        """
        key = self._key_builder.emotion_state(character_id)
        
        # 使用 Redis 的 HINCRBYFLOAT
        new_P = await self.hincrbyfloat(key, "P", delta_P)
        new_A = await self.hincrbyfloat(key, "A", delta_A)
        new_D = await self.hincrbyfloat(key, "D", delta_D)
        
        # 更新时间戳
        await self.hset(key, "timestamp", str(datetime.now().timestamp()))
        
        return {
            "P": new_P,
            "A": new_A,
            "D": new_D,
        }

    async def delete_state(self, character_id: str) -> int:
        """删除情绪状态

        Args:
            character_id: 角色 ID

        Returns:
            删除的 key 数量
        """
        key = self._key_builder.emotion_state(character_id)
        return await self.delete(key)

    # ============================================================
    # 心动事件详情（Hash）
    # ============================================================

    async def save_heart_event(
        self,
        character_id: str,
        user_id: str,
        event_id: str,
        event_data: Dict[str, Any],
        ex: int = 86400 * 7,
    ) -> None:
        """保存心动事件详情到 Hash

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_id: 事件 ID
            event_data: 事件数据
            ex: 过期时间（秒）
        """
        key = self._key_builder.heart_event(character_id, user_id, event_id)
        
        # 转换数据为字符串
        data = {}
        for k, v in event_data.items():
            if isinstance(v, (dict, list)):
                data[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, datetime):
                data[k] = v.isoformat()
            else:
                data[k] = str(v)
        
        await self.hset(key, mapping=data)
        await self.expire(key, ex)

    async def get_heart_event(
        self,
        character_id: str,
        user_id: str,
        event_id: str,
    ) -> Optional[Dict[str, Any]]:
        """获取心动事件详情

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_id: 事件 ID

        Returns:
            事件数据或 None
        """
        key = self._key_builder.heart_event(character_id, user_id, event_id)
        return await self.hgetall(key)

    # ============================================================
    # 心动事件列表（Sorted Set）
    # ============================================================

    async def add_heartbeat_event(
        self,
        character_id: str,
        user_id: str,
        event_id: str,
        intensity: float,
        timestamp: float = None,
    ) -> int:
        """添加心动事件到列表

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_id: 事件 ID
            intensity: 心动强度（作为 score）
            timestamp: 时间戳（可选）

        Returns:
            添加的数量
        """
        key = self._key_builder.heart_events_list(character_id, user_id)
        score = timestamp or datetime.now().timestamp()
        return await self.zadd(key, member=event_id, score=score)

    async def get_heartbeat_events(
        self,
        character_id: str,
        user_id: str,
        limit: int = 10,
        descending: bool = True,
    ) -> List[Dict[str, Any]]:
        """获取心动事件列表

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数
            descending: 是否倒序（最新在前）

        Returns:
            心动事件列表（包含 event_id 和 score）
        """
        key = self._key_builder.heart_events_list(character_id, user_id)
        
        if descending:
            results = await self.zrevrange(key, 0, limit - 1, withscores=True)
        else:
            results = await self.zrange(key, 0, limit - 1, withscores=True)
        
        return [
            {"event_id": item[0], "score": item[1]}
            for item in results
        ]

    async def count_heartbeat_events(
        self,
        character_id: str,
        user_id: str,
    ) -> int:
        """统计心动事件数量"""
        key = self._key_builder.heart_events_list(character_id, user_id)
        # 使用 Redis ZCARD 命令
        client = await self._redis.get_client()
        return await client.zcard(key)


# ============================================================
# 全局实例
# ============================================================

_emotion_repo: Optional[EmotionStateRepository] = None


def get_emotion_repo() -> EmotionStateRepository:
    """获取 EmotionStateRepository 实例"""
    global _emotion_repo
    if _emotion_repo is None:
        _emotion_repo = EmotionStateRepository()
    return _emotion_repo