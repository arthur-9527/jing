"""
OpenClaw Redis 任务仓库

基于 Stone 数据层 OpenClawTaskRepository。
模型对象 (Task) ↔ dict 转换在此层完成。

Redis Key 设计（由 Stone KeyBuilder 管理）：
- agent:openclaw:task:{task_id} (Hash) - 任务详情
- agent:queue:openclaw (List) - Pending 任务队列
"""

import json
import time
from typing import Optional, List, Dict, Any
from loguru import logger

from app.stone.repositories.openclaw_task import (
    OpenClawTaskRepository as StoneOpenClawRepo,
    get_openclaw_task_repo,
)
from .models import Task, TaskStatus


class OpenClawTaskRepository:
    """OpenClaw 任务存储（委托给 Stone OpenClawTaskRepository）

    模型对象 (Task) ↔ dict 转换在此层完成。
    """

    def __init__(self):
        self._stone = get_openclaw_task_repo()

    @property
    def is_connected(self) -> bool:
        return True  # Stone 全局管理连接

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    # ============================================================
    # 任务 CRUD
    # ============================================================

    async def create_task(
        self,
        tool_prompt: str,
        task_id: Optional[str] = None,
        user_input: Optional[str] = None,
        memory_context: Optional[str] = None,
        conversation_history: Optional[str] = None,
        inner_monologue: Optional[str] = None,
        emotion_delta: Optional[Dict[str, float]] = None,
    ) -> str:
        task_id = await self._stone.create_task(
            task_id=task_id,
            tool_prompt=tool_prompt,
            user_input=user_input,
            memory_context=memory_context,
            conversation_history=conversation_history,
            inner_monologue=inner_monologue,
            emotion_delta=emotion_delta,
        )
        logger.debug(f"[RedisRepo] 任务已创建(Stone): {task_id}, status=PENDING")
        return task_id

    async def get_task(self, task_id: str) -> Optional[Task]:
        data = await self._stone.get_task(task_id)
        if not data:
            return None
        try:
            return Task.from_dict(data)
        except Exception as e:
            logger.error(f"[RedisRepo] 反序列化任务失败: {task_id}, {e}")
            return None

    async def get_task_by_run_id(self, run_id: str) -> Optional[Task]:
        data = await self._stone.get_task_by_run_id(run_id)
        if not data:
            return None
        try:
            return Task.from_dict(data)
        except Exception as e:
            logger.warning(f"[RedisRepo] 解析任务失败: {e}")
            return None

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        **kwargs,
    ) -> bool:
        return await self._stone.update_status(task_id, status.value, **kwargs)

    async def update_result(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> bool:
        return await self._stone.update_result(task_id, result, error)

    async def update_final_result(
        self,
        task_id: str,
        final_result: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> bool:
        return await self._stone.update_final_result(task_id, final_result, error)

    async def delete_task(self, task_id: str) -> bool:
        return await self._stone.delete_task(task_id)

    # ============================================================
    # 队列操作
    # ============================================================

    async def pop_pending_task(self) -> Optional[Task]:
        data = await self._stone.pop_pending_task()
        if not data:
            return None
        try:
            return Task.from_dict(data)
        except Exception as e:
            logger.warning(f"[RedisRepo] 队列中的任务解析失败: {e}")
            return None

    async def push_pending_task(self, task_id: str) -> None:
        await self._stone.push_pending_task(task_id)

    async def get_pending_queue_length(self) -> int:
        return await self._stone.get_pending_queue_length()

    async def get_all_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> List[Task]:
        status_str = status.value if status else None
        dicts = await self._stone.get_all_tasks(status=status_str, limit=limit)
        tasks = []
        for data in dicts:
            try:
                tasks.append(Task.from_dict(data))
            except Exception as e:
                logger.warning(f"[RedisRepo] 解析任务失败: {e}")
        return tasks

    async def get_task_count_by_status(self, status: TaskStatus) -> int:
        return await self._stone.get_task_count_by_status(status.value)

    # ============================================================
    # 批量操作
    # ============================================================

    async def clear_all_tasks(self) -> int:
        return await self._stone.clear_all_tasks()

    async def clear_pending_queue(self) -> int:
        return await self._stone.clear_pending_queue()

    # ============================================================
    # 监控和统计
    # ============================================================

    async def get_stats(self) -> Dict[str, Any]:
        stats = await self._stone.get_stats()
        stats["connected"] = True
        return stats

    async def has_previous_state(self) -> bool:
        return await self._stone.has_previous_state()

    async def clear_all_on_restart(self) -> int:
        return await self._stone.clear_all_on_restart()


# ============================================================
# 全局实例（懒加载）
# ============================================================

_repository: Optional[OpenClawTaskRepository] = None


async def get_task_repository() -> OpenClawTaskRepository:
    global _repository
    if _repository is None:
        _repository = OpenClawTaskRepository()
    return _repository


def set_task_repository(repo: OpenClawTaskRepository) -> None:
    global _repository
    _repository = repo
