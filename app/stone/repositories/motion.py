"""Motion Repository - 动作数据访问层

提供 motions, keyframes, motion_tags, motion_tag_map 的 CRUD 操作。
"""

from typing import Optional, List, Tuple, Dict, Any
from uuid import UUID

from sqlalchemy import select, func, and_, delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.stone.database import Database, get_database
from app.stone.repositories.base import BaseRepository
from app.stone.models.motion import motions, keyframes, motion_tags, motion_tag_map


class MotionRepository(BaseRepository):
    """动作 Repository

    提供动作相关的数据库操作：
    - 动作列表查询（分页、筛选）
    - 动作详情查询
    - 关键帧查询
    - 标签关联查询
    """

    def __init__(self, db: Database = None):
        super().__init__(db or get_database())

    # ============================================================
    # Motion CRUD
    # ============================================================

    async def get_list(
        self,
        status: str = None,
        is_loopable: bool = None,
        min_duration: float = None,
        max_duration: float = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """获取动作列表（分页）

        Args:
            status: 状态筛选
            is_loopable: 是否可循环
            min_duration: 最小时长
            max_duration: 最大时长
            page: 页码
            page_size: 每页数量

        Returns:
            (motions_list, total_count)
        """
        # 构建筛选条件
        filters = []

        if status:
            filters.append(motions.c.status == status)

        if is_loopable is not None:
            filters.append(motions.c.is_loopable == is_loopable)

        if min_duration is not None:
            filters.append(motions.c.original_duration >= min_duration)

        if max_duration is not None:
            filters.append(motions.c.original_duration <= max_duration)

        # 查询总数
        total_stmt = select(func.count()).select_from(motions)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))

        total = await self._scalar(total_stmt)

        # 查询数据
        data_stmt = select(motions)
        if filters:
            data_stmt = data_stmt.where(and_(*filters))

        data_stmt = data_stmt.offset((page - 1) * page_size).limit(page_size)

        results = await self._mappings(data_stmt)

        return list(results), total or 0

    async def get_by_id(self, motion_id: UUID) -> Optional[Dict[str, Any]]:
        """根据 ID 获取动作详情

        Args:
            motion_id: 动作 ID

        Returns:
            动作字典或 None
        """
        stmt = select(motions).where(motions.c.id == motion_id)
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据名称获取动作

        Args:
            name: 动作名称

        Returns:
            动作字典或 None
        """
        stmt = select(motions).where(motions.c.name == name)
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def create(self, data: Dict[str, Any]) -> UUID:
        """创建动作

        Args:
            data: 动作数据

        Returns:
            创建的动作 ID
        """
        stmt = insert(motions).values(**data).returning(motions.c.id)
        result = await self._execute_and_commit(stmt)
        return result.scalar()

    async def update(self, motion_id: UUID, data: Dict[str, Any]) -> bool:
        """更新动作

        Args:
            motion_id: 动作 ID
            data: 更新数据

        Returns:
            是否更新成功
        """
        stmt = update(motions).where(motions.c.id == motion_id).values(**data)
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0

    async def delete(self, motion_id: UUID) -> bool:
        """删除动作（级联删除关键帧和标签关联）

        Args:
            motion_id: 动作 ID

        Returns:
            是否删除成功
        """
        stmt = delete(motions).where(motions.c.id == motion_id)
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0

    # ============================================================
    # Keyframe CRUD
    # ============================================================

    async def get_keyframes(
        self,
        motion_id: UUID,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取动作的关键帧

        Args:
            motion_id: 动作 ID
            start_frame: 起始帧索引
            end_frame: 结束帧索引

        Returns:
            关键帧列表
        """
        stmt = select(keyframes).where(keyframes.c.motion_id == motion_id)

        if start_frame is not None:
            stmt = stmt.where(keyframes.c.frame_index >= start_frame)

        if end_frame is not None:
            stmt = stmt.where(keyframes.c.frame_index <= end_frame)

        stmt = stmt.order_by(keyframes.c.frame_index)

        results = await self._mappings(stmt)
        return list(results)

    async def get_single_keyframe(
        self,
        motion_id: UUID,
        frame_index: int,
    ) -> Optional[Dict[str, Any]]:
        """获取单帧数据

        Args:
            motion_id: 动作 ID
            frame_index: 帧索引

        Returns:
            关键帧字典或 None
        """
        stmt = select(keyframes).where(
            and_(
                keyframes.c.motion_id == motion_id,
                keyframes.c.frame_index == frame_index,
            )
        )
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def batch_insert_keyframes(self, data: List[Dict[str, Any]]) -> List[UUID]:
        """批量插入关键帧

        Args:
            data: 关键帧数据列表

        Returns:
            插入的关键帧 ID 列表
        """
        stmt = insert(keyframes).values(data).returning(keyframes.c.id)
        result = await self._execute_and_commit(stmt)
        return [row[0] for row in result.fetchall()]

    async def delete_keyframes(self, motion_id: UUID) -> int:
        """删除动作的所有关键帧

        Args:
            motion_id: 动作 ID

        Returns:
            删除的数量
        """
        stmt = delete(keyframes).where(keyframes.c.motion_id == motion_id)
        result = await self._execute_and_commit(stmt)
        return result.rowcount

    # ============================================================
    # Tag 关联查询
    # ============================================================

    async def get_motion_tags(self, motion_id: UUID) -> List[Dict[str, Any]]:
        """获取动作的标签列表

        Args:
            motion_id: 动作 ID

        Returns:
            标签列表（包含权重）
        """
        stmt = (
            select(motion_tags, motion_tag_map.c.weight)
            .select_from(motion_tag_map.join(motion_tags, motion_tag_map.c.tag_id == motion_tags.c.id))
            .where(motion_tag_map.c.motion_id == motion_id)
        )

        results = await self._mappings(stmt)
        return list(results)

    async def add_tag_to_motion(
        self,
        motion_id: UUID,
        tag_id: UUID,
        weight: float = 1.0,
    ) -> UUID:
        """为动作添加标签

        Args:
            motion_id: 动作 ID
            tag_id: 标签 ID
            weight: 权重

        Returns:
            关联记录 ID
        """
        stmt = insert(motion_tag_map).values(
            motion_id=motion_id,
            tag_id=tag_id,
            weight=weight,
        ).returning(motion_tag_map.c.id)

        result = await self._execute_and_commit(stmt)
        return result.scalar()

    async def remove_tag_from_motion(
        self,
        motion_id: UUID,
        tag_id: UUID,
    ) -> bool:
        """移除动作的标签

        Args:
            motion_id: 动作 ID
            tag_id: 标签 ID

        Returns:
            是否删除成功
        """
        stmt = delete(motion_tag_map).where(
            and_(
                motion_tag_map.c.motion_id == motion_id,
                motion_tag_map.c.tag_id == tag_id,
            )
        )
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0


