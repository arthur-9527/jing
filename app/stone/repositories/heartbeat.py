"""HeartbeatEvent Repository - 心动事件数据访问

提供 heartbeat_events 表的 CRUD 操作：
- insert: 插入心动事件
- get_high_intensity: 获取高强度心动事件
- search_fts: FTS 搜索
- get_recent: 获取最近心动事件
"""

from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, and_, text

from app.stone.database import Database, get_database
from app.stone.models.memory import heartbeat_events
from app.stone.repositories.base import BaseRepository


class HeartbeatEventRepository(BaseRepository):
    """心动事件 Repository"""

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
        event_node: str,
        trigger_text: str,
        emotion_state: dict,
        intensity: float,
        event_subtype: str = None,
        inner_monologue: str = None,
        source_message_id: int = None,
    ) -> int:
        """插入心动事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_node: 事件节点 (emotion_peak/relationship/user_reveal/special_moment)
            trigger_text: 触发文本
            emotion_state: PAD 状态快照
            intensity: 心动强度 (0-1)
            event_subtype: 事件子类型
            inner_monologue: 内心独白
            source_message_id: 来源消息 ID

        Returns:
            插入的事件 ID
        """
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "event_node": event_node,
            "event_subtype": event_subtype,
            "trigger_text": trigger_text,
            "emotion_state": emotion_state,
            "intensity": intensity,
            "inner_monologue": inner_monologue,
            "source_message_id": source_message_id,
        }
        return await self._insert(heartbeat_events, data)

    async def batch_insert(self, events: List[dict]) -> List[int]:
        """批量插入心动事件

        Args:
            events: 事件列表

        Returns:
            插入的事件 ID 列表
        """
        return await self._batch_insert(heartbeat_events, events)

    # ============================================================
    # 读操作
    # ============================================================

    async def get_recent(
        self,
        character_id: str,
        user_id: str,
        limit: int = 20,
        days: int = 7,
    ) -> List[dict]:
        """获取最近心动事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数
            days: 最近天数

        Returns:
            心动事件列表
        """
        cutoff = datetime.now() - timedelta(days=days)
        stmt = (
            select(heartbeat_events)
            .where(
                and_(
                    heartbeat_events.c.character_id == character_id,
                    heartbeat_events.c.user_id == user_id,
                    heartbeat_events.c.created_at >= cutoff,
                )
            )
            .order_by(heartbeat_events.c.intensity.desc(), heartbeat_events.c.created_at.desc())
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
        """按时间范围获取心动事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            start_time: 开始时间
            end_time: 结束时间
            limit: 最大条数

        Returns:
            心动事件列表
        """
        stmt = (
            select(heartbeat_events)
            .where(
                and_(
                    heartbeat_events.c.character_id == character_id,
                    heartbeat_events.c.user_id == user_id,
                    heartbeat_events.c.created_at >= start_time,
                    heartbeat_events.c.created_at <= end_time,
                )
            )
            .order_by(heartbeat_events.c.intensity.desc(), heartbeat_events.c.created_at.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def get_by_node(
        self,
        character_id: str,
        user_id: str,
        event_node: str,
        limit: int = 10,
    ) -> List[dict]:
        """根据事件节点获取心动事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_node: 事件节点类型
            limit: 最大条数

        Returns:
            心动事件列表
        """
        stmt = (
            select(heartbeat_events)
            .where(
                and_(
                    heartbeat_events.c.character_id == character_id,
                    heartbeat_events.c.user_id == user_id,
                    heartbeat_events.c.event_node == event_node,
                )
            )
            .order_by(heartbeat_events.c.created_at.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def search_fts(
        self,
        character_id: str,
        user_id: str,
        query: str,
        limit: int = 10,
        days: int = None,
        use_chinese: bool = True,
    ) -> List[dict]:
        """全文搜索心动事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            query: 搜索查询
            limit: 最大条数
            days: 时间范围（天），None 表示不限
            use_chinese: 是否使用中文分词

        Returns:
            匹配的事件列表
        """
        from datetime import datetime, timedelta

        tsv_column = "trigger_text_tsv_cn" if use_chinese else "trigger_text_tsv"
        fts_config = "chinese_zh" if use_chinese else "simple"

        time_condition = ""
        params = {
            "character_id": character_id,
            "user_id": user_id,
            "query": query,
            "limit": limit,
        }
        if days is not None:
            time_condition = "AND created_at >= :cutoff"
            params["cutoff"] = datetime.now() - timedelta(days=days)

        sql = text(f"""
            SELECT id, character_id, user_id, event_node, event_subtype,
                   trigger_text, emotion_state, intensity, inner_monologue, created_at,
                   ts_rank({tsv_column}, websearch_to_tsquery('{fts_config}', :query)) as rank
            FROM heartbeat_events
            WHERE character_id = :character_id
              AND user_id = :user_id
              {time_condition}
              AND {tsv_column} @@ websearch_to_tsquery('{fts_config}', :query)
            ORDER BY rank DESC, intensity DESC
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(sql, params)
            return [dict(row) for row in result.mappings()]

    async def get_by_id(self, event_id: int) -> Optional[dict]:
        """根据 ID 获取心动事件

        Args:
            event_id: 事件 ID

        Returns:
            事件字典或 None
        """
        return await self._get_by_id(heartbeat_events, event_id)

    async def get_high_intensity(
        self,
        character_id: str,
        user_id: str,
        min_intensity: float = 0.5,
        limit: int = 10,
        days: int = None,
    ) -> List[dict]:
        """获取高强度心动事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            min_intensity: 最小强度阈值
            limit: 最大条数
            days: 最近天数（可选）

        Returns:
            高强度心动事件列表
        """
        conditions = [
            heartbeat_events.c.character_id == character_id,
            heartbeat_events.c.user_id == user_id,
            heartbeat_events.c.intensity >= min_intensity,
        ]
        
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            conditions.append(heartbeat_events.c.created_at >= cutoff)

        stmt = (
            select(heartbeat_events)
            .where(and_(*conditions))
            .order_by(heartbeat_events.c.intensity.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def get_by_ids(
        self,
        ids: List[int],
    ) -> List[dict]:
        """根据 ID 列表批量获取心动事件

        Args:
            ids: 事件 ID 列表

        Returns:
            心动事件列表
        """
        if not ids:
            return []
        
        stmt = (
            select(heartbeat_events)
            .where(heartbeat_events.c.id.in_(ids))
            .order_by(heartbeat_events.c.intensity.desc())
        )
        return await self._mappings(stmt)

    async def count(
        self,
        character_id: str,
        user_id: str,
        days: int = None,
    ) -> int:
        """统计心动事件数量

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            days: 最近天数（可选）

        Returns:
            事件数量
        """
        conditions = [
            heartbeat_events.c.character_id == character_id,
            heartbeat_events.c.user_id == user_id,
        ]
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            conditions.append(heartbeat_events.c.created_at >= cutoff)

        stmt = select(heartbeat_events.c.id).where(and_(*conditions))
        result = await self._scalar(stmt)
        return result if result else 0


# ============================================================
# 全局实例（懒加载）
# ============================================================

_heartbeat_repo: Optional[HeartbeatEventRepository] = None


def get_heartbeat_repo() -> HeartbeatEventRepository:
    """获取 HeartbeatEventRepository 实例"""
    global _heartbeat_repo
    if _heartbeat_repo is None:
        _heartbeat_repo = HeartbeatEventRepository()
    return _heartbeat_repo