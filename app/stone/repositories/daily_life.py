"""DailyLifeEvent Repository - 日常事务事件数据访问

提供 daily_life_events 表的 CRUD 操作：
- insert: 插入日常事务事件
- get_recent: 获取最近事件
- get_by_date_range: 按时间范围获取
"""

from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, and_

from app.stone.database import Database, get_database
from app.stone.models.memory import daily_life_events, heartbeat_events
from app.stone.repositories.base import BaseRepository


class DailyLifeEventRepository(BaseRepository):
    """日常事务事件 Repository"""

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
        event_time: datetime,
        scenario: str,
        scenario_detail: str = None,
        dialogue: str = None,
        inner_monologue: str = None,
        emotion_delta: dict = None,
        intensity: float = 0.3,
    ) -> int:
        """插入日常事务事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            event_time: 事件时间
            scenario: 场景类型
            scenario_detail: 场景详情
            dialogue: 对话内容
            inner_monologue: 内心独白
            emotion_delta: 情绪变化
            intensity: 事件强度

        Returns:
            插入的事件 ID
        """
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "event_time": event_time,
            "scenario": scenario,
            "scenario_detail": scenario_detail,
            "dialogue": dialogue,
            "inner_monologue": inner_monologue,
            "emotion_delta": emotion_delta or {"P": 0.0, "A": 0.0, "D": 0.0},
            "intensity": intensity,
        }
        return await self._insert(daily_life_events, data)

    async def insert_with_heartbeat(
        self,
        event_data: dict,
        event_node: str = "special_moment",
        event_subtype: str = None,
    ) -> tuple[int, int]:
        """插入日常事务事件并同时写入心动事件（原子性事务）

        Args:
            event_data: 事件数据字典
            event_node: 心动事件节点
            event_subtype: 心动事件子类型

        Returns:
            (event_id, heartbeat_id) 元组
        """
        async with self._db.get_session() as session:
            # 1. 插入日常事务事件
            event_ins = daily_life_events.insert().values(
                character_id=event_data["character_id"],
                user_id=event_data["user_id"],
                event_time=event_data["event_time"],
                scenario=event_data.get("scenario", ""),
                scenario_detail=event_data.get("scenario_detail"),
                dialogue=event_data.get("dialogue"),
                inner_monologue=event_data.get("inner_monologue"),
                emotion_delta=event_data.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0}),
                intensity=event_data.get("intensity", 0.3),
                target_user_id=event_data.get("target_user_id"),
                share_text=event_data.get("share_text"),
                share_image_prompt=event_data.get("share_image_prompt"),
                share_video_prompt=event_data.get("share_video_prompt"),
                share_executed=event_data.get("share_executed", False),
            )
            event_result = await session.execute(event_ins)
            event_id = event_result.inserted_primary_key[0]

            # 2. 插入心动事件
            heartbeat_ins = heartbeat_events.insert().values(
                character_id=event_data["character_id"],
                user_id=event_data["user_id"],
                event_node=event_node,
                event_subtype=event_subtype,
                trigger_text=event_data.get("dialogue", "") or event_data.get("scenario", ""),
                emotion_state=event_data.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0}),
                intensity=event_data.get("intensity", 0.3),
                inner_monologue=event_data.get("inner_monologue"),
                source_message_id=None,
            )
            heartbeat_result = await session.execute(heartbeat_ins)
            heartbeat_id = heartbeat_result.inserted_primary_key[0]

            # 3. 更新日常事务事件的 heartbeat_event_id
            await session.execute(
                daily_life_events.update()
                .where(daily_life_events.c.id == event_id)
                .values(heartbeat_event_id=heartbeat_id)
            )

            await session.commit()

            return event_id, heartbeat_id

    # ============================================================
    # 读操作
    # ============================================================

    async def get_recent(
        self,
        character_id: str,
        user_id: str,
        limit: int = 10,
        days: int = 7,
    ) -> List[dict]:
        """获取最近的日常事务事件

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数
            days: 最近天数

        Returns:
            事件列表
        """
        cutoff = datetime.now() - timedelta(days=days)
        stmt = (
            select(daily_life_events)
            .where(
                and_(
                    daily_life_events.c.character_id == character_id,
                    daily_life_events.c.user_id == user_id,
                    daily_life_events.c.event_time >= cutoff,
                )
            )
            .order_by(daily_life_events.c.event_time.desc())
            .limit(limit)
        )
        results = await self._mappings(stmt)
        # 反转顺序，使最早的在前面
        return list(reversed(results))

    async def get_by_date_range(
        self,
        character_id: str,
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 50,
    ) -> List[dict]:
        """按时间范围获取日常事务事件

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
            select(daily_life_events)
            .where(
                and_(
                    daily_life_events.c.character_id == character_id,
                    daily_life_events.c.user_id == user_id,
                    daily_life_events.c.event_time >= start_time,
                    daily_life_events.c.event_time <= end_time,
                )
            )
            .order_by(daily_life_events.c.event_time.desc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def get_by_id(self, event_id: int) -> Optional[dict]:
        """根据 ID 获取事件"""
        return await self._get_by_id(daily_life_events, event_id)

    async def count(
        self,
        character_id: str,
        user_id: str,
        days: int = None,
    ) -> int:
        """统计事件数量"""
        conditions = [
            daily_life_events.c.character_id == character_id,
            daily_life_events.c.user_id == user_id,
        ]
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            conditions.append(daily_life_events.c.event_time >= cutoff)

        stmt = select(daily_life_events.c.id).where(and_(*conditions))
        result = await self._scalar(stmt)
        return result if result else 0


# ============================================================
# 全局实例（懒加载）
# ============================================================

_daily_life_repo: Optional[DailyLifeEventRepository] = None


def get_daily_life_repo() -> DailyLifeEventRepository:
    """获取 DailyLifeEventRepository 实例"""
    global _daily_life_repo
    if _daily_life_repo is None:
        _daily_life_repo = DailyLifeEventRepository()
    return _daily_life_repo