"""Redis 连接池管理 - Stone 数据层核心模块

统一管理 Redis 连接，提供：
- 异步连接池
- 常用操作的便捷封装
- Key 前缀管理
"""

from typing import Optional, Any, Union
import json

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import settings


class RedisPool:
    """Redis 连接池管理器"""

    def __init__(self):
        self._pool: Optional[Redis] = None
        self._initialized: bool = False

    async def initialize(self, redis_url: str = None) -> None:
        """初始化 Redis 连接池

        Args:
            redis_url: Redis 连接 URL，默认使用 settings.REDIS_URL
        """
        if self._initialized:
            return

        url = redis_url or settings.REDIS_URL

        self._pool = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
        )

        # 测试连接
        await self._pool.ping()

        self._initialized = True
        print("[Stone] Redis connection pool initialized")

    async def close(self) -> None:
        """关闭 Redis 连接池"""
        if self._pool:
            await self._pool.aclose()
            self._pool = None
            self._initialized = False
            print("[Stone] Redis connection pool closed")

    def get_client(self) -> Redis:
        """获取 Redis 客户端"""
        if not self._initialized or not self._pool:
            raise RuntimeError("Redis not initialized. Call initialize() first.")
        return self._pool

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized

    # ============================================================
    # 常用操作封装
    # ============================================================

    async def get(self, key: str) -> Optional[str]:
        """获取值"""
        return await self._pool.get(key)

    async def set(
        self, key: str, value: Union[str, dict, list], ex: int = None
    ) -> None:
        """设置值

        Args:
            key: Redis key
            value: 值（自动 JSON 序列化 dict/list）
            ex: 过期时间（秒）
        """
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        await self._pool.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        """删除 key(s)"""
        return await self._pool.delete(*keys)

    async def exists(self, key: str) -> bool:
        """检查 key 是否存在"""
        return await self._pool.exists(key) > 0

    async def expire(self, key: str, seconds: int) -> bool:
        """设置过期时间"""
        return await self._pool.expire(key, seconds)

    async def ttl(self, key: str) -> int:
        """获取剩余过期时间"""
        return await self._pool.ttl(key)

    # ============================================================
    # Hash 操作
    # ============================================================

    async def hget(self, key: str, field: str) -> Optional[str]:
        """获取 Hash 字段值"""
        return await self._pool.hget(key, field)

    async def hgetall(self, key: str) -> dict:
        """获取 Hash 所有字段"""
        result = await self._pool.hgetall(key)
        return result or {}

    async def hset(
        self, key: str, field: str = None, value: Any = None, mapping: dict = None
    ) -> int:
        """设置 Hash 字段

        Args:
            key: Redis key
            field: 字段名（单字段模式）
            value: 字段值（单字段模式）
            mapping: 字段-值映射（批量模式）
        """
        if mapping:
            # 序列化 dict/list 值
            serialized = {}
            for k, v in mapping.items():
                if isinstance(v, (dict, list)):
                    serialized[k] = json.dumps(v, ensure_ascii=False)
                else:
                    serialized[k] = str(v)
            return await self._pool.hset(key, mapping=serialized)
        elif field and value:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            return await self._pool.hset(key, field, str(value))
        else:
            raise ValueError("Either mapping or field+value must be provided")

    async def hdel(self, key: str, *fields: str) -> int:
        """删除 Hash 字段"""
        return await self._pool.hdel(key, *fields)

    async def hincrbyfloat(self, key: str, field: str, amount: float) -> float:
        """Hash 字段浮点数增量"""
        return await self._pool.hincrbyfloat(key, field, amount)

    async def hexists(self, key: str, field: str) -> bool:
        """检查 Hash 字段是否存在"""
        return await self._pool.hexists(key, field)

    # ============================================================
    # List 操作
    # ============================================================

    async def lpush(self, key: str, value: Union[str, dict, list]) -> int:
        """左侧推入"""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return await self._pool.lpush(key, value)

    async def rpush(self, key: str, value: Union[str, dict, list]) -> int:
        """右侧推入"""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return await self._pool.rpush(key, value)

    async def lpop(self, key: str) -> Optional[str]:
        """左侧弹出"""
        return await self._pool.lpop(key)

    async def rpop(self, key: str) -> Optional[str]:
        """右侧弹出"""
        return await self._pool.rpop(key)

    async def lrange(self, key: str, start: int, end: int) -> list:
        """获取列表范围"""
        return await self._pool.lrange(key, start, end)

    async def llen(self, key: str) -> int:
        """获取列表长度"""
        return await self._pool.llen(key)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        """裁剪列表"""
        await self._pool.ltrim(key, start, end)

    async def lset(self, key: str, index: int, value: Union[str, dict, list]) -> None:
        """设置列表元素"""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        await self._pool.lset(key, index, value)

    async def lrem(self, key: str, count: int, value: str) -> int:
        """删除列表元素"""
        return await self._pool.lrem(key, count, value)

    # ============================================================
    # Sorted Set 操作
    # ============================================================

    async def zadd(
        self, key: str, mapping: dict = None, member: str = None, score: float = None
    ) -> int:
        """添加 Sorted Set 成员

        Args:
            key: Redis key
            mapping: member-score 映射
            member: 成员名（单成员模式）
            score: 分数（单成员模式）
        """
        if mapping:
            return await self._pool.zadd(key, mapping)
        elif member and score is not None:
            return await self._pool.zadd(key, {member: score})
        else:
            raise ValueError("Either mapping or member+score must be provided")

    async def zrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list:
        """获取 Sorted Set 范围"""
        return await self._pool.zrange(key, start, end, withscores=withscores)

    async def zrevrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list:
        """获取 Sorted Set 范围（倒序）"""
        return await self._pool.zrevrange(key, start, end, withscores=withscores)

    async def zrem(self, key: str, *members: str) -> int:
        """删除 Sorted Set 成员"""
        return await self._pool.zrem(key, *members)

    async def zcard(self, key: str) -> int:
        """获取 Sorted Set 成员数"""
        return await self._pool.zcard(key)

    async def zscore(self, key: str, member: str) -> Optional[float]:
        """获取成员分数"""
        return await self._pool.zscore(key, member)

    # ============================================================
    # Scan 操作
    # ============================================================

    async def scan_iter(self, match: str = None, count: int = 100):
        """扫描匹配的 keys"""
        async for key in self._pool.scan_iter(match=match, count=count):
            yield key


# ============================================================
# 全局单例
# ============================================================

_redis_instance: Optional[RedisPool] = None


def get_redis_pool() -> RedisPool:
    """获取全局 Redis 实例（懒加载）"""
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = RedisPool()
    return _redis_instance


async def init_redis_pool(redis_url: str = None) -> None:
    """初始化全局 Redis 连接池"""
    redis = get_redis_pool()
    await redis.initialize(redis_url)


async def close_redis_pool() -> None:
    """关闭全局 Redis 连接池"""
    redis = get_redis_pool()
    await redis.close()


# ============================================================
# 兼容旧接口（过渡期使用）
# ============================================================


async def get_redis() -> Redis:
    """获取 Redis 客户端（兼容旧接口）

    注意：此接口返回原生 Redis 客户端，推荐使用 RedisPool
    """
    pool = get_redis_pool()
    if not pool.is_initialized:
        await pool.initialize()
    return pool.get_client()


async def init_redis() -> None:
    """初始化 Redis（兼容旧接口）"""
    await init_redis_pool()


async def close_redis() -> None:
    """关闭 Redis（兼容旧接口）"""
    await close_redis_pool()