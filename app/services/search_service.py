"""搜索服务 - 标签搜索和语义搜索"""

from sqlalchemy import select, func, and_, or_, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID

from app.models.motion import Motion
from app.models.tag import MotionTag, MotionTagMap
from app.schemas.search import TagSearchQuery


class SearchService:
    """搜索服务"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def search_by_tags(
        self,
        query: TagSearchQuery
    ) -> List[Dict[str, Any]]:
        """
        根据标签搜索动作（支持8个维度）
        
        返回匹配的动作列表，按匹配分数排序
        """
        # 构建标签过滤条件
        tag_filters = []
        tag_params = {}
        
        if query.emotion:
            tag_filters.append(
                and_(MotionTag.tag_type == 'emotion', MotionTag.tag_name == query.emotion)
            )
            tag_params['emotion'] = query.emotion
            
        if query.action:
            tag_filters.append(
                and_(MotionTag.tag_type == 'action', MotionTag.tag_name == query.action)
            )
            tag_params['action'] = query.action
            
        if query.scene:
            tag_filters.append(
                and_(MotionTag.tag_type == 'scene', MotionTag.tag_name == query.scene)
            )
            tag_params['scene'] = query.scene
            
        if query.intensity:
            tag_filters.append(
                and_(MotionTag.tag_type == 'intensity', MotionTag.tag_name == query.intensity)
            )
            tag_params['intensity'] = query.intensity
        
        if query.style:
            tag_filters.append(
                and_(MotionTag.tag_type == 'style', MotionTag.tag_name == query.style)
            )
            tag_params['style'] = query.style
            
        if query.speed:
            tag_filters.append(
                and_(MotionTag.tag_type == 'speed', MotionTag.tag_name == query.speed)
            )
            tag_params['speed'] = query.speed
            
        if query.rhythm:
            tag_filters.append(
                and_(MotionTag.tag_type == 'rhythm', MotionTag.tag_name == query.rhythm)
            )
            tag_params['rhythm'] = query.rhythm
            
        if query.complexity:
            tag_filters.append(
                and_(MotionTag.tag_type == 'complexity', MotionTag.tag_name == query.complexity)
            )
            tag_params['complexity'] = query.complexity
        
        if not tag_filters:
            return []
        
        # 查询匹配的动作和分数
        stmt = (
            select(
                Motion,
                func.sum(MotionTagMap.weight).label('tag_score')
            )
            .join(MotionTagMap, Motion.id == MotionTagMap.motion_id)
            .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
            .where(or_(*tag_filters))
            .group_by(Motion.id)
            .order_by(func.sum(MotionTagMap.weight).desc())
            .limit(query.limit)
        )
        
        result = await self.db.execute(stmt)
        rows = result.all()

        motion_ids = [motion.id for motion, _ in rows]
        matched_tags_by_motion: dict[UUID, list[str]] = {}

        if motion_ids:
            matched_tags_stmt = (
                select(MotionTagMap.motion_id, MotionTag.tag_type, MotionTag.tag_name)
                .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
                .where(
                    and_(
                        MotionTagMap.motion_id.in_(motion_ids),
                        or_(*tag_filters),
                    )
                )
            )
            matched_tags_result = await self.db.execute(matched_tags_stmt)
            for motion_id, tag_type, tag_name in matched_tags_result.all():
                matched_tags_by_motion.setdefault(motion_id, []).append(f"{tag_type}:{tag_name}")

        # 构建结果
        results = []
        for motion, tag_score in rows:
            results.append({
                "id": motion.id,
                "name": motion.name,
                "display_name": motion.display_name,
                "original_duration": motion.original_duration,
                "match_score": float(tag_score),
                "matched_tags": matched_tags_by_motion.get(motion.id, [])
            })

        return results
    
    async def search_semantic(
        self,
        query_embedding: List[float],
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        语义搜索动作
        
        Args:
            query_embedding: 查询文本的 embedding 向量
            limit: 返回数量限制
            
        Returns:
            按相似度排序的动作列表
        """
        # 使用 pgvector 的余弦相似度搜索
        # 1 - (embedding <=> query_embedding) 返回相似度 (0-1)
        # 注意：在 pgvector 0.2.x 中，应使用 Vector 列的 .cosine_distance() 方法
        
        stmt = (
            select(
                Motion,
                (1 - Motion.embedding.cosine_distance(query_embedding)).label('similarity')
            )
            .where(Motion.embedding.isnot(None))
            .order_by(Motion.embedding.cosine_distance(query_embedding))
            .limit(limit)
        )
        
        result = await self.db.execute(stmt)
        rows = result.all()
        
        results = []
        for motion, similarity in rows:
            results.append({
                "id": motion.id,
                "name": motion.name,
                "display_name": motion.display_name,
                "original_duration": motion.original_duration,
                "similarity": float(similarity)
            })
        
        return results
    
    async def get_all_tags(
        self,
        tag_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取所有标签
        
        Args:
            tag_type: 可选的标签类型过滤
            
        Returns:
            标签列表
        """
        if tag_type:
            stmt = select(MotionTag).where(MotionTag.tag_type == tag_type)
        else:
            stmt = select(MotionTag)
        
        stmt = stmt.order_by(MotionTag.tag_type, MotionTag.tag_name)
        
        result = await self.db.execute(stmt)
        tags = result.scalars().all()
        
        return [
            {
                "id": tag.id,
                "tag_type": tag.tag_type,
                "tag_name": tag.tag_name,
                "display_name": tag.display_name
            }
            for tag in tags
        ]