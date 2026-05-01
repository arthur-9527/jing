"""Agent 状态 Repository

提供 agent_state 表的数据访问：
- upsert 操作（插入或更新）
- 获取状态
- 获取所有活跃用户
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.stone.database import get_database
from app.stone.repositories.base import BaseRepository
from app.stone.models.memory import agent_state

logger = logging.getLogger(__name__)


class AgentStateRepository(BaseRepository):
    """Agent 状态 Repository
    
    表: agent_state
    """
    
    table = agent_state
    
    def __init__(self, db=None):
        """初始化
        
        Args:
            db: Database 实例（可选，默认使用全局实例）
        """
        # 使用传入的 db 或全局数据库实例
        super().__init__(db or get_database())
    
    def _json_value(self, v):
        """解析 JSON 值"""
        if isinstance(v, str):
            return json.loads(v)
        return v
    
    async def upsert(
        self,
        character_id: str,
        user_id: str,
        pad_state: dict,
        conversation_history: list | None = None,
        turn_count: int = 0,
    ) -> None:
        """插入或更新 Agent 状态
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
            pad_state: PAD 情绪状态
            conversation_history: 对话历史
            turn_count: 对话轮次
        """
        if conversation_history is None:
            conversation_history = []
        
        stmt = pg_insert(self.table).values(
            character_id=character_id,
            user_id=user_id,
            pad_state=pad_state,
            conversation_history=conversation_history,
            turn_count=turn_count,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[self.table.c.character_id, self.table.c.user_id],
            set_={
                "pad_state": stmt.excluded.pad_state,
                "conversation_history": stmt.excluded.conversation_history,
                "turn_count": stmt.excluded.turn_count,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        
        await self._execute_and_commit(stmt)
    
    async def get(self, character_id: str, user_id: str) -> dict | None:
        """获取 Agent 状态
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
        
        Returns:
            Agent 状态字典，不存在则返回 None
        """
        stmt = select(
            self.table.c.pad_state,
            self.table.c.conversation_history,
            self.table.c.turn_count,
            self.table.c.updated_at,
        ).where(
            self.table.c.character_id == character_id,
            self.table.c.user_id == user_id,
        )
        
        result = await self._mappings(stmt)
        
        if not result:
            return None
        
        row = result[0]
        return {
            "pad_state": self._json_value(row["pad_state"]),
            "conversation_history": self._json_value(row["conversation_history"]),
            "turn_count": row["turn_count"],
            "updated_at": row["updated_at"],
        }
    
    async def get_all_active_users(self) -> list[dict]:
        """获取所有活跃的 (character_id, user_id) 组合
        
        Returns:
            活跃用户列表 [{"character_id": ..., "user_id": ...}, ...]
        """
        stmt = select(
            self.table.c.character_id,
            self.table.c.user_id,
        ).distinct()
        
        return await self._mappings(stmt)


# 全局实例
_agent_state_repo: AgentStateRepository | None = None


def get_agent_state_repo() -> AgentStateRepository:
    """获取 AgentStateRepository 实例（全局单例）"""
    global _agent_state_repo
    if _agent_state_repo is None:
        _agent_state_repo = AgentStateRepository()
    return _agent_state_repo