"""动作查询服务"""

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import Optional, List, Tuple
from uuid import UUID

from app.models.motion import Motion, Keyframe
from app.models.tag import MotionTag, MotionTagMap
from app.schemas.motion import MotionListQuery


class MotionService:
    """动作查询服务"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_motion_list(
        self,
        query: MotionListQuery
    ) -> Tuple[List[Motion], int]:
        """
        获取动作列表
        
        Returns:
            (motions, total_count)
        """
        # 构建基础查询
        filters = []
        
        if query.status:
            filters.append(Motion.status == query.status)
        
        if query.is_loopable is not None:
            filters.append(Motion.is_loopable == query.is_loopable)
        
        if query.min_duration is not None:
            filters.append(Motion.original_duration >= query.min_duration)
        
        if query.max_duration is not None:
            filters.append(Motion.original_duration <= query.max_duration)
        
        # 查询总数
        total_query = select(func.count()).select_from(Motion)
        if filters:
            total_query = total_query.where(and_(*filters))
        total_result = await self.db.execute(total_query)
        total = total_result.scalar()
        
        # 查询数据
        stmt = select(Motion).where(and_(*filters)) if filters else select(Motion)
        stmt = stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)
        
        result = await self.db.execute(stmt)
        motions = result.scalars().all()
        
        return list(motions), total
    
    async def get_motion_by_id(self, motion_id: UUID) -> Optional[Motion]:
        """根据 ID 获取动作详情"""
        stmt = select(Motion).where(Motion.id == motion_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_motion_with_tags(self, motion_id: UUID) -> Optional[Motion]:
        """获取动作详情（包含标签）"""
        stmt = (
            select(Motion)
            .options(selectinload(Motion.tag_map).selectinload(MotionTagMap.tag))
            .where(Motion.id == motion_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_keyframes(
        self,
        motion_id: UUID,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None
    ) -> List[Keyframe]:
        """获取动作的关键帧"""
        stmt = select(Keyframe).where(Keyframe.motion_id == motion_id)
        
        if start_frame is not None:
            stmt = stmt.where(Keyframe.frame_index >= start_frame)
        
        if end_frame is not None:
            stmt = stmt.where(Keyframe.frame_index <= end_frame)
        
        stmt = stmt.order_by(Keyframe.frame_index)
        
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
    
    async def get_single_keyframe(
        self,
        motion_id: UUID,
        frame_index: int
    ) -> Optional[Keyframe]:
        """获取单帧数据"""
        stmt = select(Keyframe).where(
            and_(
                Keyframe.motion_id == motion_id,
                Keyframe.frame_index == frame_index
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_motion_name(self, motion_id: UUID) -> Optional[str]:
        """获取动作名称"""
        stmt = select(Motion.name).where(Motion.id == motion_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()