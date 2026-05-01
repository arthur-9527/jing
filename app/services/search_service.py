"""搜索服务 - 标签搜索和语义搜索，使用 Stone 数据层"""

from sqlalchemy import select, func, and_, or_
from typing import Optional, List, Dict, Any

from app.stone import get_motion_repo, get_tag_repo
from app.stone.repositories.motion import MotionRepository, TagRepository
from app.stone.models.motion import motions, motion_tags, motion_tag_map
from app.schemas.search import TagSearchQuery


class SearchService:
    """搜索服务 - 使用 Stone 数据层"""

    def __init__(
        self,
        motion_repo: MotionRepository = None,
        tag_repo: TagRepository = None,
    ):
        """初始化

        Args:
            motion_repo: MotionRepository 实例
            tag_repo: TagRepository 实例
        """
        self._motion_repo = motion_repo or get_motion_repo()
        self._tag_repo = tag_repo or get_tag_repo()

    async def search_by_tags(
        self,
        query: TagSearchQuery,
    ) -> List[Dict[str, Any]]:
        """根据标签搜索动作（支持8个维度）

        返回匹配的动作列表，按匹配分数排序
        """
        # 构建标签过滤条件
        tag_filters = []

        if query.emotion:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'emotion', motion_tags.c.tag_name == query.emotion)
            )

        if query.action:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'action', motion_tags.c.tag_name == query.action)
            )

        if query.scene:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'scene', motion_tags.c.tag_name == query.scene)
            )

        if query.intensity:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'intensity', motion_tags.c.tag_name == query.intensity)
            )

        if query.style:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'style', motion_tags.c.tag_name == query.style)
            )

        if query.speed:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'speed', motion_tags.c.tag_name == query.speed)
            )

        if query.rhythm:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'rhythm', motion_tags.c.tag_name == query.rhythm)
            )

        if query.complexity:
            tag_filters.append(
                and_(motion_tags.c.tag_type == 'complexity', motion_tags.c.tag_name == query.complexity)
            )

        if not tag_filters:
            return []

        # 使用 Repository 的内部方法执行复杂查询
        # 查询匹配的动作和分数
        stmt = (
            select(
                motions,
                func.sum(motion_tag_map.c.weight).label('tag_score')
            )
            .select_from(
                motions.join(motion_tag_map, motions.c.id == motion_tag_map.c.motion_id)
                .join(motion_tags, motion_tag_map.c.tag_id == motion_tags.c.id)
            )
            .where(or_(*tag_filters))
            .group_by(motions.c.id)
            .order_by(func.sum(motion_tag_map.c.weight).desc())
            .limit(query.limit)
        )

        rows = await self._motion_repo._mappings(stmt)

        # 获取每个动作匹配的标签
        motion_ids = [row['id'] for row in rows]

        matched_tags_by_motion: dict = {}
        if motion_ids:
            matched_tags_stmt = (
                select(
                    motion_tag_map.c.motion_id,
                    motion_tags.c.tag_type,
                    motion_tags.c.tag_name,
                )
                .select_from(
                    motion_tag_map.join(motion_tags, motion_tag_map.c.tag_id == motion_tags.c.id)
                )
                .where(
                    and_(
                        motion_tag_map.c.motion_id.in_(motion_ids),
                        or_(*tag_filters),
                    )
                )
            )
            matched_rows = await self._motion_repo._mappings(matched_tags_stmt)
            for row in matched_rows:
                motion_id = row['motion_id']
                matched_tags_by_motion.setdefault(motion_id, []).append(
                    f"{row['tag_type']}:{row['tag_name']}"
                )

        # 构建结果
        results = []
        for row in rows:
            results.append({
                "id": row['id'],
                "name": row['name'],
                "display_name": row['display_name'],
                "original_duration": row['original_duration'],
                "match_score": float(row['tag_score']),
                "matched_tags": matched_tags_by_motion.get(row['id'], []),
            })

        return results

    async def search_semantic(
        self,
        query_embedding: List[float],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """语义搜索动作

        Args:
            query_embedding: 查询文本的 embedding 向量
            limit: 返回数量限制

        Returns:
            按相似度排序的动作列表
        """
        # 使用 pgvector 的余弦相似度搜索
        stmt = (
            select(
                motions,
                (1 - motions.c.embedding.cosine_distance(query_embedding)).label('similarity')
            )
            .where(motions.c.embedding.isnot(None))
            .order_by(motions.c.embedding.cosine_distance(query_embedding))
            .limit(limit)
        )

        rows = await self._motion_repo._mappings(stmt)

        results = []
        for row in rows:
            results.append({
                "id": row['id'],
                "name": row['name'],
                "display_name": row['display_name'],
                "original_duration": row['original_duration'],
                "similarity": float(row['similarity']),
            })

        return results

    async def get_all_tags(
        self,
        tag_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取所有标签

        Args:
            tag_type: 可选的标签类型过滤

        Returns:
            标签列表
        """
        tags, _ = await self._tag_repo.get_list(tag_type=tag_type, page_size=1000)

        return [
            {
                "id": tag['id'],
                "tag_type": tag['tag_type'],
                "tag_name": tag['tag_name'],
                "display_name": tag['display_name'],
            }
            for tag in tags
        ]