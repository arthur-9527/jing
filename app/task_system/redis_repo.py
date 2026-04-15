"""
任务系统 Redis 存储 - 主队列任务存储

核心功能：
1. 任务 CRUD 操作
2. 状态更新
3. 结果存储
4. 启动时清空队列
"""

import time
import json
import uuid
from typing import Optional, Dict, Any, List

from loguru import logger
import redis.asyncio as redis

from .models import Task, TaskStatus
from .config import get_task_system_settings


class TaskRepository:
    """任务系统主队列 Redis 存储
    
    Redis Key 设计：
    - task_system:task:{task_id}         # 任务详情
    - task_system:pending                 # Pending 任务列表（有序）
    - task_system:stats                   # 统计信息
    """
    
    # Redis Key 前缀
    KEY_PREFIX = "task_system"
    KEY_TASK = f"{KEY_PREFIX}:task"      # task_system:task:{task_id}
    KEY_PENDING = f"{KEY_PREFIX}:pending" # task_system:pending
    KEY_STATS = f"{KEY_PREFIX}:stats"     # task_system:stats
    
    def __init__(self):
        self._settings = get_task_system_settings()
        self._redis: Optional[redis.Redis] = None
        self._connected = False
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    async def connect(self) -> None:
        """连接 Redis"""
        if self._connected:
            return
        
        try:
            from app.config import settings
            
            # 使用 REDIS_URL 连接（支持 redis://host:port/db 格式）
            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
            
            # 测试连接
            await self._redis.ping()
            self._connected = True
            logger.info("[TaskRepository] Redis 已连接")
            
        except Exception as e:
            logger.error(f"[TaskRepository] Redis 连接失败: {e}")
            raise
    
    async def disconnect(self) -> None:
        """断开 Redis 连接"""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            self._connected = False
            logger.info("[TaskRepository] Redis 已断开")
    
    # ===== 任务 CRUD =====
    
    async def create_task(
        self,
        tool_prompt: str,
        provider_name: str = "openclaw",
        context: Dict[str, Any] = None,
    ) -> str:
        """创建任务
        
        Args:
            tool_prompt: LLM 工具调用提示
            provider_name: Provider 名称
            context: 任务上下文
        
        Returns:
            任务 ID
        """
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            tool_prompt=tool_prompt,
            provider_name=provider_name,
            context=context or {},
            status=TaskStatus.PENDING,
        )
        
        # 存储任务详情
        await self._redis.hset(
            f"{self.KEY_TASK}:{task_id}",
            mapping=task.to_dict()
        )
        
        # 加入 Pending 队列
        await self._redis.rpush(self.KEY_PENDING, task_id)
        
        # 更新统计
        await self._update_stats("created", 1)
        
        logger.info(f"[TaskRepository] 任务已创建: {task_id[:8]}...")
        return task_id
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        data = await self._redis.hgetall(f"{self.KEY_TASK}:{task_id}")
        if not data:
            return None
        return Task.from_dict(data)
    
    async def update_task(self, task: Task) -> None:
        """更新任务"""
        await self._redis.hset(
            f"{self.KEY_TASK}:{task.id}",
            mapping=task.to_dict()
        )
    
    async def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        # 从 Pending 队列移除
        await self._redis.lrem(self.KEY_PENDING, 0, task_id)
        
        # 删除任务详情
        result = await self._redis.delete(f"{self.KEY_TASK}:{task_id}")
        
        return result > 0
    
    # ===== 状态更新 =====
    
    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: str = None,
    ) -> None:
        """更新任务状态"""
        task = await self.get_task(task_id)
        if not task:
            logger.warning(f"[TaskRepository] 任务不存在: {task_id[:8]}...")
            return
        
        # 更新状态
        task.status = status
        if error:
            task.error = error
        
        # 更新时间戳
        if status == TaskStatus.SUBMITTED:
            task.submitted_at = time.time()
        elif status == TaskStatus.RUNNING:
            task.started_at = time.time()
            # 从 Pending 队列移除
            await self._redis.lrem(self.KEY_PENDING, 0, task_id)
        elif status == TaskStatus.PROVIDER_DONE:
            task.provider_done_at = time.time()
        elif status == TaskStatus.COMPLETED:
            task.completed_at = time.time()
        
        # 保存
        await self.update_task(task)
        
        # 更新统计
        await self._update_stats(status.value, 1)
        
        logger.debug(f"[TaskRepository] 状态更新: {task_id[:8]}... → {status.value}")
    
    # ===== 结果存储 =====
    
    async def update_provider_result(
        self,
        task_id: str,
        result: Dict[str, Any],
    ) -> None:
        """存储 Provider 原始结果"""
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
        """存储二次改写后的播报内容"""
        task = await self.get_task(task_id)
        if not task:
            return
        
        task.broadcast_content = broadcast
        task.status = TaskStatus.COMPLETED
        task.completed_at = time.time()
        
        await self.update_task(task)
    
    # ===== 队列操作 =====
    
    async def pop_pending_task(self) -> Optional[Task]:
        """从 Pending 队列弹出一个任务"""
        task_id = await self._redis.lpop(self.KEY_PENDING)
        if not task_id:
            return None
        return await self.get_task(task_id)
    
    async def get_pending_count(self) -> int:
        """获取 Pending 队列长度"""
        return await self._redis.llen(self.KEY_PENDING)
    
    async def get_all_pending_tasks(self) -> List[Task]:
        """获取所有 Pending 任务"""
        task_ids = await self._redis.lrange(self.KEY_PENDING, 0, -1)
        tasks = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks
    
    # ===== 启动清理 =====
    
    async def clear_all_on_start(self) -> int:
        """启动时清空所有任务
        
        Returns:
            清空的任务数量
        """
        count = 0
        
        # 获取所有任务 ID
        pattern = f"{self.KEY_TASK}:*"
        keys = []
        cursor = 0
        while True:
            cursor, partial = await self._redis.scan(cursor, match=pattern, count=100)
            keys.extend(partial)
            if cursor == 0:
                break
        
        # 删除所有任务
        if keys:
            count = await self._redis.delete(*keys)
        
        # 清空 Pending 队列
        await self._redis.delete(self.KEY_PENDING)
        
        # 重置统计
        await self._redis.delete(self.KEY_STATS)
        
        logger.warning(f"[TaskRepository] 启动清理：清空 {count} 个任务")
        return count
    
    # ===== 统计 =====
    
    async def _update_stats(self, field: str, delta: int) -> None:
        """更新统计"""
        await self._redis.hincrby(self.KEY_STATS, field, delta)
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = await self._redis.hgetall(self.KEY_STATS) or {}
        stats["pending_count"] = await self.get_pending_count()
        stats["connected"] = self._connected
        return stats
    
    async def get_tasks_by_status(
        self,
        status: TaskStatus,
        limit: int = 100,
    ) -> List[Task]:
        """按状态查询任务
        
        Args:
            status: 任务状态
            limit: 最大返回数量
        
        Returns:
            任务列表
        
        Note:
            使用 scan 遍历所有任务，效率较低。
            生产环境建议使用索引或分片。
        """
        tasks = []
        pattern = f"{self.KEY_TASK}:*"
        cursor = 0
        
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            
            for key in keys:
                data = await self._redis.hgetall(key)
                if data:
                    task = Task.from_dict(data)
                    if task.status == status:
                        tasks.append(task)
                        if len(tasks) >= limit:
                            return tasks
            
            if cursor == 0:
                break
        
        return tasks


# ===== 全局实例（懒加载）=====
_repository: Optional[TaskRepository] = None


async def get_task_repository() -> TaskRepository:
    """获取任务存储实例"""
    global _repository
    if _repository is None:
        _repository = TaskRepository()
        await _repository.connect()
    return _repository


def get_task_repository_sync() -> TaskRepository:
    """获取任务存储实例（不自动连接）"""
    global _repository
    if _repository is None:
        _repository = TaskRepository()
    return _repository


def reset_task_repository():
    """重置全局实例（用于测试）"""
    global _repository
    if _repository is not None:
        try:
            # 尝试断开连接
            if _repository._redis:
                _repository._redis.aclose()
        except Exception:
            pass
    _repository = None
