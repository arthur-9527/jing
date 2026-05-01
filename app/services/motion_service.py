"""动作查询服务 - 使用 Stone Repository"""

from typing import Optional, List, Tuple, Dict, Any
from uuid import UUID

from app.stone import get_motion_repo
from app.stone.repositories.motion import MotionRepository
from app.schemas.motion import MotionListQuery


class MotionService:
    """动作查询服务 - 使用 Stone 数据层"""

    def __init__(self, motion_repo: MotionRepository = None):
        """初始化

        Args:
            motion_repo: MotionRepository 实例，默认使用全局实例
        """
        self._repo = motion_repo or get_motion_repo()

    async def get_motion_list(
        self,
        query: MotionListQuery,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """获取动作列表

        Returns:
            (motions, total_count)
        """
        motions, total = await self._repo.get_list(
            status=query.status,
            is_loopable=query.is_loopable,
            min_duration=query.min_duration,
            max_duration=query.max_duration,
            page=query.page,
            page_size=query.page_size,
        )

        return motions, total

    async def get_motion_by_id(self, motion_id: UUID) -> Optional[Dict[str, Any]]:
        """根据 ID 获取动作详情"""
        return await self._repo.get_by_id(motion_id)

    async def get_motion_with_tags(self, motion_id: UUID) -> Optional[Dict[str, Any]]:
        """获取动作详情（包含标签）

        注意：返回的动作字典会额外包含 tags 字段
        """
        motion = await self._repo.get_by_id(motion_id)
        if not motion:
            return None

        # 获取关联的标签
        tags = await self._repo.get_motion_tags(motion_id)

        # 将标签信息附加到返回结果
        motion_with_tags = dict(motion)
        motion_with_tags["tags"] = tags

        return motion_with_tags

    async def get_keyframes(
        self,
        motion_id: UUID,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取动作的关键帧"""
        return await self._repo.get_keyframes(motion_id, start_frame, end_frame)

    async def get_single_keyframe(
        self,
        motion_id: UUID,
        frame_index: int,
    ) -> Optional[Dict[str, Any]]:
        """获取单帧数据"""
        return await self._repo.get_single_keyframe(motion_id, frame_index)

    async def get_motion_name(self, motion_id: UUID) -> Optional[str]:
        """获取动作名称"""
        motion = await self._repo.get_by_id(motion_id)
        return motion.get("name") if motion else None