"""Affection Repository (Redis) - 好感度数据访问

提供好感度的 Redis 操作：
- get_level: 获取好感度等级
- set_level: 设置好感度等级
- update_level: 更新好感度（增量）
- get_context: 获取好感度上下文
- set_context: 设置好感度上下文
"""

from typing import Optional, Dict, Any, List
import json
from datetime import datetime
from enum import Enum

from app.stone.redis_pool import RedisPool, get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository


# 三维好感度维度枚举
class AffectionDimension(Enum):
    """好感度维度"""
    TRUST = "trust"
    INTIMACY = "intimacy"
    RESPECT = "respect"


# 好感度等级定义
AFFECTION_LEVELS = {
    0: {"name": "陌生", "min": 0.0, "max": 0.1},
    1: {"name": "认识", "min": 0.1, "max": 0.25},
    2: {"name": "朋友", "min": 0.25, "max": 0.4},
    3: {"name": "好友", "min": 0.4, "max": 0.55},
    4: {"name": "亲密", "min": 0.55, "max": 0.7},
    5: {"name": "恋人", "min": 0.7, "max": 0.85},
    6: {"name": "深爱", "min": 0.85, "max": 1.0},
}


class AffectionRepository(RedisRepository):
    """好感度 Repository (Redis)

    使用 Hash 存储好感度状态：
    - level: 好感度等级 (0-6)
    - value: 好感度数值 (0-1)
    - interactions: 互动次数
    - last_interaction: 最后互动时间
    """

    def __init__(self, redis: RedisPool = None, key_builder: RedisKeyBuilder = None):
        """初始化

        Args:
            redis: RedisPool 实例，默认使用全局实例
            key_builder: RedisKeyBuilder 实例
        """
        super().__init__(redis or get_redis_pool(), "affection")
        self._key_builder = key_builder or RedisKeyBuilder()

    # ============================================================
    # 好感度状态读写
    # ============================================================

    # ============================================================
    # 三维好感度状态读写（兼容 AffectionService）
    # ============================================================

    async def get_state_3d(
        self,
        character_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """获取三维好感度状态

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            三维状态字典，包含每个维度的 base 和 emotional_retained
            格式：{
                "trust_base": float,
                "trust_emotional_retained": float,
                "intimacy_base": float,
                "intimacy_emotional_retained": float,
                "respect_base": float,
                "respect_emotional_retained": float,
            }
        """
        key = self._key_builder.affection_state(character_id, user_id)
        data = await self.hgetall(key)
        
        if not data:
            return None
        
        # 转换数值类型
        result = {}
        for field, value in data.items():
            if field.endswith("_base") or field.endswith("_emotional_retained"):
                result[field] = float(value) if value else 0.0
            else:
                result[field] = value
        
        return result

    async def set_state_3d(
        self,
        character_id: str,
        user_id: str,
        dimensions: Dict[AffectionDimension, Dict[str, float]],
    ) -> None:
        """设置三维好感度状态

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            dimensions: 三维状态，格式：
                {
                    AffectionDimension.TRUST: {"base": float, "emotional_retained": float},
                    AffectionDimension.INTIMACY: {"base": float, "emotional_retained": float},
                    AffectionDimension.RESPECT: {"base": float, "emotional_retained": float},
                }
        """
        key = self._key_builder.affection_state(character_id, user_id)
        
        mapping = {}
        for dim, state in dimensions.items():
            dim_key = dim.value
            mapping[f"{dim_key}_base"] = str(state.get("base", 0.0))
            mapping[f"{dim_key}_emotional_retained"] = str(state.get("emotional_retained", 0.0))
        
        await self.hset(key, mapping=mapping)

    async def incr_retained(
        self,
        character_id: str,
        user_id: str,
        dimension: AffectionDimension,
        delta: float,
    ) -> float:
        """增加感性保留值（原子操作 HINCRBYFLOAT）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            dimension: 好感度维度
            delta: 增量值

        Returns:
            更新后的感性保留值
        """
        key = self._key_builder.affection_state(character_id, user_id)
        retained_key = f"{dimension.value}_emotional_retained"
        new_value = await self.hincrbyfloat(key, retained_key, delta)
        return new_value

    async def settle_3d(
        self,
        character_id: str,
        user_id: str,
        new_bases: Dict[AffectionDimension, float],
    ) -> None:
        """日记结算：更新 base，归零 emotional_retained

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            new_bases: 各维度新 base 值
        """
        key = self._key_builder.affection_state(character_id, user_id)
        
        mapping = {}
        for dim in AffectionDimension:
            dim_key = dim.value
            mapping[f"{dim_key}_base"] = str(new_bases.get(dim, 0.0))
            mapping[f"{dim_key}_emotional_retained"] = "0.0"
        
        await self.hset(key, mapping=mapping)

    # ============================================================
    # 单维度好感度（旧接口，兼容）
    # ============================================================

    async def get_state(self, character_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """获取好感度状态（单维度旧接口）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            好感度状态字典
        """
        key = self._key_builder.affection_state(character_id, user_id)
        data = await self.hgetall(key)
        
        if not data:
            return None
        
        # 转换数值类型
        result = {}
        for field, value in data.items():
            if field in ("level", "interactions", "positive_events", "negative_events"):
                result[field] = int(value)
            elif field in ("value", "trust", "intimacy"):
                result[field] = float(value)
            elif field in ("last_interaction", "last_positive", "last_negative"):
                result[field] = float(value)
            else:
                result[field] = value
        
        return result

    async def set_state(
        self,
        character_id: str,
        user_id: str,
        value: float,
        level: int = None,
        interactions: int = 0,
        metadata: dict = None,
    ) -> None:
        """设置好感度状态

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            value: 好感度数值 (0-1)
            level: 好感度等级（自动计算如果未提供）
            interactions: 互动次数
            metadata: 其他元数据
        """
        key = self._key_builder.affection_state(character_id, user_id)
        
        # 自动计算等级
        if level is None:
            level = self._calculate_level(value)
        
        data = {
            "value": str(value),
            "level": str(level),
            "interactions": str(interactions),
            "last_interaction": str(datetime.now().timestamp()),
        }
        
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (dict, list)):
                    data[k] = json.dumps(v)
                else:
                    data[k] = str(v)
        
        await self.hset(key, mapping=data)

    async def update_value(
        self,
        character_id: str,
        user_id: str,
        delta: float,
        is_positive: bool = True,
    ) -> Dict[str, Any]:
        """更新好感度数值（增量）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            delta: 增量值
            is_positive: 是否为正向变化

        Returns:
            更新后的好感度状态
        """
        key = self._key_builder.affection_state(character_id, user_id)
        
        # 更新好感度值
        new_value = await self.hincrbyfloat(key, "value", delta)
        
        # 限制范围 [0, 1]
        new_value = max(0.0, min(1.0, new_value))
        await self.hset(key, "value", str(new_value))
        
        # 更新等级
        new_level = self._calculate_level(new_value)
        await self.hset(key, "level", str(new_level))
        
        # 更新互动次数
        await self.hincrbyfloat(key, "interactions", 1)
        
        # 更新事件计数
        if is_positive:
            await self.hincrbyfloat(key, "positive_events", 1)
            await self.hset(key, "last_positive", str(datetime.now().timestamp()))
        else:
            await self.hincrbyfloat(key, "negative_events", 1)
            await self.hset(key, "last_negative", str(datetime.now().timestamp()))
        
        # 更新时间戳
        await self.hset(key, "last_interaction", str(datetime.now().timestamp()))
        
        return await self.get_state(character_id, user_id)

    async def delete_state(self, character_id: str, user_id: str) -> int:
        """删除好感度状态

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            删除的 key 数量
        """
        key = self._key_builder.affection_state(character_id, user_id)
        return await self.delete(key)

    # ============================================================
    # 好感度上下文
    # ============================================================

    async def get_context(self, character_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """获取好感度上下文

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            好感度上下文字典
        """
        key = self._key_builder.affection_context(character_id, user_id)
        data = await self.get(key)
        
        if not data:
            return None
        
        return json.loads(data)

    async def set_context(
        self,
        character_id: str,
        user_id: str,
        context: Dict[str, Any],
        ttl: int = 3600,
    ) -> None:
        """设置好感度上下文

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            context: 上下文数据
            ttl: 过期时间（秒）
        """
        key = self._key_builder.affection_context(character_id, user_id)
        await self.set(key, json.dumps(context), ex=ttl)

    async def delete_context(self, character_id: str, user_id: str) -> int:
        """删除好感度上下文

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            删除的 key 数量
        """
        key = self._key_builder.affection_context(character_id, user_id)
        return await self.delete(key)

    async def refresh_context_ttl(self, character_id: str, user_id: str, ttl: int = 3600) -> bool:
        """刷新好感度上下文 TTL

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            ttl: 过期时间（秒）

        Returns:
            是否成功刷新
        """
        key = self._key_builder.affection_context(character_id, user_id)
        return await self.expire(key, ttl)

    # 好感度状态 scan 迭代（用于衰减任务）
    async def scan_state_keys(
        self,
        pattern: str = None,
        count: int = 100,
    ) -> list[str]:
        """扫描好感度状态 key

        Args:
            pattern: 匹配模式（默认 affection:*）
            count: 每次扫描数量

        Returns:
            匹配的 key 列表
        """
        client = await self._redis.get_client()
        match = pattern or f"{self._namespace}:affection:*"
        keys = []
        cursor = 0
        while True:
            cursor, batch = await client.scan(cursor, match=match, count=count)
            for k in batch:
                keys.append(k.decode() if isinstance(k, bytes) else k)
            if cursor == 0:
                break
        return keys

    async def get_state_retained(self, character_id: str, user_id: str) -> dict[str, float]:
        """获取三维 emotional_retained 值

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            {dim_name: retained_value} 字典
        """
        key = self._key_builder.affection_state(character_id, user_id)
        fields = [
            "trust_emotional_retained",
            "intimacy_emotional_retained",
            "respect_emotional_retained",
        ]
        result = {}
        for field in fields:
            val = await self.hget(key, field)
            result[field] = float(val) if val else 0.0
        return result

    async def set_state_retained(
        self,
        character_id: str,
        user_id: str,
        retained: dict[str, float],
    ) -> None:
        """设置三维 emotional_retained 值

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            retained: {dim_name_emotional_retained: value} 字典
        """
        key = self._key_builder.affection_state(character_id, user_id)
        mapping = {k: str(v) for k, v in retained.items()}
        await self.hset(key, mapping=mapping)

    # ============================================================
    # 辅助方法
    # ============================================================

    def _calculate_level(self, value: float) -> int:
        """根据数值计算好感度等级

        Args:
            value: 好感度数值 (0-1)

        Returns:
            好感度等级 (0-6)
        """
        for level, info in AFFECTION_LEVELS.items():
            if info["min"] <= value < info["max"]:
                return level
        return 6  # 最高等级

    def get_level_info(self, level: int) -> Dict[str, Any]:
        """获取等级信息

        Args:
            level: 好感度等级

        Returns:
            等级信息字典
        """
        return AFFECTION_LEVELS.get(level, AFFECTION_LEVELS[0])


# ============================================================
# 全局实例
# ============================================================

_affection_repo: Optional[AffectionRepository] = None


def get_affection_repo() -> AffectionRepository:
    """获取 AffectionRepository 实例"""
    global _affection_repo
    if _affection_repo is None:
        _affection_repo = AffectionRepository()
    return _affection_repo