"""
OpenClaw Redis 任务仓库

负责：
- 任务CRUD操作（创建、查询、更新）
- 任务队列管理（pending队列）
- 任务状态持久化
- Redis连接管理
"""

import asyncio
import json
import time
import uuid
from typing import Optional, List, Dict, Any
from loguru import logger

import redis.asyncio as aioredis

from .config import get_openclaw_config
from .models import Task, TaskStatus


class OpenClawTaskRepository:
    """OpenClaw任务Redis仓库

    管理任务的持久化存储和队列操作。

    Redis数据结构：
    - Key: openclaw:task:{task_id} (Hash) - 任务详情
    - Key: openclaw:queue:pending (List) - 待分配任务队列
    - TTL: 任务记录过期时间（默认1小时）
    """

    def __init__(self):
        self._config = get_openclaw_config()
        self._redis: Optional[aioredis.Redis] = None
        self._connected = False

    async def connect(self) -> None:
        """连接Redis"""
        if self._connected:
            return

        try:
            self._redis = await aioredis.from_url(
                self._config.redis.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # 测试连接
            await self._redis.ping()
            self._connected = True
            logger.info(f"[RedisRepo] 已连接: {self._config.redis.redis_url}")
        except Exception as e:
            logger.error(f"[RedisRepo] 连接失败: {e}")
            raise

    async def disconnect(self) -> None:
        """断开Redis连接"""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("[RedisRepo] 已断开连接")

    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._redis is not None

    def _get_task_key(self, task_id: str) -> str:
        """获取任务的Redis key"""
        return f"{self._config.redis.key_prefix}:task:{task_id}"

    def _get_pending_queue_key(self) -> str:
        """获取pending队列的Redis key"""
        return f"{self._config.redis.key_prefix}:queue:pending"

    # ==================== 任务CRUD操作 ====================

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
        """创建新任务

        Args:
            tool_prompt: LLM的工具调用提示
            task_id: 可选的任务ID（不提供则自动生成）
            user_input: 用户输入（用于二次处理）
            memory_context: 记忆上下文（用于二次处理）
            conversation_history: 对话历史（用于二次处理）
            inner_monologue: 第一阶段内心独白（用于二次处理）
            emotion_delta: 情绪变化（用于二次处理）

        Returns:
            任务ID
        """
        if not self._connected:
            await self.connect()

        if task_id is None:
            task_id = uuid.uuid4().hex

        task = Task(
            id=task_id,
            tool_prompt=tool_prompt,
            status=TaskStatus.PENDING,
            created_at=time.time(),
            user_input=user_input,
            memory_context=memory_context,
            conversation_history=conversation_history,
            inner_monologue=inner_monologue,
            emotion_delta=emotion_delta,
        )

        # 保存任务到Redis
        task_key = self._get_task_key(task_id)
        task_data = task.to_dict()

        # 过滤None值（Redis hset不接受None）
        task_data_filtered = {k: v for k, v in task_data.items() if v is not None}

        pipe = self._redis.pipeline()
        pipe.hset(task_key, mapping=task_data_filtered)
        pipe.expire(task_key, self._config.redis.task_ttl)
        await pipe.execute()

        # 添加到pending队列
        queue_key = self._get_pending_queue_key()
        await self._redis.lpush(queue_key, task_id)

        logger.debug(f"[RedisRepo] 任务已创建: {task_id}, status=PENDING")
        return task_id

    async def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务

        Args:
            task_id: 任务ID

        Returns:
            Task对象，不存在返回None
        """
        if not self._connected:
            await self.connect()

        task_key = self._get_task_key(task_id)
        task_data = await self._redis.hgetall(task_key)

        if not task_data:
            return None

        try:
            return Task.from_dict(task_data)
        except Exception as e:
            logger.error(f"[RedisRepo] 反序列化任务失败: {task_id}, {e}")
            return None

    async def get_task_by_run_id(self, run_id: str) -> Optional[Task]:
        """根据runId查询任务

        Args:
            run_id: OpenClaw的runId

        Returns:
            Task对象，不存在返回None
        """
        if not self._connected:
            await self.connect()

        # 扫描所有task key
        pattern = f"{self._config.redis.key_prefix}:task:*"
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_data = await self._redis.hgetall(key)
            if not task_data:
                continue

            try:
                task = Task.from_dict(task_data)
                if task.run_id == run_id:
                    logger.debug(f"[RedisRepo] 通过runId找到任务: {run_id[:8]}... -> {task.id[:8]}...")
                    return task
            except Exception as e:
                logger.warning(f"[RedisRepo] 解析任务失败: {key}, {e}")
                continue

        logger.debug(f"[RedisRepo] 未找到runId对应的任务: {run_id[:8]}...")
        return None

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        **kwargs,
    ) -> bool:
        """更新任务状态

        Args:
            task_id: 任务ID
            status: 新状态
            **kwargs: 其他要更新的字段（如session_key, run_id, result等）

        Returns:
            是否更新成功
        """
        if not self._connected:
            await self.connect()

        task_key = self._get_task_key(task_id)

        # 构建更新数据
        updates = {
            "status": status.value,
        }

        # 添加时间戳（保持float类型）
        if status == TaskStatus.ASSIGNED:
            updates["assigned_at"] = time.time()
        elif status == TaskStatus.RUNNING:
            updates["started_at"] = time.time()
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
            updates["completed_at"] = time.time()

        # 添加额外字段
        for key, value in kwargs.items():
            if value is not None:
                # 复杂类型转JSON字符串
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                updates[key] = value

        # 更新
        if updates:
            await self._redis.hset(task_key, mapping=updates)
            logger.debug(f"[RedisRepo] 任务状态更新: {task_id} -> {status.value}")

        return True

    async def update_result(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> bool:
        """更新OpenClaw任务结果（第一阶段）

        Args:
            task_id: 任务ID
            result: OpenClaw返回结果
            error: 错误信息

        Returns:
            是否更新成功

        Note:
            此方法将任务状态更新为 OPENCLAW_DONE，等待二次处理
        """
        updates = {}
        if result is not None:
            updates["result"] = json.dumps(result, ensure_ascii=False)
        if error is not None:
            updates["error"] = error

        # 更新为 OPENCLAW_DONE 状态，并记录完成时间
        updates["openclaw_done_at"] = time.time()

        return await self.update_status(
            task_id,
            TaskStatus.OPENCLAW_DONE if not error else TaskStatus.FAILED,
            **updates,
        )

    async def update_final_result(
        self,
        task_id: str,
        final_result: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> bool:
        """更新LLM二次处理后的最终结果（第二阶段）

        Args:
            task_id: 任务ID
            final_result: LLM二次处理后的最终结果
            error: 错误信息

        Returns:
            是否更新成功

        Note:
            此方法将任务状态更新为 COMPLETED，整个任务流程结束
        """
        updates = {}
        if final_result is not None:
            updates["final_result"] = json.dumps(final_result, ensure_ascii=False)
        if error is not None:
            updates["error"] = error

        # 更新为 COMPLETED 状态，并记录最终完成时间
        updates["completed_at"] = time.time()

        return await self.update_status(
            task_id,
            TaskStatus.COMPLETED if not error else TaskStatus.FAILED,
            **updates,
        )

    async def delete_task(self, task_id: str) -> bool:
        """删除任务

        Args:
            task_id: 任务ID

        Returns:
            是否删除成功
        """
        if not self._connected:
            await self.connect()

        task_key = self._get_task_key(task_id)
        deleted = await self._redis.delete(task_key)

        if deleted > 0:
            logger.debug(f"[RedisRepo] 任务已删除: {task_id}")
        return deleted > 0

    # ==================== 队列操作 ====================

    async def pop_pending_task(self) -> Optional[Task]:
        """从pending队列取出一个任务（原子操作，RPOP）

        Returns:
            Task对象，队列空返回None
        """
        if not self._connected:
            await self.connect()

        queue_key = self._get_pending_queue_key()

        # RPOP：从队列右侧取出（FIFO）
        task_id = await self._redis.rpop(queue_key)
        if not task_id:
            return None

        # 获取任务详情
        task = await self.get_task(task_id)
        if task:
            logger.debug(f"[RedisRepo] 从队列取出任务: {task_id}")
        else:
            logger.warning(f"[RedisRepo] 队列中的任务不存在: {task_id}")

        return task

    async def push_pending_task(self, task_id: str) -> None:
        """将任务推回pending队列（LPUSH）

        Args:
            task_id: 任务ID
        """
        if not self._connected:
            await self.connect()

        queue_key = self._get_pending_queue_key()
        await self._redis.lpush(queue_key, task_id)
        logger.debug(f"[RedisRepo] 任务推回队列: {task_id}")

    async def get_pending_queue_length(self) -> int:
        """获取pending队列长度

        Returns:
            队列中待处理任务数
        """
        if not self._connected:
            await self.connect()

        queue_key = self._get_pending_queue_key()
        return await self._redis.llen(queue_key)

    async def get_all_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> List[Task]:
        """获取所有任务（可选状态过滤）

        Args:
            status: 任务状态过滤（None表示全部）
            limit: 最大返回数量

        Returns:
            任务列表
        """
        if not self._connected:
            await self.connect()

        # 扫描所有task key
        pattern = f"{self._config.redis.key_prefix}:task:*"
        task_keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_keys.append(key)

        # 获取任务详情
        tasks = []
        for key in task_keys[:limit]:
            task_data = await self._redis.hgetall(key)
            if not task_data:
                continue

            try:
                task = Task.from_dict(task_data)
                # 状态过滤
                if status is None or task.status == status:
                    tasks.append(task)
            except Exception as e:
                logger.warning(f"[RedisRepo] 解析任务失败: {key}, {e}")
                continue

        # 按创建时间排序（新到旧）
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    async def get_task_count_by_status(self, status: TaskStatus) -> int:
        """获取指定状态的任务数量

        Args:
            status: 任务状态

        Returns:
            任务数量
        """
        tasks = await self.get_all_tasks(status=status, limit=10000)
        return len(tasks)

    # ==================== 批量操作 ====================

    async def clear_all_tasks(self) -> int:
        """清空所有任务（危险操作，仅用于测试）

        Returns:
            删除的任务数量
        """
        if not self._connected:
            await self.connect()

        # 扫描所有task key
        pattern = f"{self._config.redis.key_prefix}:task:*"
        task_keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_keys.append(key)

        # 删除
        if task_keys:
            deleted = await self._redis.delete(*task_keys)
            logger.warning(f"[RedisRepo] 已清空所有任务: {deleted}个")
            return deleted

        return 0

    async def clear_pending_queue(self) -> int:
        """清空pending队列

        Returns:
            清空的任务数量
        """
        if not self._connected:
            await self.connect()

        queue_key = self._get_pending_queue_key()
        count = await self._redis.delete(queue_key)
        logger.warning(f"[RedisRepo] 已清空pending队列: {count}个任务")
        return count

    # ==================== 监控和统计 ====================

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息

        Returns:
            统计数据字典
        """
        if not self._connected:
            await self.connect()

        stats = {
            "connected": self._connected,
            "pending_queue_length": await self.get_pending_queue_length(),
            "total_tasks": 0,
            "by_status": {},
        }

        # 统计各状态任务数
        for status in TaskStatus:
            count = await self.get_task_count_by_status(status)
            stats["by_status"][status.value] = count
            stats["total_tasks"] += count

        return stats

    async def has_previous_state(self) -> bool:
        """检查是否有遗留的状态（用于检测项目重启）

        Returns:
            True表示有未完成的任务需要清理
        """
        if not self._connected:
            await self.connect()

        stats = await self.get_stats()
        unfinished = (
            stats["by_status"].get("pending", 0) +
            stats["by_status"].get("assigned", 0) +
            stats["by_status"].get("running", 0)
        )

        return unfinished > 0

    async def clear_all_on_restart(self) -> int:
        """项目重启时清空所有任务和队列

        Returns:
            清空的任务数量
        """
        if not self._connected:
            await self.connect()

        logger.warning("[RedisRepo] 项目重启，开始清空所有任务")

        # 清空队列
        queue_count = await self.clear_pending_queue()

        # 删除所有任务
        task_count = await self.clear_all_tasks()

        total_count = queue_count + task_count

        logger.warning(
            f"[RedisRepo] 清空完成: 队列{queue_count}个, 任务{task_count}个, 共{total_count}个"
        )

        return total_count


# ==================== 全局实例（懒加载） ====================

_repository: Optional[OpenClawTaskRepository] = None


async def get_task_repository() -> OpenClawTaskRepository:
    """获取任务仓库实例（单例）"""
    global _repository
    if _repository is None:
        _repository = OpenClawTaskRepository()
        await _repository.connect()
    return _repository


def set_task_repository(repo: OpenClawTaskRepository) -> None:
    """手动设置任务仓库实例（用于测试）"""
    global _repository
    _repository = repo