class TagRepository(BaseRepository):
    """标签 Repository

    提供标签相关的数据库操作：
    - 标签列表查询
    - 标签创建、更新、删除
    """

    def __init__(self, db: Database = None):
        super().__init__(db or get_database())

    async def get_list(
        self,
        tag_type: str = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """获取标签列表

        Args:
            tag_type: 标签类型筛选
            page: 页码
            page_size: 每页数量

        Returns:
            (tags_list, total_count)
        """
        filters = []

        if tag_type:
            filters.append(motion_tags.c.tag_type == tag_type)

        # 查询总数
        total_stmt = select(func.count()).select_from(motion_tags)
        if filters:
            total_stmt = total_stmt.where(and_(*filters))

        total = await self._scalar(total_stmt)

        # 查询数据
        data_stmt = select(motion_tags)
        if filters:
            data_stmt = data_stmt.where(and_(*filters))

        data_stmt = data_stmt.offset((page - 1) * page_size).limit(page_size)

        results = await self._mappings(data_stmt)

        return list(results), total or 0

    async def get_by_id(self, tag_id: UUID) -> Optional[Dict[str, Any]]:
        """根据 ID 获取标签

        Args:
            tag_id: 标签 ID

        Returns:
            标签字典或 None
        """
        stmt = select(motion_tags).where(motion_tags.c.id == tag_id)
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def get_by_name(
        self,
        tag_type: str,
        tag_name: str,
    ) -> Optional[Dict[str, Any]]:
        """根据类型和名称获取标签

        Args:
            tag_type: 标签类型
            tag_name: 标签名称

        Returns:
            标签字典或 None
        """
        stmt = select(motion_tags).where(
            and_(
                motion_tags.c.tag_type == tag_type,
                motion_tags.c.tag_name == tag_name,
            )
        )
        results = await self._mappings(stmt)
        return results[0] if results else None

    async def create(self, data: Dict[str, Any]) -> UUID:
        """创建标签

        Args:
            data: 标签数据

        Returns:
            创建的标签 ID
        """
        stmt = insert(motion_tags).values(**data).returning(motion_tags.c.id)
        result = await self._execute_and_commit(stmt)
        return result.scalar()

    async def update(self, tag_id: UUID, data: Dict[str, Any]) -> bool:
        """更新标签

        Args:
            tag_id: 标签 ID
            data: 更新数据

        Returns:
            是否更新成功
        """
        stmt = update(motion_tags).where(motion_tags.c.id == tag_id).values(**data)
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0

    async def delete(self, tag_id: UUID) -> bool:
        """删除标签

        Args:
            tag_id: 标签 ID

        Returns:
            是否删除成功
        """
        stmt = delete(motion_tags).where(motion_tags.c.id == tag_id)
        result = await self._execute_and_commit(stmt)
        return result.rowcount > 0

    async def get_motions_by_tag(self, tag_id: UUID) -> List[Dict[str, Any]]:
        """获取使用该标签的所有动作

        Args:
            tag_id: 标签 ID

        Returns:
            动作列表
        """
        stmt = (
            select(motions)
            .select_from(motion_tag_map.join(motions, motion_tag_map.c.motion_id == motions.c.id))
            .where(motion_tag_map.c.tag_id == tag_id)
        )

        results = await self._mappings(stmt)
        return list(results)


# ============================================================
# 全局实例
# ============================================================

_motion_repo: Optional[MotionRepository] = None
_tag_repo: Optional[TagRepository] = None


def get_motion_repo() -> MotionRepository:
    """获取 MotionRepository 实例"""
    global _motion_repo
    if _motion_repo is None:
        _motion_repo = MotionRepository()
    return _motion_repo


def get_tag_repo() -> TagRepository:
    """获取 TagRepository 实例"""
    global _tag_repo
    if _tag_repo is None:
        _tag_repo = TagRepository()
    return _tag_repo