"""ChatMessage Repository - 聊天记录数据访问

提供 chat_messages 表的 CRUD 操作：
- insert: 插入聊天记录
- batch_insert: 批量插入
- get_recent: 获取最近聊天记录
- search_fts: FTS 搜索
- mark_extracted: 标记已提取
- cleanup_old: 清理过期记录
"""

from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, update, delete, and_, or_, text, func

from app.stone.database import Database, get_database
from app.stone.models.memory import chat_messages
from app.stone.repositories.base import BaseRepository


class ChatMessageRepository(BaseRepository):
    """聊天记录 Repository"""

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
        role: str,
        content: str,
        inner_monologue: str = None,
        turn_id: int = None,
        metadata: dict = None,
    ) -> int:
        """插入单条聊天记录

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            role: 角色（user/assistant）
            content: 内容
            inner_monologue: 内心独白（仅 assistant）
            turn_id: 对话轮次 ID
            metadata: 附加信息

        Returns:
            插入的消息 ID
        """
        data = {
            "character_id": character_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "inner_monologue": inner_monologue,
            "turn_id": turn_id,
            "metadata": metadata or {},
        }
        return await self._insert(chat_messages, data)

    async def batch_insert(self, messages: List[dict]) -> List[int]:
        """批量插入聊天记录

        Args:
            messages: 消息列表，每条消息包含 character_id, user_id, role, content 等字段

        Returns:
            插入的消息 ID 列表
        """
        # 确保 metadata 字段存在
        for msg in messages:
            if "metadata" not in msg:
                msg["metadata"] = {}
        return await self._batch_insert(chat_messages, messages)

    async def mark_extracted(self, message_ids: List[int], extracted_at: datetime = None) -> int:
        """标记消息已被提取

        Args:
            message_ids: 消息 ID 列表
            extracted_at: 提取时间，默认为当前时间

        Returns:
            更新的记录数
        """
        stmt = (
            update(chat_messages)
            .where(chat_messages.c.id.in_(message_ids))
            .values(
                is_extracted=True,
                extracted_at=extracted_at or datetime.now(),
            )
        )
        result = await self._execute_and_commit(stmt)
        return result.rowcount

    async def get_by_date_range(
        self,
        character_id: str,
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 100,
    ) -> List[dict]:
        """按时间范围获取聊天消息

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            start_time: 开始时间
            end_time: 结束时间
            limit: 最大条数

        Returns:
            消息列表
        """
        stmt = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.created_at >= start_time,
                    chat_messages.c.created_at <= end_time,
                )
            )
            .order_by(chat_messages.c.created_at.asc())
            .limit(limit)
        )
        return await self._mappings(stmt)

    async def cleanup_old(self, character_id: str, user_id: str, days: int = 14) -> int:
        """清理过期聊天记录

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            days: 保留天数

        Returns:
            删除的记录数
        """
        cutoff = datetime.now() - timedelta(days=days)
        stmt = (
            delete(chat_messages)
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.created_at < cutoff,
                    chat_messages.c.is_extracted == True,  # 只删除已提取的
                )
            )
        )
        result = await self._execute_and_commit(stmt)
        return result.rowcount

    # ============================================================
    # 读操作
    # ============================================================

    async def get_recent(
        self,
        character_id: str,
        user_id: str,
        limit: int = 50,
        days: int = 3,
    ) -> List[dict]:
        """获取最近的聊天记录

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数
            days: 最近天数

        Returns:
            聊天记录列表
        """
        cutoff = datetime.now() - timedelta(days=days)
        stmt = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.created_at >= cutoff,
                )
            )
            .order_by(chat_messages.c.created_at.desc())
            .limit(limit)
        )
        results = await self._mappings(stmt)
        # 反转顺序，使最早的在前面
        return list(reversed(results))

    async def get_by_turn_id(self, turn_id: int) -> List[dict]:
        """根据对话轮次 ID 获取消息

        Args:
            turn_id: 对话轮次 ID

        Returns:
            该轮次的消息列表
        """
        stmt = (
            select(chat_messages)
            .where(chat_messages.c.turn_id == turn_id)
            .order_by(chat_messages.c.created_at)
        )
        return await self._mappings(stmt)

    async def get_unextracted(
        self,
        character_id: str,
        user_id: str,
        limit: int = 100,
    ) -> List[dict]:
        """获取未提取的消息

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            limit: 最大条数

        Returns:
            未提取的消息列表
        """
        stmt = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.is_extracted == False,
                )
            )
            .order_by(chat_messages.c.created_at)
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
        """全文搜索聊天记录

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            query: 搜索查询
            limit: 最大条数
            days: 最近天数（可选）
            use_chinese: 是否使用中文分词

        Returns:
            匹配的消息列表（包含 rank）
        """
        # 选择 FTS 配置
        tsv_column = "content_tsv_cn" if use_chinese else "content_tsv"
        fts_config = "chinese_zh" if use_chinese else "simple"
        
        # 参数
        params = {
            "character_id": character_id,
            "user_id": user_id,
            "query": query,
            "limit": limit,
        }

        # 时间过滤
        time_condition = ""
        if days:
            time_condition = "AND created_at >= :cutoff"
            params["cutoff"] = datetime.now() - timedelta(days=days)

        # 构建 SQL（tsv_column/fts_config 为内部常量，非用户输入）
        sql = text(f"""
            SELECT id, character_id, user_id, role, content, inner_monologue, created_at,
                   ts_rank({tsv_column}, websearch_to_tsquery('{fts_config}', :query)) as rank
            FROM chat_messages
            WHERE character_id = :character_id
              AND user_id = :user_id
              AND {tsv_column} @@ websearch_to_tsquery('{fts_config}', :query)
              {time_condition}
            ORDER BY rank DESC, created_at DESC
            LIMIT :limit
        """)

        async with self._db.get_session() as session:
            result = await session.execute(sql, params)
            return [dict(row) for row in result.mappings()]

    async def get_context_around_message(
        self,
        character_id: str,
        user_id: str,
        message_id: int,
        context_before: int = 2,
        context_after: int = 2,
    ) -> List[dict]:
        """获取消息上下文

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            message_id: 目标消息 ID
            context_before: 前面消息数量
            context_after: 后面消息数量

        Returns:
            包含上下文的消息列表
        """
        # 获取目标消息的时间
        target_msg = await self.get_by_id(message_id)
        if not target_msg:
            return []
        
        target_time = target_msg.get("created_at")
        
        # 获取前面的消息
        stmt_before = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.created_at < target_time,
                )
            )
            .order_by(chat_messages.c.created_at.desc())
            .limit(context_before)
        )
        before_msgs = await self._mappings(stmt_before)
        
        # 获取后面的消息
        stmt_after = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.character_id == character_id,
                    chat_messages.c.user_id == user_id,
                    chat_messages.c.created_at > target_time,
                )
            )
            .order_by(chat_messages.c.created_at.asc())
            .limit(context_after)
        )
        after_msgs = await self._mappings(stmt_after)
        
        # 合并：前面的（反转） + 目标 + 后面的
        result = list(reversed(before_msgs)) + [target_msg] + after_msgs
        return result

    async def get_by_id(self, message_id: int) -> Optional[dict]:
        """根据 ID 获取消息

        Args:
            message_id: 消息 ID

        Returns:
            消息字典或 None
        """
        return await self._get_by_id(chat_messages, message_id)

    async def count(
        self,
        character_id: str,
        user_id: str,
        days: int = None,
    ) -> int:
        """统计消息数量

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            days: 最近天数（可选）

        Returns:
            消息数量
        """
        conditions = [
            chat_messages.c.character_id == character_id,
            chat_messages.c.user_id == user_id,
        ]
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            conditions.append(chat_messages.c.created_at >= cutoff)

        stmt = select(func.count(chat_messages.c.id)).where(and_(*conditions))
        result = await self._scalar(stmt)
        return result if result else 0


# ============================================================
# 全局实例（懒加载）
# ============================================================

_chat_repo: Optional[ChatMessageRepository] = None


def get_chat_repo() -> ChatMessageRepository:
    """获取 ChatMessageRepository 实例"""
    global _chat_repo
    if _chat_repo is None:
        _chat_repo = ChatMessageRepository()
    return _chat_repo