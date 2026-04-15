"""记忆系统表的 CRUD 操作封装

说明：
- 统一使用 SQLAlchemy AsyncSession
- 覆盖新记忆系统的所有表：chat_messages, key_events, heartbeat_events, daily_diary, weekly_index, monthly_index, annual_index
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    String,
    Table,
    Text,
    TIMESTAMP,
    Boolean,
    Date,
    func,
    insert,
    select,
    update,
    delete,
    and_,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from pgvector.sqlalchemy import Vector

from app.config import settings
from app.database import Base, get_db_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table definitions (SQLAlchemy Core)
# ---------------------------------------------------------------------------

# 聊天记录表
chat_messages = Table(
    "chat_messages",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("role", String(16), nullable=False),  # 'user' / 'assistant'
    Column("content", Text, nullable=False),
    Column("inner_monologue", Text),  # 内心独白（仅 assistant 有）
    Column("turn_id", BigInteger),  # 对话轮次ID
    Column("metadata", JSONB, server_default="{}"),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("is_extracted", Boolean, server_default="false"),
    Column("extracted_at", TIMESTAMP(timezone=True)),
)

# 关键事件表
key_events = Table(
    "key_events",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("event_date", Date),
    Column("content", Text, nullable=False),
    Column("source_message_ids", ARRAY(BigInteger), server_default="{}"),
    Column("importance", Float, server_default="0.5"),
    Column("is_active", Boolean, server_default="true"),
    Column("expires_at", TIMESTAMP(timezone=True)),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# 心动事件表
heartbeat_events = Table(
    "heartbeat_events",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("event_node", String(32), nullable=False),
    Column("event_subtype", String(32)),
    Column("trigger_text", Text, nullable=False),
    Column("emotion_state", JSONB, nullable=False),
    Column("intensity", Float, nullable=False),
    Column("inner_monologue", Text),
    Column("source_message_id", BigInteger),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# 日记表
daily_diary = Table(
    "daily_diary",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("diary_date", Date, nullable=False),
    Column("summary", Text, nullable=False),
    Column("embedding", Vector(settings.EMBEDDING_DIM)),
    Column("key_event_ids", ARRAY(BigInteger), server_default="{}"),
    Column("heartbeat_ids", ARRAY(BigInteger), server_default="{}"),
    Column("source_message_ids", ARRAY(BigInteger), server_default="{}"),
    Column("mood_summary", JSONB),
    Column("highlight_count", Integer, server_default="0"),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# 周索引表
weekly_index = Table(
    "weekly_index",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("week_start", Date, nullable=False),
    Column("week_end", Date, nullable=False),
    Column("summary", Text, nullable=False),
    Column("embedding", Vector(settings.EMBEDDING_DIM)),
    Column("diary_ids", ARRAY(BigInteger), server_default="{}"),
    Column("highlight_events", JSONB),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# 月索引表
monthly_index = Table(
    "monthly_index",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("summary", Text, nullable=False),
    Column("embedding", Vector(settings.EMBEDDING_DIM)),
    Column("weekly_ids", ARRAY(BigInteger), server_default="{}"),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# 年索引表
annual_index = Table(
    "annual_index",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("year", Integer, nullable=False),
    Column("summary", Text, nullable=False),
    Column("embedding", Vector(settings.EMBEDDING_DIM)),
    Column("monthly_ids", ARRAY(BigInteger), server_default="{}"),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)


def _json_value(v):
    """解析 JSON 值"""
    if isinstance(v, str):
        return json.loads(v)
    return v


# ---------------------------------------------------------------------------
# chat_messages CRUD
# ---------------------------------------------------------------------------


async def insert_chat_message(
    character_id: str,
    user_id: str,
    role: str,
    content: str,
    inner_monologue: Optional[str] = None,
    turn_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> int:
    """插入一条聊天记录"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(chat_messages)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    role=role,
                    content=content,
                    inner_monologue=inner_monologue,
                    turn_id=turn_id,
                    metadata=(metadata or {}),
                )
                .returning(chat_messages.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.debug(f"[DB] insert_chat_message: id={record_id}, role={role}")
    return int(record_id)


async def batch_insert_chat_messages(messages: list[dict]) -> list[int]:
    """批量插入聊天记录（定时任务用）

    Args:
        messages: 消息列表，每项包含 character_id, user_id, role, content, inner_monologue, turn_id, metadata

    Returns:
        插入的记录 ID 列表
    """
    if not messages:
        return []

    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(chat_messages)
                .values(messages)
                .returning(chat_messages.c.id)
            )
            result = await session.execute(stmt)
            record_ids = [row[0] for row in result.fetchall()]
    logger.info(f"[DB] batch_insert_chat_messages: {len(record_ids)} 条")
    return record_ids


