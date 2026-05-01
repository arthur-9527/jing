"""角色背景知识 Repository

提供 character_background 表的数据访问：
- 向量检索（cosine similarity）
- CRUD 操作
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select, insert
from pgvector.sqlalchemy import Vector

from app.stone.database import get_database
from app.stone.repositories.base import BaseRepository
from app.stone.models.memory import character_background

logger = logging.getLogger(__name__)


class BackgroundRepository(BaseRepository):
    """角色背景知识 Repository
    
    表: character_background
    """
    
    table = character_background
    
    async def search_vector(
        self,
        character_id: str,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict]:
        """向量检索背景知识
        
        Args:
            character_id: 角色ID
            query_embedding: 查询向量
            limit: 返回条数
        
        Returns:
            匹配的背景知识列表（按相似度排序）
        """
        t0 = time.monotonic()
        
        db = get_database()
        
        # 计算 cosine distance
        distance = self.table.c.embedding.cosine_distance(query_embedding)
        similarity = (1 - distance).label("similarity")
        
        stmt = (
            select(
                self.table.c.id,
                self.table.c.chunk_text,
                self.table.c.metadata,
                similarity,
            )
            .where(
                self.table.c.character_id == character_id,
                self.table.c.embedding.isnot(None),
            )
            .order_by(distance)
            .limit(limit)
        )
        
        rows = await db.execute(stmt)
        result = [dict(r) for r in rows]
        
        logger.info(f"[Timing] BackgroundRepository.search_vector: {(time.monotonic()-t0)*1000:.0f}ms, {len(result)} 条")
        return result
    
    async def insert(
        self,
        character_id: str,
        chunk_text: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> int:
        """插入背景知识
        
        Args:
            character_id: 角色ID
            chunk_text: 文本内容
            embedding: 向量
            metadata: 元数据
        
        Returns:
            记录ID
        """
        db = get_database()
        
        stmt = (
            insert(self.table)
            .values(
                character_id=character_id,
                chunk_text=chunk_text,
                embedding=embedding,
                metadata=(metadata or {}),
            )
            .returning(self.table.c.id)
        )
        
        result = await db.execute(stmt)
        row = result.first()
        return int(row["id"]) if row else 0


# 全局实例
_background_repo: BackgroundRepository | None = None


def get_background_repo() -> BackgroundRepository:
    """获取 BackgroundRepository 实例（全局单例）"""
    global _background_repo
    if _background_repo is None:
        _background_repo = BackgroundRepository()
    return _background_repo