"""角色背景知识表 + Agent状态表 CRUD 操作封装

说明：
- 统一使用 SQLAlchemy AsyncSession（复用 app/database.py 的 engine/pool）
- 记忆系统相关表已迁移到 memory_models.py
"""

from __future__ import annotations

import json

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    String,
    Table,
    Text,
    TIMESTAMP,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pgvector.sqlalchemy import Vector

from app.config import settings
from app.database import Base, get_db_session


# ---------------------------------------------------------------------------
# Table definitions (SQLAlchemy Core)
# ---------------------------------------------------------------------------

character_background = Table(
    "character_background",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False, index=True),
    Column("chunk_text", Text, nullable=False),
    Column("embedding", Vector(settings.EMBEDDING_DIM)),
    Column("metadata", JSONB, server_default="{}"),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

agent_state = Table(
    "agent_state",
    Base.metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("character_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("pad_state", JSONB, nullable=False),
    Column("conversation_history", JSONB, server_default="[]"),
    Column("turn_count", Integer, server_default="0"),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=func.now()),
)


def _json_value(v):
    if isinstance(v, str):
        return json.loads(v)
    return v


# ---------------------------------------------------------------------------
# character_background
# ---------------------------------------------------------------------------


async def insert_background(
    character_id: str,
    chunk_text: str,
    embedding: list[float],
    metadata: dict | None = None,
) -> int:
    async with get_db_session() as session:
        async with session.begin():
            stmt = (
                insert(character_background)
                .values(
                    character_id=character_id,
                    chunk_text=chunk_text,
                    embedding=embedding,
                    metadata=(metadata or {}),
                )
                .returning(character_background.c.id)
            )
            record_id = await session.scalar(stmt)
    return int(record_id)


async def search_background(
    character_id: str,
    query_embedding: list[float],
    limit: int = 5,
) -> list[dict]:
    import time
    import logging
    logger = logging.getLogger(__name__)
    
    t0 = time.monotonic()
    distance = character_background.c.embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity")

    async with get_db_session() as session:
        stmt = (
            select(
                character_background.c.id,
                character_background.c.chunk_text,
                character_background.c.metadata,
                similarity,
            )
            .where(
                character_background.c.character_id == character_id,
                character_background.c.embedding.isnot(None),
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).mappings().all()

    result = [dict(r) for r in rows]
    logger.info(f"[Timing] search_background: {(time.monotonic()-t0)*1000:.0f}ms, {len(result)} 条")
    return result


# ---------------------------------------------------------------------------
# agent_state
# ---------------------------------------------------------------------------


async def upsert_agent_state(
    character_id: str,
    user_id: str,
    pad_state: dict,
    conversation_history: list = None,
    turn_count: int = 0,
):
    if conversation_history is None:
        conversation_history = []
    async with get_db_session() as session:
        async with session.begin():
            stmt = pg_insert(agent_state).values(
                character_id=character_id,
                user_id=user_id,
                pad_state=pad_state,
                conversation_history=conversation_history,
                turn_count=turn_count,
                updated_at=func.now(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[agent_state.c.character_id, agent_state.c.user_id],
                set_={
                    "pad_state": stmt.excluded.pad_state,
                    "conversation_history": stmt.excluded.conversation_history,
                    "turn_count": stmt.excluded.turn_count,
                    "updated_at": func.now(),
                },
            )
            await session.execute(stmt)


async def get_agent_state(character_id: str, user_id: str) -> dict | None:
    async with get_db_session() as session:
        stmt = select(
            agent_state.c.pad_state,
            agent_state.c.conversation_history,
            agent_state.c.turn_count,
            agent_state.c.updated_at,
        ).where(
            agent_state.c.character_id == character_id,
            agent_state.c.user_id == user_id,
        )
        row = (await session.execute(stmt)).mappings().first()

    if row is None:
        return None

    return {
        "pad_state": _json_value(row["pad_state"]),
        "conversation_history": _json_value(row["conversation_history"]),
        "turn_count": row["turn_count"],
        "updated_at": row["updated_at"],
    }