async def search_chat_messages_fts(
    character_id: str,
    user_id: str,
    query: str,
    limit: int = 10,
    days: int = 3,
) -> list[dict]:
    """PostgreSQL FTS 全文检索聊天记录

    Args:
        character_id: 角色ID
        user_id: 用户ID
        query: 搜索查询（自然语言输入，websearch_to_tsquery 会自动处理）
        limit: 返回条数
        days: 搜索最近N天的记录

    Returns:
        匹配的聊天记录列表
        
    Note:
        使用 websearch_to_tsquery 处理自然语言输入：
        - 支持 "关键词1 关键词2" 格式（自动转换为 AND）
        - 支持 "关键词1 OR 关键词2" 格式
        - 支持 "-关键词" 排除格式
        - 自动处理特殊字符
    """
    t0 = time.monotonic()

    async with get_db_session() as session:
        # 使用 websearch_to_tsquery 处理自然语言输入
        # 限定时间范围，使用中文分词
        stmt = text("""
            SELECT id, role, content, inner_monologue, turn_id, created_at,
                   ts_rank_cd(content_tsv_cn, websearch_to_tsquery('chinese_zh', :query)) AS rank
            FROM chat_messages
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND content_tsv_cn @@ websearch_to_tsquery('chinese_zh', :query)
              AND created_at >= NOW() - make_interval(days => :days)
            ORDER BY rank DESC, created_at DESC
            LIMIT :limit
        """)
        result = await session.execute(
            stmt,
            {
                "character_id": character_id,
                "user_id": user_id,
                "query": query,
                "days": days,
                "limit": limit,
            }
        )
        rows = result.mappings().all()

    records = [dict(r) for r in rows]
    logger.info(f"[Timing] search_chat_messages_fts: {(time.monotonic()-t0)*1000:.0f}ms, {len(records)} 条")
    return records


async def get_recent_chat_messages(
    character_id: str,
    user_id: str,
    limit: int = 50,
    days: int = 3,
) -> list[dict]:
    """获取最近N天的聊天记录

    Args:
        character_id: 角色ID
        user_id: 用户ID
        limit: 返回条数
        days: 最近N天

    Returns:
        聊天记录列表
    """
    t0 = time.monotonic()

    async with get_db_session() as session:
        stmt = (
            select(
                chat_messages.c.id,
                chat_messages.c.role,
                chat_messages.c.content,
                chat_messages.c.inner_monologue,
                chat_messages.c.turn_id,
                chat_messages.c.metadata,
                chat_messages.c.created_at,
            )
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.created_at >= func.now() - text(f"INTERVAL '{days} days'"),
                )
            )
            .order_by(chat_messages.c.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    records = [dict(r) for r in rows]
    logger.info(f"[Timing] get_recent_chat_messages: {(time.monotonic()-t0)*1000:.0f}ms, {len(records)} 条")
    return records


async def get_unextracted_chat_messages(
    character_id: str,
    user_id: str,
    hours: int = 1,
    limit: int = 100,
) -> list[dict]:
    """获取未提取的聊天记录（定时任务用）

    Args:
        character_id: 角色ID
        user_id: 用户ID
        hours: 最近N小时
        limit: 返回条数

    Returns:
        未提取的聊天记录列表
    """
    async with get_db_session() as session:
        stmt = (
            select(
                chat_messages.c.id,
                chat_messages.c.role,
                chat_messages.c.content,
                chat_messages.c.inner_monologue,
                chat_messages.c.turn_id,
                chat_messages.c.created_at,
            )
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.is_extracted == False,
                    chat_messages.c.created_at >= func.now() - text(f"INTERVAL '{hours} hours'"),
                )
            )
            .order_by(chat_messages.c.created_at.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def mark_messages_extracted(message_ids: list[int]) -> int:
    """标记消息已提取

    Args:
        message_ids: 消息ID列表

    Returns:
        更新的条数
    """
    if not message_ids:
        return 0

    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                update(chat_messages)
                .where(chat_messages.c.id.in_(message_ids))
                .values(is_extracted=True, extracted_at=func.now())
            )
            result = await session.execute(stmt)
            count = result.rowcount

    logger.info(f"[DB] mark_messages_extracted: {count} 条")
    return count


async def cleanup_old_chat_messages(
    character_id: str,
    user_id: str,
    days: int = 7,
) -> int:
    """清理旧的聊天记录（保留已提取的）

    Args:
        character_id: 角色ID
        user_id: 用户ID
        days: 删除N天前的记录

    Returns:
        删除的条数
    """
    async with get_db_session() as session:
        async with session.begin():
            # 只删除未提取的旧记录
            stmt = (
                delete(chat_messages)
                .where(
                    and_(
                        chat_messages.c.character_id == character_id,
                        chat_messages.c.user_id == user_id,
                        chat_messages.c.created_at < func.now() - text(f"INTERVAL '{days} days'"),
                        chat_messages.c.is_extracted == False,
                    )
                )
            )
            result = await session.execute(stmt)
            count = result.rowcount

    logger.info(f"[DB] cleanup_old_chat_messages: 删除 {count} 条")
    return count


