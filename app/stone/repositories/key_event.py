"""KeyEvent Repository - 关键事件数据访问

提供 key_events 表的 CRUD 操作：
- insert: 插入关键事件
- batch_insert: 批量插入
- get_by_type: 按类型获取
- search_fts: FTS 搜索
- deactivate: 失效事件
- get_special_events: 获取特殊日期事件
"""

from datetime import datetime, date, timedelta
from typing import Optional, List

from sqlalchemy import select, update, delete, and_, text

from app.stone.database import Database, get_database
from app.stone.models.memory import key_events
from app.stone.repositories.base import BaseRepository


class KeyEventRepository(BaseRepository):
    """关键事件 Repository"""

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
        event_type: str,
        content: str,
        event_date: date = None,
        source_message_ids: List[int] = None,
        importance: float = 0.5,
        expires_at: datetime = None,
    ) -> int:
        """插入单条关键事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_type: 事件类型 (preference/fact/schedule/experience/emotion_trigger/initiative)
            content: 事件内容
            event_date: 重要日期（生日、纪念日等）
            source_message_ids: 来源消息 ID 列表
            importance: 重要性评分 (0-1)
            expires_at: 过期时间（日程类）

        Returns:
            插入的事件 ID
        """
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "event_type": event_type,
            "content": content,
            "event_date": event_date,
            "source_message_ids": source_message_ids or [],
            "importance": importance,
            "expires_at": expires_at,
        }
        return await self._insert(key_events, data)

    async def batch_insert(self, events: List[dict]) -> List[int]:
        """批量插入关键事件

        Args:
            events: 事件列表

        Returns:
            插入的事件 ID 列表
        """
        for event in events:
            if "source_message_ids" not in event:
                event["source_message_ids"] = []
            if "importance" not in event:
                event["importance"] = 0.5
        return await self._batch_insert(key_events, events)

    async def deactivate(self, event_id: int) -> bool:
        """失效事件

        Args:
            event_id: 事件 ID

        Returns:
            是否成功
        """
        return await self._update(key_events, event_id, {"is_active": False})

    async def batch_deactivate(self, event_ids: List[int]) -> int:
        """批量失效事件

        Args:
            event_ids: 事件 ID 列表

        Returns:
            更新的记录数
        """
        stmt = (
            update(key_events)
            .where(key_events.c.id.in_(event_ids))
            .values(is_active=False, updated_at=datetime.now())
        )
        result = await self._execute_and_commit(stmt)
        return result.rowcount

    async def cleanup_expired(self, character_id: str, user_id: str) -> int:
        """清理过期事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID

        Returns:
            失效的记录数
        """
        stmt = (
            update(key_events)
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.expires_at < datetime.now(),
                    key_events.c.is_active == True,
                )
            )
            .values(is_active=False, updated_at=datetime.now())
        )
        result = await self._execute_and_commit(stmt)
        return result.rowcount

    # ============================================================
    # 读操作
    # ============================================================

    async def get_by_type(
        self,
        character_id: str,
        user_id: str,
        event_type: str,
        limit: int = 50,
    ) -> List[dict]:
        """按类型获取事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_type: 事件类型
            limit: 最大条数

        Returns:
            事件列表
        """
        stmt = (
            select(key_events)
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.event_type == event_type,
                    key_events.c.is_active == True,
                )
            )
            .order_by(key_events.c.importance.desc(), key_events.c.created_at.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def get_by_date_range(
        self,
        character_id: str,
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 50,
    ) -> List[dict]:
        """按时间范围获取关键事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            start_time: 开始时间
            end_time: 结束时间
            limit: 最大条数

        Returns:
            事件列表
        """
        stmt = (
            select(key_events)
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.created_at >= start_time,
                    key_events.c.created_at <= end_time,
                    key_events.c.is_active == True,
                )
            )
            .order_by(key_events.c.importance.desc(), key_events.c.created_at.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def get_all_active(
        self,
        character_id: str,
        user_id: str,
        limit: int = 100,
    ) -> List[dict]:
        """获取所有活跃事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数

        Returns:
            事件列表
        """
        stmt = (
            select(key_events)
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.is_active == True,
                )
            )
            .order_by(key_events.c.importance.desc(), key_events.c.created_at.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def get_special_events(
        self,
        character_id: str,
        user_id: str,
        days_ahead: int = 30,
    ) -> List[dict]:
        """获取即将到来的特殊日期事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            days_ahead: 提前天数

        Returns:
            特殊事件列表
        """
        today = date.today()
        end_date = today + timedelta(days=days_ahead)

        stmt = (
            select(key_events)
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.event_date >= today,
                    key_events.c.event_date <= end_date,
                    key_events.c.is_active == True,
                )
            )
            .order_by(key_events.c.event_date)
        )
        return await self._mappings(stmt)

    async def search_fts(
        self,
        character_id: str,
        user_id: str,
        query: str,
        limit: int = 10,
        event_types: List[str] = None,
        use_chinese: bool = True,
    ) -> List[dict]:
        """全文搜索关键事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            query: 搜索查询
            limit: 最大条数
            event_types: 限定事件类型列表，None 表示不限
            use_chinese: 是否使用中文分词

        Returns:
            匹配的事件列表
        """
        tsv_column = "content_tsv_cn" if use_chinese else "content_tsv"
        fts_config = "chinese_zh" if use_chinese else "simple"

        type_condition = ""
        params = {
            "character_id": character_id,
            "user_id": user_id,
            "query": query,
            "limit": limit,
        }
        if event_types:
            type_condition = "AND event_type = ANY(:event_types)"
            params["event_types"] = tuple(event_types)

        sql = text(f"""
            SELECT id, character_id, user_id, event_type, event_date, content, importance, created_at,
                   ts_rank({tsv_column}, websearch_to_tsquery('{fts_config}', :query)) as rank
            FROM key_events
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND is_active = TRUE
              {type_condition}
              AND {tsv_column} @@ websearch_to_tsquery('{fts_config}', :query)
            ORDER BY rank DESC, importance DESC
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(sql, params)
            return [dict(row) for row in result.mappings()]

    async def get_by_id(self, event_id: int) -> Optional[dict]:
        """根据 ID 获取事件"""
        return await self._get_by_id(key_events, event_id)

    async def get_by_ids(
        self,
        ids: List[int],
    ) -> List[dict]:
        """根据 ID 列表批量获取关键事件

        Args:
            ids: 事件 ID 列表

        Returns:
            关键事件列表
        """
        if not ids:
            return []
        
        stmt = (
            select(key_events)
            .where(key_events.c.id.in_(ids))
            .order_by(key_events.c.importance.desc())
        )
        return await self._mappings(stmt)

    async def count(
        self,
        character_id: str,
        user_id: str,
        event_type: str = None,
    ) -> int:
        """统计事件数量"""
        conditions = [
            key_events.c.character_id == character_id,
            key_events.c.user_id == user_id,
            key_events.c.is_active == True,
        ]
        if event_type:
            conditions.append(key_events.c.event_type == event_type)

        stmt = select(key_events.c.id).where(and_(*conditions))
        result = await self._scalar(stmt)
        return result if result else 0


# ============================================================
# 全局实例
# ============================================================

_key_event_repo: Optional[KeyEventRepository] = None


def get_key_event_repo() -> KeyEventRepository:
    """获取 KeyEventRepository 实例"""
    global _key_event_repo
    if _key_event_repo is None:
        _key_event_repo = KeyEventRepository()
    return _key_event_repo