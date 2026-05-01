"""
播报队列 Redis 仓库

基于 Stone 数据层 PlaybackQueueRepository。
模型对象 (PlaybackTask) ↔ dict 转换在此层完成。

Redis Key 设计（由 Stone KeyBuilder 管理）：
- agent:queue:playback (List) - 待播报队列
- agent:playback:{task_id} (Hash) - 任务详情
"""

from typing import Optional, Dict, Any

from loguru import logger

from app.stone.repositories.playback_queue import (
    PlaybackQueueRepository as StonePlaybackQueueRepo,
    get_playback_queue_repo,
)
from .models import PlaybackTask


class PlaybackQueueRepository:
    """播报队列存储（委托给 Stone PlaybackQueueRepository）

    模型对象 (PlaybackTask) ↔ dict 转换在此层完成。
    """

    def __init__(self):
        self._stone = get_playback_queue_repo()

    @property
    def is_connected(self) -> bool:
        return True  # Stone 全局管理连接

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    # ============================================================
    # 入队/出队
    # ============================================================

    async def enqueue(self, task: PlaybackTask, priority: bool = False) -> None:
        task_data = task.to_dict()
        await self._stone.enqueue(task.id, task_data, priority)
        logger.info(
            f"[PlaybackQueueRepo] 任务入队(Stone): {task.to_summary()}, priority={priority}"
        )

    async def pop(self) -> Optional[PlaybackTask]:
        data = await self._stone.pop()
        if not data:
            return None
        return PlaybackTask.from_dict(data)

    async def get_task(self, task_id: str) -> Optional[PlaybackTask]:
        data = await self._stone.get_task(task_id)
        if not data:
            return None
        try:
            return PlaybackTask.from_dict(data)
        except Exception as e:
            logger.error(f"[PlaybackQueueRepo] 反序列化任务失败: {task_id}, {e}")
            return None

    # ============================================================
    # 队列状态查询
    # ============================================================

    async def get_queue_length(self) -> int:
        return await self._stone.get_queue_length()

    async def peek_all(self) -> list[str]:
        return await self._stone.peek_all()

    async def get_all_tasks(self) -> list[PlaybackTask]:
        task_ids = await self.peek_all()
        tasks = []
        for tid in task_ids:
            task = await self.get_task(tid)
            if task:
                tasks.append(task)
        return tasks

    # ============================================================
    # 清空操作
    # ============================================================

    async def clear_all(self) -> int:
        return await self._stone.clear_all()

    # ============================================================
    # 统计
    # ============================================================

    async def get_stats(self) -> Dict[str, Any]:
        return await self._stone.get_stats()


# ============================================================
# 全局实例（懒加载）
# ============================================================

_repository: Optional[PlaybackQueueRepository] = None


async def get_playback_repository() -> PlaybackQueueRepository:
    global _repository
    if _repository is None:
        _repository = PlaybackQueueRepository()
    return _repository


def set_playback_repository(repo: PlaybackQueueRepository) -> None:
    global _repository
    _repository = repo