# ---------------------------------------------------------------------------
# key_events CRUD
# ---------------------------------------------------------------------------


async def insert_key_event(
    character_id: str,
    user_id: str,
    event_type: str,
    content: str,
    event_date: Optional[date] = None,
    source_message_ids: Optional[list[int]] = None,
    importance: float = 0.5,
    expires_at: Optional[datetime] = None,
) -> int:
    """插入关键事件"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(key_events)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    event_type=event_type,
                    event_date=event_date,
                    content=content,
                    source_message_ids=(source_message_ids or []),
                    importance=importance,
                    expires_at=expires_at,
                    updated_at=func.now(),
                )
                .returning(key_events.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.debug(f"[DB] insert_key_event: id={record_id}, type={event_type}")
    return int(record_id)


async def batch_insert_key_events(events: list[dict]) -> list[int]:
    """批量插入关键事件"""
    if not events:
        return []

    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(key_events)
                .values(events)
                .returning(key_events.c.id)
            )
            result = await session.execute(stmt)
            record_ids = [row[0] for row in result.fetchall()]
    logger.info(f"[DB] batch_insert_key_events: {len(record_ids)} 条")
    return record_ids


async def search_key_events_fts(
    character_id: str,
    user_id: str,
    query: str,
    limit: int = 10,
    event_types: Optional[list[str]] = None,
) -> list[dict]:
    """PostgreSQL FTS 全文检索关键事件

    Args:
        character_id: 角色ID
        user_id: 用户ID
        query: 搜索查询（自然语言输入）
        limit: 返回条数
        event_types: 限定事件类型列表

    Returns:
        匹配的关键事件列表
        
    Note:
        使用 websearch_to_tsquery 处理自然语言输入，自动处理特殊字符
    """
    t0 = time.monotonic()

    async with get_db_session() as session:
        # 构建基础查询（使用 websearch_to_tsquery 处理自然语言）
        base_query = """
            SELECT id, event_type, event_date, content, importance, is_active, created_at,
                   ts_rank_cd(content_tsv_cn, websearch_to_tsquery('chinese_zh', :query)) AS rank
            FROM key_events
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND is_active = TRUE
              AND content_tsv_cn @@ websearch_to_tsquery('chinese_zh', :query)
        """

        # 添加类型过滤（使用 ANY 语法，兼容 asyncpg）
        if event_types:
            base_query += " AND event_type = ANY(:event_types)"

        base_query += " ORDER BY rank DESC, created_at DESC LIMIT :limit"

        stmt = text(base_query)
        params = {
            "character_id": character_id,
            "user_id": user_id,
            "query": query,
            "limit": limit,
        }
        if event_types:
            params["event_types"] = event_types  # ANY 接受 list

        result = await session.execute(stmt, params)
        rows = result.mappings().all()

    records = [dict(r) for r in rows]
    logger.info(f"[Timing] search_key_events_fts: {(time.monotonic()-t0)*1000:.0f}ms, {len(records)} 条")
    return records


async def get_key_events_by_type(
    character_id: str,
    user_id: str,
    event_type: str,
    limit: int = 20,
    active_only: bool = True,
) -> list[dict]:
    """按类型获取关键事件

    Args:
        character_id: 角色ID
        user_id: 用户ID
        event_type: 事件类型
        limit: 返回条数
        active_only: 只返回有效事件

    Returns:
        关键事件列表
    """
    async with get_db_session() as session:
        conditions = [
            key_events.c.character_id == character_id,
            key_events.c.user_id == user_id,
            key_events.c.event_type == event_type,
        ]
        if active_only:
            conditions.append(key_events.c.is_active == True)

        stmt = (
            select(
                key_events.c.id,
                key_events.c.event_type,
                key_events.c.event_date,
                key_events.c.content,
                key_events.c.importance,
                key_events.c.is_active,
                key_events.c.expires_at,
                key_events.c.created_at,
            )
            .where(and_(*conditions))
            .order_by(key_events.c.importance.desc(), key_events.c.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def get_recent_key_events(
    character_id: str,
    user_id: str,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """获取最近N天的关键事件"""
    async with get_db_session() as session:
        stmt = (
            select(
                key_events.c.id,
                key_events.c.event_type,
                key_events.c.event_date,
                key_events.c.content,
                key_events.c.importance,
                key_events.c.created_at,
            )
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.is_active == True,
                    key_events.c.created_at >= func.now() - text(f"INTERVAL '{days} days'"),
                )
            )
            .order_by(key_events.c.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def get_special_events(
    character_id: str,
    user_id: str,
    limit: int = 50,
) -> list[dict]:
    """获取所有特殊事件（不限时间）
    
    特殊事件类型包括：
    - preference: 用户偏好（喜欢什么、讨厌什么）
    - fact: 用户事实（生日、职业等）
    - schedule: 日程事件
    - initiative: 主动记忆（关系里程碑）
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        limit: 返回条数
    
    Returns:
        特殊事件列表（按重要性排序）
    """
    async with get_db_session() as session:
        stmt = (
            select(
                key_events.c.id,
                key_events.c.event_type,
                key_events.c.event_date,
                key_events.c.content,
                key_events.c.importance,
                key_events.c.expires_at,
                key_events.c.created_at,
            )
            .where(
                and_(
                    key_events.c.character_id == character_id,
                    key_events.c.user_id == user_id,
                    key_events.c.is_active == True,
                    key_events.c.event_type.in_(['preference', 'fact', 'schedule', 'initiative']),
                )
            )
            .order_by(key_events.c.importance.desc(), key_events.c.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def deactivate_key_event(event_id: int) -> bool:
    """失效关键事件"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                update(key_events)
                .where(key_events.c.id == event_id)
                .values(is_active=False, updated_at=func.now())
            )
            result = await session.execute(stmt)
            return result.rowcount > 0


