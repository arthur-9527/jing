"""OpenClaw Task Repository - OpenClaw 任务数据访问

提供 OpenClaw 任务系统的 Redis 操作：
- create_task: 创建任务
- update_status: 更新任务状态
- update_result: 更新任务结果
- pop_pending_task: 弹出待处理任务
- get_stats: 获取统计信息

基于 services/openclaw/redis_repo.py 迁移
"""

from typing import Optional, Dict, Any, List
import uuid
import time
import json

from loguru import logger

from app.stone.redis_pool import RedisPool, get_redis_pool
from app.stone.key_builder import RedisKeyBuilder
from app.stone.repositories.base import RedisRepository


class OpenClawTaskRepository(RedisRepository):
    """OpenClaw 任务 Repository (Redis)

    Redis Key 设计：
    - openclaw:{task_id}     # 任务详情 (Hash)
    - queue:openclaw         # Pending 任务队列 (List)
    """

    # 任务 TTL（默认 1 小时）
    TASK_TTL = 3600

    def __init__(self, redis: RedisPool = None, key_builder: RedisKeyBuilder = None):
        """初始化

        Args:
            redis: RedisPool 实例
            key_builder: RedisKeyBuilder 实例
        """
        super().__init__(redis or get_redis_pool(), "agent")
        self._key_builder = key_builder or RedisKeyBuilder()

    # ============================================================
    # Key 构建方法
    # ============================================================

    def _task_key(self, task_id: str) -> str:
        """任务详情 Key"""
        return self._key_builder.openclaw_task(task_id)

    def _queue_key(self) -> str:
        """Pending 队列 Key"""
        return self._key_builder.openclaw_queue()

    # ============================================================
    # 任务 CRUD
    # ============================================================

    async def create_task(
        self,
        task_id: str = None,
        tool_prompt: str = "",
        user_input: Optional[str] = None,
        memory_context: Optional[str] = None,
        conversation_history: Optional[str] = None,
        inner_monologue: Optional[str] = None,
        emotion_delta: Optional[Dict[str, float]] = None,
        status: str = "pending",
    ) -> str:
        """创建任务

        Args:
            task_id: 任务 ID（可选，自动生成）
            tool_prompt: LLM 工具调用提示
            user_input: 用户输入
            memory_context: 记忆上下文
            conversation_history: 对话历史
            inner_monologue: 内心独白
            emotion_delta: 情绪变化
            status: 初始状态

        Returns:
            任务 ID
        """
        if task_id is None:
            task_id = uuid.uuid4().hex

        task_data = {
            "id": task_id,
            "tool_prompt": tool_prompt,
            "status": status,
            "created_at": str(time.time()),
        }

        # 添加可选字段
        if user_input:
            task_data["user_input"] = user_input
        if memory_context:
            task_data["memory_context"] = memory_context
        if conversation_history:
            task_data["conversation_history"] = conversation_history
        if inner_monologue:
            task_data["inner_monologue"] = inner_monologue
        if emotion_delta:
            task_data["emotion_delta"] = json.dumps(emotion_delta, ensure_ascii=False)

        # 保存任务详情
        task_key = self._task_key(task_id)
        await self.hset(task_key, mapping=task_data)
        await self.expire(task_key, self.TASK_TTL)

        # 添加到 Pending 队列
        queue_key = self._queue_key()
        await self.lpush(queue_key, task_id)

        logger.debug(f"[OpenClawRepo] 任务已创建: {task_id}, status={status}")
        return task_id

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务详情

        Args:
            task_id: 任务 ID

        Returns:
            任务字典或 None
        """
        task_key = self._task_key(task_id)
        return await self.hgetall(task_key)

    async def get_task_by_run_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """根据 runId 查询任务

        Args:
            run_id: OpenClaw 的 runId

        Returns:
            任务字典或 None
        """
        pattern = self._key_builder.build("openclaw_task", task_id="*")
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_data = await self.hgetall(key)
            if not task_data:
                continue

            if task_data.get("run_id") == run_id:
                logger.debug(f"[OpenClawRepo] 通过 runId 找到任务: {run_id[:8]}...")
                return task_data

        logger.debug(f"[OpenClawRepo] 未找到 runId 对应的任务: {run_id[:8]}...")
        return None

    async def update_status(
        self,
        task_id: str,
        status: str,
        **kwargs,
    ) -> bool:
        """更新任务状态

        Args:
            task_id: 任务 ID
            status: 新状态
            **kwargs: 其他要更新的字段

        Returns:
            是否更新成功
        """
        task_key = self._task_key(task_id)

        # 构建更新数据
        updates = {"status": status}

        # 添加时间戳
        if status == "assigned":
            updates["assigned_at"] = str(time.time())
        elif status == "running":
            updates["started_at"] = str(time.time())
        elif status in ("completed", "failed", "timeout", "cancelled"):
            updates["completed_at"] = str(time.time())

        # 添加额外字段
        for key, value in kwargs.items():
            if value is not None:
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                updates[key] = value

        # 更新
        if updates:
            await self.hset(task_key, mapping=updates)
            logger.debug(f"[OpenClawRepo] 任务状态更新: {task_id} -> {status}")

        return True

    async def update_result(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> bool:
        """更新 OpenClaw 任务结果（第一阶段）

        Args:
            task_id: 任务 ID
            result: OpenClaw 返回结果
            error: 错误信息

        Returns:
            是否更新成功
        """
        updates = {}
        if result is not None:
            updates["result"] = json.dumps(result, ensure_ascii=False)
        if error is not None:
            updates["error"] = error

        updates["openclaw_done_at"] = str(time.time())

        return await self.update_status(
            task_id,
            "openclaw_done" if not error else "failed",
            **updates,
        )

    async def update_final_result(
        self,
        task_id: str,
        final_result: Optional[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> bool:
        """更新 LLM 二次处理后的最终结果（第二阶段）

        Args:
            task_id: 任务 ID
            final_result: 最终结果
            error: 错误信息

        Returns:
            是否更新成功
        """
        updates = {}
        if final_result is not None:
            updates["final_result"] = json.dumps(final_result, ensure_ascii=False)
        if error is not None:
            updates["error"] = error

        updates["completed_at"] = str(time.time())

        return await self.update_status(
            task_id,
            "completed" if not error else "failed",
            **updates,
        )

    async def delete_task(self, task_id: str) -> bool:
        """删除任务

        Args:
            task_id: 任务 ID

        Returns:
            是否删除成功
        """
        task_key = self._task_key(task_id)
        deleted = await self.delete(task_key)

        if deleted > 0:
            logger.debug(f"[OpenClawRepo] 任务已删除: {task_id}")
        return deleted > 0

    # ============================================================
    # 队列操作
    # ============================================================

    async def pop_pending_task(self) -> Optional[Dict[str, Any]]:
        """从 Pending 队列取出一个任务（RPOP，FIFO）

        Returns:
            任务字典或 None
        """
        queue_key = self._queue_key()
        task_id = await self.rpop(queue_key)
        if not task_id:
            return None

        task = await self.get_task(task_id)
        if task:
            logger.debug(f"[OpenClawRepo] 从队列取出任务: {task_id}")
        else:
            logger.warning(f"[OpenClawRepo] 队列中的任务不存在: {task_id}")

        return task

    async def push_pending_task(self, task_id: str) -> None:
        """将任务推回 Pending 队列（LPUSH）

        Args:
            task_id: 任务 ID
        """
        queue_key = self._queue_key()
        await self.lpush(queue_key, task_id)
        logger.debug(f"[OpenClawRepo] 任务推回队列: {task_id}")

    async def get_pending_queue_length(self) -> int:
        """获取 Pending 队列长度"""
        queue_key = self._queue_key()
        return await self.llen(queue_key)

    async def get_all_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """获取所有任务（可选状态过滤）

        Args:
            status: 状态过滤
            limit: 最大返回数量

        Returns:
            任务列表
        """
        pattern = self._key_builder.build("openclaw_task", task_id="*")
        task_keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_keys.append(key)

        tasks = []
        for key in task_keys[:limit]:
            task_data = await self.hgetall(key)
            if not task_data:
                continue

            # 状态过滤
            if status is None or task_data.get("status") == status:
                tasks.append(task_data)

        # 按创建时间排序
        tasks.sort(key=lambda t: float(t.get("created_at", 0)), reverse=True)
        return tasks

    async def get_task_count_by_status(self, status: str) -> int:
        """获取指定状态的任务数量"""
        tasks = await self.get_all_tasks(status=status, limit=10000)
        return len(tasks)

    # ============================================================
    # 批量操作
    # ============================================================

    async def clear_all_tasks(self) -> int:
        """清空所有任务

        Returns:
            删除的任务数量
        """
        pattern = self._key_builder.build("openclaw_task", task_id="*")
        task_keys = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            task_keys.append(key)

        if task_keys:
            deleted = await self._redis.delete(*task_keys)
            logger.warning(f"[OpenClawRepo] 已清空所有任务: {deleted}个")
            return deleted

        return 0

    async def clear_pending_queue(self) -> int:
        """清空 Pending 队列"""
        queue_key = self._queue_key()
        count = await self.delete(queue_key)
        logger.warning(f"[OpenClawRepo] 已清空 pending 队列")
        return count

    # ============================================================
    # 监控和统计
    # ============================================================

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "pending_queue_length": await self.get_pending_queue_length(),
            "total_tasks": 0,
            "by_status": {},
        }

        # 统计各状态任务数
        status_list = ["pending", "assigned", "running", "openclaw_done", "completed", "failed", "timeout", "cancelled"]
        for status in status_list:
            count = await self.get_task_count_by_status(status)
            stats["by_status"][status] = count
            stats["total_tasks"] += count

        return stats

    async def has_previous_state(self) -> bool:
        """检查是否有遗留的状态（用于检测项目重启）"""
        stats = await self.get_stats()
        unfinished = (
            stats["by_status"].get("pending", 0) +
            stats["by_status"].get("assigned", 0) +
            stats["by_status"].get("running", 0)
        )
        return unfinished > 0

    async def clear_all_on_restart(self) -> int:
        """项目重启时清空所有任务和队列"""
        logger.warning("[OpenClawRepo] 项目重启，开始清空所有任务")

        queue_count = await self.clear_pending_queue()
        task_count = await self.clear_all_tasks()

        total_count = queue_count + task_count
        logger.warning(
            f"[OpenClawRepo] 清空完成: 队列{queue_count}个, 任务{task_count}个"
        )

        return total_count


# ============================================================
# 全局实例（懒加载）
# ============================================================

_openclaw_task_repo: Optional[OpenClawTaskRepository] = None


def get_openclaw_task_repo() -> OpenClawTaskRepository:
    """获取 OpenClawTaskRepository 实例"""
    global _openclaw_task_repo
    if _openclaw_task_repo is None:
        _openclaw_task_repo = OpenClawTaskRepository()
    return _openclaw_task_repo