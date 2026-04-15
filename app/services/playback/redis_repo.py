"""
播报队列 Redis 仓库

负责播报任务的持久化存储和队列操作。

Redis 数据结构：
- Key: playback:queue:pending (List) - 待播报队列（FIFO）
- Key: playback:task:{task_id} (Hash) - 任务详情
- TTL: 任务记录过期时间（默认 1 小时）
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any
from loguru import logger

import redis.asyncio as aioredis

from app.config import settings
from .models import PlaybackTask


class PlaybackQueueRepository:
    """播报队列 Redis 仓库
    
    管理播报任务的持久化存储和队列操作。
    
    Redis Key 设计：
    - playback:queue:pending (List) - 待播报队列（LPUSH/RPOP 实现 FIFO）
    - playback:task:{task_id} (Hash) - 任务详情（支持查询）
    """
    
    def __init__(self, redis_url: Optional[str] = None):
        """初始化仓库
        
        Args:
            redis_url: Redis 连接 URL，默认从配置读取
        """
        self._redis_url = redis_url or settings.REDIS_URL
        self._redis: Optional[aioredis.Redis] = None
        self._connected = False
        
        # Key 前缀和 TTL
        self._key_prefix = "playback"
        self._task_ttl = 3600  # 1 小时
    
    async def connect(self) -> None:
        """连接 Redis"""
        if self._connected:
            return
        
        try:
            self._redis = await aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # 测试连接
            await self._redis.ping()
            self._connected = True
            logger.info(f"[PlaybackQueueRepo] Redis 已连接: {self._redis_url}")
        except Exception as e:
            logger.error(f"[PlaybackQueueRepo] Redis 连接失败: {e}")
            raise
    
    async def disconnect(self) -> None:
        """断开 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("[PlaybackQueueRepo] Redis 已断开连接")
    
    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._redis is not None
    
    def _get_queue_key(self) -> str:
        """获取队列的 Redis key"""
        return f"{self._key_prefix}:queue:pending"
    
    def _get_task_key(self, task_id: str) -> str:
        """获取任务的 Redis key"""
        return f"{self._key_prefix}:task:{task_id}"
    
    # ==================== 入队/出队操作 ====================
    
    async def enqueue(self, task: PlaybackTask, priority: bool = False) -> None:
        """将播报任务入队
        
        Args:
            task: 播报任务对象
            priority: 是否优先入队（队首插队），默认 False（队尾顺序）
        
        Redis List 设计：
        - 队尾入队（默认）：RPUSH → 顺序播报
        - 队首入队（优先）：LPUSH → 插队播报
        - 出队：LPOP → 从队首取出（优先任务先执行）
        """
        if not self._connected:
            await self.connect()
        
        # 保存任务详情到 Hash
        task_key = self._get_task_key(task.id)
        task_data = task.to_dict()
        
        # 过滤 None 值
        task_data_filtered = {k: v for k, v in task_data.items() if v is not None}
        
        # 添加 priority 标记（用于调试）
        task_data_filtered["priority"] = str(priority)
        
        pipe = self._redis.pipeline()
        pipe.hset(task_key, mapping=task_data_filtered)
        pipe.expire(task_key, self._task_ttl)
        
        # 入队：队尾或队首
        queue_key = self._get_queue_key()
        if priority:
            # LPUSH：队首插入（优先播报）
            pipe.lpush(queue_key, task.id)
        else:
            # RPUSH：队尾插入（顺序播报）
            pipe.rpush(queue_key, task.id)
        
        await pipe.execute()
        
        queue_len = await self._redis.llen(queue_key)
        position = "队首（优先）" if priority else "队尾（顺序）"
        logger.info(
            f"[PlaybackQueueRepo] 任务入队 {position}: {task.to_summary()}, "
            f"queue_size={queue_len}"
        )
    
    async def pop(self) -> Optional[PlaybackTask]:
        """从队列取出一个任务（从队首取出）
        
        Returns:
            PlaybackTask 对象，队列空返回 None
        
        设计：
        - LPOP：从队首取出（优先任务先执行）
        - 配合 RPUSH（队尾入队）实现 FIFO
        - 配合 LPUSH（队首入队）实现优先插队
        """
        if not self._connected:
            await self.connect()
        
        queue_key = self._get_queue_key()
        
        # LPOP：从队首取出（优先任务先执行）
        task_id = await self._redis.lpop(queue_key)
        if not task_id:
            return None
        
        # 获取任务详情
        task = await self.get_task(task_id)
        if task:
            logger.info(f"[PlaybackQueueRepo] 任务出队: {task.to_summary()}")
        else:
            logger.warning(f"[PlaybackQueueRepo] 队列中的任务不存在: {task_id}")
        
        return task
    
    async def get_task(self, task_id: str) -> Optional[PlaybackTask]:
        """获取任务详情
        
        Args:
            task_id: 任务 ID
        
        Returns:
            PlaybackTask 对象，不存在返回 None
        """
        if not self._connected:
            await self.connect()
        
        task_key = self._get_task_key(task_id)
        task_data = await self._redis.hgetall(task_key)
        
        if not task_data:
            return None
        
        try:
            return PlaybackTask.from_dict(task_data)
        except Exception as e:
            logger.error(f"[PlaybackQueueRepo] 反序列化任务失败: {task_id}, {e}")
            return None
    
    # ==================== 队列状态查询 ====================
    
    async def get_queue_length(self) -> int:
        """获取队列长度
        
        Returns:
            队列中待播报任务数
        """
        if not self._connected:
            await self.connect()
        
        queue_key = self._get_queue_key()
        return await self._redis.llen(queue_key)
    
    async def peek_all(self) -> list[str]:
        """查看队列中所有任务 ID（不出队）
        
        Returns:
            任务 ID 列表（按入队顺序）
        """
        if not self._connected:
            await self.connect()
        
        queue_key = self._get_queue_key()
        # LRANGE 0 -1 获取所有元素
        task_ids = await self._redis.lrange(queue_key, 0, -1)
        return task_ids
    
    async def get_all_tasks(self) -> list[PlaybackTask]:
        """获取队列中所有任务详情
        
        Returns:
            PlaybackTask 列表（按入队顺序）
        """
        task_ids = await self.peek_all()
        tasks = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks
    
    # ==================== 清空操作（重启时调用） ====================
    
    async def clear_all(self) -> int:
        """清空所有播报任务（重启时调用）
        
        Returns:
            清空的任务数量
        """
        if not self._connected:
            await self.connect()
        
        logger.warning("[PlaybackQueueRepo] 开始清空所有播报任务...")
        
        # 1. 删除 pending 队列
        queue_key = self._get_queue_key()
        queue_count = await self._redis.delete(queue_key)
        
        # 2. 删除所有任务详情
        pattern = f"{self._key_prefix}:task:*"
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
    
    # ==================== 监控和统计 ====================
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息
        
        Returns:
            统计数据字典
        """
        if not self._connected:
            await self.connect()
        
        queue_len = await self.get_queue_length()
        
        return {
            "connected": self._connected,
            "queue_length": queue_len,
            "queue_key": self._get_queue_key(),
            "task_ttl": self._task_ttl,
        }


# ==================== 全局实例（懒加载） ====================

_repository: Optional[PlaybackQueueRepository] = None


async def get_playback_repository() -> PlaybackQueueRepository:
    """获取播报队列仓库实例（单例）"""
    global _repository
    if _repository is None:
        _repository = PlaybackQueueRepository()
        await _repository.connect()
    return _repository


def set_playback_repository(repo: PlaybackQueueRepository) -> None:
    """手动设置仓库实例（用于测试）"""
    global _repository
    _repository = repo