async def deactivate_expired_events() -> int:
    """失效过期的事件（日程类）"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                update(key_events)
                .where(
                    and_(
                        key_events.c.is_active == True,
                        key_events.c.expires_at < func.now(),
                    )
                )
                .values(is_active=False, updated_at=func.now())
            )
            result = await session.execute(stmt)
            count = result.rowcount

    logger.info(f"[DB] deactivate_expired_events: {count} 条")
    return count


# ---------------------------------------------------------------------------
# heartbeat_events CRUD
# ---------------------------------------------------------------------------


async def insert_heartbeat_event(
    character_id: str,
    user_id: str,
    event_node: str,
    trigger_text: str,
    emotion_state: dict,
    intensity: float,
    event_subtype: Optional[str] = None,
    inner_monologue: Optional[str] = None,
    source_message_id: Optional[int] = None,
) -> int:
    """插入心动事件"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(heartbeat_events)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    event_node=event_node,
                    event_subtype=event_subtype,
                    trigger_text=trigger_text,
                    emotion_state=emotion_state,
                    intensity=intensity,
                    inner_monologue=inner_monologue,
                    source_message_id=source_message_id,
                )
                .returning(heartbeat_events.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.debug(f"[DB] insert_heartbeat_event: id={record_id}, node={event_node}, intensity={intensity:.2f}")
    return int(record_id)


async def batch_insert_heartbeat_events(events: list[dict]) -> list[int]:
    """批量插入心动事件"""
    if not events:
        return []

    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(heartbeat_events)
                .values(events)
                .returning(heartbeat_events.c.id)
            )
            result = await session.execute(stmt)
            record_ids = [row[0] for row in result.fetchall()]
    logger.info(f"[DB] batch_insert_heartbeat_events: {len(record_ids)} 条")
    return record_ids


