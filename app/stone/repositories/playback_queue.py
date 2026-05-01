"""PlaybackQueue Repository - 播报队列数据访问

提供播报系统的 Redis 操作：
- enqueue: 任务入队
- pop: 任务出队
- get_task: 获取任务详情
- get_queue_length: 队列长度
- clear_all: 清空所有任务

基于 realtime/playback/redis_repo.py 迁移
"""

from typing import Optional, Dict, Any, List
import time

from loguru import logger

from app.stone.redis_pool import RedisPool, get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository


class PlaybackQueueRepository(RedisRepository):
    """播报队列 Repository (Redis)

    Redis Key 设计：
    - playback:{task_id}     # 任务详情 (Hash)
    - queue:playback         # 待播报队列 (List)
    """

    # 任务 TTL（1小时）
    TASK_TTL = 3600

    def __init__(self, redis: RedisPool = None, key_builder: RedisKeyBuilder = None):
        """初始化

        Args:
            redis: RedisPool 实例，默认使用全局实例
            key_builder: RedisKeyBuilder 实例
        """
        super().__init__(redis or get_redis_pool(), "agent")
        self._key_builder = key_builder or RedisKeyBuilder()

    # ============================================================
    # Key 构建方法
    # ============================================================

    def _task_key(self, task_id: str) -> str:
        """任务详情 Key"""
        return self._key_builder.playback_task(task_id)

    def _queue_key(self) -> str:
        """队列 Key"""
        return self._key_builder.playback_queue()

    # ============================================================
    # 入队/出队操作
    # ============================================================

    async def enqueue(
        self,
        task_id: str,
        task_data: Dict[str, Any],
        priority: bool = False,
    ) -> None:
        """将播报任务入队

        Args:
            task_id: 任务 ID
            task_data: 任务数据
            priority: 是否优先入队（队首插队），默认 False（队尾顺序）

        设计：
        - 队尾入队（默认）：RPUSH → 顺序播报
        - 队首入队（优先）：LPUSH → 插队播报
        - 出队：LPOP → 从队首取出（优先任务先执行）
        """
        # 过滤 None 值
        task_data_filtered = {k: v for k, v in task_data.items() if v is not None}
        task_data_filtered["priority"] = str(priority)
        task_data_filtered["enqueued_at"] = str(time.time())

        # 保存任务详情到 Hash
        task_key = self._task_key(task_id)
        await self.hset(task_key, mapping=task_data_filtered)
        await self.expire(task_key, self.TASK_TTL)

        # 入队：队尾或队首
        queue_key = self._queue_key()
        if priority:
            # LPUSH：队首插入（优先播报）
            await self.lpush(queue_key, task_id)
            position = "队首（优先）"
        else:
            # RPUSH：队尾插入（顺序播报）
            await self.rpush(queue_key, task_id)
            position = "队尾（顺序）"

        queue_len = await self.llen(queue_key)
        logger.info(
            f"[PlaybackQueueRepo] 任务入队 {position}: task_id={task_id[:8]}..., "
            f"queue_size={queue_len}"
        )

    async def pop(self) -> Optional[Dict[str, Any]]:
        """从队列取出一个任务（从队首取出）

        Returns:
            任务字典，队列空返回 None

        设计：
        - LPOP：从队首取出（优先任务先执行）
        - 配合 RPUSH（队尾入队）实现 FIFO
        - 配合 LPUSH（队首入队）实现优先插队
        """
        queue_key = self._queue_key()

        # LPOP：从队首取出（优先任务先执行）
        task_id = await self.lpop(queue_key)
        if not task_id:
            return None

        # 获取任务详情
        task = await self.get_task(task_id)
        if task:
            logger.info(f"[PlaybackQueueRepo] 任务出队: task_id={task_id[:8]}...")
        else:
            logger.warning(f"[PlaybackQueueRepo] 队列中的任务不存在: {task_id}")

        return task

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务详情

        Args:
            task_id: 任务 ID

        Returns:
            任务字典或 None
        """
        task_key = self._task_key(task_id)
        return await self.hgetall(task_key)

    # ============================================================
    # 队列状态查询
    # ============================================================

    async def get_queue_length(self) -> int:
        """获取队列长度"""
        queue_key = self._queue_key()
        return await self.llen(queue_key)

    async def peek_all(self) -> List[str]:
        """查看队列中所有任务 ID（不出队）

        Returns:
            任务 ID 列表（按入队顺序）
        """
        queue_key = self._queue_key()
        return await self.lrange(queue_key, 0, -1)

    async def get_all_tasks(self) -> List[Dict[str, Any]]:
        """获取队列中所有任务详情

        Returns:
            任务列表（按入队顺序）
        """
        task_ids = await self.peek_all()
        tasks = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks

    # ============================================================
    # 清空操作（重启时调用）
    # ============================================================

    async def clear_all(self) -> int:
        """清空所有播报任务（重启时调用）

        Returns:
            清空的任务数量
        """
        logger.warning("[PlaybackQueueRepo] 开始清空所有播报任务...")

        # 1. 删除 pending 队列
        queue_key = self._queue_key()
        queue_count = await self.delete(queue_key)

        # 2. 删除所有任务详情
        pattern = self._key_builder.build("playback_task", task_id="*")
        task_keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_keys.append(key)

        task_count = 0
        if task_keys:
            task_count = await self._redis.delete(*task_keys)

        total = queue_count + task_count
        logger.warning(
            f"[PlaybackQueueRepo] 清空完成：队列 {queue_count} 个，"
            f"任务 {task_count} 个，共 {total} 个"
        )
        return total

    # ============================================================
    # 监控和统计
    # ============================================================

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        queue_len = await self.get_queue_length()

        return {
            "queue_length": queue_len,
            "queue_key": self._queue_key(),
            "task_ttl": self.TASK_TTL,
        }


# ============================================================
# 全局实例（懒加载）
# ============================================================

_playback_queue_repo: Optional[PlaybackQueueRepository] = None


def get_playback_queue_repo() -> PlaybackQueueRepository:
    """获取 PlaybackQueueRepository 实例"""
    global _playback_queue_repo
    if _playback_queue_repo is None:
        _playback_queue_repo = PlaybackQueueRepository()
    return _playback_queue_repo