"""Diary Repository - 日记数据访问

提供 daily_diary 表的 CRUD 操作：
- insert: 插入日记
- get_by_date: 按日期获取
- get_recent: 获取最近日记
- search_vector: 向量相似度搜索
"""

from datetime import datetime, date, timedelta
from typing import Optional, List

from sqlalchemy import select, and_, text
import numpy as np

from app.stone.database import Database, get_database
from app.stone.models.memory import daily_diary, weekly_index, monthly_index, annual_index
from app.stone.repositories.base import BaseRepository


class DiaryRepository(BaseRepository):
    """日记 Repository"""

    def __init__(self, db: Database = None):
        """初始化

        Args:
            db: Database 实例，默认使用全局实例
        """
        super().__init__(db or get_database())

    # ============================================================
    # 写操作
    # ============================================================

    async def insert(
        self,
        character_id: str,
        user_id: str,
        diary_date: date,
        summary: str,
        embedding: List[float] = None,
        key_event_ids: List[int] = None,
        heartbeat_ids: List[int] = None,
        source_message_ids: List[int] = None,
        mood_summary: dict = None,
        highlight_count: int = 0,
    ) -> int:
        """插入日记

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            diary_date: 日记日期
            summary: 摘要内容
            embedding: 向量嵌入
            key_event_ids: 关键事件 ID 列表
            heartbeat_ids: 心动事件 ID 列表
            source_message_ids: 来源消息 ID 列表
            mood_summary: 心情摘要
            highlight_count: 高亮数量

        Returns:
            插入的日记 ID
        """
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "diary_date": diary_date,
            "summary": summary,
            "embedding": embedding,
            "key_event_ids": key_event_ids or [],
            "heartbeat_ids": heartbeat_ids or [],
            "source_message_ids": source_message_ids or [],
            "mood_summary": mood_summary,
            "highlight_count": highlight_count,
        }
        return await self._insert(daily_diary, data)

    async def batch_insert(self, diaries: List[dict]) -> List[int]:
        """批量插入日记

        Args:
            diaries: 日记列表

        Returns:
            插入的日记 ID 列表
        """
        for diary in diaries:
            if "key_event_ids" not in diary:
                diary["key_event_ids"] = []
            if "heartbeat_ids" not in diary:
                diary["heartbeat_ids"] = []
            if "source_message_ids" not in diary:
                diary["source_message_ids"] = []
        return await self._batch_insert(daily_diary, diaries)

    # ============================================================
    # 读操作
    # ============================================================

    async def get_recent(
        self,
        character_id: str,
        user_id: str,
        limit: int = 14,
        days: int = 30,
    ) -> List[dict]:
        """获取最近的日记

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数
            days: 最近天数

        Returns:
            日记列表
        """
        cutoff = date.today() - timedelta(days=days)
        stmt = (
            select(daily_diaries)
            .where(
                and_(
                    daily_diaries.c.character_id == character_id,
                    daily_diaries.c.user_id == user_id,
                    daily_diaries.c.diary_date >= cutoff,
                )
            )
            .order_by(daily_diaries.c.diary_date.desc())
            .limit(limit)
        )
        results = await self._mappings(stmt)
        # 反转顺序，使最早的在前面
        return list(reversed(results))

    async def get_by_date(
        self,
        character_id: str,
        user_id: str,
        diary_date: date,
    ) -> Optional[dict]:
        """按日期获取日记

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            diary_date: 日记日期

        Returns:
            日记字典或 None
        """
        stmt = (
            select(daily_diary)
            .where(
                and_(
                    daily_diary.c.character_id == character_id,
                    daily_diary.c.user_id == user_id,
                    daily_diary.c.diary_date == diary_date,
                )
            )
        )
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def get_by_id(self, diary_id: int) -> Optional[dict]:
        """根据 ID 获取日记"""
        return await self._get_by_id(daily_diary, diary_id)

    async def search_vector(
        self,
        character_id: str,
        user_id: str,
        query_embedding: List[float],
        limit: int = 5,
        days: int = 90,
    ) -> List[dict]:
        """向量相似度搜索日记

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            query_embedding: 查询向量
            limit: 最大条数
            days: 搜索范围天数

        Returns:
            相似日记列表（包含距离）
        """
        cutoff = date.today() - timedelta(days=days)
        
        # 使用 pgvector 的余弦距离
        sql = text("""
            SELECT id, character_id, user_id, diary_date, summary, 
                   key_event_ids, heartbeat_ids, mood_summary, highlight_count,
                   1 - (embedding <=> :embedding) as similarity
            FROM daily_diary
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND diary_date >= :cutoff
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :embedding
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(
                sql,
                {
                    "character_id": character_id,
                    "user_id": user_id,
                    "embedding": str(query_embedding),
                    "cutoff": cutoff,
                    "limit": limit,
                }
            )
            return [dict(row) for row in result.mappings()]

    async def count(
        self,
        character_id: str,
        user_id: str,
        days: int = None,
    ) -> int:
        """统计日记数量"""
        conditions = [
            daily_diary.c.character_id == character_id,
            daily_diary.c.user_id == user_id,
        ]
        if days:
            cutoff = date.today() - timedelta(days=days)
            conditions.append(daily_diary.c.diary_date >= cutoff)

        stmt = select(daily_diary.c.id).where(and_(*conditions))
        result = await self._scalar(stmt)
        return result if result else 0


# ============================================================
# 索引 Repository（周/月/年）
# ============================================================

class WeeklyIndexRepository(BaseRepository):
    """周索引 Repository"""

    def __init__(self, db: Database = None):
        super().__init__(db or get_database())

    async def insert(
        self,
        character_id: str,
        user_id: str,
        week_start: date,
        week_end: date,
        summary: str,
        embedding: List[float] = None,
        diary_ids: List[int] = None,
        highlight_events: dict = None,
    ) -> int:
        """插入周索引"""
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "week_start": week_start,
            "week_end": week_end,
            "summary": summary,
            "embedding": embedding,
            "diary_ids": diary_ids or [],
            "highlight_events": highlight_events,
        }
        return await self._insert(weekly_index, data)

    async def get_by_week(
        self,
        character_id: str,
        user_id: str,
        week_start: date,
    ) -> Optional[dict]:
        """按周获取索引"""
        stmt = (
            select(weekly_index)
            .where(
                and_(
                    weekly_index.c.character_id == character_id,
                    weekly_index.c.user_id == user_id,
                    weekly_index.c.week_start == week_start,
                )
            )
        )
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def search_vector(
        self,
        character_id: str,
        user_id: str,
        query_embedding: List[float],
        limit: int = 5,
    ) -> List[dict]:
        """向量搜索周索引"""
        sql = text("""
            SELECT id, week_start, week_end, summary, diary_ids, highlight_events,
                   1 - (embedding <=> :embedding) as similarity
            FROM weekly_index
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :embedding
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(
                sql,
                {
                    "character_id": character_id,
                    "user_id": user_id,
                    "embedding": str(query_embedding),
                    "limit": limit,
                }
            )
            return [dict(row) for row in result.mappings()]


class MonthlyIndexRepository(BaseRepository):
    """月索引 Repository"""

    def __init__(self, db: Database = None):
        super().__init__(db or get_database())

    async def search_vector(
        self,
        character_id: str,
        user_id: str,
        query_embedding: List[float],
        year: int = None,
        limit: int = 5,
    ) -> List[dict]:
        """向量搜索月索引

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            query_embedding: 查询向量
            year: 可选年份过滤
            limit: 最大条数

        Returns:
            相似月索引列表（包含相似度）
        """
        year_filter = f"AND year = {year}" if year else ""
        sql = text(f"""
            SELECT id, year, month, summary, weekly_ids,
                   1 - (embedding <=> :embedding) as similarity
            FROM monthly_index
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND embedding IS NOT NULL
              {year_filter}
            ORDER BY embedding <=> :embedding
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(
                sql,
                {
                    "character_id": character_id,
                    "user_id": user_id,
                    "embedding": str(query_embedding),
                    "limit": limit,
                }
            )
            return [dict(row) for row in result.mappings()]

    async def insert(
        self,
        character_id: str,
        user_id: str,
        year: int,
        month: int,
        summary: str,
        embedding: List[float] = None,
        weekly_ids: List[int] = None,
    ) -> int:
        """插入月索引"""
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "year": year,
            "month": month,
            "summary": summary,
            "embedding": embedding,
            "weekly_ids": weekly_ids or [],
        }
        return await self._insert(monthly_index, data)

    async def get_by_month(
        self,
        character_id: str,
        user_id: str,
        year: int,
        month: int,
    ) -> Optional[dict]:
        """按月获取索引"""
        stmt = (
            select(monthly_index)
            .where(
                and_(
                    monthly_index.c.character_id == character_id,
                    monthly_index.c.user_id == user_id,
                    monthly_index.c.year == year,
                    monthly_index.c.month == month,
                )
            )
        )
        results = await self._mappings(stmt)
        return results[0] if results else None


class AnnualIndexRepository(BaseRepository):
    """年索引 Repository"""

    def __init__(self, db: Database = None):
        super().__init__(db or get_database())

    async def search_vector(
        self,
        character_id: str,
        user_id: str,
        query_embedding: List[float],
        limit: int = 5,
    ) -> List[dict]:
        """向量搜索年索引

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            query_embedding: 查询向量
            limit: 最大条数

        Returns:
            相似年索引列表（包含相似度）
        """
        sql = text("""
            SELECT id, year, summary, monthly_ids,
                   1 - (embedding <=> :embedding) as similarity
            FROM annual_index
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :embedding
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(
                sql,
                {
                    "character_id": character_id,
                    "user_id": user_id,
                    "embedding": str(query_embedding),
                    "limit": limit,
                }
            )
            return [dict(row) for row in result.mappings()]

    async def insert(
        self,
        character_id: str,
        user_id: str,
        year: int,
        summary: str,
        embedding: List[float] = None,
        monthly_ids: List[int] = None,
    ) -> int:
        """插入年索引"""
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "year": year,
            "summary": summary,
            "embedding": embedding,
            "monthly_ids": monthly_ids or [],
        }
        return await self._insert(annual_index, data)

    async def get_by_year(
        self,
        character_id: str,
        user_id: str,
        year: int,
    ) -> Optional[dict]:
        """按年获取索引"""
        stmt = (
            select(annual_index)
            .where(
                and_(
                    annual_index.c.character_id == character_id,
                    annual_index.c.user_id == user_id,
                    annual_index.c.year == year,
                )
            )
        )
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def get_by_ids(
        self,
        ids: List[int],
    ) -> List[dict]:
        """根据 ID 列表批量获取年索引"""
        if not ids:
            return []
        stmt = select(annual_index).where(annual_index.c.id.in_(ids))
        return await self._mappings(stmt)


# ============================================================
# 全局实例
# ============================================================

_diary_repo: Optional[DiaryRepository] = None
_weekly_repo: Optional[WeeklyIndexRepository] = None
_monthly_repo: Optional[MonthlyIndexRepository] = None
_annual_repo: Optional[AnnualIndexRepository] = None


def get_diary_repo() -> DiaryRepository:
    """获取 DiaryRepository 实例"""
    global _diary_repo
    if _diary_repo is None:
        _diary_repo = DiaryRepository()
    return _diary_repo


def get_weekly_repo() -> WeeklyIndexRepository:
    """获取 WeeklyIndexRepository 实例"""
    global _weekly_repo
    if _weekly_repo is None:
        _weekly_repo = WeeklyIndexRepository()
    return _weekly_repo


def get_monthly_repo() -> MonthlyIndexRepository:
    """获取 MonthlyIndexRepository 实例"""
    global _monthly_repo
    if _monthly_repo is None:
        _monthly_repo = MonthlyIndexRepository()
    return _monthly_repo


def get_annual_repo() -> AnnualIndexRepository:
    """获取 AnnualIndexRepository 实例"""
    global _annual_repo
    if _annual_repo is None:
        _annual_repo = AnnualIndexRepository()
    return _annual_repo