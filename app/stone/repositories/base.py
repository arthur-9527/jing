"""Repository 基类 - Stone 数据层

提供：
- BaseRepository: PostgreSQL Repository 基类
- RedisRepository: Redis Repository 基类
"""

from typing import Any, Optional, TypeVar, Generic
from abc import ABC

from sqlalchemy import select, insert, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import Result

from app.stone.database import Database
from app.stone.redis_pool import RedisPool

T = TypeVar("T")


class BaseRepository(ABC):
    """PostgreSQL Repository 基类

    子类需要实现具体的 CRUD 方法
    """

    def __init__(self, db: Database):
        """初始化

        Args:
            db: Database 实例
        """
        self._db = db

    async def _execute(self, stmt) -> Result:
        """执行 SQL 语句

        Args:
            stmt: SQLAlchemy 语句

        Returns:
            执行结果
        """
        async with self._db.get_session() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result

    async def _execute_and_commit(self, stmt) -> Result:
        """执行 SQL 语句并提交

        Args:
            stmt: SQLAlchemy 语句

        Returns:
            执行结果
        """
        async with self._db.get_session() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result

    async def _scalar(self, stmt) -> Any:
        """执行并返回单个值

        Args:
            stmt: SQLAlchemy 语句

        Returns:
            单个值
        """
        async with self._db.get_session() as session:
            result = await session.execute(stmt)
            return result.scalar()

    async def _scalars(self, stmt) -> list:
        """执行并返回值列表

        Args:
            stmt: SQLAlchemy 语句

        Returns:
            值列表
        """
        async with self._db.get_session() as session:
            result = await session.execute(stmt)
            return result.scalars().all()

    async def _mappings(self, stmt) -> list[dict]:
        """执行并返回字典列表

        Args:
            stmt: SQLAlchemy 语句

        Returns:
            字典列表
        """
        async with self._db.get_session() as session:
            result = await session.execute(stmt)
            return result.mappings().all()

    async def _insert(self, table, data: dict) -> int:
        """插入单条记录

        Args:
            table: SQLAlchemy Table
            data: 数据字典

        Returns:
            插入的 ID
        """
        stmt = insert(table).values(**data).returning(table.c.id)
        result = await self._execute_and_commit(stmt)
        return result.scalar()

    async def _batch_insert(self, table, data: list[dict]) -> list[int]:
        """批量插入记录

        Args:
            table: SQLAlchemy Table
            data: 数据列表

        Returns:
            插入的 ID 列表
        """
        stmt = insert(table).values(data).returning(table.c.id)
        result = await self._execute_and_commit(stmt)
        return [row[0] for row in result.fetchall()]

    async def _update(self, table, id: int, data: dict) -> bool:
        """更新记录

        Args:
            table: SQLAlchemy Table
            id: 记录 ID
            data: 更新数据

        Returns:
            是否更新成功
        """
        stmt = update(table).where(table.c.id == id).values(**data)
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0

    async def _delete(self, table, id: int) -> bool:
        """删除记录

        Args:
            table: SQLAlchemy Table
            id: 记录 ID

        Returns:
            是否删除成功
        """
        stmt = delete(table).where(table.c.id == id)
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0

    async def _get_by_id(self, table, id: int) -> Optional[dict]:
        """根据 ID 获取记录

        Args:
            table: SQLAlchemy Table
            id: 记录 ID

        Returns:
            记录字典或 None
        """
        stmt = select(table).where(table.c.id == id)
        result = await self._mappings(stmt)
        return result[0] if result else None


class RedisRepository(ABC):
    """Redis Repository 基类

    子类需要实现具体的 Redis 操作方法
    """

    def __init__(self, redis: RedisPool, namespace: str):
        """初始化

        Args:
            redis: RedisPool 实例
            namespace: Key 命名空间
        """
        self._redis = redis
        self._namespace = namespace

    def _build_key(self, *parts: str) -> str:
        """构建 Redis Key

        Args:
            *parts: Key 各部分

        Returns:
            完整的 Redis Key
        """
        return f"{self._namespace}:{':'.join(str(p) for p in parts)}"

    # ============================================================
    # 常用操作（透传到 RedisPool）
    # ============================================================

    async def get(self, key: str) -> Optional[str]:
        """获取值"""
        return await self._redis.get(key)

    async def set(self, key: str, value: Any, ex: int = None) -> None:
        """设置值"""
        await self._redis.set(key, value, ex)

    async def delete(self, key: str) -> int:
        """删除 key"""
        return await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        """检查 key 是否存在"""
        return await self._redis.exists(key)

    async def expire(self, key: str, seconds: int) -> bool:
        """设置过期时间"""
        return await self._redis.expire(key, seconds)

    # ============================================================
    # Hash 操作
    # ============================================================

    async def hget(self, key: str, field: str) -> Optional[str]:
        """获取 Hash 字段"""
        return await self._redis.hget(key, field)

    async def hgetall(self, key: str) -> dict:
        """获取 Hash 所有字段"""
        return await self._redis.hgetall(key)

    async def hset(
        self, key: str, field: str = None, value: Any = None, mapping: dict = None
    ) -> int:
        """设置 Hash 字段"""
        return await self._redis.hset(key, field, value, mapping)

    async def hincrbyfloat(self, key: str, field: str, amount: float) -> float:
        """Hash 字段浮点数增量"""
        return await self._redis.hincrbyfloat(key, field, amount)

    # ============================================================
    # List 操作
    # ============================================================

    async def lpush(self, key: str, value: Any) -> int:
        """左侧推入"""
        return await self._redis.lpush(key, value)

    async def rpush(self, key: str, value: Any) -> int:
        """右侧推入"""
        return await self._redis.rpush(key, value)

    async def lpop(self, key: str) -> Optional[str]:
        """左侧弹出"""
        return await self._redis.lpop(key)

    async def rpop(self, key: str) -> Optional[str]:
        """右侧弹出"""
        return await self._redis.rpop(key)

    async def lrange(self, key: str, start: int, end: int) -> list:
        """获取列表范围"""
        return await self._redis.lrange(key, start, end)

    async def llen(self, key: str) -> int:
        """获取列表长度"""
        return await self._redis.llen(key)

    async def lset(self, key: str, index: int, value: Any) -> None:
        """设置列表指定索引的值"""
        await self._redis.lset(key, index, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        """裁剪列表"""
        await self._redis.ltrim(key, start, end)

    # ============================================================
    # Sorted Set 操作
    # ============================================================

    async def zadd(self, key: str, mapping: dict = None, member: str = None, score: float = None) -> int:
        """添加 Sorted Set 成员"""
        return await self._redis.zadd(key, mapping, member, score)

    async def zrange(self, key: str, start: int, end: int, withscores: bool = False) -> list:
        """获取 Sorted Set 范围"""
        return await self._redis.zrange(key, start, end, withscores)

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False) -> list:
        """获取 Sorted Set 范围（倒序）"""
        return await self._redis.zrevrange(key, start, end, withscores)