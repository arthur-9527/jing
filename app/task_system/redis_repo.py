"""
任务系统 Redis 存储 - 主队列任务存储

基于 Stone 数据层基础设施（RedisPool + KeyBuilder + RedisRepository）。
模型对象 (Task) ↔ dict 转换在此层完成。

Redis Key 设计（由 Stone KeyBuilder 管理）：
- agent:task:{task_id}         # 任务详情 (Hash)
- agent:queue:task:pending     # Pending 任务列表 (List)
- agent:stats:task             # 统计信息 (Hash)
"""

import time
import json
import uuid
from typing import Optional, Dict, Any, List

from loguru import logger

from app.stone.redis_pool import get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository
from .models import Task, TaskStatus
from .config import get_task_system_settings


class TaskRepository(RedisRepository):
    """任务系统主队列存储（基于 Stone 基础设施）

    所有 Redis 操作委托给 Stone 的 RedisPool + KeyBuilder。
    模型对象 (Task) ↔ dict 转换在此层完成。
    """

    def __init__(self):
        super().__init__(get_redis_pool(), "agent")
        self._settings = get_task_system_settings()
        self._key_builder = RedisKeyBuilder()

    # ============================================================
    # Key 构建
    # ============================================================

    def _task_key(self, task_id: str) -> str:
        return self._key_builder.task(task_id)

    def _pending_queue_key(self) -> str:
        return self._key_builder.task_queue("pending")

    def _stats_key(self) -> str:
        return self._key_builder.task_stats()

    # ============================================================
    # 连接管理（Stone 统一管理，不再需要单独的 connect/disconnect）
    # ============================================================

    @property
    def is_connected(self) -> bool:
        return True  # Stone RedisPool 全局管理连接

    async def connect(self) -> None:
        pass  # Stone 统一管理

    async def disconnect(self) -> None:
        pass  # Stone 统一管理

    # ============================================================
    # 任务 CRUD
    # ============================================================

    async def create_task(
        self,
        tool_prompt: str,
        provider_name: str = "openclaw",
        context: Dict[str, Any] = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            tool_prompt=tool_prompt,
            provider_name=provider_name,
            context=context or {},
            status=TaskStatus.PENDING,
        )

        await self.hset(self._task_key(task_id), mapping=task.to_dict())
        await self.rpush(self._pending_queue_key(), task_id)
        await self._update_stats("created", 1)

        logger.info(f"[TaskRepository] 任务已创建: {task_id[:8]}...")
        return task_id

    async def get_task(self, task_id: str) -> Optional[Task]:
        data = await self.hgetall(self._task_key(task_id))
        if not data:
            return None
        return Task.from_dict(data)

    async def update_task(self, task: Task) -> None:
        await self.hset(self._task_key(task.id), mapping=task.to_dict())

    async def delete_task(self, task_id: str) -> bool:
        await self._redis.lrem(self._pending_queue_key(), 0, task_id)
        result = await self.delete(self._task_key(task_id))
        return result > 0

    # ============================================================
    # 状态更新
    # ============================================================

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: str = None,
    ) -> None:
        task = await self.get_task(task_id)
        if not task:
            logger.warning(f"[TaskRepository] 任务不存在: {task_id[:8]}...")
            return

        task.status = status
        if error:
            task.error = error

        if status == TaskStatus.SUBMITTED:
            task.submitted_at = time.time()
        elif status == TaskStatus.RUNNING:
            task.started_at = time.time()
            await self._redis.lrem(self._pending_queue_key(), 0, task_id)
        elif status == TaskStatus.PROVIDER_DONE:
            task.provider_done_at = time.time()
        elif status == TaskStatus.COMPLETED:
            task.completed_at = time.time()

        await self.update_task(task)
        await self._update_stats(status.value, 1)

        logger.debug(f"[TaskRepository] 状态更新: {task_id[:8]}... → {status.value}")

    async def update_provider_result(
        self,
        task_id: str,
        result: Dict[str, Any],
    ) -> None:
        task = await self.get_task(task_id)
        if not task:
            return

        task.provider_result = result
        task.status = TaskStatus.PROVIDER_DONE
        task.provider_done_at = time.time()

        await self.update_task(task)

    async def update_broadcast_content(
        self,
        task_id: str,
        broadcast: Dict[str, Any],
    ) -> None:
        task = await self.get_task(task_id)
        if not task:
            return

        task.broadcast_content = broadcast
        task.status = TaskStatus.COMPLETED
        task.completed_at = time.time()

        await self.update_task(task)

    # ============================================================
    # 队列操作
    # ============================================================

    async def pop_pending_task(self) -> Optional[Task]:
        task_id = await self.lpop(self._pending_queue_key())
        if not task_id:
            return None
        return await self.get_task(task_id)

    async def get_pending_count(self) -> int:
        return await self.llen(self._pending_queue_key())

    async def get_all_pending_tasks(self) -> List[Task]:
        task_ids = await self.lrange(self._pending_queue_key(), 0, -1)
        tasks = []
        for tid in task_ids:
            task = await self.get_task(tid)
            if task:
                tasks.append(task)
        return tasks

    # ============================================================
    # 启动清理
    # ============================================================

    async def clear_all_on_start(self) -> int:
        pattern = self._key_builder.build("task", task_id="*")
        keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            keys.append(key)

        count = 0
        if keys:
            count = await self._redis.delete(*keys)

        await self.delete(self._pending_queue_key())
        await self.delete(self._stats_key())

        logger.warning(f"[TaskRepository] 启动清理：清空 {count} 个任务")
        return count

    # ============================================================
    # 统计
    # ============================================================

    async def _update_stats(self, field: str, delta: int) -> None:
        await self.hincrbyfloat(self._stats_key(), field, float(delta))

    async def get_stats(self) -> Dict[str, Any]:
        stats = await self.hgetall(self._stats_key()) or {}
        stats["pending_count"] = await self.get_pending_count()
        stats["connected"] = True
        return stats

    async def get_tasks_by_status(
        self,
        status: TaskStatus,
        limit: int = 100,
    ) -> List[Task]:
        tasks = []
        pattern = self._key_builder.build("task", task_id="*")

        async for key in self._redis.scan_iter(match=pattern, count=100):
            data = await self.hgetall(key)
            if data:
                task = Task.from_dict(data)
                if task.status == status:
                    tasks.append(task)
                    if len(tasks) >= limit:
                        return tasks

        return tasks


# ============================================================
# 全局实例（懒加载）
# ============================================================

_repository: Optional[TaskRepository] = None


async def get_task_repository() -> TaskRepository:
    global _repository
    if _repository is None:
        _repository = TaskRepository()
    return _repository


def get_task_repository_sync() -> TaskRepository:
    global _repository
    if _repository is None:
        _repository = TaskRepository()
    return _repository


def reset_task_repository():
    global _repository
    _repository = None
