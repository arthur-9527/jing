"""TaskQueue Repository - 任务队列数据访问

提供任务系统的 Redis 操作：
- create_task: 创建任务
- update_status: 更新任务状态
- pop_pending: 弹出待处理任务
- get_stats: 获取统计信息

基于 task_system/redis_repo.py 迁移
"""

from typing import Optional, Dict, Any, List
import uuid
import time

from loguru import logger

from app.stone.redis_pool import RedisPool, get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository


class TaskQueueRepository(RedisRepository):
    """任务队列 Repository (Redis)

    Redis Key 设计：
    - task:{task_id}         # 任务详情 (Hash)
    - queue:pending          # Pending 任务列表 (List)
    - stats:task             # 统计信息 (Hash)
    """

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
        return self._key_builder.task(task_id)

    def _pending_queue_key(self) -> str:
        """Pending 队列 Key"""
        return self._key_builder.task_queue("pending")

    def _stats_key(self) -> str:
        """统计 Key"""
        return self._key_builder.task_stats()

    # ============================================================
    # 任务 CRUD
    # ============================================================

    async def create_task(
        self,
        task_id: str = None,
        tool_prompt: str = "",
        provider_name: str = "openclaw",
        context: Dict[str, Any] = None,
        status: str = "pending",
    ) -> str:
        """创建任务

        Args:
            task_id: 任务 ID（可选，自动生成）
            tool_prompt: LLM 工具调用提示
            provider_name: Provider 名称
            context: 任务上下文
            status: 初始状态

        Returns:
            任务 ID
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        task_data = {
            "id": task_id,
            "tool_prompt": tool_prompt,
            "provider_name": provider_name,
            "context": context or {},
            "status": status,
            "created_at": str(time.time()),
        }

        # 存储任务详情
        key = self._task_key(task_id)
        await self.hset(key, mapping=task_data)

        # 加入 Pending 队列
        queue_key = self._pending_queue_key()
        await self.rpush(queue_key, task_id)

        # 更新统计
        await self._increment_stats("created")

        logger.info(f"[TaskQueueRepo] 任务已创建: {task_id[:8]}...")
        return task_id

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务详情

        Args:
            task_id: 任务 ID

        Returns:
            任务字典或 None
        """
        key = self._task_key(task_id)
        return await self.hgetall(key)

    async def update_task(self, task_id: str, data: Dict[str, Any]) -> None:
        """更新任务

        Args:
            task_id: 任务 ID
            data: 更新数据
        """
        key = self._task_key(task_id)
        await self.hset(key, mapping=data)

    async def delete_task(self, task_id: str) -> bool:
        """删除任务

        Args:
            task_id: 任务 ID

        Returns:
            是否删除成功
        """
        # 从 Pending 队列移除
        queue_key = self._pending_queue_key()
        await self.lrem(queue_key, 0, task_id)

        # 删除任务详情
        key = self._task_key(task_id)
        result = await self.delete(key)

        return result > 0

    # ============================================================
    # 状态更新
    # ============================================================

    async def update_status(
        self,
        task_id: str,
        status: str,
        error: str = None,
    ) -> None:
        """更新任务状态

        Args:
            task_id: 任务 ID
            status: 新状态
            error: 错误信息（可选）
        """
        task = await self.get_task(task_id)
        if not task:
            logger.warning(f"[TaskQueueRepo] 任务不存在: {task_id[:8]}...")
            return

        # 更新状态
        task["status"] = status
        if error:
            task["error"] = error

        # 更新时间戳
        if status == "submitted":
            task["submitted_at"] = str(time.time())
        elif status == "running":
            task["started_at"] = str(time.time())
            # 从 Pending 队列移除
            queue_key = self._pending_queue_key()
            await self.lrem(queue_key, 0, task_id)
        elif status == "provider_done":
            task["provider_done_at"] = str(time.time())
        elif status == "completed":
            task["completed_at"] = str(time.time())

        # 保存
        await self.update_task(task_id, task)

        # 更新统计
        await self._increment_stats(status)

        logger.debug(f"[TaskQueueRepo] 状态更新: {task_id[:8]}... → {status}")

    async def update_provider_result(
        self,
        task_id: str,
        result: Dict[str, Any],
    ) -> None:
        """存储 Provider 原始结果

        Args:
            task_id: 任务 ID
            result: Provider 结果
        """
        task = await self.get_task(task_id)
        if not task:
            return

        task["provider_result"] = result
        task["status"] = "provider_done"
        task["provider_done_at"] = str(time.time())

        await self.update_task(task_id, task)

    async def update_broadcast_content(
        self,
        task_id: str,
        broadcast: Dict[str, Any],
    ) -> None:
        """存储二次改写后的播报内容

        Args:
            task_id: 任务 ID
            broadcast: 播报内容
        """
        task = await self.get_task(task_id)
        if not task:
            return

        task["broadcast_content"] = broadcast
        task["status"] = "completed"
        task["completed_at"] = str(time.time())

        await self.update_task(task_id, task)

    # ============================================================
    # 队列操作
    # ============================================================

    async def pop_pending_task(self) -> Optional[Dict[str, Any]]:
        """从 Pending 队列弹出一个任务

        Returns:
            任务字典或 None
        """
        queue_key = self._pending_queue_key()
        task_id = await self.lpop(queue_key)
        if not task_id:
            return None
        return await self.get_task(task_id)

    async def get_pending_count(self) -> int:
        """获取 Pending 队列长度"""
        queue_key = self._pending_queue_key()
        return await self.llen(queue_key)

    async def get_all_pending_tasks(self) -> List[Dict[str, Any]]:
        """获取所有 Pending 任务"""
        queue_key = self._pending_queue_key()
        task_ids = await self.lrange(queue_key, 0, -1)
        tasks = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks

    # ============================================================
    # 统计
    # ============================================================

    async def _increment_stats(self, field: str, delta: int = 1) -> None:
        """更新统计"""
        stats_key = self._stats_key()
        await self.hincrbyfloat(stats_key, field, delta)

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats_key = self._stats_key()
        stats = await self.hgetall(stats_key) or {}
        stats["pending_count"] = await self.get_pending_count()
        return stats

    # ============================================================
    # 启动清理
    # ============================================================

    async def clear_all_on_start(self) -> int:
        """启动时清空所有任务

        Returns:
            清空的任务数量
        """
        count = 0

        # 获取所有任务 Key
        pattern = self._key_builder.build("task", task_id="*")
        keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            keys.append(key)

        # 删除所有任务
        if keys:
            count = await self._redis.delete(*keys)

        # 清空 Pending 队列
        queue_key = self._pending_queue_key()
        await self.delete(queue_key)

        # 重置统计
        stats_key = self._stats_key()
        await self.delete(stats_key)

        logger.warning(f"[TaskQueueRepo] 启动清理：清空 {count} 个任务")
        return count


# ============================================================
# 全局实例（懒加载）
# ============================================================

_task_queue_repo: Optional[TaskQueueRepository] = None


def get_task_queue_repo() -> TaskQueueRepository:
    """获取 TaskQueueRepository 实例"""
    global _task_queue_repo
    if _task_queue_repo is None:
        _task_queue_repo = TaskQueueRepository()
    return _task_queue_repo