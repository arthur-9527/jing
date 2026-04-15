"""记忆系统定时任务调度器

功能：
1. 每小时从 Redis 持久化队列批量写入 PostgreSQL + 事件提取
2. 可扩展：日记生成、周索引、月索引等任务
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from loguru import logger

from app.config import settings
from app.services.chat_history.conversation_buffer import get_conversation_buffer
from app.agent.db.memory_models import (
    batch_insert_chat_messages,
    batch_insert_key_events,
    batch_insert_heartbeat_events,
    mark_messages_extracted,
    cleanup_old_chat_messages,
)
from app.agent.memory.extractor import get_memory_extractor
from app.agent.memory.generator import get_memory_generator


class MemoryScheduler:
    """记忆系统定时任务调度器"""

    def __init__(self):
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("[MemoryScheduler] 已在运行中，跳过启动")
            return

        logger.info("[MemoryScheduler] 启动定时任务调度器...")

        # 创建调度器
        self._scheduler = AsyncIOScheduler()

        # 注册定时任务
        self._register_jobs()

        # 启动调度器
        self._scheduler.start()
        self._running = True

        logger.info("[MemoryScheduler] 定时任务调度器已启动")

    async def stop(self) -> None:
        """停止调度器"""
        if not self._running or not self._scheduler:
            return

        logger.info("[MemoryScheduler] 停止定时任务调度器...")
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("[MemoryScheduler] 定时任务调度器已停止")

    def _register_jobs(self) -> None:
        """注册定时任务"""
        # 任务1: 每小时从 Redis 持久化队列写入 PostgreSQL
        self._scheduler.add_job(
            self._job_flush_persistent_queue,
            trigger=CronTrigger(minute=0),  # 每小时整点执行
            id="flush_persistent_queue",
            name="持久化队列写入数据库",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: flush_persistent_queue (每小时整点)")

        # 任务2: 每天凌晨2点生成日记
        self._scheduler.add_job(
            self._job_generate_daily_diary,
            trigger=CronTrigger(hour=2, minute=0),
            id="generate_daily_diary",
            name="生成日记",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_daily_diary (每天02:00)")

        # 任务3: 每周日凌晨3点生成周索引
        self._scheduler.add_job(
            self._job_generate_weekly_index,
            trigger=CronTrigger(day_of_week=0, hour=3, minute=0),  # 周日3点
            id="generate_weekly_index",
            name="生成周索引",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_weekly_index (每周日03:00)")

        # 任务4: 每月最后一天凌晨4点生成月索引
        self._scheduler.add_job(
            self._job_generate_monthly_index,
            trigger=CronTrigger(day=28, hour=4, minute=0),  # 28号4点（避免31号问题）
            id="generate_monthly_index",
            name="生成月索引",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_monthly_index (每月28日04:00)")

        # 任务5: 每年12月31日凌晨5点生成年索引
        self._scheduler.add_job(
            self._job_generate_annual_index,
            trigger=CronTrigger(month=12, day=31, hour=5, minute=0),
            id="generate_annual_index",
            name="生成年索引",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_annual_index (每年12月31日05:00)")

    async def _job_flush_persistent_queue(self) -> None:
        """定时任务：从 Redis 持久化队列写入 PostgreSQL + 事件提取
        
        流程：
        1. 从 Redis 持久化队列获取所有消息
        2. 批量写入 PostgreSQL chat_messages 表
        3. LLM 提取关键事件 + 心动事件
        4. 写入 key_events / heartbeat_events 表
        5. 标记消息已提取
        6. 清空持久化队列
        """
        logger.info("[MemoryScheduler] 开始执行持久化队列写入任务...")

        try:
            # 获取所有用户的持久化队列
            # 目前只有一个默认用户，后续可扩展为多用户
            character_id = getattr(settings, 'CHARACTER_ID', 'daji')
            user_id = "default_user"
            
            buffer = await get_conversation_buffer(
                user_id=user_id,
                character_id=character_id,
            )

            # 获取持久化队列中的消息
            messages = await buffer.get_all_persistent_messages()

            if not messages:
                logger.info("[MemoryScheduler] 持久化队列为空，跳过写入")
                return

            logger.info(f"[MemoryScheduler] 持久化队列有 {len(messages)} 条消息待写入")

            # 转换消息格式为数据库格式
            db_messages = []
            for msg in messages:
                db_msg = {
                    "character_id": msg.get("character_id", character_id),
                    "user_id": msg.get("user_id", user_id),
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "inner_monologue": msg.get("inner_monologue"),
                    "turn_id": msg.get("turn_id"),
                    "metadata": {
                        "ts": msg.get("ts"),
                        "item_id": msg.get("item_id"),
                    },
                }
                db_messages.append(db_msg)

            # Step 1-2: 批量写入数据库
            record_ids = await batch_insert_chat_messages(db_messages)
            logger.info(f"[MemoryScheduler] 成功写入 {len(record_ids)} 条消息到 chat_messages")

            # Step 3-5: 事件提取（如果有消息写入成功）
            if record_ids:
                await self._extract_events_from_messages(
                    messages=messages,
                    record_ids=record_ids,
                    character_id=character_id,
                    user_id=user_id,
                )

            # Step 6: 清空持久化队列
            await buffer.clear_persistent_queue()

            logger.info("[MemoryScheduler] 持久化队列写入任务完成")

        except Exception as e:
            logger.error(f"[MemoryScheduler] 持久化队列写入任务失败: {e}")

    async def _extract_events_from_messages(
        self,
        messages: list[dict],
        record_ids: list[int],
        character_id: str,
        user_id: str,
    ) -> None:
        """从消息中提取事件
        
        Args:
            messages: 原始消息列表
            record_ids: 写入数据库后的消息ID列表
            character_id: 角色ID
            user_id: 用户ID
        """
        logger.info("[MemoryScheduler] 开始事件提取...")
        
        try:
            # 获取提取器
            extractor = get_memory_extractor()
            
            # 并行提取关键事件和心动事件
            key_events, heartbeat_events = await extractor.extract_all(
                messages=messages,
                character_id=character_id,
                user_id=user_id,
                source_message_ids=record_ids,
            )
            
            # 写入关键事件
            if key_events:
                key_event_ids = await batch_insert_key_events(key_events)
                logger.info(f"[MemoryScheduler] 写入 {len(key_event_ids)} 条关键事件")
            
            # 写入心动事件
            if heartbeat_events:
                heartbeat_ids = await batch_insert_heartbeat_events(heartbeat_events)
                logger.info(f"[MemoryScheduler] 写入 {len(heartbeat_ids)} 条心动事件")
            
            # 标记消息已提取
            marked_count = await mark_messages_extracted(record_ids)
            logger.info(f"[MemoryScheduler] 标记 {marked_count} 条消息已提取")
            
        except Exception as e:
            logger.error(f"[MemoryScheduler] 事件提取失败: {e}")

    async def _job_generate_daily_diary(self) -> None:
        """定时任务：每天凌晨2点生成日记"""
        logger.info("[MemoryScheduler] 开始执行日记生成任务...")

        try:
            character_id = getattr(settings, 'CHARACTER_ID', 'daji')
            user_id = "default_user"

            generator = get_memory_generator()
            
            # 生成昨天的日记
            from datetime import date, timedelta
            yesterday = date.today() - timedelta(days=1)
            
            diary_id = await generator.generate_daily_diary(
                character_id=character_id,
                user_id=user_id,
                diary_date=yesterday,
            )
            
            if diary_id:
                logger.info(f"[MemoryScheduler] 日记生成成功: id={diary_id}, date={yesterday}")
            else:
                logger.info(f"[MemoryScheduler] 日记生成跳过: date={yesterday} (无数据)")

        except Exception as e:
            logger.error(f"[MemoryScheduler] 日记生成任务失败: {e}")

    async def _job_generate_weekly_index(self) -> None:
        """定时任务：每周日凌晨3点生成周索引 + 清理旧聊天记录
        
        流程：
        1. 生成上周的周索引
        2. 清理上上周的聊天记录（保留14天）
        
        清理逻辑：
        - 比如：生成第2周(8-14)的周索引后，删除第-1周(14天前)的聊天记录
        - 保证聊天记录最多保留14天
        """
        logger.info("[MemoryScheduler] 开始执行周索引生成任务...")

        try:
            character_id = getattr(settings, 'CHARACTER_ID', 'daji')
            user_id = "default_user"

            generator = get_memory_generator()
            
            # 生成上周的周索引
            from datetime import date, timedelta
            today = date.today()
            # 上周的周一到周日
            week_start = today - timedelta(days=today.weekday() + 7)  # 上周一
            week_end = week_start + timedelta(days=6)  # 上周日
            
            weekly_id = await generator.generate_weekly_index(
                character_id=character_id,
                user_id=user_id,
                week_start=week_start,
                week_end=week_end,
            )
            
            if weekly_id:
                logger.info(f"[MemoryScheduler] 周索引生成成功: id={weekly_id}, week={week_start}~{week_end}")
            else:
                logger.info(f"[MemoryScheduler] 周索引生成跳过: week={week_start}~{week_end} (无日记)")

            # Step 2: 清理上上周的聊天记录（14天前的）
            # 删除14天前未提取的聊天记录（已提取的保留在事件/日记中）
            deleted_count = await cleanup_old_chat_messages(
                character_id=character_id,
                user_id=user_id,
                days=14,
            )
            
            if deleted_count > 0:
                logger.info(f"[MemoryScheduler] 清理完成: 删除 {deleted_count} 条14天前的聊天记录")

        except Exception as e:
            logger.error(f"[MemoryScheduler] 周索引生成任务失败: {e}")

    async def _job_generate_monthly_index(self) -> None:
        """定时任务：每月28号凌晨4点生成月索引"""
        logger.info("[MemoryScheduler] 开始执行月索引生成任务...")

        try:
            character_id = getattr(settings, 'CHARACTER_ID', 'daji')
            user_id = "default_user"

            generator = get_memory_generator()
            
            # 生成上个月的月索引
            from datetime import date, timedelta
            today = date.today()
            # 上个月的年份和月份
            if today.month == 1:
                year = today.year - 1
                month = 12
            else:
                year = today.year
                month = today.month - 1
            
            monthly_id = await generator.generate_monthly_index(
                character_id=character_id,
                user_id=user_id,
                year=year,
                month=month,
            )
            
            if monthly_id:
                logger.info(f"[MemoryScheduler] 月索引生成成功: id={monthly_id}, {year}-{month}")
            else:
                logger.info(f"[MemoryScheduler] 月索引生成跳过: {year}-{month} (无周索引)")

        except Exception as e:
            logger.error(f"[MemoryScheduler] 月索引生成任务失败: {e}")

    async def _job_generate_annual_index(self) -> None:
        """定时任务：每年12月31日凌晨5点生成年索引"""
        logger.info("[MemoryScheduler] 开始执行年索引生成任务...")

        try:
            character_id = getattr(settings, 'CHARACTER_ID', 'daji')
            user_id = "default_user"

            generator = get_memory_generator()
            
            # 生成今年的年索引（年末总结）
            from datetime import date
            year = date.today().year
            
            annual_id = await generator.generate_annual_index(
                character_id=character_id,
                user_id=user_id,
                year=year,
            )
            
            if annual_id:
                logger.info(f"[MemoryScheduler] 年索引生成成功: id={annual_id}, year={year}")
            else:
                logger.info(f"[MemoryScheduler] 年索引生成跳过: year={year} (无月索引)")

        except Exception as e:
            logger.error(f"[MemoryScheduler] 年索引生成任务失败: {e}")

    async def flush_now(self, user_id: str = "default_user", character_id: str = "daji") -> int:
        """立即执行持久化队列写入（手动触发）
        
        Args:
            user_id: 用户ID
            character_id: 角色ID
        
        Returns:
            写入的消息条数
        """
        logger.info(f"[MemoryScheduler] 手动触发持久化队列写入: user={user_id}, character={character_id}")

        try:
            buffer = await get_conversation_buffer(user_id=user_id, character_id=character_id)
            messages = await buffer.get_all_persistent_messages()

            if not messages:
                return 0

            db_messages = []
            for msg in messages:
                db_msg = {
                    "character_id": msg.get("character_id", character_id),
                    "user_id": msg.get("user_id", user_id),
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "inner_monologue": msg.get("inner_monologue"),
                    "turn_id": msg.get("turn_id"),
                    "metadata": {
                        "ts": msg.get("ts"),
                        "item_id": msg.get("item_id"),
                    },
                }
                db_messages.append(db_msg)

            record_ids = await batch_insert_chat_messages(db_messages)
            await buffer.clear_persistent_queue()

            return len(record_ids)

        except Exception as e:
            logger.error(f"[MemoryScheduler] 手动写入失败: {e}")
            return 0

    async def run_all_tasks_now(self) -> dict:
        """手动触发所有定时任务（测试用）
        
        Returns:
            各任务的执行结果
        """
        logger.info("[MemoryScheduler] 手动触发所有定时任务...")
        
        results = {}
        
        try:
            # 执行持久化队列写入
            await self._job_flush_persistent_queue()
            results["flush"] = "done"
        except Exception as e:
            results["flush"] = f"error: {e}"
        
        try:
            # 执行日记生成
            await self._job_generate_daily_diary()
            results["diary"] = "done"
        except Exception as e:
            results["diary"] = f"error: {e}"
        
        try:
            # 执行周索引生成
            await self._job_generate_weekly_index()
            results["weekly"] = "done"
        except Exception as e:
            results["weekly"] = f"error: {e}"
        
        try:
            # 执行月索引生成
            await self._job_generate_monthly_index()
            results["monthly"] = "done"
        except Exception as e:
            results["monthly"] = f"error: {e}"
        
        try:
            # 执行年索引生成
            await self._job_generate_annual_index()
            results["annual"] = "done"
        except Exception as e:
            results["annual"] = f"error: {e}"
        
        # 注意：清理任务已合并到周索引任务中，不再单独执行
        
        logger.info(f"[MemoryScheduler] 所有任务执行完成: {results}")
        return results


# 全局实例
_scheduler: Optional[MemoryScheduler] = None


def get_memory_scheduler() -> MemoryScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = MemoryScheduler()
    return _scheduler


async def start_memory_scheduler() -> None:
    """启动全局调度器"""
    scheduler = get_memory_scheduler()
    await scheduler.start()


async def stop_memory_scheduler() -> None:
    """停止全局调度器"""
    global _scheduler
    if _scheduler:
        await _scheduler.stop()