async def get_recent_heartbeat_events(
    character_id: str,
    user_id: str,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """获取最近N天的心动事件"""
    async with get_db_session() as session:
        stmt = (
            select(
                heartbeat_events.c.id,
                heartbeat_events.c.event_node,
                heartbeat_events.c.event_subtype,
                heartbeat_events.c.trigger_text,
                heartbeat_events.c.emotion_state,
                heartbeat_events.c.intensity,
                heartbeat_events.c.inner_monologue,
                heartbeat_events.c.created_at,
            )
            .where(
                and_(
                    heartbeat_events.c.character_id == character_id,
                    heartbeat_events.c.user_id == user_id,
                    heartbeat_events.c.created_at >= func.now() - text(f"INTERVAL '{days} days'"),
                )
            )
            .order_by(heartbeat_events.c.intensity.desc(), heartbeat_events.c.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def get_high_intensity_heartbeat_events(
    character_id: str,
    user_id: str,
    min_intensity: float = 0.5,
    limit: int = 20,
    days: Optional[int] = None,
) -> list[dict]:
    """获取高强度心动事件
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        min_intensity: 最小强度阈值
        limit: 返回条数
        days: 最近N天（None表示不限时间）
    
    Returns:
        心动事件列表
    """
    async with get_db_session() as session:
        conditions = [
            heartbeat_events.c.character_id == character_id,
            heartbeat_events.c.user_id == user_id,
            heartbeat_events.c.intensity >= min_intensity,
        ]
        
        # 添加时间限制
        if days is not None:
            conditions.append(
                heartbeat_events.c.created_at >= func.now() - text(f"INTERVAL '{days} days'")
            )
        
        stmt = (
            select(
                heartbeat_events.c.id,
                heartbeat_events.c.event_node,
                heartbeat_events.c.event_subtype,
                heartbeat_events.c.trigger_text,
                heartbeat_events.c.intensity,
                heartbeat_events.c.inner_monologue,
                heartbeat_events.c.created_at,
            )
            .where(and_(*conditions))
            .order_by(heartbeat_events.c.intensity.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def search_heartbeat_events_fts(
    character_id: str,
    user_id: str,
    query: str,
    limit: int = 3,
    days: int = 7,
) -> list[dict]:
    """PostgreSQL FTS 全文检索心动事件

    Args:
        character_id: 角色ID
        user_id: 用户ID
        query: 搜索查询（自然语言输入）
        limit: 返回条数
        days: 搜索最近N天的记录

    Returns:
        匹配的心动事件列表
        
    Note:
        使用 websearch_to_tsquery 处理自然语言输入，自动处理特殊字符
    """
    t0 = time.monotonic()

    async with get_db_session() as session:
        # 使用 websearch_to_tsquery 处理自然语言输入
        stmt = text("""
            SELECT id, event_node, event_subtype, trigger_text, intensity,
                   inner_monologue, created_at,
                   ts_rank_cd(trigger_text_tsv_cn, websearch_to_tsquery('chinese_zh', :query)) AS rank
            FROM heartbeat_events
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND trigger_text_tsv_cn @@ websearch_to_tsquery('chinese_zh', :query)
              AND created_at >= NOW() - make_interval(days => :days)
            ORDER BY rank DESC, intensity DESC
            LIMIT :limit
        """)
        result = await session.execute(
            stmt,
            {
                "character_id": character_id,
                "user_id": user_id,
                "query": query,
                "days": days,
                "limit": limit,
            }
        )
        rows = result.mappings().all()

    records = [dict(r) for r in rows]
    logger.info(f"[Timing] search_heartbeat_events_fts: {(time.monotonic()-t0)*1000:.0f}ms, {len(records)} 条")
    return records


async def get_chat_context_around_message(
    character_id: str,
    user_id: str,
    message_id: int,
    context_before: int = 2,
    context_after: int = 2,
) -> list[dict]:
    """获取聊天消息的上下文

    获取指定消息ID前后的对话上下文：
    - context_before 条之前的消息
    - 匹配的消息本身
    - context_after 条之后的消息

    Args:
        character_id: 角色ID
        user_id: 用户ID
        message_id: 匹配的消息ID
        context_before: 前面取多少条
        context_after: 后面取多少条

    Returns:
        上下文消息列表（按时间正序）
    """
    async with get_db_session() as session:
        # 先获取匹配消息的创建时间
        stmt = text("""
            SELECT created_at FROM chat_messages
            WHERE id = :message_id
              AND character_id = :character_id
              AND user_id = :user_id
        """)
        result = await session.execute(
            stmt,
            {"message_id": message_id, "character_id": character_id, "user_id": user_id}
        )
        row = result.first()
        if not row:
            return []
        
        target_time = row[0]
        
        # 获取上下文：前面的消息 + 匹配消息 + 后面的消息
        # 使用子查询获取前面的消息ID
        context_stmt = text("""
            WITH before_msgs AS (
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE character_id = :character_id
                  AND user_id = :user_id
                  AND created_at < :target_time
                ORDER BY created_at DESC
                LIMIT :context_before
            ),
            after_msgs AS (
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE character_id = :character_id
                  AND user_id = :user_id
                  AND created_at > :target_time
                ORDER BY created_at ASC
                LIMIT :context_after
            ),
            target_msg AS (
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE id = :message_id
            )
            SELECT id, role, content, created_at FROM (
                SELECT * FROM before_msgs
                UNION ALL
                SELECT * FROM target_msg
                UNION ALL
                SELECT * FROM after_msgs
            ) AS all_msgs
            ORDER BY created_at ASC
        """)
        
        result = await session.execute(
            context_stmt,
            {
                "character_id": character_id,
                "user_id": user_id,
                "target_time": target_time,
                "message_id": message_id,
                "context_before": context_before,
                "context_after": context_after,
            }
        )
        rows = result.mappings().all()

    return [dict(r) for r in rows]


async def batch_get_chat_contexts(
    character_id: str,
    user_id: str,
    message_ids: list[int],
    context_before: int = 2,
    context_after: int = 2,
) -> list[dict]:
    """批量获取多条消息的上下文（优化 N+1 查询）

    一次性 SQL 查询获取所有匹配消息的上下文，避免多次查询。
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        message_ids: 匹配的消息ID列表
        context_before: 每条消息前面取多少条
        context_after: 每条消息后面取多少条

    Returns:
        所有上下文消息列表（按时间正序，已去重）
    """
    if not message_ids:
        return []
    
    t0 = time.monotonic()
    
    async with get_db_session() as session:
        # 使用单个查询获取所有上下文
        # 策略：找出所有匹配消息的时间范围，然后获取区间内的消息
        stmt = text("""
            WITH target_msgs AS (
                SELECT id, created_at
                FROM chat_messages
                WHERE id = ANY(:message_ids)
                  AND character_id = :character_id
                  AND user_id = :user_id
            ),
            time_ranges AS (
                SELECT 
                    MIN(created_at) - make_interval(secs => 60 * :context_before) AS min_time,
                    MAX(created_at) + make_interval(secs => 60 * :context_after) AS max_time
                FROM target_msgs
            )
            SELECT DISTINCT m.id, m.role, m.content, m.created_at
            FROM chat_messages m, time_ranges tr
            WHERE m.character_id = :character_id
              AND m.user_id = :user_id
              AND m.created_at >= tr.min_time
              AND m.created_at <= tr.max_time
            ORDER BY m.created_at ASC
        """)
        
        result = await session.execute(
            stmt,
            {
                "character_id": character_id,
                "user_id": user_id,
                "message_ids": message_ids,
                "context_before": context_before,
                "context_after": context_after,
            }
        )
        rows = result.mappings().all()

    records = [dict(r) for r in rows]
    logger.info(f"[Timing] batch_get_chat_contexts: {(time.monotonic()-t0)*1000:.0f}ms, {len(records)} 条")
    return records


async def get_key_events_by_ids(event_ids: list[int]) -> list[dict]:
    """根据 ID 列表批量获取关键事件
    
    Args:
        event_ids: 事件ID列表
    
    Returns:
        关键事件列表
    """
    if not event_ids:
        return []
    
    async with get_db_session() as session:
        stmt = (
            select(
                key_events.c.id,
                key_events.c.event_type,
                key_events.c.event_date,
                key_events.c.content,
                key_events.c.importance,
                key_events.c.created_at,
            )
            .where(key_events.c.id.in_(event_ids))
            .order_by(key_events.c.importance.desc())
        )
        rows = (await session.execute(stmt)).mappings().all()
    
    return [dict(r) for r in rows]


async def get_heartbeat_events_by_ids(event_ids: list[int]) -> list[dict]:
    """根据 ID 列表批量获取心动事件
    
    Args:
        event_ids: 事件ID列表
    
    Returns:
        心动事件列表
    """
    if not event_ids:
        return []
    
    async with get_db_session() as session:
        stmt = (
            select(
                heartbeat_events.c.id,
                heartbeat_events.c.event_node,
                heartbeat_events.c.event_subtype,
                heartbeat_events.c.trigger_text,
                heartbeat_events.c.intensity,
                heartbeat_events.c.inner_monologue,
                heartbeat_events.c.created_at,
            )
            .where(heartbeat_events.c.id.in_(event_ids))
            .order_by(heartbeat_events.c.intensity.desc())
        )
        rows = (await session.execute(stmt)).mappings().all()
    
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# daily_diary CRUD
# ---------------------------------------------------------------------------


async def insert_daily_diary(
    character_id: str,
    user_id: str,
    diary_date: date,
    summary: str,
    embedding: Optional[list[float]] = None,
    key_event_ids: Optional[list[int]] = None,
    heartbeat_ids: Optional[list[int]] = None,
    source_message_ids: Optional[list[int]] = None,
    mood_summary: Optional[dict] = None,
    highlight_count: int = 0,
) -> int:
    """插入日记"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(daily_diary)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    diary_date=diary_date,
                    summary=summary,
                    embedding=embedding,
                    key_event_ids=(key_event_ids or []),
                    heartbeat_ids=(heartbeat_ids or []),
                    source_message_ids=(source_message_ids or []),
                    mood_summary=mood_summary,
                    highlight_count=highlight_count,
                )
                .returning(daily_diary.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.info(f"[DB] insert_daily_diary: id={record_id}, date={diary_date}")
    return int(record_id)


async def upsert_daily_diary(
    character_id: str,
    user_id: str,
    diary_date: date,
    summary: str,
    embedding: Optional[list[float]] = None,
    key_event_ids: Optional[list[int]] = None,
    heartbeat_ids: Optional[list[int]] = None,
    source_message_ids: Optional[list[int]] = None,
    mood_summary: Optional[dict] = None,
    highlight_count: int = 0,
) -> int:
    """Upsert 日记（存在则更新，不存在则插入）"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = pg_insert(daily_diary).values(
                character_id=character_id,
                user_id=user_id,
                diary_date=diary_date,
                summary=summary,
                embedding=embedding,
                key_event_ids=(key_event_ids or []),
                heartbeat_ids=(heartbeat_ids or []),
                source_message_ids=(source_message_ids or []),
                mood_summary=mood_summary,
                highlight_count=highlight_count,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[daily_diary.c.character_id, daily_diary.c.user_id, daily_diary.c.diary_date],
                set_={
                    "summary": stmt.excluded.summary,
                    "embedding": stmt.excluded.embedding,
                    "key_event_ids": stmt.excluded.key_event_ids,
                    "heartbeat_ids": stmt.excluded.heartbeat_ids,
                    "source_message_ids": stmt.excluded.source_message_ids,
                    "mood_summary": stmt.excluded.mood_summary,
                    "highlight_count": stmt.excluded.highlight_count,
                },
            ).returning(daily_diary.c.id)
            record_id = await session.scalar(stmt)
    logger.info(f"[DB] upsert_daily_diary: id={record_id}, date={diary_date}")
    return int(record_id)


async def get_daily_diary(
    character_id: str,
    user_id: str,
    diary_date: date,
) -> Optional[dict]:
    """获取指定日期的日记"""
    async with get_db_session() as session:
        stmt = (
            select(
                daily_diary.c.id,
                daily_diary.c.diary_date,
                daily_diary.c.summary,
                daily_diary.c.key_event_ids,
                daily_diary.c.heartbeat_ids,
                daily_diary.c.mood_summary,
                daily_diary.c.highlight_count,
                daily_diary.c.created_at,
            )
            .where(
                and_(
                    daily_diary.c.character_id == character_id,
                    daily_diary.c.user_id == user_id,
                    daily_diary.c.diary_date == diary_date,
                )
            )
        )
        row = (await session.execute(stmt)).mappings().first()

    return dict(row) if row else None


async def get_recent_daily_diaries(
    character_id: str,
    user_id: str,
    days: int = 7,
    limit: int = 7,
) -> list[dict]:
    """获取最近N天的日记"""
    async with get_db_session() as session:
        stmt = (
            select(
                daily_diary.c.id,
                daily_diary.c.diary_date,
                daily_diary.c.summary,
                daily_diary.c.key_event_ids,
                daily_diary.c.heartbeat_ids,
                daily_diary.c.mood_summary,
                daily_diary.c.highlight_count,
            )
            .where(
                and_(
                    daily_diary.c.character_id == character_id,
                    daily_diary.c.user_id == user_id,
                    daily_diary.c.diary_date >= func.current_date() - text(f"INTERVAL '{days} days'"),
                )
            )
            .order_by(daily_diary.c.diary_date.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    return [dict(r) for r in rows]


async def search_diary_by_embedding(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
    limit: int = 5,
) -> list[dict]:
    """向量检索日记"""
    t0 = time.monotonic()

    distance = daily_diary.c.embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity")

    async with get_db_session() as session:
        stmt = (
            select(
                daily_diary.c.id,
                daily_diary.c.diary_date,
                daily_diary.c.summary,
                daily_diary.c.key_event_ids,
                daily_diary.c.heartbeat_ids,
                similarity,
            )
            .where(
                and_(
                    daily_diary.c.character_id == character_id,
                    daily_diary.c.user_id == user_id,
                    daily_diary.c.embedding.isnot(None),
                )
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    result = [dict(r) for r in rows]
    logger.info(f"[Timing] search_diary_by_embedding: {(time.monotonic()-t0)*1000:.0f}ms, {len(result)} 条")
    return result


# ---------------------------------------------------------------------------
# weekly_index CRUD
# ---------------------------------------------------------------------------


async def insert_weekly_index(
    character_id: str,
    user_id: str,
    week_start: date,
    week_end: date,
    summary: str,
    embedding: Optional[list[float]] = None,
    diary_ids: Optional[list[int]] = None,
    highlight_events: Optional[dict] = None,
) -> int:
    """插入周索引"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(weekly_index)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    week_start=week_start,
                    week_end=week_end,
                    summary=summary,
                    embedding=embedding,
                    diary_ids=(diary_ids or []),
                    highlight_events=highlight_events,
                )
                .returning(weekly_index.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.info(f"[DB] insert_weekly_index: id={record_id}, week={week_start}~{week_end}")
    return int(record_id)


async def get_weekly_index_by_date(
    character_id: str,
    user_id: str,
    target_date: date,
) -> Optional[dict]:
    """根据日期获取对应的周索引"""
    async with get_db_session() as session:
        stmt = (
            select(
                weekly_index.c.id,
                weekly_index.c.week_start,
                weekly_index.c.week_end,
                weekly_index.c.summary,
                weekly_index.c.diary_ids,
            )
            .where(
                and_(
                    weekly_index.c.character_id == character_id,
                    weekly_index.c.user_id == user_id,
                    weekly_index.c.week_start <= target_date,
                    weekly_index.c.week_end >= target_date,
                )
            )
        )
        row = (await session.execute(stmt)).mappings().first()

    return dict(row) if row else None


async def search_weekly_by_embedding(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
    limit: int = 3,
) -> list[dict]:
    """向量检索周索引"""
    t0 = time.monotonic()

    distance = weekly_index.c.embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity")

    async with get_db_session() as session:
        stmt = (
            select(
                weekly_index.c.id,
                weekly_index.c.week_start,
                weekly_index.c.week_end,
                weekly_index.c.summary,
                weekly_index.c.diary_ids,
                similarity,
            )
            .where(
                and_(
                    weekly_index.c.character_id == character_id,
                    weekly_index.c.user_id == user_id,
                    weekly_index.c.embedding.isnot(None),
                )
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    result = [dict(r) for r in rows]
    logger.info(f"[Timing] search_weekly_by_embedding: {(time.monotonic()-t0)*1000:.0f}ms, {len(result)} 条")
    return result


# ---------------------------------------------------------------------------
# monthly_index CRUD
# ---------------------------------------------------------------------------


async def insert_monthly_index(
    character_id: str,
    user_id: str,
    year: int,
    month: int,
    summary: str,
    embedding: Optional[list[float]] = None,
    weekly_ids: Optional[list[int]] = None,
) -> int:
    """插入月索引"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(monthly_index)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    year=year,
                    month=month,
                    summary=summary,
                    embedding=embedding,
                    weekly_ids=(weekly_ids or []),
                )
                .returning(monthly_index.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.info(f"[DB] insert_monthly_index: id={record_id}, {year}-{month}")
    return int(record_id)


async def get_monthly_index(
    character_id: str,
    user_id: str,
    year: int,
    month: int,
) -> Optional[dict]:
    """获取指定月份的索引"""
    async with get_db_session() as session:
        stmt = (
            select(
                monthly_index.c.id,
                monthly_index.c.year,
                monthly_index.c.month,
                monthly_index.c.summary,
                monthly_index.c.weekly_ids,
            )
            .where(
                and_(
                    monthly_index.c.character_id == character_id,
                    monthly_index.c.user_id == user_id,
                    monthly_index.c.year == year,
                    monthly_index.c.month == month,
                )
            )
        )
        row = (await session.execute(stmt)).mappings().first()

    return dict(row) if row else None


async def search_monthly_by_embedding(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
    year: Optional[int] = None,
    limit: int = 3,
) -> list[dict]:
    """向量检索月索引"""
    t0 = time.monotonic()

    distance = monthly_index.c.embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity")

    async with get_db_session() as session:
        conditions = [
            monthly_index.c.character_id == character_id,
            monthly_index.c.user_id == user_id,
            monthly_index.c.embedding.isnot(None),
        ]
        if year:
            conditions.append(monthly_index.c.year == year)

        stmt = (
            select(
                monthly_index.c.id,
                monthly_index.c.year,
                monthly_index.c.month,
                monthly_index.c.summary,
                monthly_index.c.weekly_ids,
                similarity,
            )
            .where(and_(*conditions))
            .order_by(distance)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    result = [dict(r) for r in rows]
    logger.info(f"[Timing] search_monthly_by_embedding: {(time.monotonic()-t0)*1000:.0f}ms, {len(result)} 条")
    return result


# ---------------------------------------------------------------------------
# annual_index CRUD
# ---------------------------------------------------------------------------


async def insert_annual_index(
    character_id: str,
    user_id: str,
    year: int,
    summary: str,
    embedding: Optional[list[float]] = None,
    monthly_ids: Optional[list[int]] = None,
) -> int:
    """插入年索引"""
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(annual_index)
                .values(
                    character_id=character_id,
                    user_id=user_id,
                    year=year,
                    summary=summary,
                    embedding=embedding,
                    monthly_ids=(monthly_ids or []),
                )
                .returning(annual_index.c.id)
            )
            record_id = await session.scalar(stmt)
    logger.info(f"[DB] insert_annual_index: id={record_id}, year={year}")
    return int(record_id)


async def get_annual_index(
    character_id: str,
    user_id: str,
    year: int,
) -> Optional[dict]:
    """获取指定年份的索引"""
    async with get_db_session() as session:
        stmt = (
            select(
                annual_index.c.id,
                annual_index.c.year,
                annual_index.c.summary,
                annual_index.c.monthly_ids,
            )
            .where(
                and_(
                    annual_index.c.character_id == character_id,
                    annual_index.c.user_id == user_id,
                    annual_index.c.year == year,
                )
            )
        )
        row = (await session.execute(stmt)).mappings().first()

    return dict(row) if row else None


async def search_annual_by_embedding(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
    limit: int = 3,
) -> list[dict]:
    """向量检索年索引"""
    t0 = time.monotonic()

    distance = annual_index.c.embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity")

    async with get_db_session() as session:
        stmt = (
            select(
                annual_index.c.id,
                annual_index.c.year,
                annual_index.c.summary,
                annual_index.c.monthly_ids,
                similarity,
            )
            .where(
                and_(
                    annual_index.c.character_id == character_id,
                    annual_index.c.user_id == user_id,
                    annual_index.c.embedding.isnot(None),
                )
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    result = [dict(r) for r in rows]
    logger.info(f"[Timing] search_annual_by_embedding: {(time.monotonic()-t0)*1000:.0f}ms, {len(result)} 条")
    return result